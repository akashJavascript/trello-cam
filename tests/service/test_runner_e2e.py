"""The whole service loop, offline: fake Trello, scripted Onshape, fake Fusion worker."""

import copy
import sys
from datetime import datetime, timedelta, timezone

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from autocam_core.hotfolder import Queue
from autocam_service.config import DEFAULT_CONFIG, REPO_ROOT, parse_config
from autocam_service.onshape.cache import OnshapeCache
from autocam_service.onshape.client import OnshapeClient
from autocam_service.onshape.export import Exporter, PollSchedule
from autocam_service.onshape.ledger import Ledger
from autocam_service.run_state import RunStore
from autocam_service.runner import Runner, Services
from autocam_service.tracker.base import Attachment, Card
from autocam_service.tracker.fake import FakeTracker
from fakeonshape import FakeTransport, resp
from fakeworker import run_fake_worker

RAW = tomllib.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
D, V, W, E = "a" * 24, "b" * 24, "c" * 24, "d" * 24
STUDIO = f"https://cad.onshape.com/documents/{D}/v/{V}/e/{E}"
NOW = datetime(2026, 10, 1, 22, 0, tzinfo=timezone.utc)
PARTS = [{"name": "hood_gusset", "partId": "JHD", "bodyType": "solid", "material": {"displayName": "Aluminum - 6061"}},
         {"name": "window", "partId": "JHG", "bodyType": "solid", "material": {"displayName": "Polycarbonate"}}]


def onshape_routes():
    return FakeTransport([
        ("GET", "/parts/", [resp(body=PARTS)]),
        ("POST", "/translations", [resp(body={"id": "T1"}), resp(body={"id": "T2"})]),
        ("GET", "/translations/T1", [resp(body={"requestState": "DONE", "resultExternalDataIds": ["F1"]})]),
        ("GET", "/translations/T2", [resp(body={"requestState": "DONE", "resultExternalDataIds": ["F2"]})]),
        ("GET", "/externaldata/F1", [resp(200, b"ISO-10303-21; gusset")]),
        ("GET", "/externaldata/F2", [resp(200, b"ISO-10303-21; window")]),
    ])


def cards():
    return [
        Card("ctl", "Run nest", "", "run_nest", "https://trello.example/c/ctl"),
        Card("ca", "hood_gusset", f"Qty: 2\n{STUDIO}", "ready_for_cam", "https://trello.example/c/ca"),
        Card("cb", "window", f"Qty: 1\n{STUDIO}", "ready_for_cam", "https://trello.example/c/cb", ("Smoked",)),
        Card("cc", "bracket", f"Qty: 1\nhttps://cad.onshape.com/documents/{D}/w/{W}/e/{E}", "ready_for_cam",
             "https://trello.example/c/cc"),
        Card("cd", "spacer", "Qty: 1\nMaterial: 5052", "ready_for_cam", "https://trello.example/c/cd",
             attachments=(Attachment("att-d", "spacer.step", "https://trello.example/a/d"),)),
    ]


class Harness:
    def __init__(self, tmp_path, transport=None, card_list=None, online=True, **cfg):
        data = copy.deepcopy(RAW)
        for key in ("alu_4mm", "alu_eighth", "poly_4mm"):
            f = tmp_path / "templates" / f"{key}.f3dhsm-template"
            f.parent.mkdir(exist_ok=True)
            f.write_bytes(b"template")
            data["templates"][key]["file"] = str(f)
        data["paths"] = {k: str(tmp_path / k) for k in ("queue", "cache", "state", "logs")}
        for dotted, value in cfg.items():
            node = data
            *path, last = dotted.split("__")
            for p in path:
                node = node[p]
            node[last] = value
        self.cfg = parse_config(data, root=REPO_ROOT)
        self.now = NOW
        self.tracker = FakeTracker(card_list if card_list is not None else cards())
        self.tracker.downloads["att-d"] = b"ISO-10303-21; spacer"
        self.queue = Queue(self.cfg.paths.queue).ensure()
        self.store = RunStore(self.cfg.paths.state)
        self.ledger = Ledger(self.cfg.paths.state / "onshape_ledger.jsonl", clock=lambda: self.now)
        self.transport = transport or onshape_routes()
        self.online = online
        self.runner = Runner(Services(self.cfg, self.tracker, self.queue, self.store, self.ledger,
                                      exporter_for=self.exporter, clock=lambda: self.now))

    def exporter(self, run_id):
        o = self.cfg.onshape
        client = OnshapeClient(base_url=o.base_url, access_key="AK", secret_key="SK", ledger=self.ledger,
                               transport=self.transport, run_id=run_id, max_calls=o.per_run_max_calls,
                               retry_after_max_wait_s=o.retry_after_max_wait_s, sleep=lambda s: None) if self.online else None
        return Exporter(client, OnshapeCache(self.cfg.paths.cache),
                        PollSchedule(o.poll_first_s, o.poll_factor, o.poll_max_s, o.poll_max_count), sleep=lambda s: None)

    def list_of(self, card_id):
        return self.tracker.get_card(card_id).list_key

    def sheet_cards(self):
        return [c for c in self.tracker.cards.values() if c.list_key == "sheet_review"]

    def files_on(self, card_id):
        return [name for cid, name, _ in self.tracker.files.values() if cid == card_id]


def test_full_run(tmp_path):
    h = Harness(tmp_path)
    h.runner.tick()
    assert sorted(h.queue.pending()) == ["r001-al5052", "r001-al6061", "r001-pc_smoked"]
    assert h.list_of("cc") == "needs_fixing" and "workspace" in h.tracker.comments_on("cc")[0]
    assert h.list_of("ctl") == "control"
    assert h.tracker.comments_on("ctl")[0].startswith("Run r001 started: 3 part card(s) in 3 job(s).")
    assert h.ledger.month_count() == 7   # one parts list + 2 x (translation, poll, download)
    assert all(h.list_of(c) == "ready_for_cam" for c in ("ca", "cb", "cd"))

    run_fake_worker(h.queue)
    h.runner.tick()

    sheets = {c.name: c for c in h.sheet_cards()}
    assert set(sheets) == {"5052 0.125 - 4 mm O-flute ALU - S1 (0 pauses) - r001",
                           "6061 0.125 - 4 mm O-flute ALU - S1 (1 pauses) - r001",
                           "PC smoked 0.125 - 4 mm O-flute POLY - S1 (0 pauses) - r001"}
    alu = sheets["6061 0.125 - 4 mm O-flute ALU - S1 (1 pauses) - r001"]
    assert h.files_on(alu.id) == ["6061_0p125_r001_S1.tap", "6061_0p125_r001_S1.png"]
    assert "**LOAD: 4 mm O-flute ALU**" in alu.desc and "lowest Z 0.0000 in" in alu.desc
    assert h.tracker.checklist(alu.id, "Review").total == 4
    for c in ("ca", "cb", "cd"):
        assert h.list_of(c) == "nested"
        assert h.tracker.comments_on(c)[-1].startswith("Nested in run r001")
    assert [url for cid, url, _ in h.tracker.links if cid == "ca"] == [alu.url]
    assert h.tracker.comments_on("ctl")[-1].startswith("Run r001 finished.")
    assert h.store.active() is None
    assert not any(e[0] == "move" and e[2] == "ready_to_cut" for e in h.tracker.log)


def test_second_run_uses_the_cache(tmp_path):
    h = Harness(tmp_path)
    h.runner.tick()
    run_fake_worker(h.queue)
    h.runner.tick()
    used = h.ledger.month_count()
    for cid in ("ca", "cb"):
        h.tracker.move(cid, "ready_for_cam")
    h.tracker.move("ctl", "run_nest")
    h.runner.tick()
    assert sorted(h.queue.pending()) == ["r002-al6061", "r002-pc_smoked"]
    assert h.ledger.month_count() == used    # no Onshape calls at all


class FlakyTracker(FakeTracker):
    def __init__(self, *a, fail_on_call=2, **kw):
        super().__init__(*a, **kw)
        self.program_calls = 0
        self.fail_on_call = fail_on_call

    def attach_program(self, *a, **kw):
        self.program_calls += 1
        if self.program_calls == self.fail_on_call:
            raise ConnectionError("Trello went away")
        return super().attach_program(*a, **kw)


def test_crash_mid_publish_resumes_without_duplicates(tmp_path):
    h = Harness(tmp_path)
    flaky = FlakyTracker(cards())
    flaky.downloads["att-d"] = b"ISO-10303-21; spacer"
    h.tracker = flaky
    h.runner.t = flaky
    h.runner.s.tracker = flaky
    h.runner.tick()
    run_fake_worker(h.queue)
    with pytest.raises(ConnectionError):
        h.runner.tick()
    h.runner.tick()
    assert len(h.sheet_cards()) == 3
    taps = [name for _, name, _ in flaky.files.values() if name.endswith(".tap")]
    assert sorted(taps) == ["5052_0p125_r001_S1.tap", "6061_0p125_r001_S1.tap", "PCsmoked_0p125_r001_S1.tap"]
    assert sum(1 for e in flaky.log if e[0] == "move" and e[1] == "ca") == 1
    assert h.store.active() is None


def test_rejected_program_is_never_uploaded(tmp_path):
    h = Harness(tmp_path)
    h.runner.tick()
    run_fake_worker(h.queue, reject_sheet=True)
    h.runner.tick()
    assert all(c.name.startswith("NOT CUTTABLE") for c in h.sheet_cards())
    assert not any(name.endswith(".tap") for _, name, _ in h.tracker.files.values())
    assert h.list_of("ca") == "needs_fixing"
    assert "program was rejected" in h.tracker.comments_on("ca")[-1]


def test_outlines_that_never_reach_the_stock_bottom_are_rejected(tmp_path):
    h = Harness(tmp_path)
    h.runner.tick()
    run_fake_worker(h.queue, shallow_outlines=True)
    h.runner.tick()
    assert all(c.name.startswith("NOT CUTTABLE") for c in h.sheet_cards())
    assert not any(name.endswith(".tap") for _, name, _ in h.tracker.files.values())
    alu = [c for c in h.sheet_cards() if "6061" in c.name][0]
    assert "never reaches the stock bottom" in alu.desc


def test_service_rechecks_the_bytes_it_uploads(tmp_path):
    h = Harness(tmp_path)
    h.runner.tick()
    run_fake_worker(h.queue)
    tap = h.queue.done / "r001-al6061" / "6061_0p125_r001_S1.tap"
    tap.write_bytes(tap.read_bytes().replace(b"G1 Z0. F20.", b"G1 Z-0.01 F20.", 1))  # tampered after Fusion's check
    h.runner.tick()
    alu = [c for c in h.sheet_cards() if "6061" in c.name][0]
    assert alu.name.startswith("NOT CUTTABLE") and "changed after Fusion checked it" in alu.desc
    assert h.files_on(alu.id) == ["6061_0p125_r001_S1.png"]


def test_failed_deferred_and_rejected_parts(tmp_path):
    h = Harness(tmp_path)
    h.runner.tick()
    run_fake_worker(h.queue, fail_job="r001-pc_smoked", defer_part="p01", part_error=None)
    h.runner.tick()
    assert h.list_of("cb") == "ready_for_cam" and "failed" in h.tracker.comments_on("cb")[-1]
    assert any("r001-pc_smoked failed in Fusion" in t for t in h.tracker.comments_on("ctl"))
    assert h.list_of("ca") == "ready_for_cam" and "none were cut" in h.tracker.comments_on("ca")[-1]


def test_part_error_from_fusion_goes_to_needs_fixing(tmp_path):
    h = Harness(tmp_path)
    h.runner.tick()
    run_fake_worker(h.queue, part_error="p01")
    h.runner.tick()
    assert h.list_of("ca") == "needs_fixing"
    assert "not a stock thickness" in h.tracker.comments_on("ca")[-1]


def test_ready_to_cut_needs_a_complete_checklist(tmp_path):
    h = Harness(tmp_path)
    h.runner.tick()
    run_fake_worker(h.queue)
    h.runner.tick()
    sheet = h.sheet_cards()[0]
    h.tracker.cards[sheet.id] = sheet.__class__(**{**sheet.__dict__, "list_key": "ready_to_cut"})  # a human moves it
    h.runner.tick()
    assert h.list_of(sheet.id) == "sheet_review"
    assert "checklist is 0/4 done" in h.tracker.comments_on(sheet.id)[-1]
    h.tracker.tick_all(sheet.id, "Review")
    h.tracker.cards[sheet.id] = h.tracker.cards[sheet.id].__class__(
        **{**h.tracker.cards[sheet.id].__dict__, "list_key": "ready_to_cut"})
    h.runner.tick()
    assert h.list_of(sheet.id) == "ready_to_cut"


def test_budget_refusal_spends_nothing(tmp_path):
    h = Harness(tmp_path, onshape__per_run_max_calls=3)
    h.runner.tick()
    assert h.queue.pending() == [] and h.ledger.month_count() == 0
    assert "Run not started" in h.tracker.comments_on("ctl")[0]
    assert h.list_of("ca") == "ready_for_cam" and h.list_of("ctl") == "control"
    assert h.list_of("cc") == "needs_fixing"     # card format problems are still reported


def test_trigger_during_a_run_and_job_timeout(tmp_path):
    h = Harness(tmp_path)
    h.runner.tick()
    h.tracker.move("ctl", "run_nest")
    h.now = NOW + timedelta(hours=2)
    h.runner.tick()
    assert any("Run r001 is still in progress" in t for t in h.tracker.comments_on("ctl"))
    assert h.list_of("ctl") == "control"
    assert sum("hasn't finished" in t for t in h.tracker.comments_on("ctl")) == 3   # one per queued job
    h.runner.tick()
    assert sum("hasn't finished" in t for t in h.tracker.comments_on("ctl")) == 3   # reported once each


def test_offline_leaves_uncached_cards_queued(tmp_path):
    h = Harness(tmp_path, online=False)
    h.runner.tick()
    assert h.queue.pending() == ["r001-al5052"]           # only the .step card could be prepared
    assert h.list_of("ca") == "ready_for_cam" and h.list_of("cb") == "ready_for_cam"
    assert "2 card(s) were left in Ready for CAM" in h.tracker.comments_on("ctl")[0]


def test_missing_template_keeps_cards_queued(tmp_path):
    h = Harness(tmp_path)
    (tmp_path / "templates" / "poly_4mm.f3dhsm-template").unlink()
    h.runner.tick()
    assert sorted(h.queue.pending()) == ["r001-al5052", "r001-al6061"]
    assert h.list_of("cb") == "ready_for_cam"
    assert any("Can't CAM pc_smoked parts yet" in t for t in h.tracker.comments_on("ctl"))
