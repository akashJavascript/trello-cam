"""More than one cutter per part (core 0.19.0, the user's "bigger idea" #1): a part whose only problems for the 4 mm
are small through holes and tight-cornered cutouts keeps the 4 mm; those features go in the sheet's small-tool
program. A tight corner on the outline or in a pocket, or the Tool 1/8 label, still needs the 1/8 for the part."""

import copy

from autocam_core.sheetcheck import check_small_program
from autocam_core.tooling import Split, part_tool, plan_features, sheet_tool
from geombuilder import PlateBuilder
from test_holes_plate_tooling import EIGHTH, MM4, RULES, analysis
from test_schema_hotfolder import JOB


def need(b, **kw):
    return part_tool(analysis(b), family="aluminum", default=MM4, small=EIGHTH, rules=RULES, force_small=False,
                     split=True, **kw)


def tight_cutout(b, r=0.07):
    walls = [b._face(kind="plane", z0=0.0, z1=b.t, normal_dot=0.0, area=0.1) for _ in range(4)]
    walls.append(b._cyl(r, (6.0, 6.0), 90.0, 0.0, b.t))
    b._loop(b._top_id(), walls)


def test_small_holes_are_split_off_and_the_part_stays_on_the_4mm():
    b = PlateBuilder()
    b.hole(0.136, center=(1, 1))
    b.hole(0.136, center=(2, 1))
    b.hole(0.25, center=(3, 1))
    n = need(b)
    assert n.tool == "t1_4mm_alu" and n.split is not None and n.errors == ()
    a = analysis(b)
    assert [round(a.through_holes[i].diameter_in, 3) for i in n.split.holes] == [0.136, 0.136]
    main = plan_features(a, MM4, RULES, without=n.split)
    small = plan_features(a, EIGHTH, RULES, only=n.split)
    assert main.errors == () and len(main.bore) == 1 and main.drill == ()          # the 0.25 bored with the 4 mm
    assert len(small.drill) == 2 and small.bore == () and small.inner_loops == ()  # 0.136 is a 1/8 drill size


def test_a_tight_cutout_is_split_off_whole():
    b = PlateBuilder()
    tight_cutout(b)
    b.cutout(4.0)
    n = need(b)
    assert n.tool == "t1_4mm_alu" and len(n.split.loops) == 1
    a = analysis(b)
    main = plan_features(a, MM4, RULES, without=n.split)
    small = plan_features(a, EIGHTH, RULES, only=n.split)
    assert len(main.inner_loops) == 1 and len(small.inner_loops) == 1
    assert {(l.face_id, l.index) for l in main.inner_loops}.isdisjoint((l.face_id, l.index) for l in small.inner_loops)


def test_whole_part_on_the_eighth_when_its_outline_or_a_pocket_needs_it():
    b = PlateBuilder()
    b.hole(0.136)
    b.inside_corner(0.07)                                        # a corner of the outline
    assert need(b).tool == "t12_eighth_alu" and need(b).split is None
    b = PlateBuilder()
    b.hole(0.136)
    b.pocket(0.05, corner_r=0.07)                                # a pocket corner too tight for the 4 mm
    assert need(b).tool == "t12_eighth_alu"
    b = PlateBuilder()
    b.hole(0.136)
    forced = part_tool(analysis(b), family="aluminum", default=MM4, small=EIGHTH, rules=RULES, force_small=True,
                       split=True)
    assert forced.tool == "t12_eighth_alu" and forced.split is None   # the Tool 1/8 label: the whole part
    off = part_tool(analysis(b), family="aluminum", default=MM4, small=EIGHTH, rules=RULES, force_small=False)
    assert off.tool == "t12_eighth_alu"                               # splitting off: as before


def test_a_split_part_doesnt_move_its_sheet_but_a_whole_eighth_part_does():
    b = PlateBuilder()
    b.hole(0.136)
    split = need(b)
    whole = PlateBuilder()
    whole.inside_corner(0.07)
    assert sheet_tool({"p01": split, "p02": need(PlateBuilder())}, "t1_4mm_alu", "t12_eighth_alu") == ("t1_4mm_alu", ())
    tool, forced = sheet_tool({"p01": split, "p02": need(whole)}, "t1_4mm_alu", "t12_eighth_alu")
    assert tool == "t12_eighth_alu" and [k for k, _ in forced] == ["p02"]


def test_nothing_to_split_without_small_features():
    assert need(PlateBuilder()).split is None and need(PlateBuilder()).tool == "t1_4mm_alu"
    assert isinstance(Split((), ()), Split)


SMALL = """[S1_SMALL]
M11 C8
G90
G20
G53 Z
[drill holes]
S18000
M3
G4 X4.
G0 X2. Y5.
Z0.325
G81 X2. Y5. Z0. R0.325 F20.
G80
G0 Z2.425
M12 C8
G53 Z
M5
G53 P10
"""


def eighth_job():
    data = copy.deepcopy(JOB)
    eighth = dict(data["tooling"]["tools"][data["tooling"]["default"]])
    eighth.update(key="t12_eighth_alu", guid="5f3a0f4e-0000-4000-8000-000000000012", number=12, diameter_in=0.125,
                  drill_sizes_in=[0.125, 0.136])
    data["tooling"]["tools"]["t12_eighth_alu"] = eighth
    data["tooling"]["small_features"] = "t12_eighth_alu"
    data["tooling"]["split"] = True
    from autocam_core.schema_job import load_job
    return load_job(data)


def test_the_small_program_cuts_through_with_the_small_tool_only():
    job = eighth_job()
    t = job.material.thicknesses_in[0]
    key = job.parts[0].part_key
    ok = check_small_program(SMALL.encode(), job, t, "t12_eighth_alu", {key: 1})
    assert ok.passed, ok.problems()
    assert not check_small_program(SMALL.encode(), job, t, job.tooling.default, {key: 1}).passed   # not its tool
    shallow = check_small_program(SMALL.replace("Z0. R", "Z0.03 R").encode(), job, t, "t12_eighth_alu", {key: 1})
    assert any("never reaches the stock bottom" in p for p in shallow.problems())
    outline = check_small_program(SMALL.replace("[drill holes]", "[outer p01-1]").encode(), job, t,
                                  "t12_eighth_alu", {key: 1})
    assert any("cuts outlines" in p for p in outline.problems())
    stops = check_small_program(SMALL.replace("G80", "G80\nM0").encode(), job, t, "t12_eighth_alu", {key: 1})
    assert not stops.passed
