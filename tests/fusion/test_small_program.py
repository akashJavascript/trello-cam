"""More than one cutter per part (core 0.19.0): small holes and tight cutouts in a SMALL program with the 1/8 in,
run before the sheet's own, which stays on the 4 mm with the outlines last."""

from dataclasses import replace

from autocam_core import errors as E
from autocam_core.names import small_program_name
from autocam_service.sheet_cards import sheet_description
from geombuilder import PlateBuilder
from rig import Rig, plate

EIGHTH = "t12_eighth_alu"


def tight_cutout_plate():
    b = PlateBuilder("window")
    walls = [b._face(kind="plane", z0=0.0, z1=b.t, normal_dot=0.0, area=0.1) for _ in range(4)]
    walls.append(b._cyl(0.07, (2.0, 2.0), 90.0, 0.0, b.t))
    b._loop(b._top_id(), walls)
    b.cutout(4.0)
    return b.build()


def test_small_holes_get_their_own_eighth_program_and_the_sheet_stays_on_the_4mm(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("bracket", 2, plate(holes=(0.136, 0.136, 0.25)), (4.0, 4.0)), ("spacer", 1, plate(), (3.0, 3.0))])
    assert job.tooling.split
    result = rig.run(job)
    assert result.status == "ok", (result.sheets[0].errors, [p.errors for p in result.parts])
    (sheet,) = result.sheets
    assert sheet.tool == "t1_4mm_alu" and sheet.tool_forced_by == ()
    (small,) = sheet.programs
    name = small_program_name(sheet.name)
    assert (small.kind, small.name, small.runs, small.tap) == ("small", name, "before", f"{name}.tap")
    assert small.tool_number == job.tooling.tools[EIGHTH].number and small.tool_guid == job.tooling.tools[EIGHTH].guid
    assert [(p.part_key, p.count) for p in small.parts] == [("p01", 2)] and small.feature == "2 holes in bracket"
    s = rig.fake.sheets[name]
    assert s["face_of"] == sheet.name and s["copies"] == ["p01.1", "p01.2"]
    assert len(s["fills"]["[drill] holes"].holes) == 4                          # 2 holes x 2 copies, drilled with the 1/8
    assert not any(n.startswith("[outer") for n in s["ops"])                    # outlines stay in the sheet's program
    main = rig.fake.sheets[sheet.name]
    assert "[drill] holes" not in main["fills"]                                 # the 4 mm drills nothing here
    assert len(main["fills"]["[bore] holes"].holes) == 2                        # the 0.25 holes, bored with the 4 mm
    view = rig.service_view(job, result)
    (vs,) = view.sheets
    assert vs.cuttable and all(p.ok for p in vs.programs)
    desc = sheet_description(job, view, vs, resume_key="F1", part_cards={"p01": ("Bracket", ""), "p02": ("Spacer", "")})
    assert f"1. First cut what only the {small.cutter_label} can: load it (T{small.tool_number}), measure it, and run {name}.tap" in desc
    assert "It cuts only the 2 holes in bracket; leave the sheet where it is." in desc and "It cuts everything else, outlines last." in desc


def test_a_tight_cutout_goes_in_the_small_program_with_its_tabs(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("window", 1, tight_cutout_plate(), (4.0, 4.0))])
    job = replace(job, parts=tuple(replace(p, tabs=True) for p in job.parts))
    result = rig.run(job)
    (sheet,) = result.sheets
    assert result.status == "ok", sheet.errors
    s = rig.fake.sheets[small_program_name(sheet.name)]
    tabbed = [n for n in s["ops"] if n.startswith("[inner] cutouts - ")]
    assert len(tabbed) == 1 and len(s["fills"][tabbed[0]].loops) == 1         # the tight one, its slug tabbed
    main = rig.fake.sheets[sheet.name]
    assert sum(len(f.loops) for n, f in main["fills"].items() if n.startswith("[inner]")) == 1   # the other cutout


def test_a_part_needing_the_eighth_whole_still_moves_its_sheet(tmp_path):
    rig = Rig(tmp_path)
    whole = PlateBuilder("corner")
    whole.inside_corner(0.07)
    job = rig.job([("bracket", 1, plate(holes=(0.136,)), (4.0, 4.0)), ("corner", 1, whole.build(), (3.0, 3.0))])
    result = rig.run(job)
    (sheet,) = result.sheets
    assert sheet.tool == EIGHTH and [f.part_key for f in sheet.tool_forced_by] == ["p02"]
    assert sheet.programs == ()                                                 # one tool: nothing to split


def test_a_small_template_with_another_tool_stops_the_sheet(tmp_path):
    rig = Rig(tmp_path, guid_for={"alu_eighth": "00000000-0000-0000-0000-000000000000"})
    result = rig.run(rig.job([("bracket", 1, plate(holes=(0.136,)), (4.0, 4.0))]))
    (sheet,) = result.sheets
    assert sheet.tap is None and any(e.code == E.TOOL_GUID_MISMATCH and "small-tool" in e.msg for e in sheet.errors)


def test_a_small_program_cutting_into_a_part_stops_the_sheet(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("bracket", 1, plate(holes=(0.136,)), (4.0, 4.0))])
    rig.fake.gouges["6061_0p125_r001_S1_SMALL"] = [(12, 1.0, 1.0)]
    result = rig.run(job)
    (sheet,) = result.sheets
    assert sheet.tap is None and any("small-tool program: the tool cuts into a part" in e.msg for e in sheet.errors)
    (small,) = sheet.programs
    assert small.tap is None and (rig.out / small.tap_rejected).is_file()
    assert [vs.cuttable for vs in rig.service_view(job, result).sheets] == [False]
