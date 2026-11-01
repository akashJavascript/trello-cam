"""Faced parts (core 0.14.0): the Face label gives a sheet a FACE program of its own, run first, that skims those
parts' tops and nothing else (decisions, 2026-11-01)."""

from autocam_core import errors as E
from autocam_core.names import face_program_name
from rig import Rig, plate

T2 = "4147915e-c136-481d-bcaa-213e9c8e6623"


def faced_job(rig, face=("gusset",)):
    return rig.job([("gusset", 2, plate(), (4.0, 4.0)), ("spacer", 1, plate(), (3.0, 3.0))], face=face)


def test_a_faced_part_gets_a_face_program_over_its_copies_only(tmp_path):
    rig = Rig(tmp_path)
    job = faced_job(rig)
    assert job.face.tool.guid == T2 and job.face.depth_in == 0.005
    result = rig.run(job)

    assert result.status == "ok", (result.sheets[0].errors, [p.errors for p in result.parts])
    (sheet,) = result.sheets
    (face,) = sheet.programs
    name = face_program_name(sheet.name)
    assert (face.kind, face.name, face.runs, face.tap) == ("face", name, "before", f"{name}.tap")
    assert face.tool_guid == T2 and face.tool_number == 2 and face.depth_in == 0.005
    assert [(p.part_key, p.count) for p in face.parts] == [("p01", 2)]
    s = rig.fake.sheets[name]
    assert s["face_of"] == sheet.name and s["copies"] == ["p01.1", "p01.2"]      # its own setup, on the same stock
    assert s["origin"] == rig.fake.sheets[sheet.name]["origin"]
    fill = s["fills"]["[face] tops"]
    assert [cid for cid, _ in fill.tops] == ["p01.1", "p01.2"] and fill.depth_in == 0.005
    data = (rig.out / face.tap).read_bytes()
    assert b"Z0.1200" in data and b"[outer" not in data                         # 1/8 in stock, 0.005 off
    assert face.guard.passed and abs(face.guard.min_z_in - 0.12) < 1e-9
    view = rig.service_view(job, result)
    (vs,) = view.sheets
    assert vs.cuttable and vs.face is not None and vs.face.ok


def test_no_faced_parts_no_face_program(tmp_path):
    rig = Rig(tmp_path)
    result = rig.run(faced_job(rig, face=()))
    assert result.status == "ok"
    assert result.sheets[0].programs == ()
    assert not any("face_of" in s for s in rig.fake.sheets.values())


def test_a_face_pass_that_leaves_the_part_stops_the_sheet(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.face_inset_in = -0.1                  # the cutter's center runs 0.1 in outside the part (clear of the
                                                   # clamps: the guard would catch that first)
    result = rig.run(faced_job(rig))
    (sheet,) = result.sheets
    assert sheet.tap is None and any("leaves the faced parts" in e.msg for e in sheet.errors)
    (face,) = sheet.programs
    assert face.tap is None and (rig.out / face.tap_rejected).is_file()


def test_a_face_pass_deeper_than_asked_stops_the_sheet(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.face_extra_depth_in = 0.01
    result = rig.run(faced_job(rig))
    (sheet,) = result.sheets
    assert sheet.tap is None
    assert any(e.code == E.TAP_REJECTED and "face program" in e.msg and "below" in e.msg for e in sheet.errors)


def test_a_face_template_with_another_tool_stops_the_sheet(tmp_path):
    rig = Rig(tmp_path, guid_for={"face": "e5dd75b2-ea23-4767-b10b-756a9444cc2e"})
    result = rig.run(faced_job(rig))
    (sheet,) = result.sheets
    assert sheet.tap is None and sheet.programs == ()
    assert any(e.code == E.TOOL_GUID_MISMATCH and "face op" in e.msg for e in sheet.errors)


def test_a_face_template_needs_exactly_one_face_op(tmp_path):
    rig = Rig(tmp_path)
    (tmp_path / "templates" / "face.f3dhsm-template").write_text(f'[["[face] tops", "{T2}"], ["[outer] x", "{T2}"]]')
    result = rig.run(faced_job(rig))
    assert any(e.code == E.TEMPLATE_PROBLEM and "exactly one op" in e.msg for e in result.sheets[0].errors)


def test_the_service_refuses_a_sheet_whose_face_program_went_missing(tmp_path):
    rig = Rig(tmp_path)
    job = faced_job(rig)
    result = rig.run(job)
    (rig.out / result.sheets[0].programs[0].tap).unlink()
    (vs,) = rig.service_view(job, result).sheets
    assert not vs.cuttable and "missing" in vs.face.problems[0]


def test_the_service_refuses_a_faced_sheet_without_a_face_program(tmp_path):
    from dataclasses import replace
    from autocam_core.schema_result import result_json
    rig = Rig(tmp_path)
    job = faced_job(rig)
    result = rig.run(job)
    result = replace(result, sheets=(replace(result.sheets[0], programs=()),))
    (rig.out / "result.json").write_text(result_json(result), encoding="utf-8")
    (vs,) = rig.service_view(job, result).sheets
    assert not vs.cuttable and any("no face program" in p for p in vs.problems)


# ---- the check both sides run on a FACE program (sheetcheck.check_face_program)

def face_program(*moves, z="0.1200"):
    lines = ["[x_FACE]", "M11 C8", "G90", "G20", "G53 Z", "[face tops]", "S18000", "M3", "G4 X4.",
             "G0 X5. Y5.", "G0 Z0.325", f"G1 Z{z} F20.", "G1 X7. F90.", "G1 Y7.", "G1 X5.", *moves, "G0 Z2.125",
             "M12 C8", "G53 Z", "M5", "G53 P10"]
    return ("\r\n".join(lines) + "\r\n").encode("ascii")


def check(rig, data, parts=None):
    from autocam_core.sheetcheck import check_face_program
    job = faced_job(rig)
    return check_face_program(data, job, 0.125, parts or {"p01": 2})


def test_the_face_check_passes_a_skim(tmp_path):
    c = check(Rig(tmp_path), face_program())
    assert c.passed, c.problems()


def test_the_face_check_allows_one_posting_unit_and_no_more(tmp_path):
    rig = Rig(tmp_path)
    assert check(rig, face_program(z="0.1199")).passed
    c = check(rig, face_program(z="0.1198"))
    assert not c.passed and "below Z0.1199" in c.problems()[0]


def test_the_face_check_wants_the_face_depth_reached(tmp_path):
    c = check(Rig(tmp_path), face_program(z="0.1240"))
    assert not c.passed and "never gets to the face depth" in c.problems()[0]


def test_the_face_check_refuses_stops_outlines_and_unfaced_parts(tmp_path):
    rig = Rig(tmp_path)
    assert any("never stops" in p for p in check(rig, face_program("M0")).problems())
    assert any("cuts outlines" in p for p in check(rig, face_program("[outer p02-1]")).problems())
    assert any("don't ask for facing: p02" in p for p in check(rig, face_program(), {"p01": 2, "p02": 1}).problems())


# ---- the face template (fusion/tools/make_templates/make_face.py)

def test_the_face_template_is_alu_4mm_s_pocket_op_with_t2(tmp_path):
    import re
    import subprocess
    import sys
    from autocam_service.config import DEFAULT_CONFIG, REPO_ROOT, load_config
    cfg = load_config(DEFAULT_CONFIG)
    out = tmp_path / "face.f3dhsm-template"
    subprocess.run([sys.executable, str(REPO_ROOT / "fusion/tools/make_templates/make_face.py"),
                    str(REPO_ROOT / "fusion/templates/alu_4mm.f3dhsm-template"), str(cfg.fusion.tool_library),
                    str(out)], check=True, capture_output=True)
    assert out.read_bytes() == cfg.face.template.read_bytes()          # the committed one is what it makes
    text = out.read_text(encoding="utf-8")
    ops = re.findall(r'<template description="([^"]*)"', text)
    assert ops == ["[face] tops"]
    assert re.findall(r'<tool guid="([^"]*)"', text) == [cfg.face.guid]
    for name, value in (("stockToLeave", "-tool_diameter * 0.45"), ("bottomHeight_mode", "'from stock top'"),
                        ("topHeight_mode", "'from stock top'"), ("tool_number", "2")):
        assert f'<parameter expression="{value}" name="{name}"/>' in text


def test_a_faced_part_placed_by_hand_gets_its_face_program_too(tmp_path):
    import dataclasses
    rig = Rig(tmp_path)
    job = rig.job([("bracket", 3, plate(name="bracket"), (5.0, 2.0))], face=("bracket",))
    job = dataclasses.replace(job, parts=(dataclasses.replace(job.parts[0], by_hand=True),))
    result = rig.run(job)
    (sheet,) = result.sheets
    assert sheet.by_hand and sheet.repeat == 3 and sheet.tap, sheet.errors
    (face,) = sheet.programs
    assert face.tap and [(p.part_key, p.count) for p in face.parts] == [("p01", 1)]
    s = rig.fake.sheets[face.name]
    assert s["origin"] == rig.fake.sheets[sheet.name]["origin"] and s["size"] == (5.0, 2.0)   # the same zero
