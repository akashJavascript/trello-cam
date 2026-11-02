"""Scribed bend lines (core 0.18.0): a bent part's card can ask for its bend lines scribed on top, with the sheet's
own endmill in the sheet's program or with the chamfer mill (T16) in a SCRIBE program run before it (the user's
choice, 2026-11-01: both, off unless asked; 10 to 15 thou deep). Scribing never holds a sheet up."""

import json

from autocam_core.names import face_program_name, scribe_program_name
from autocam_core.scribe import in_sections, scribe_sections
from autocam_core.toolpoints import cutting_points
from autocam_service.sheet_cards import sheet_description
from geombuilder import PlateBuilder
from rig import Rig, plate

T16 = "915d08db-80ad-44e2-8fd5-6b9ed2129f59"
BEND = {"name": "Bend D", "angle_deg": 90.0, "direction": "down", "radius_in": 0.05, "order": 1}
X, WIDTH = 2.9165, 0.167                          # the bend zone: 3 in from the left, 0.167 in wide


def bent_plate():
    """A 6 x 4 in flat whose top is split along its one bend: flat, strip, flat (face ids 1, 7, 8)."""
    b = PlateBuilder("bracket")
    z = b._face(kind="plane", z0=0.125, z1=0.125, normal_dot=1.0, area=0.67)
    f = b._face(kind="plane", z0=0.125, z1=0.125, normal_dot=1.0, area=11.67)
    return b.build(), (b.top, z, f)


def top_loops(ids, w=6.0, h=4.0):
    a, z, f = ids
    x0, x1 = X, X + WIDTH
    return {a: [("a0", (0, 0), (x0, 0), True), ("e1", (x0, 0), (x0, h), True), ("a2", (x0, h), (0, h), True),
                ("a3", (0, h), (0, 0), True)],
            z: [("z0", (x0, 0), (x1, 0), True), ("e2", (x1, 0), (x1, h), True), ("z2", (x1, h), (x0, h), True),
                ("e1", (x0, h), (x0, 0), True)],
            f: [("f0", (x1, 0), (w, 0), True), ("f1", (w, 0), (w, h), True), ("f2", (w, h), (x1, h), True),
                ("e2", (x1, h), (x1, 0), True)]}


def bent_job(rig, way="endmill", face=(), qty=2, lines=True):
    geom, ids = bent_plate()
    job = rig.job([("bracket", qty, geom, (6.0, 4.0)), ("spacer", 1, plate(), (3.0, 3.0))], face=face,
                  bends={"bracket": [BEND]}, scribe={"bracket": way})
    if lines:
        rig.fake.tops[str(rig.tmp / "steps" / "bracket.step")] = top_loops(ids)
    return job


def test_the_endmill_scribes_in_the_sheets_own_program_before_the_outlines(tmp_path):
    rig = Rig(tmp_path)
    job = bent_job(rig)
    assert job.scribe.depth_in == 0.012 and [p.scribe for p in job.parts] == ["endmill", ""]
    result = rig.run(job)
    assert result.status == "ok", (result.sheets[0].errors, [p.errors for p in result.parts])
    (sheet,) = result.sheets
    s = rig.fake.sheets[sheet.name]
    assert s["ops"].index("[scribe] bend lines") < min(s["ops"].index(n) for n, _, _ in s["outer"])
    fill = s["fills"]["[scribe] bend lines"]
    assert fill.tag == "scribe" and fill.depth_in == 0.012 and len(fill.lines) == 2       # one bend, two copies
    assert abs(fill.stock_in - (-(0.15748 / 2 - 0.001))) < 1e-9      # the center on the line ('off' is refused)
    for x0, y0, x1, y1 in fill.lines:
        box = rig.fake.boxes[next(c for c in s["copies"] if abs(rig.fake.boxes[c].x0 + X + WIDTH / 2 - x0) < 1e-9)]
        assert x0 == x1 and {y0 - box.y0, y1 - box.y0} == {0.0, 4.0}
    assert [(p.part_key, p.count) for p in sheet.scribed] == [("p01", 2)] and sheet.programs == ()
    text = (rig.out / sheet.tap).read_text()
    assert "[scribe bend lines]" in text and "Z0.1130" in text                            # 1/8 in less 0.012
    points = cutting_points(text, 0.125)
    outside = [p for p in points if not in_sections(p[0], scribe_sections(text))]
    assert rig.fake.points_checked[sheet.name] == len(outside) < len(points)   # scribing isn't a gouge: on its lines
    (vs,) = rig.service_view(job, result).sheets
    assert vs.cuttable, vs.problems


def test_the_chamfer_mill_scribes_in_a_program_of_its_own(tmp_path):
    rig = Rig(tmp_path)
    job = bent_job(rig, "chamfer")
    assert job.scribe.chamfer.guid == T16 and job.scribe.chamfer.number == 16
    assert json.loads(job.scribe.chamfer_tool_json)["description"] == "6mm 45 deg chamfer"
    result = rig.run(job)
    assert result.status == "ok", result.sheets[0].errors
    (sheet,) = result.sheets
    (scribe,) = sheet.programs
    name = scribe_program_name(sheet.name)
    assert (scribe.kind, scribe.name, scribe.runs, scribe.tap) == ("scribe", name, "before", f"{name}.tap")
    assert (scribe.tool_guid, scribe.tool_number, scribe.depth_in) == (T16, 16, 0.012)
    assert [(p.part_key, p.count) for p in scribe.parts] == [("p01", 2)] and sheet.scribed == ()
    s = rig.fake.sheets[name]
    assert s["face_of"] == sheet.name and s["copies"] == ["p01.1", "p01.2"] and s["ops"] == ["[scribe] bend lines"]
    assert s["tools"] == {"[scribe] bend lines": T16}
    text = (rig.out / scribe.tap).read_text()
    assert "[scribe bend lines]" in text and "[outer" not in text and "Z0.1130" in text
    assert "[scribe" not in (rig.out / sheet.tap).read_text()
    view = rig.service_view(job, result)
    (vs,) = view.sheets
    assert vs.cuttable and vs.scribe is not None and vs.scribe.ok
    desc = sheet_description(job, view, vs, resume_key="F1", part_cards={"p01": ("Bracket", "")})
    assert f"First scribe the bend lines: load the 6 mm 45 deg chamfer mill (T16), measure it, and run {name}.tap" in desc
    assert "2. Then load the" in desc and "Bracket (1 of 2) - bent - scribed" in desc


def test_a_faced_part_is_scribed_below_its_faced_top(tmp_path):
    rig = Rig(tmp_path)
    result = rig.run(bent_job(rig, "chamfer", face=("bracket",)))
    (sheet,) = result.sheets
    assert [p.kind for p in sheet.programs] == ["face", "scribe"]
    s = rig.fake.sheets[scribe_program_name(sheet.name)]
    assert s["fills"]["[scribe] bend lines faced"].depth_in == 0.012 + 0.005
    assert face_program_name(sheet.name) in rig.fake.sheets
    assert "Z0.1080" in (rig.out / sheet.programs[1].tap).read_text()


def test_endmill_scribing_off_its_lines_stops_the_sheet(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.scribe_off_in = 0.05
    (sheet,) = rig.run(bent_job(rig)).sheets
    assert sheet.tap is None and any("leaves the bend lines" in e.msg for e in sheet.errors)


def test_endmill_scribing_too_deep_stops_the_sheet(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.scribe_extra_depth_in = 0.01
    (sheet,) = rig.run(bent_job(rig)).sheets
    assert sheet.tap is None and any("the scribing at line" in e.msg and "below" in e.msg for e in sheet.errors)


def test_a_chamfer_program_that_fails_its_checks_is_dropped_not_the_sheet(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.scribe_off_in = 0.05
    job = bent_job(rig, "chamfer")
    result = rig.run(job)
    (sheet,) = result.sheets
    assert sheet.tap is not None and sheet.programs == ()
    assert any("mark the bend lines by hand on bracket" in w.msg and "leaves the bend lines" in w.msg
               for w in sheet.warnings)
    assert (rig.out / f"{scribe_program_name(sheet.name)}.REJECTED.tap").is_file()
    assert [vs.cuttable for vs in rig.service_view(job, result).sheets] == [True]


def test_no_chamfer_mill_on_the_op_no_scribe_program(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.fail_set_tool = True
    (sheet,) = rig.run(bent_job(rig, "chamfer")).sheets
    assert sheet.tap is not None and sheet.programs == ()
    assert any("no scribe program" in w.msg and "not settable" in w.msg for w in sheet.warnings)
    s = rig.fake.sheets[scribe_program_name(sheet.name)]
    assert s["ops"] == [] and s["suppressed"] == ["[scribe] bend lines"]     # left out, never deleted (r041-r043)


def test_bend_lines_that_cant_be_found_are_marked_by_hand(tmp_path):
    rig = Rig(tmp_path)
    (sheet,) = rig.run(bent_job(rig, lines=False)).sheets
    assert sheet.tap is not None and sheet.scribed == ()
    assert any("bracket: its bend lines aren't scribed (its top isn't split along its bends). Mark them by hand."
               == w.msg for w in sheet.warnings)
    assert "[scribe] bend lines" not in rig.fake.sheets[sheet.name]["ops"]


def test_a_scribe_op_that_wont_generate_is_taken_out(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.op_states["[scribe] bend lines"] = (False, "Failed to generate toolpath", None)
    job = bent_job(rig)
    result = rig.run(job)
    (sheet,) = result.sheets
    assert sheet.tap is not None and sheet.scribed == () and sheet.errors == ()
    assert rig.fake.sheets[sheet.name]["suppressed"] == ["[scribe] bend lines"]
    assert any("Failed to generate toolpath" in w.msg and "bracket" in w.msg for w in sheet.warnings)
    assert [vs.cuttable for vs in rig.service_view(job, result).sheets] == [True]


def test_a_scribe_op_that_cant_be_set_up_is_left_out_not_deleted(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.fail_scribe_fill = True
    job = bent_job(rig)
    result = rig.run(job)
    (sheet,) = result.sheets
    assert sheet.tap is not None and sheet.errors == () and sheet.scribed == ()
    assert rig.fake.sheets[sheet.name]["suppressed"] == ["[scribe] bend lines"]
    assert any("bad chain" in w.msg and "Part" not in w.msg and "bracket" in w.msg for w in sheet.warnings)
    assert "[scribe" not in (rig.out / sheet.tap).read_text()


def test_only_bent_parts_are_scribed(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("spacer", 1, plate(), (3.0, 3.0))], scribe={"spacer": "endmill"})
    assert job.parts[0].scribe == ""
