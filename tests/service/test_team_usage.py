"""The whole team's Onshape API count on the System card (panel/usage.py, 2026-11-01): the endpoint the enterprise
admin's Developer page reads, one call a day through the panel's limits."""

from datetime import datetime, timezone

from autocam_service.panel.onshape_reads import PanelOnshape
from autocam_service.panel.usage import TeamUsage, card_line, summary
from autocam_service.onshape.client import OnshapeClient
from fakeonshape import FakeTransport, resp
from test_panel import Clock
from test_runner_e2e import Harness

COMPANY = "652459d6412ce911b57afb73"
DAY = 24 * 3600.0
NOV1 = datetime(2026, 11, 1, 17, 0, tzinfo=timezone.utc).timestamp()


def body(start, keys=45):
    """As the real one came back (2026-11-01), counted from `start`."""
    return {"keyCount": keys, "apiUsageStartDate": "2026-02-20T00:00:00.000+00:00", "keyOverageCount": 0,
            "clientOverageCount": 0, "allocationInfo": {"allocationStartDate": "2026-10-09T00:00:00.000+00:00",
                                                        "apiLimit": 10000, "allocationEndDate": "2027-10-09T00:00:00.000+00:00",
                                                        "ownerType": 1, "overageEnabled": False},
            "endDate": "2026-11-02T00:00:00.000+00:00", "startDate": f"{start}T00:00:00.000+00:00",
            "ownerId": COMPANY, "clientCount": 0}


def rig(tmp_path, answers):
    h = Harness(tmp_path, card_list=[])
    clock = Clock()
    clock.t = NOV1
    transport = FakeTransport([("GET", "/metrics/api/summary", answers)])

    def client_for(n):
        return OnshapeClient(base_url=h.cfg.onshape.base_url, access_key="AK", secret_key="SK", ledger=h.ledger,
                             transport=transport, run_id="panel", max_calls=n, retry_after_max_wait_s=0,
                             sleep=lambda s: None)

    onshape = PanelOnshape(ledger=h.ledger, client_for=client_for, cache_root=h.cfg.paths.cache, calls_per_day=60,
                           calls_per_year=1500, year_start="01-01", parts_cache_s=600, assembly_cache_s=1800,
                           max_assemblies=4)
    usage = TeamUsage(onshape, COMPANY, tmp_path / "usage.json", 24, clock=clock)
    return usage, transport, clock, h


def test_the_response_is_read_as_the_cycles_count_and_limit():
    assert summary(body("2026-10-09")) == {"count": 45, "limit": 10000, "cycle_start": "2026-10-09",
                                           "cycle_end": "2027-10-09", "counted_from": "2026-10-09"}
    assert summary({"keyCount": 3}) is None and summary([]) is None


def test_the_first_read_finds_the_cycle_then_its_one_call_a_day(tmp_path):
    usage, transport, clock, h = rig(tmp_path, [resp(body=body("2025-10-31", 300)), resp(body=body("2026-10-09"))] +
                                     [resp(body=body("2026-10-09", 60))])
    assert usage.tick()
    first, second = [u for _, u, _, _ in transport.sent]
    assert "startDate=2025-10-31" in first and "startDate=2026-10-09" in second and f"ownerId={COMPANY}" in second
    assert usage.state()["count"] == 45                      # counted from the cycle's start, not a year back
    assert not usage.tick() and len(transport.sent) == 2      # not again the same day
    clock.t += DAY
    assert usage.tick() and len(transport.sent) == 3 and usage.state()["count"] == 60
    assert h.ledger.day_count(panel=True) == 3 and h.ledger.month_count(panel=False) == 0   # the panel's calls


def test_keys_that_cant_read_it_wait_a_week(tmp_path):
    for status in (403, 401):
        (tmp_path / str(status)).mkdir()
        usage, transport, clock, _ = rig(tmp_path / str(status), [resp(status=status, body={"message": "No"})] * 2)
        assert not usage.tick() and len(transport.sent) == 1
        clock.t += 6 * DAY
        assert not usage.tick() and len(transport.sent) == 1
        clock.t += 2 * DAY
        usage.tick()
        assert len(transport.sent) == 2


def test_onshape_not_answering_is_tried_again_hours_later(tmp_path):
    usage, transport, clock, _ = rig(tmp_path, [resp(status=503, body={})] + [resp(body=body("2026-10-09"))] * 2)
    assert not usage.tick()
    sent = len(transport.sent)
    clock.t += 3600
    assert not usage.tick() and len(transport.sent) == sent
    clock.t += 6 * 3600
    assert usage.tick()


def test_the_system_card_line(tmp_path):
    usage, _, clock, _ = rig(tmp_path, [resp(body=body("2026-10-09"))])
    now = datetime.fromtimestamp(NOV1, timezone.utc)
    assert card_line(tmp_path / "usage.json", now) == ""     # nothing read yet
    usage.tick()
    assert card_line(tmp_path / "usage.json", now) == (
        "Whole team's Onshape API calls (everyone's, from Onshape): 45 of 10,000 (0%) this year, which ends "
        "Oct 9, 2027 (read Nov 1).")
    later = datetime.fromtimestamp(NOV1 + 3 * DAY, timezone.utc)
    assert card_line(tmp_path / "usage.json", later) == ""                          # read daily: missed two reads
    assert card_line(tmp_path / "usage.json", later, every_hours=168).startswith("Whole team's")   # read weekly
    assert card_line(tmp_path / "usage.json", datetime.fromtimestamp(NOV1 + 15 * DAY, timezone.utc), 168) == ""
