"""M5: the System card's status, and the comments when jobs wait for a Fusion that isn't running."""

import types
from datetime import datetime, timedelta, timezone

from autocam_service import app
from autocam_service.runner import Runner
from autocam_service.tracker.dryrun import DryRunTracker
from test_runner_autostart import SYS, add, harness, step_card


def updates(h):
    return [e for e in h.tracker.log if e[0] == "update" and e[1] == SYS]


def beat(h, at, job=None):
    h.queue.write_heartbeat({"ts": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "job": job, "core_version": "0.2.0"})


def test_status_is_written_when_it_changes_and_every_ten_minutes(tmp_path):
    h = harness(tmp_path, delay=120)
    beat(h, h.now)
    h.runner.tick()
    h.runner.report_health()
    desc = h.tracker.cards[SYS].desc
    assert desc.startswith("Status at") and "Fusion: running, idle." in desc and "Run: none." in desc
    h.now += timedelta(minutes=1)
    beat(h, h.now)
    h.runner.report_health()
    assert len(updates(h)) == 1                                   # nothing changed: not rewritten
    add(h, step_card("c1", "plate"))
    h.runner.tick()
    h.runner.report_health()
    assert len(updates(h)) == 2
    assert "Ready for CAM: 1 card waiting, the next run starts in about 2 min." in h.tracker.cards[SYS].desc
    h.tracker.move("c1", "inbox")
    h.runner.tick()
    h.runner.report_health()
    h.now += timedelta(minutes=10)
    beat(h, h.now)
    h.runner.report_health()
    assert len(updates(h)) == 4                                   # the time is refreshed every 10 minutes


def test_one_comment_when_fusion_is_down_with_work_waiting_and_one_when_it_is_back(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"), delay=0)
    beat(h, h.now - timedelta(hours=1))                           # Fusion closed an hour ago
    h.runner.tick()                                               # r001 queues a job
    h.runner.report_health()
    h.runner.report_health()
    down = [c for c in h.tracker.comments_on(SYS) if c.startswith("Fusion isn't running")]
    assert down == ["Fusion isn't running, and 1 job is waiting for it. Start Fusion with the auto-CAM add-in on "
                    "the shop PC."]
    assert "Fusion: NOT RUNNING (last seen 60 min ago)" in h.tracker.cards[SYS].desc
    beat(h, h.now)
    h.runner.report_health()
    h.runner.report_health()
    assert [c for c in h.tracker.comments_on(SYS) if c.startswith("Fusion is running again")] == [
        "Fusion is running again. The waiting jobs carry on."]


def test_a_quiet_night_with_fusion_closed_says_nothing(tmp_path):
    h = harness(tmp_path)
    h.runner.tick()
    h.runner.report_health()
    assert "no sign of the auto-CAM add-in yet" in h.tracker.cards[SYS].desc
    assert h.tracker.comments_on(SYS) == []


def test_dry_runs_write_no_status(tmp_path):
    h = harness(tmp_path)
    dry = DryRunTracker(h.tracker)
    runner = Runner(h.runner.s.__class__(**{**h.runner.s.__dict__, "tracker": dry}), start_delay_s=0)
    runner.report_health()
    assert dry.intended == [] and h.tracker.cards[SYS].desc == ""


def test_the_last_error_reaches_the_status():
    seen, ticks = [], []

    def tick():
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("Trello went away")
    runner = types.SimpleNamespace(tick=tick, report_health=seen.append)
    answers = [False, True]
    code = types.SimpleNamespace(should_restart=lambda: answers.pop(0))
    now = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
    assert app.run_forever(runner, 60, code, sleep=lambda s: None, clock=lambda: now) == app.EXIT_RESTART
    assert seen[0].endswith(": RuntimeError: Trello went away") and seen[1] == seen[0]
