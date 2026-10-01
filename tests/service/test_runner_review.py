"""Regression tests for the safety review of the runner (crash-safety, trust, dry runs, checklist guard)."""

import json

import pytest

from autocam_core.hotfolder import Queue
from autocam_service.runner import Runner
from autocam_service.tracker.dryrun import DryRunTracker
from autocam_service.tracker.fake import FakeTracker
from fakeworker import run_fake_worker
from test_runner_e2e import Harness, cards


def use_tracker(h, tracker):
    if isinstance(tracker, FakeTracker):
        tracker.downloads["att-d"] = b"ISO-10303-21; spacer"
    h.tracker = tracker
    h.runner = Runner(h.runner.s.__class__(**{**h.runner.s.__dict__, "tracker": tracker}))


class FailingOnce(FakeTracker):
    """Fails the first comment on the control card (Trello hiccup right after jobs were queued)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.failed = False

    def _comment(self, card_id, text):
        if card_id == "ctl" and not self.failed:
            self.failed = True
            raise ConnectionError("Trello went away")
        super()._comment(card_id, text)


def test_trello_failure_after_queueing_resumes_the_same_run(tmp_path):
    h = Harness(tmp_path)
    use_tracker(h, FailingOnce(cards()))
    with pytest.raises(ConnectionError):
        h.runner.tick()
    spent = h.ledger.month_count()
    for _ in range(3):
        h.runner.tick()
    assert h.store.run_ids() == ["r001"]
    assert h.ledger.month_count() == spent                       # nothing exported twice
    assert sorted(h.queue.pending()) == ["r001-al5052", "r001-al6061", "r001-pc_smoked"]
    started = [t for t in h.tracker.comments_on("ctl") if t.startswith("Run r001 started")]
    assert len(started) == 1 and h.list_of("ctl") == "control"


def test_crash_between_recording_and_submitting_a_job(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    real_submit = Queue.submit
    calls = {"n": 0}

    def flaky_submit(self, job_id, text, resume=False):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk hiccup")
        return real_submit(self, job_id, text, resume)

    monkeypatch.setattr(Queue, "submit", flaky_submit)
    with pytest.raises(OSError):
        h.runner.tick()
    spent = h.ledger.month_count()
    h.runner.tick()                                               # resumes the interrupted start
    assert h.store.run_ids() == ["r001"] and h.ledger.month_count() == spent
    assert sorted(h.queue.pending()) == ["r001-al5052", "r001-al6061"]   # recorded job resubmitted
    assert any("interrupted while starting" in t for t in h.tracker.comments_on("ctl"))
    assert h.list_of("cb") == "ready_for_cam"                     # never reached: stays queued


def _rewrite_result(h, job_id, fn):
    path = h.queue.done / job_id / "result.json"
    data = json.loads(path.read_text())
    fn(data, h.queue.done / job_id)
    path.write_text(json.dumps(data))


def test_service_does_not_trust_the_workers_cut_plan(tmp_path):
    h = Harness(tmp_path)
    h.runner.tick()
    run_fake_worker(h.queue)

    def lie(data, folder):
        sheet = data["sheets"][0]
        tap = folder / sheet["tap"]
        text = tap.read_bytes().decode()
        no_pause = "\r\n".join(l for l in text.split("\r\n")
                               if l not in ("[PART 1 DONE - PAUSE]", "[REMOVE PART THEN RESUME]", "M0", "M12 C8",
                                            "M11 C8", "M5", "G53 P10", "S18000", "M3", "G4 X4") or l == "")
        tap.write_bytes(no_pause.encode())
        import hashlib
        sheet["tap_sha256"] = sheet["guard"]["sha256"] = hashlib.sha256(no_pause.encode()).hexdigest()
        sheet["outer_order"] = []
        sheet["thickness_in"] = 0.0

    _rewrite_result(h, "r001-al6061", lie)
    h.runner.tick()
    alu = [c for c in h.sheet_cards() if "6061" in c.name][0]
    assert alu.name.startswith("NOT CUTTABLE")
    assert "not a 6061 stock thickness" in alu.desc
    assert not any(n.endswith(".tap") for n in h.files_on(alu.id))
    assert h.list_of("ca") == "needs_fixing"


def test_part_reported_placed_nowhere_is_not_nested(tmp_path):
    h = Harness(tmp_path)
    h.runner.tick()
    run_fake_worker(h.queue)

    def nothing_placed(data, folder):
        data["parts"][0]["placed"] = 0
        data["parts"][0]["sheets"] = []

    _rewrite_result(h, "r001-al6061", nothing_placed)
    h.runner.tick()
    assert h.list_of("ca") == "needs_fixing"
    assert "doesn't add up" in h.tracker.comments_on("ca")[-1]


def test_dry_run_and_real_run_never_mix(tmp_path):
    h = Harness(tmp_path)
    h.runner.tick()                                               # a real run is active
    run_fake_worker(h.queue)
    real = h.tracker
    use_tracker(h, DryRunTracker(real))
    h.runner.tick()                                               # dry tick must leave it alone
    assert not any(c.list_key == "sheet_review" for c in real.cards.values())
    assert h.store.active() is not None
    use_tracker(h, real)
    h.runner.tick()
    assert len(h.sheet_cards()) == 3 and h.store.active() is None


def _human_moves(h, card_id, list_key):
    c = h.tracker.cards[card_id]
    h.tracker.cards[card_id] = c.__class__(**{**c.__dict__, "list_key": list_key})


def test_rejected_sheet_cannot_sit_in_ready_to_cut(tmp_path):
    h = Harness(tmp_path)
    h.runner.tick()
    run_fake_worker(h.queue, reject_sheet=True)
    h.runner.tick()
    sheet = h.sheet_cards()[0]
    _human_moves(h, sheet.id, "ready_to_cut")
    h.runner.tick()
    assert h.list_of(sheet.id) == "sheet_review"
    assert "program was rejected" in h.tracker.comments_on(sheet.id)[-1]


def test_checklist_with_items_removed_does_not_count(tmp_path):
    h = Harness(tmp_path)
    h.runner.tick()
    run_fake_worker(h.queue)
    h.runner.tick()
    sheet = h.sheet_cards()[0]
    h.tracker.checklists[(sheet.id, "Review")] = [["Simulated in Fusion", True]]   # someone deleted items
    _human_moves(h, sheet.id, "ready_to_cut")
    h.runner.tick()
    assert h.list_of(sheet.id) == "sheet_review"


def test_cards_the_service_did_not_make_are_left_alone(tmp_path):
    h = Harness(tmp_path)
    h.tracker.cards["manual"] = h.tracker.cards["ctl"].__class__("manual", "hand-made job", "", "ready_to_cut", "u")
    h.runner.tick()
    assert h.list_of("manual") == "ready_to_cut"
