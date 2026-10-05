import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest

from autocam_service.onshape.budget import PROCEED, REFUSE, WARN, decide, estimate_calls
from autocam_service.onshape.cache import OnshapeCache
from autocam_service.onshape.client import (
    BudgetExceeded, OnshapeClient, OnshapeError, QuotaExhausted, RateLimited, sign,
)
from autocam_service.onshape.export import Exporter, ExportError, PollSchedule, TryAgainLater
from autocam_service.onshape.ledger import Ledger, budget_year_start
from autocam_service.onshape.urls import parse_link
from fakeonshape import FakeTransport, resp

D, V, E = "a" * 24, "b" * 24, "c" * 24
LINK = parse_link(f"https://cad.onshape.com/documents/{D}/v/{V}/e/{E}")
NOW = datetime(2026, 10, 1, 22, 0, tzinfo=timezone.utc)


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
    ex = Exporter(client(ledger, t), OnshapeCache(tmp_path / "cache"), POLLS, sleep=sleeps.append, clock=lambda: 0.0)
    first = ex.export(LINK, "hood_gusset", "p01")
    assert (first.part_id, first.material, first.from_cache) == ("JHD", "Aluminum - 6061", False)
    assert first.step_path.read_bytes() == b"ISO-10303-21;"
    assert sleeps == [4, 8] and len(t.sent) == 5
    again = Exporter(None, OnshapeCache(tmp_path / "cache"), POLLS).export(LINK, "hood_gusset")
    assert again.from_cache and again.step_sha256 == first.step_sha256
    assert ex.is_cached(LINK, "hood_gusset")


@pytest.mark.parametrize("name, message", [
    ("nope", "no part named 'nope'"), ("dup", "2 parts in that Part Studio are named 'dup'"), ("skin", "is a sheet body"),
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


def test_status_checks_are_timed_from_the_start_of_the_export():
    polls = PollSchedule(first_s=5, factor=3, max_s=60, max_count=4)
    assert polls.at() == [5, 20, 65, 125]
    assert polls.waits_from(0) == [5, 15, 45, 60]
    assert polls.waits_from(50) == [15, 60]                 # started a while ago: only the checks still to come
    assert polls.waits_from(20) == [0, 45, 60]              # one is due now
    assert polls.waits_from(200) == [0] and polls.waits_from(None) == [0]   # past them all: one check now


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s


class TimedTranslations(FakeTransport):
    """Each translation is done once the clock reaches its time."""

    def __init__(self, routes, clock, done_at):
        super().__init__(routes)
        self.clock, self.done_at = clock, done_at

    def send(self, method, url, headers, body):
        tid = url.rsplit("/", 1)[-1]
        if method == "GET" and "/translations/" in url:
            self.sent.append((method, url, dict(headers), body))
            done = self.clock.now >= self.done_at[tid]
            return resp(body={"requestState": "DONE" if done else "ACTIVE", "resultExternalDataIds": [f"F{tid}"] if done else []})
        return super().send(method, url, headers, body)


TWO = [{"name": "hood_gusset", "partId": "JHD", "bodyType": "solid"}, {"name": "plate", "partId": "JHP", "bodyType": "solid"}]


def test_exports_started_together_need_one_check_after_the_first(ledger, tmp_path):
    clock = Clock()
    t = TimedTranslations([("GET", "/parts/", [resp(body=TWO)]),
                           ("POST", "/translations", [resp(body={"id": "T1"}), resp(body={"id": "T2"})]),
                           ("GET", "/externaldata/", [resp(200, b"ISO 1"), resp(200, b"ISO 2")])],
                          clock, {"T1": 30, "T2": 30})
    ex = Exporter(client(ledger, t), OnshapeCache(tmp_path), PollSchedule(5, 3, 60, 4), sleep=clock.sleep, clock=clock)
    assert ex.start(LINK, "hood_gusset") and ex.start(LINK, "plate")
    a, b = ex.export(LINK, "hood_gusset"), ex.export(LINK, "plate")
    assert a.step_path.read_bytes() == b"ISO 1" and b.step_path.read_bytes() == b"ISO 2"
    kinds = ["poll" if "/translations/" in u else m for m, u, _, _ in t.sent if "/parts/" not in u and "/externaldata/" not in u]
    assert kinds == ["POST", "POST", "poll", "poll", "poll", "poll"]   # 3 for the first (5, 20, 65 s), 1 for the second
    assert clock.now == 65


def test_a_problem_met_while_starting_costs_no_second_call(ledger, tmp_path):
    t = FakeTransport([("GET", "/parts/", [resp(400, body={"message": "not a Part Studio"})])])
    ex = Exporter(client(ledger, t), OnshapeCache(tmp_path), POLLS, sleep=lambda s: None)
    assert ex.start(LINK, "hood_gusset")                    # not a reason to stop starting the others
    for _ in range(2):
        with pytest.raises(OnshapeError):
            ex.export(LINK, "hood_gusset")
    assert len(t.sent) == 1


def test_running_out_of_calls_while_starting_stops_the_starts(ledger, tmp_path):
    t = FakeTransport(export_routes())
    ex = Exporter(client(ledger, t, max_calls=1), OnshapeCache(tmp_path), POLLS, sleep=lambda s: None)
    assert not ex.start(LINK, "hood_gusset")                # the parts list fit, the translation didn't
    with pytest.raises(BudgetExceeded):
        ex.export(LINK, "hood_gusset")


def test_after_a_stop_while_starting_only_cached_parts_come_through(ledger, tmp_path):
    cache = OnshapeCache(tmp_path)
    cache.put_parts(LINK, TWO + [{"name": "bracket", "partId": "JHB", "bodyType": "solid"}])
    cache.put_translation(LINK, "JHD", "T1", 0.0)           # hood_gusset: started, not downloaded
    cache.put_step(LINK, "JHB", b"ISO bracket")             # bracket: cached
    t = FakeTransport([("POST", "/translations", [resp(429, b"", retry_after="600")])])
    ex = Exporter(client(ledger, t), cache, POLLS, sleep=lambda s: None, clock=lambda: 0.0)
    assert not ex.start(LINK, "plate")                      # a long rate limit
    with pytest.raises(RateLimited):
        ex.export(LINK, "hood_gusset")                      # its checks aren't even tried
    assert ex.export(LINK, "bracket").from_cache
    assert len(t.sent) == 1


@pytest.mark.parametrize("started", [None, -600.0])
def test_an_export_left_from_an_earlier_run_gets_one_check(ledger, tmp_path, started):
    cache = OnshapeCache(tmp_path)
    cache.put_parts(LINK, PARTS)
    cache.put_translation(LINK, "JHD", "T1", started)       # None: kept before the start time was
    t = FakeTransport([("GET", "/translations/T1", [resp(body={"requestState": "ACTIVE"})] * 3)])
    ex = Exporter(client(ledger, t), cache, POLLS, sleep=lambda s: None, clock=lambda: 0.0)
    with pytest.raises(TryAgainLater):
        ex.export(LINK, "hood_gusset")
    assert len(t.sent) == 1 and cache.translation(LINK, "JHD") == "T1"


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


# ---- review fixes

def test_entry_after_a_torn_line_still_counts(ledger):
    a = ledger.begin(run_id="r1", method="GET", url="u", purpose="p")
    ledger.finish(a, 200, 1, billable=True)
    with open(ledger.path, "a") as f:
        f.write('{"id": "torn", "phase": "pen')          # crash mid-append, no newline
    b = ledger.begin(run_id="r1", method="GET", url="u", purpose="p")
    ledger.finish(b, 200, 1, billable=True)
    assert ledger.month_count() == 2


@pytest.mark.parametrize("content", ["{}", "null", "[]", "0", "", "garbage"])
def test_any_latch_file_stops_calls(ledger, content):
    ledger.latch_path.parent.mkdir(parents=True, exist_ok=True)
    ledger.latch_path.write_text(content)
    assert ledger.latched()
    t = FakeTransport([("GET", "/parts/", [resp(body=[])])])
    with pytest.raises(BudgetExceeded):
        client(ledger, t).get_json("/api/v10/parts/d/x", purpose="p")
    assert t.sent == []


@pytest.mark.parametrize("location", ["http://cad.onshape.com/api/v10/x", "https://cad.onshape.com:8443/api/v10/x"])
def test_no_signature_over_plain_http_or_other_ports(ledger, location):
    t = FakeTransport([("GET", "/externaldata/", [resp(307, b"", location=location)]),
                       ("GET", "/api/v10/x", [resp(200, b"data")])])
    client(ledger, t).get_bytes("/api/v10/documents/d/x/externaldata/f", purpose="dl")
    assert "Authorization" not in t.sent[1][2]


# ---- workspace links: pinned to a microversion per run; the cache follows the microversion

WS = f"https://cad.onshape.com/documents/{'a' * 24}/w/{'c' * 24}/e/{'d' * 24}"


def ws_routes(mid, export=True):
    routes = [("GET", "/currentmicroversion", [resp(body={"microversion": mid})]),
              ("GET", f"/parts/d/{'a' * 24}/w/{'c' * 24}/", [resp(body=PARTS)])]
    if export:
        routes += [("POST", f"/partstudios/d/{'a' * 24}/w/{'c' * 24}/", [resp(body={"id": "T1"})]),
                   ("GET", "/translations/T1", [resp(body={"requestState": "DONE", "resultExternalDataIds": ["F1"]})]),
                   ("GET", "/externaldata/F1", [resp(200, b"ISO-10303-21; step")])]
    return routes


def exporter(ledger, tmp_path, routes):
    t = FakeTransport(routes)
    return Exporter(client(ledger, t), OnshapeCache(tmp_path / "cache"), POLLS, sleep=lambda s: None), t


def test_workspace_link_is_pinned_and_exported_from_the_workspace(ledger, tmp_path):
    ex, t = exporter(ledger, tmp_path, ws_routes("e" * 24))
    out = ex.export(parse_link(WS), "hood_gusset")
    assert out.microversion == "e" * 24 and not out.from_cache
    urls = [u for _, u, _, _ in t.sent]
    assert "/currentmicroversion" in urls[0] and len(urls) == 5
    assert out.step_path.name.startswith(f"{'a' * 24}_m{'e' * 24}_{'d' * 24}")


def test_unchanged_workspace_costs_one_call_next_run(ledger, tmp_path):
    ex, _ = exporter(ledger, tmp_path, ws_routes("e" * 24))
    ex.export(parse_link(WS), "hood_gusset")
    again, t = exporter(ledger, tmp_path, [("GET", "/currentmicroversion", [resp(body={"microversion": "e" * 24})])])
    out = again.export(parse_link(WS), "hood_gusset")
    assert out.from_cache and len(t.sent) == 1


def test_edited_workspace_is_exported_again(ledger, tmp_path):
    ex, _ = exporter(ledger, tmp_path, ws_routes("e" * 24))
    ex.export(parse_link(WS), "hood_gusset")
    again, t = exporter(ledger, tmp_path, ws_routes("f" * 24))
    out = again.export(parse_link(WS), "hood_gusset")
    assert not out.from_cache and out.microversion == "f" * 24 and len(t.sent) == 5


def test_one_pin_per_workspace_per_run(ledger, tmp_path):
    ex, t = exporter(ledger, tmp_path, ws_routes("e" * 24, export=False))
    ex.find_part(parse_link(WS), "hood_gusset")
    ex.find_part(parse_link(WS), "HOOD_GUSSET")
    assert sum("/currentmicroversion" in u for _, u, _, _ in t.sent) == 1
    assert not ex.is_cached(parse_link(WS), "hood_gusset")     # unpinned: never assumed cached


@pytest.mark.parametrize("title", ["HOOD_GUSSET", "  hood_gusset ", "Hood_Gusset"])
def test_part_names_ignore_case_and_spaces(ledger, tmp_path, title):
    ex, _ = exporter(ledger, tmp_path, [("GET", "/parts/", [resp(body=PARTS)])])
    assert ex.find_part(LINK, title)["partId"] == "JHD"


def test_single_part_studio_needs_no_name(ledger, tmp_path):
    only = [{"name": "Part 1", "partId": "JHD", "bodyType": "solid"}]
    ex, _ = exporter(ledger, tmp_path, [("GET", "/parts/", [resp(body=only)])])
    assert ex.find_part(LINK, "left gusset")["partId"] == "JHD"


def test_a_name_that_isnt_there_suggests_the_closest():
    from autocam_service.onshape.export import closest_name, pick_part
    parts = [{"name": n, "bodyType": "solid"} for n in ("P-2011", "P-2015", "P-2032", "35T HTD Belt")]
    with pytest.raises(ExportError, match=r"no part named 'P-032' in that Part Studio\. Did you mean P-2032\? "
                                          r"Make the card title that\. Parts there: 35T HTD Belt, P-2011"):
        pick_part(parts, "P-032")
    assert closest_name("p 2015", ["P-2015", "P-2011"]) == "P-2015"
    with pytest.raises(ExportError, match="Make the card title the part's name"):
        pick_part(parts, "intake roller")                       # nothing close: no guess
