"""Tabs: the template's tabs turned on for the outlines and cutouts of parts that ask for them (PartSpec.tabs),
a count per contour from its length (autocam_core/tabs.py)."""

import dataclasses

from autocam_core.schema_job import TabSpec
from geombuilder import PlateBuilder
from rig import OPS, Rig, plate


def with_cutouts(name, lengths):
    b = PlateBuilder(name)                      # outline: four walls of 8 in = 32 in
    for i, n in enumerate(lengths):
        b.cutout(n, center=(1.0 + i, 2.0))
    return b.build()


def test_only_the_parts_that_ask_get_tabs(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("small", 2, plate(name="small"), (3.0, 3.0)), ("big", 1, plate(name="big"), (10.0, 5.0))])
    job = dataclasses.replace(job, parts=(dataclasses.replace(job.parts[0], tabs=True), job.parts[1]),
                              tabs=TabSpec(distance_in=8.0))
    result = rig.run(job)
    [sheet] = result.sheets
    assert sheet.tap and not sheet.errors
    [fake] = rig.fake.sheets.values()
    # 32 in / 8 in = 4 each, at points; not p02's
    assert {op: len(pts) for op, pts in fake["tab_points"].items()} == {"[outer] p01-1": 4, "[outer] p01-2": 4}
    assert fake["tabs"] == {}
    program = (rig.out / sheet.tap).read_text()
    assert program.count("G1 Z0.04") == 2                                          # the program passed its checks


def test_no_tabs_without_the_box(tmp_path):
    rig = Rig(tmp_path)
    result = rig.run(rig.job([("small", 2, plate(name="small"), (3.0, 3.0))]))
    assert result.sheets[0].tap
    [fake] = rig.fake.sheets.values()
    assert fake["tabs"] == {} and fake["tab_points"] == {}


def test_every_cutout_gets_tabs_by_its_length_however_short(tmp_path):
    rig = Rig(tmp_path)
    # Cutouts 20, 5, 2 and 1.6 in around, and one of 0.5 in: one per 2.5 in, 2 to 6, as many as fit. With the
    # 0.3 in tabs and the 4 mm cutter, each tab needs 2 x (0.3 + 0.157) = 0.91 in of contour.
    tabbed = with_cutouts("bracket", [20.0, 5.0, 2.0, 1.6, 0.5])
    plain = with_cutouts("spacer", [5.0])
    job = rig.job([("bracket", 1, tabbed, (8.0, 6.0)), ("spacer", 1, plain, (4.0, 4.0))])
    job = dataclasses.replace(job, parts=(dataclasses.replace(job.parts[0], tabs=True), job.parts[1]),
                              tabs=dataclasses.replace(job.tabs, width_in=0.3, height_in=0.0394, wide_max_tier=4))
    result = rig.run(job)
    [sheet] = result.sheets
    assert sheet.tap and not sheet.errors
    [fake] = rig.fake.sheets.values()
    inner = [op for op in fake["ops"] if op.startswith("[inner]")]
    assert inner == ["[inner] cutouts", "[inner] cutouts - 1 tab each", "[inner] cutouts - 2 tabs each",
                     "[inner] cutouts - 6 tabs each"]
    assert fake["ops"].index(inner[-1]) < min(i for i, op in enumerate(fake["ops"]) if op.startswith("[outer]"))
    points = {op: len(pts) for op, pts in fake["tab_points"].items()}
    # 20 in -> 6; 5 in -> 2; 2 in -> 2 (the minimum, and 2 fit); 1.6 in -> 1 fits; 0.5 in: none fit, so it's
    # cut with the spacer's cutout, without tabs.
    assert {op: len(fake["fills"][op].loops) for op in inner} == {
        "[inner] cutouts": 2, "[inner] cutouts - 1 tab each": 1, "[inner] cutouts - 2 tabs each": 2,
        "[inner] cutouts - 6 tabs each": 1}
    assert points["[inner] cutouts - 6 tabs each"] == 6 and points["[inner] cutouts - 2 tabs each"] == 2 * 2
    assert points["[outer] p01-1"] == 6 and "[outer] p02-1" not in points
    # every tabbed op gets the config's size; the untabbed ones keep the template's
    assert set(fake["tab_size"]) == set(points) and set(fake["tab_size"].values()) == {(0.3, 0.0394)}


def test_tab_points_on_the_outline_are_on_its_sides_clear_of_the_corners(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("bracket", 1, with_cutouts("bracket", []), (8.0, 6.0))])
    job = dataclasses.replace(job, parts=(dataclasses.replace(job.parts[0], tabs=True),))
    result = rig.run(job)
    [fake] = rig.fake.sheets.values()
    [box] = [rig.fake.boxes[c] for c in fake["copies"]]
    pts = fake["tab_points"]["[outer] p01-1"]
    corners = [(box.x0, box.y0), (box.x1, box.y0), (box.x1, box.y1), (box.x0, box.y1)]
    on_edge = [min(abs(x - box.x0), abs(x - box.x1)) < 1e-3 or min(abs(y - box.y0), abs(y - box.y1)) < 1e-3
               for x, y in pts]
    assert len(pts) == 6 and all(on_edge)
    assert all(min(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for cx, cy in corners) > 4 / 25.4 for x, y in pts)
    assert any("6 tabs at points, 6 on straight edges, 6 clear of corners" in n for n in result.sheets[0].notes)


def test_if_fusion_wont_take_points_it_spreads_the_same_number_evenly(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.fail_tab_points = True
    tabbed = with_cutouts("bracket", [5.0])
    job = rig.job([("bracket", 1, tabbed, (8.0, 6.0))])
    job = dataclasses.replace(job, parts=(dataclasses.replace(job.parts[0], tabs=True),),
                              tabs=dataclasses.replace(job.tabs, width_in=0.3, height_in=0.0394, wide_max_tier=0))
    result = rig.run(job)
    [sheet] = result.sheets
    assert sheet.tap and not sheet.errors
    [fake] = rig.fake.sheets.values()
    assert fake["tabs"] == {"[inner] cutouts - 2 tabs each (narrow tabs)": 2, "[outer] p01-1": 6} and fake["tab_points"] == {}
    assert any("tabs at points didn't work" in n and "spread evenly by Fusion instead" in n for n in sheet.notes)
    assert fake["tab_size"]["[outer] p01-1"][0] < 0.2       # Fusion's own spots can be on curves: never 0.3 in there


def test_the_preview_gets_label_spots_for_every_part(tmp_path):
    import json
    rig = Rig(tmp_path)
    result = rig.run(rig.job([("gusset", 2, plate(name="gusset"), (6.0, 4.0)),
                              ("bracket", 1, with_cutouts("bracket", [5.0]), (8.0, 6.0))]))
    [sheet] = result.sheets
    spots = json.loads((rig.out / f"{sheet.name}.labels.json").read_text())
    assert spots["image"] == [1000, 1600] and spots["sheet"] == sheet.name
    order = {inst: n for n, inst in enumerate(sheet.outer_order, 1)}
    assert [s["n"] for s in spots["labels"]] == list(range(1, 4))
    names = {s["n"]: (s["name"], s["copy"]) for s in spots["labels"]}
    assert names[order["p01-1"]] == ("gusset", "1/2") and names[order["p01-2"]] == ("gusset", "2/2")
    assert names[order["p02-1"]] == ("bracket", "")
    assert all(0 < s["x"] < 1000 and 0 < s["y"] < 1600 and s["room"] > 0 for s in spots["labels"])


def test_wide_tabs_only_where_their_ramp_has_room_else_the_templates(tmp_path):
    # 2026-11-02: 0.3 in tabs crashed Fusion's stock simulation whenever one's ramp ran into a corner, fillet or
    # curve (tsize1, r054, r059) and never with every one on room for its whole ramp (r060). The fake's cutouts are
    # 0.5 in squares: no room for a 0.3 in tab's ramp, so they keep the template's width in an op of their own.
    rig = Rig(tmp_path)
    job = rig.job([("bracket", 1, with_cutouts("bracket", [5.0, 5.0]), (8.0, 6.0))])
    job = dataclasses.replace(job, parts=(dataclasses.replace(job.parts[0], tabs=True),),
                              tabs=dataclasses.replace(job.tabs, width_in=0.3, height_in=0.0394, wide_max_tier=0))
    result = rig.run(job)
    [sheet] = result.sheets
    assert sheet.tap and not sheet.errors
    [fake] = rig.fake.sheets.values()
    inner = [op for op in fake["ops"] if op.startswith("[inner]")]
    assert "[inner] cutouts - 2 tabs each (narrow tabs)" in inner and "[inner] cutouts - 2 tabs each" not in inner
    assert fake["tab_size"]["[inner] cutouts - 2 tabs each (narrow tabs)"] == (0.0, 0.0394)    # the template's width
    assert fake["tab_size"]["[outer] p01-1"] == (0.3, 0.0394) and len(fake["tab_points"]["[outer] p01-1"]) == 6


def test_a_sheet_with_a_cutout_that_needs_tabs_on_a_curve_keeps_the_templates_width_everywhere(tmp_path):
    # r061/r062: beside 0.3 in tabs, even the template's tabs on a curve crashed Fusion; on their own they never did
    # (r058). A round cutout has nowhere else for its tabs, so its whole sheet gets the template's width.
    rig = Rig(tmp_path)
    rig.fake.round_loops = True
    job = rig.job([("bracket", 1, with_cutouts("bracket", [5.0, 5.0]), (8.0, 6.0))])
    job = dataclasses.replace(job, parts=(dataclasses.replace(job.parts[0], tabs=True),),
                              tabs=dataclasses.replace(job.tabs, width_in=0.3, height_in=0.0394, wide_max_tier=0))
    result = rig.run(job)
    [sheet] = result.sheets
    assert sheet.tap and not sheet.errors
    [fake] = rig.fake.sheets.values()
    assert "[inner] cutouts - 2 tabs each" in fake["ops"] and not any("narrow" in op for op in fake["ops"])
    assert set(fake["tab_size"].values()) == {(0.0, 0.0394)}                   # the template's width everywhere
    assert len(fake["tab_points"]["[inner] cutouts - 2 tabs each"]) == 4         # the round ones still get theirs
    assert any("needs tabs on a curve" in n for n in sheet.notes)


def wide_job(rig, free_max_in):
    job = rig.job([("bracket", 1, with_cutouts("bracket", [5.0, 5.0]), (8.0, 6.0))])
    return dataclasses.replace(job, parts=(dataclasses.replace(job.parts[0], tabs=True),),
                               tabs=dataclasses.replace(job.tabs, width_in=0.3, height_in=0.0394, wide_max_tier=0,
                                                        free_max_in=free_max_in))


def test_small_curved_cutouts_are_cut_free_so_the_rest_get_wide_tabs(tmp_path):
    # The user's pick (2026-11-02): a round cutout has no straight edge for a tab, and a tab on a curve beside
    # 0.3 in tabs crashed Fusion (r061, r062). One this small is cut free instead, and the sheet keeps 0.3 in.
    rig = Rig(tmp_path)
    rig.fake.round_loops = True                                                  # 0.5 in circles
    result = rig.run(wide_job(rig, free_max_in=2.0))
    [sheet] = result.sheets
    assert sheet.tap and not sheet.errors
    [fake] = rig.fake.sheets.values()
    assert not any(op.startswith("[inner] cutouts - ") for op in fake["ops"])   # no tabbed copy of [inner]
    assert len(fake["fills"]["[inner] cutouts"].loops) == 2                      # both in the plain, untabbed op
    assert fake["tab_size"]["[outer] p01-1"] == (0.3, 0.0394)
    [warning] = [w.msg for w in sheet.warnings if "cut free" in w.msg]
    assert warning.startswith("2 small curved cutouts cut free with no tabs") and "0.50 in across" in warning
    assert "p01-1 (2)" in warning and "loose slugs" in warning


def test_curved_cutouts_over_the_size_limit_keep_their_tabs_and_the_templates_width(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.round_loops = True
    result = rig.run(wide_job(rig, free_max_in=0.4))                             # the circles are 0.5 in across
    [sheet] = result.sheets
    [fake] = rig.fake.sheets.values()
    assert "[inner] cutouts - 2 tabs each" in fake["ops"]
    assert set(fake["tab_size"].values()) == {(0.0, 0.0394)}
    assert not any("cut free" in w.msg for w in sheet.warnings)


def test_cutouts_with_straight_edges_are_never_cut_free(tmp_path):
    rig = Rig(tmp_path)                                                          # 0.5 in squares: narrow tabs fit
    result = rig.run(wide_job(rig, free_max_in=2.0))
    [fake] = rig.fake.sheets.values()
    assert "[inner] cutouts - 2 tabs each (narrow tabs)" in fake["ops"]
    assert not any("cut free" in w.msg for w in result.sheets[0].warnings)


def test_nothing_is_cut_free_when_something_else_keeps_the_setup_narrow():
    # A curvy outline (or a fitted slug) needs tabs on a curve too: the setup stays at the template's width, so
    # cutting the small cutouts free would gain nothing. They keep their tabs.
    import math
    from autocam_core.tabs import Seg
    from autocam_worker import pipeline

    circle = [Seg("curve", tuple((0.25 * math.cos(2 * math.pi * k / 48), 0.25 * math.sin(2 * math.pi * k / 48))
                                 for k in range(49)))]

    class Loops:
        def loop_segments(self, cid, fid, index):
            return circle

    tabs = TabSpec(width_in=0.3, wide_max_tier=0, free_max_in=2.0)
    fake_job = type("J", (), {"tabs": tabs})()
    sheet = type("S", (), {"notes": [], "warnings": [], "instances": [], "name": "S1"})()
    tabbed = {2: [("c1", 1, 0), ("c1", 1, 1)]}
    loops = []
    wide = pipeline._wide_setup(Loops(), fake_job, sheet, tabbed, loops, [(("c1", 1, None), 2)], 0.157, "S1")
    assert not wide and tabbed == {2: [("c1", 1, 0), ("c1", 1, 1)]} and loops == [] and not sheet.warnings
