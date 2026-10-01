from datetime import datetime, timezone

from autocam_service.health import Health, heartbeat_age_s, is_stale, render

NOW = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)


def health(**kw):
    base = dict(now=NOW, heartbeat={"ts": "2026-10-02T14:59:00Z", "job": None}, queue_incoming=1,
                queue_processing=0, month_calls=12, month_soft=150, year_calls=80, year_cap=1500, latch=None,
                active_run="r004", last_error=None)
    base.update(kw)
    return Health(**base)


def test_alive_worker():
    h = health()
    assert heartbeat_age_s(NOW, h.heartbeat) == 60 and not is_stale(h, 300)
    text = render(h, 300)
    assert "Fusion worker: alive (idle)." in text and "1 waiting, 0 running" in text
    assert "12 this month (soft limit 150), 80 of 1500" in text and "Active run: r004." in text


def test_stale_or_missing_heartbeat():
    old = health(heartbeat={"ts": "2026-10-02T13:00:00Z", "job": "r004-al6061"})
    assert is_stale(old, 300) and "last seen 120 min ago" in render(old, 300)
    missing = health(heartbeat=None)
    assert is_stale(missing, 300) and "no heartbeat yet" in render(missing, 300)
    garbage = health(heartbeat={"ts": "yesterday"})
    assert is_stale(garbage, 300)


def test_latch_and_error_are_shown():
    text = render(health(latch={"ts": "2026-10-02T10:00:00Z", "reason": "402"}, last_error="Trello 401"), 300)
    assert "Onshape calls are STOPPED" in text and "Last error: Trello 401" in text
