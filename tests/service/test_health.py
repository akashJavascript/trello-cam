"""The System card's status text (health.py)."""

from datetime import datetime, timezone

from autocam_service.health import Health, heartbeat_age_s, is_stale, render, stuck

NOW = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)


def health(**kw):
    base = dict(now=NOW, heartbeat={"ts": "2026-10-02T14:59:00Z", "job": None, "core_version": "0.2.0"},
                service_core="0.2.0", queue_incoming=0, queue_processing=0, month_calls=12, month_soft=150,
                year_calls=80, year_cap=1500, latch=None, active_run=None, last_error=None)
    base.update(kw)
    return Health(**base)


def test_all_quiet():
    h = health()
    assert heartbeat_age_s(NOW, h.heartbeat) == 60 and not is_stale(h, 300)
    text = render(h, 300)
    lines = text.split("\n")
    assert lines[0].startswith("Status at ") and lines[0].endswith("The service updates this while it runs.")
    assert lines[1:] == ["Fusion: running, idle.", "Run: none.", "Ready for CAM: nothing new waiting.",
                         "Onshape calls: 12 this month (warning at 150), 80 of 1500 this budget year."]
    assert "*" not in text and "`" not in text


def test_a_run_and_waiting_cards():
    h = health(heartbeat={"ts": "2026-10-02T14:59:50Z", "job": "r006-al6061", "core_version": "0.2.0"},
               queue_processing=1, queue_incoming=1, active_run="r006", waiting_cards=2)
    text = render(h, 300)
    assert "Fusion: running job r006-al6061." in text and "Run: r006, 2 jobs in Fusion." in text
    assert "Ready for CAM: 2 cards waiting, the next run starts after this one." in text
    soon = render(health(waiting_cards=1, next_run_in_s=70), 300)
    assert "Ready for CAM: 1 card waiting, the next run starts in about 1 min." in soon


def test_fusion_down_and_stuck():
    old = health(heartbeat={"ts": "2026-10-02T13:00:00Z", "job": None})
    assert is_stale(old, 300) and "NOT RUNNING (last seen 2 h ago)" in render(old, 300)
    assert not stuck(old, 300)                                  # nothing waiting: no alarm
    assert stuck(health(heartbeat=None, queue_incoming=2), 300)
    assert "no sign of the auto-CAM add-in yet" in render(health(heartbeat=None), 300)
    assert is_stale(health(heartbeat={"ts": "yesterday"}), 300)


def test_old_add_in_latch_and_error():
    text = render(health(heartbeat={"ts": "2026-10-02T14:59:00Z", "job": None, "core_version": "0.1.0"},
                         latch={"ts": "2026-10-02T10:00:00Z", "reason": "402"}, last_error="3:00 PM: Trello 401"), 300)
    assert "The add-in runs older code (0.1.0; the service is 0.2.0)" in text
    assert "ONSHAPE CALLS ARE STOPPED" in text and "Last error: 3:00 PM: Trello 401" in text
    # The other way round (the service hasn't restarted into new code yet): no telling anyone to restart Fusion.
    newer = render(health(heartbeat={"ts": "2026-10-02T14:59:00Z", "job": None, "core_version": "0.10.0"}), 300)
    assert "The service runs older code (0.2.0; the add-in is 0.10.0)" in newer and "Stop and Run" not in newer


def test_without_time_only_changes_when_something_does():
    a = render(health(), 300, with_time=False)
    b = render(health(now=NOW.replace(minute=5), heartbeat={"ts": "2026-10-02T15:04:00Z", "job": None,
                                                              "core_version": "0.2.0"}), 300, with_time=False)
    assert a == b and not a.startswith("Status at")
