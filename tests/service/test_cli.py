import copy
import json
import sys

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from autocam_core.schema_job import load_job
from autocam_service import app, cli
from autocam_service.config import DEFAULT_CONFIG, REPO_ROOT, parse_config
from autocam_service.runner import Runner
from autocam_service.tracker.dryrun import DryRunTracker
from test_runner_e2e import Harness

RAW = tomllib.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))


def cfg_with_templates(tmp_path):
    data = copy.deepcopy(RAW)
    for key in ("alu_4mm", "alu_eighth", "poly_4mm"):
        f = tmp_path / f"{key}.f3dhsm-template"
        f.write_bytes(b"t")
        data["templates"][key]["file"] = str(f)
    data["paths"] = {k: str(tmp_path / k) for k in ("queue", "cache", "state", "logs")}
    return parse_config(data, root=REPO_ROOT)


def test_make_job_prints_a_valid_job(tmp_path, capsys):
    step = tmp_path / "gusset.step"
    step.write_bytes(b"ISO-10303-21;")
    cfg = cfg_with_templates(tmp_path)
    assert cli.make_job(cfg, "al6061", [f"hood_gusset={step}:3"], "local001", submit=False) == 0
    job = load_job(json.loads(capsys.readouterr().out))
    assert job.job_id == "local001-al6061" and job.parts[0].qty == 3 and job.parts[0].source == "local"
    assert job.post.properties["useMist"] is True and job.post.properties["safePositionMethod"] == "G53"
    assert job.tooling.default == "t1_4mm_alu" and job.tooling.small_features == "t12_eighth_alu"
    assert job.fixture.nest_region_in == (0.5, 1.25, 39.5, 22.75)


def test_make_job_submit_and_errors(tmp_path, capsys):
    step = tmp_path / "w.step"
    step.write_bytes(b"x")
    cfg = cfg_with_templates(tmp_path)
    assert cli.make_job(cfg, "pc_clear", [f"window={step}"], "local002", submit=True) == 0
    assert (cfg.paths.queue / "incoming" / "local002-pc_clear.json").exists()
    job = load_job(json.loads((cfg.paths.queue / "incoming" / "local002-pc_clear.json").read_text()))
    assert job.tooling.small_features is None          # poly 1/8 template not exported in this test
    assert job.post.properties["useMist"] is False
    assert cli.make_job(cfg, "steel", [f"x={step}"], "l", False) == 1
    assert cli.make_job(cfg, "al6061", ["no-equals-sign"], "l", False) == 1
    assert cli.make_job(cfg, "al6061", [f"x={tmp_path / 'missing.step'}"], "l", False) == 1


def test_trello_discover_prints_a_config_block(tmp_path, capsys, monkeypatch):
    class FakeBoard:
        def board_lists(self, board):
            return [("L1", "Inbox"), ("L2", "Ready for CAM"), ("L3", "Needs fixing"), ("L9", "Random")]

        def board_cards(self, board):
            return [("K1", "Run nest", "L9"), ("K2", "System", "L9")]

    monkeypatch.setattr(app, "trello_tracker", lambda cfg, env: FakeBoard())
    assert cli.trello_discover(cfg_with_templates(tmp_path), tmp_path / ".env", "BOARD") == 0
    out = capsys.readouterr().out
    assert 'ready_for_cam = "L2"   # Ready for CAM' in out
    assert 'ready_to_cut = ""   # no list named like' in out
    assert 'run_nest_control = "K1"' in out and 'system = "K2"' in out


def test_dry_run_writes_nothing_to_trello(tmp_path):
    h = Harness(tmp_path)
    dry = DryRunTracker(h.tracker)
    h.runner = Runner(h.runner.s.__class__(**{**h.runner.s.__dict__, "tracker": dry}))
    h.runner.tick()
    assert sorted(h.queue.pending()) == ["r001-al5052", "r001-al6061", "r001-pc_smoked"]
    assert h.tracker.comments == [] and h.list_of("cc") == "ready_for_cam" and h.list_of("ctl") == "run_nest"
    kinds = [w[0] for w in dry.intended]
    assert kinds.count("move") == 2 and "comment" in kinds
    assert h.store.load("r001").dry_run
