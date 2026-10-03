import copy
import json
import os

import pytest

from autocam_core import CORE_VERSION
from autocam_core.hotfolder import Queue, QueueError
from autocam_core.schema import SchemaError, to_dict
from autocam_core.schema_job import load_job
from autocam_core.schema_result import FAILED, NEEDS_REVIEW, OK, load_result, overall_status

TOOL = {"key": "t1_4mm_alu", "guid": "7b77ef53-1ace-4e3b-ac2d-380b01a638bd", "number": 1,
        "diameter_in": 0.15748, "flute_in": 0.4724, "min_inside_radius_in": 0.0787,
        "drill_sizes_in": [0.156, 0.159], "cutter_label": "4 mm O-flute ALU",
        "template_key": "alu_4mm", "template_path": "C:/dev/frc-autocam/fusion/templates/alu_4mm.f3dhsm-template"}
JOB = {
    "schema": "autocam.job/1", "core_version": CORE_VERSION, "job_id": "r017-al6061", "run_id": "r017",
    "created_utc": "2026-10-01T22:00:00Z",
    "material": {"key": "al6061", "name": "6061", "family": "aluminum", "color": "",
                 "thicknesses_in": [0.0625, 0.125], "thickness_tol_in": 0.005, "use_mist": True,
                 "program_prefix": "6061"},
    "sheet": {"length_in": 48, "width_in": 24, "reach_x_in": 40},
    "fixture": {"nest_region_in": [0.5, 1.25, 39.5, 22.75], "clamp_zones_in": [[0, 0, 48, 1.25]],
                "clamp_height_in": 1.5, "min_clear_above_stock_in": 2.0},
    "nest": {"part_spacing_in": 0.25, "max_sheets_per_group": 4, "rotation": "all", "part_in_part": False,
             "envelope_spacing_in": 6.0, "short_qty": "defer_card"},
    "tooling": {"default": "t1_4mm_alu", "small_features": None, "tools": {"t1_4mm_alu": TOOL}},
    "holes": {"drill_tol_in": 0.002, "bore_min_in": 0.197, "bore_min_tol_in": 0.002, "bore_max_in": 1.5,
              "bearing_sizes_in": [1.125, 0.875], "bearing_tol_in": 0.01},
    "plate": {"strict_inside_radius": True},
    "pauses": {"enabled": True, "after_last_part": False, "mode": "tap_text", "park": "G53 P10",
               "spindle_rpm": 18000, "dwell_s": 4.0},
    "post": {"description": "ShopSabre with automatic mist", "path": "C:/x.cps", "sha256": "ab" * 32,
             "properties": {"useMist": True, "safePositionMethod": "G53"}, "units": "in"},
    "guard": {"z_floor_in": 0.0, "allowed_g": [0, 1, 2, 3, 4, 20, 53, 80, 81, 90], "allowed_m": [0, 3, 5, 11, 12],
              "clamp_margin_in": 0.25},
    "fusion_params": {"drill": {"faces": "holeFaces"}},
    "fusion_team": {"project": "", "folder": ""},
    "parts": [{"part_key": "p01", "card_id": "c1", "card_url": "https://trello.com/c/x", "name": "gusset",
               "qty": 2, "step": "C:/cache/a.step", "step_sha256": "cd" * 32, "source": "onshape",
               "onshape": {"did": "d", "vid": "v", "eid": "e", "part_id": "JHD", "url": "https://cad.onshape.com/x", "microversion": None},
               "force_small_tool": False}],
}


def mutated(fn):
    data = copy.deepcopy(JOB)
    fn(data)
    return data


def test_job_round_trips():
    job = load_job(copy.deepcopy(JOB))
    assert job.parts[0].onshape.part_id == "JHD"
    assert job.fixture.nest_region_in == (0.5, 1.25, 39.5, 22.75)
    assert isinstance(job.sheet.length_in, float)
    assert to_dict(job) == mutated(lambda d: d.update(
        sheet={"length_in": 48.0, "width_in": 24.0, "reach_x_in": 40.0},
        fixture={**d["fixture"], "clamp_zones_in": [[0.0, 0.0, 48.0, 1.25]]}))
    assert load_job(json.loads(json.dumps(to_dict(job)))) == job


@pytest.mark.parametrize("fn, message", [
    (lambda d: d.update(extra=1), "extra: unknown key"),
    (lambda d: d.pop("guard"), "guard: missing"),
    (lambda d: d["parts"][0].update(qty="2"), "parts[0].qty: expected an integer"),
    (lambda d: d["parts"][0].update(qty=0), "parts.p01.qty: must be >= 1"),
    (lambda d: d["parts"].append(copy.deepcopy(d["parts"][0])), "parts: duplicate part_key"),
    (lambda d: d["parts"][0].update(onshape=None), "parts.p01.onshape: required exactly when source is onshape"),
    (lambda d: d.update(job_id="../evil"), "job_id: use letters"),
    (lambda d: d["guard"].update(z_floor_in=-0.1), "guard.z_floor_in: must be >= 0"),
    (lambda d: d["tooling"].update(small_features="t12"), "tooling.small_features: no tool 't12'"),
    (lambda d: d["fixture"].update(nest_region_in=[0, 1, 2]), "expected 4 items"),
    (lambda d: d["pauses"].update(enabled=1), "pauses.enabled: expected true/false"),
    (lambda d: d.update(schema="autocam.job/2"), "schema: expected autocam.job/1"),
])
def test_bad_jobs_are_rejected(fn, message):
    with pytest.raises(SchemaError) as exc:
        load_job(mutated(fn))
    assert any(message in e for e in exc.value.errors), exc.value.errors


def sheet(**kw):
    s = {"index": 1, "name": "6061_0p125_r017_S1", "stock_type": "al6061-0.125", "thickness_in": 0.125,
         "tool": "t1_4mm_alu", "tool_guid": TOOL["guid"], "cutter_label": "4 mm O-flute ALU", "template": "alu_4mm",
         "tap": "6061_0p125_r017_S1.tap", "tap_rejected": None, "tap_bytes": 100, "tap_sha256": "ef" * 32,
         "guard": {"passed": True, "sha256": "ef" * 32, "floor_in": 0.0, "min_z_in": 0.0, "units": "G20",
                   "offenders": [], "clamp_violations": [], "problems": []},
         "pauses": {"mode": "tap_text", "expected": 1, "found": 1, "entries": [{"after": "p01-1", "line": 40}],
                    "problems": []},
         "machining_time_s": 600.0, "preview_png": "6061_0p125_r017_S1.png",
         "parts": [{"part_key": "p01", "count": 2}], "outer_order": ["p01-1", "p01-2"], "tool_forced_by": [],
         "errors": [], "warnings": [], "notes": []}
    s.update(kw)
    return s


def result(**kw):
    r = {"schema": "autocam.result/1", "core_version": CORE_VERSION, "job_id": "r017-al6061", "run_id": "r017",
         "status": "ok",
         "worker": {"fusion_version": "2.0.21000", "python_version": "3.12.4", "attempt": 1,
                    "started_utc": "t0", "finished_utc": "t1", "untested_steps": []},
         "post": {"method": "path", "sha256_verified": True, "properties": {}},
         "fusion_team": None, "f3d": None, "f3d_bytes": None, "sheets": [sheet()],
         "parts": [{"part_key": "p01", "card_id": "c1", "qty": 2, "placed": 2, "deferred": False, "sheets": [1],
                    "measured_thickness_in": 0.1252, "stock_thickness_in": 0.125, "tool_need": "t1_4mm_alu",
                    "holes": {"drill": 2, "bore": 1, "bearing": 0, "inner": 1, "contour_warn": 0, "pockets": 0},
                    "errors": [], "warnings": []}],
         "errors": [], "notes": []}
    r.update(kw)
    return r


def test_result_round_trips_and_status():
    res = load_result(result())
    assert overall_status(res.sheets, res.parts, res.errors) == OK
    assert load_result(json.loads(json.dumps(to_dict(res)))) == res


def test_tap_without_passing_guard_is_invalid():
    bad = sheet(guard={**sheet()["guard"], "passed": False})
    with pytest.raises(SchemaError, match="must have a passing guard report"):
        load_result(result(sheets=[bad]))
    with pytest.raises(SchemaError, match="tap_sha256 doesn't match"):
        load_result(result(sheets=[sheet(tap_sha256="00" * 32)]))


def test_status_rules():
    res = load_result(result(sheets=[sheet(warnings=[{"code": "HOLE_CONTOURED", "msg": "check"}])]))
    assert overall_status(res.sheets, res.parts, res.errors) == NEEDS_REVIEW
    res = load_result(result(sheets=[sheet(tap=None, tap_sha256=None)],
                             errors=[{"code": "NOTHING_TO_NEST", "msg": "x"}]))
    assert overall_status(res.sheets, res.parts, res.errors) == FAILED
    assert overall_status((), (), ()) == FAILED


# ---- hot folder

def test_submit_claim_complete(tmp_path):
    q = Queue(tmp_path).ensure()
    q.submit("r001-al6061", "{}")
    assert q.pending() == ["r001-al6061"]
    with pytest.raises(QueueError, match="already exists"):
        q.submit("r001-al6061", "{}")
    claim = q.claim("r001-al6061")
    assert claim.attempt == 1 and claim.out_dir.is_dir() and not any(claim.out_dir.iterdir())
    assert q.pending() == []
    (claim.out_dir / "a.tap").write_text("x")
    with pytest.raises(QueueError, match="no result.json"):
        q.complete(claim)
    (claim.out_dir / "result.json").write_text("{}")
    done = q.complete(claim)
    assert sorted(p.name for p in done.iterdir()) == ["a.tap", "result.json"]
    assert list(q.processing.iterdir()) == []
    assert q.finished() == ["r001-al6061"]


def test_claim_of_missing_job_returns_none(tmp_path):
    assert Queue(tmp_path).ensure().claim("nope") is None


def test_bad_job_ids_are_refused(tmp_path):
    q = Queue(tmp_path).ensure()
    for bad in ("../x", "a b", ""):
        with pytest.raises(QueueError):
            q.submit(bad, "{}")


def test_interrupted_job_is_requeued_then_failed(tmp_path):
    q = Queue(tmp_path).ensure()
    q.submit("r002-pc", "{}")
    first = q.claim("r002-pc")
    (first.out_dir / "partial.tap").write_text("x")
    assert q.recover(max_attempts=2) == (["r002-pc"], [])     # Fusion died: back in line
    second = q.claim("r002-pc")
    assert second.attempt == 2 and not any(second.out_dir.iterdir())  # fresh folder, no stale .tap
    (second.out_dir / "partial.tap").write_text("x")
    assert q.recover(max_attempts=2) == ([], ["r002-pc"])     # died again: give up
    failed = q.failed / "r002-pc"
    assert (failed / "job.json").read_text() == "{}"
    assert "started this job 2 times" in (failed / "error.txt").read_text()
    assert (failed / "partial" / "partial.tap").exists()
    assert list(q.processing.iterdir()) == []


def test_fail_keeps_the_job_id_and_earlier_errors(tmp_path):
    q = Queue(tmp_path).ensure()
    q.submit("r003-x", "{}")
    q.claim("r003-x")
    (q.failed / "r003-x").mkdir()
    (q.failed / "r003-x" / "error.txt").write_text("older")
    q.fail("r003-x", "boom")
    names = sorted(p.name for p in (q.failed / "r003-x").iterdir())
    assert "error.txt" in names and any(n.startswith("error.") and n != "error.txt" for n in names)
    assert (q.failed / "r003-x" / "error.txt").read_text() == "boom\n"
    assert q.finished() == ["r003-x"]


def test_fail_with_locked_files_still_finishes(tmp_path, monkeypatch):
    import autocam_core.hotfolder as hf
    q = Queue(tmp_path).ensure()
    q.submit("r004-x", "{}")
    claim = q.claim("r004-x")
    (claim.out_dir / "locked.tap").write_text("x")
    real_replace = os.replace

    def locked(src, dst):
        if str(src).endswith((".out", "r004-x.json")):
            raise PermissionError("in use")
        return real_replace(src, dst)

    monkeypatch.setattr(hf.os, "replace", locked)
    monkeypatch.setattr(hf.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        (claim.out_dir / "result.json").write_text("{}")
        q.complete(claim)
    q.fail("r004-x", "could not publish outputs")
    text = (q.failed / "r004-x" / "error.txt").read_text()
    assert text.startswith("could not publish outputs") and "copied, not moved" in text
    assert (q.failed / "r004-x" / "job.json").read_text() == "{}"
    assert q.finished() == ["r004-x"]
    monkeypatch.setattr(hf.os, "replace", real_replace)
    assert q.recover(max_attempts=2) == ([], [])   # the locked leftover isn't re-queued


def test_resubmit_on_resume_is_a_no_op(tmp_path):
    q = Queue(tmp_path).ensure()
    q.submit("r005-x", "{}")
    q.submit("r005-x", "{}", resume=True)
    assert q.pending() == ["r005-x"] and q.where("r005-x") == "incoming" and q.where("nope") is None


def test_heartbeat(tmp_path):
    q = Queue(tmp_path).ensure()
    assert q.read_heartbeat() is None
    q.write_heartbeat({"ts": "now", "job": None})
    assert q.read_heartbeat() == {"ts": "now", "job": None}
    assert not [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]


def test_fail_writes_error_first_even_if_job_json_is_locked(tmp_path, monkeypatch):
    import autocam_core.hotfolder as hf
    q = Queue(tmp_path).ensure()
    q.submit("r006-x", "{}")
    q.claim("r006-x")
    real_replace = os.replace

    def locked(src, dst):
        if str(src).endswith("r006-x.json"):
            raise PermissionError("exclusively locked")
        return real_replace(src, dst)

    def no_copy(*a, **kw):
        raise PermissionError("exclusively locked")

    monkeypatch.setattr(hf.os, "replace", locked)
    monkeypatch.setattr(hf.shutil, "copyfile", no_copy)
    monkeypatch.setattr(hf.time, "sleep", lambda s: None)
    q.fail("r006-x", "boom")
    text = (q.failed / "r006-x" / "error.txt").read_text()
    assert text.startswith("boom") and "still in processing/" in text
    assert "r006-x" in q.finished()
