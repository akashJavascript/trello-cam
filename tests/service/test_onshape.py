import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest

from autocam_service.onshape.budget import PROCEED, REFUSE, WARN, decide, estimate_calls
from autocam_service.onshape.cache import OnshapeCache
from autocam_service.onshape.client import (
    BudgetExceeded, HttpResponse, OnshapeClient, OnshapeError, QuotaExhausted, RateLimited, sign,
)
from autocam_service.onshape.export import Exporter, ExportError, PollSchedule
from autocam_service.onshape.ledger import Ledger, budget_year_start
from autocam_service.onshape.urls import parse_link

D, V, E = "a" * 24, "b" * 24, "c" * 24
LINK = parse_link(f"https://cad.onshape.com/documents/{D}/v/{V}/e/{E}")
NOW = datetime(2026, 10, 1, 22, 0, tzinfo=timezone.utc)


def resp(status=200, body=b"{}", **headers):
    if not isinstance(body, bytes):
        body = json.dumps(body).encode()
    return HttpResponse(status, {k.lower().replace("_", "-"): v for k, v in headers.items()}, body)


class FakeTransport:
    """Scripted Onshape: routes are (method, url fragment, [responses...]) consumed in order."""

    def __init__(self, routes):
        self.routes = [(m, frag, list(rs)) for m, frag, rs in routes]
        self.sent = []

    def send(self, method, url, headers, body):
        self.sent.append((method, url, dict(headers), body))
        for m, frag, rs in self.routes:
            if m == method and frag in url and rs:
                r = rs.pop(0)
                if isinstance(r, Exception):
                    raise r
                return r
        raise AssertionError(f"unexpected request {method} {url}")


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "state" / "onshape_ledger.jsonl", clock=lambda: NOW)


def client(ledger, transport, max_calls=50, max_wait=60.0, sleeps=None):
    return OnshapeClient(base_url="https://cad.onshape.com", access_key="AK", secret_key="SK", ledger=ledger,
                         transport=transport, run_id="r001", max_calls=max_calls, retry_after_max_wait_s=max_wait,
                         sleep=(sleeps.append if sleeps is not None else lambda s: None),
                         nonce=lambda: "n" * 25, date=lambda: "Thu, 01 Oct 2026 22:00:00 GMT")


# ---- ledger

def test_ledger_counts_billable_and_dangling_calls(ledger):
    a = ledger.begin(run_id="r1", method="GET", url="u", purpose="p")
    ledger.finish(a, 200, 10, billable=True)
    b = ledger.begin(run_id="r1", method="GET", url="u", purpose="p")
    ledger.finish(b, 429, 10, billable=False)
    ledger.begin(run_id="r1", method="GET", url="u", purpose="p")          # crashed mid-request
    ledger.blocked(run_id="r1", method="GET", url="u", purpose="p", reason="cap")
    with open(ledger.path, "a") as f:
        f.write('{"id": "torn", "pha')                                       # torn last line
    assert ledger.month_count() == 2
    assert ledger.year_count("01-01") == 2


def test_month_and_budget_year_windows(tmp_path):
    clock = {"now": datetime(2026, 8, 31, 12, tzinfo=timezone.utc)}
    led = Ledger(tmp_path / "l.jsonl", clock=lambda: clock["now"])
    led.finish(led.begin(run_id="r", method="GET", url="u", purpose="p"), 200, 1, billable=True)
    clock["now"] = datetime(2026, 9, 2, tzinfo=timezone.utc)
    assert led.month_count() == 0
    assert led.year_count("09-01") == 0 and led.year_count("01-01") == 1
    assert budget_year_start(datetime(2026, 3, 5, tzinfo=timezone.utc), "09-01") == datetime(2025, 9, 1, tzinfo=timezone.utc)


def test_latch(ledger):
    assert ledger.latched() is None
    ledger.latch("402")
    assert ledger.latched()["reason"] == "402"
    assert ledger.reset_latch() and ledger.latched() is None


# ---- budget

@pytest.mark.parametrize("kwargs, action", [
    (dict(estimate=10, month_used=0, year_used=0, latched=True), REFUSE),
    (dict(estimate=61, month_used=0, year_used=0, latched=False), REFUSE),
    (dict(estimate=10, month_used=0, year_used=1495, latched=False), REFUSE),
    (dict(estimate=10, month_used=145, year_used=200, latched=False), WARN),
    (dict(estimate=10, month_used=0, year_used=0, latched=False), PROCEED),
])
def test_budget_decisions(kwargs, action):
    assert decide(per_run_max=60, monthly_soft=150, yearly_cap=1500, **kwargs).action == action


def test_estimate():
    assert estimate_calls(parts_to_export=3, studios_to_list=2, calls_per_part=5) == 17


# ---- signing

def test_hmac_signature_follows_onshape_scheme():
    h = sign(access_key="AK", secret_key="SK", method="GET", url="https://cad.onshape.com/api/v10/parts/d/X?a=B",
             content_type="application/json", nonce="N" * 25, date="Thu, 01 Oct 2026 22:00:00 GMT")
    text = ("get\n" + "n" * 25 + "\nthu, 01 oct 2026 22:00:00 gmt\napplication/json\n/api/v10/parts/d/x\na=b\n")
    expected = base64.b64encode(hmac.new(b"SK", text.encode(), hashlib.sha256).digest()).decode()
    assert h["Authorization"] == f"On AK:HmacSHA256:{expected}"
    assert h["On-Nonce"] == "N" * 25 and h["Date"].endswith("GMT")


# ---- client

def test_successful_call_is_signed_and_ledgered(ledger):
    t = FakeTransport([("GET", "/parts/", [resp(body=[{"name": "x"}])])])
    c = client(ledger, t)
    assert c.get_json("/api/v10/parts/d/x", purpose="parts_list") == [{"name": "x"}]
    assert t.sent[0][2]["Authorization"].startswith("On AK:HmacSHA256:")
    assert [e["phase"] for e in ledger.entries()] == ["pending", "done"]
    assert ledger.month_count() == 1 and "SK" not in repr(c)


def test_redirect_elsewhere_drops_the_keys(ledger):
    t = FakeTransport([
        ("GET", "/externaldata/", [resp(307, b"", location="https://files.example.com/blob?sig=1")]),
        ("GET", "files.example.com", [resp(200, b"STEPDATA")]),
    ])
    assert client(ledger, t).get_bytes("/api/v10/documents/d/x/externaldata/f", purpose="dl") == b"STEPDATA"
    assert "Authorization" in t.sent[0][2] and "Authorization" not in t.sent[1][2]
    assert ledger.month_count() == 1     # the 307 counts; the storage host isn't an Onshape call


def test_402_latches_and_stops_every_later_call(ledger):
    t = FakeTransport([("GET", "/parts/", [resp(402, b"out of calls")])])
    c = client(ledger, t)
    with pytest.raises(QuotaExhausted):
        c.get_json("/api/v10/parts/d/x", purpose="p")
    assert ledger.latched()
    with pytest.raises(BudgetExceeded, match="402"):
        c.get_json("/api/v10/parts/d/y", purpose="p")
    assert len(t.sent) == 1


def test_short_429_waits_and_retries_long_429_stops(ledger):
    sleeps = []
    t = FakeTransport([("GET", "/parts/", [resp(429, b"", retry_after="5"), resp(body=[])])])
    assert client(ledger, t, sleeps=sleeps).get_json("/api/v10/parts/d/x", purpose="p") == []
    assert sleeps == [5.0] and ledger.month_count() == 1   # the 429 isn't billable
    t = FakeTransport([("GET", "/parts/", [resp(429, b"", retry_after="450")])])
    with pytest.raises(RateLimited):
        client(ledger, t).get_json("/api/v10/parts/d/x", purpose="p")


def test_per_run_cap_blocks_before_sending(ledger):
    t = FakeTransport([("GET", "/parts/", [resp(body=[]), resp(body=[])])])
    c = client(ledger, t, max_calls=1)
    c.get_json("/api/v10/parts/d/x", purpose="p")
    with pytest.raises(BudgetExceeded, match="limit of 1"):
        c.get_json("/api/v10/parts/d/x", purpose="p")
    assert len(t.sent) == 1 and ledger.entries()[-1]["phase"] == "blocked"


def test_network_failure_is_counted_conservatively(ledger):
    t = FakeTransport([("GET", "/parts/", [ConnectionError("reset")])])
    with pytest.raises(OnshapeError, match="request to Onshape failed"):
        client(ledger, t).get_json("/api/v10/parts/d/x", purpose="p")
    assert ledger.month_count() == 1


def test_http_error_carries_status(ledger):
    t = FakeTransport([("GET", "/parts/", [resp(404, b"no such element")])])
    with pytest.raises(OnshapeError) as exc:
        client(ledger, t).get_json("/api/v10/parts/d/x", purpose="p")
    assert exc.value.status == 404 and "no such element" in str(exc.value)


# ---- export

PARTS = [{"name": "hood_gusset", "partId": "JHD", "bodyType": "solid", "material": {"displayName": "Aluminum - 6061"}},
         {"name": "dup", "partId": "JHG"}, {"name": "dup", "partId": "JHK"},
         {"name": "skin", "partId": "JHM", "bodyType": "sheet"}]
POLLS = PollSchedule(first_s=4, factor=2, max_s=30, max_count=3)


def export_routes(poll_states=("ACTIVE", "DONE")):
    polls = [resp(body={"requestState": s, "resultExternalDataIds": ["F1"] if s == "DONE" else []})
             for s in poll_states]
    return [("GET", "/parts/", [resp(body=PARTS)]),
            ("POST", "/translations", [resp(body={"id": "T1", "requestState": "ACTIVE"})]),
            ("GET", "/translations/T1", polls),
            ("GET", "/externaldata/F1", [resp(200, b"ISO-10303-21;")])]


def test_export_then_cache_hit_costs_nothing(ledger, tmp_path):
    t = FakeTransport(export_routes())
    sleeps = []
    ex = Exporter(client(ledger, t), OnshapeCache(tmp_path / "cache"), POLLS, sleep=sleeps.append)
    first = ex.export(LINK, "hood_gusset", "p01")
    assert (first.part_id, first.material, first.from_cache) == ("JHD", "Aluminum - 6061", False)
    assert first.step_path.read_bytes() == b"ISO-10303-21;"
    assert sleeps == [4, 8] and len(t.sent) == 5
    again = Exporter(None, OnshapeCache(tmp_path / "cache"), POLLS).export(LINK, "hood_gusset")
    assert again.from_cache and again.step_sha256 == first.step_sha256
    assert ex.is_cached(LINK, "hood_gusset")


@pytest.mark.parametrize("name, message", [
    ("nope", "no part named 'nope'"), ("dup", "2 parts are named 'dup'"), ("skin", "is a sheet body"),
])
def test_part_lookup_problems(ledger, tmp_path, name, message):
    ex = Exporter(client(ledger, FakeTransport(export_routes())), OnshapeCache(tmp_path), POLLS, sleep=lambda s: None)
    with pytest.raises(ExportError, match=message):
        ex.export(LINK, name)


def test_unfinished_translation_resumes_next_run(ledger, tmp_path):
    cache = OnshapeCache(tmp_path)
    t = FakeTransport(export_routes(poll_states=("ACTIVE", "ACTIVE", "ACTIVE")))
    with pytest.raises(ExportError, match="didn't finish yet"):
        Exporter(client(ledger, t), cache, POLLS, sleep=lambda s: None).export(LINK, "hood_gusset")
    assert cache.translation(LINK, "JHD") == "T1"
    t2 = FakeTransport([("GET", "/translations/T1", [resp(body={"requestState": "DONE", "resultExternalDataIds": ["F1"]})]),
                        ("GET", "/externaldata/F1", [resp(200, b"ISO")])])
    got = Exporter(client(ledger, t2), cache, POLLS, sleep=lambda s: None).export(LINK, "hood_gusset")
    assert not any(m == "POST" for m, *_ in t2.sent)       # no second translation paid for
    assert got.step_path.read_bytes() == b"ISO" and cache.translation(LINK, "JHD") is None


def test_failed_translation(ledger, tmp_path):
    routes = export_routes(poll_states=("FAILED",))
    routes[2][2][0] = resp(body={"requestState": "FAILED", "failureReason": "bad geometry"})
    cache = OnshapeCache(tmp_path)
    with pytest.raises(ExportError, match="bad geometry"):
        Exporter(client(ledger, FakeTransport(routes)), cache, POLLS, sleep=lambda s: None).export(LINK, "hood_gusset")
    assert cache.translation(LINK, "JHD") is None


def test_offline_export_needs_the_cache(tmp_path):
    with pytest.raises(ExportError, match="Onshape calls are off"):
        Exporter(None, OnshapeCache(tmp_path), POLLS).export(LINK, "hood_gusset")
