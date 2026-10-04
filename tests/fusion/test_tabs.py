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
    # config's 0.3 in tabs and the 4 mm cutter, each tab needs 2 x (0.3 + 0.157) = 0.91 in of contour.
    tabbed = with_cutouts("bracket", [20.0, 5.0, 2.0, 1.6, 0.5])
    plain = with_cutouts("spacer", [5.0])
    job = rig.job([("bracket", 1, tabbed, (8.0, 6.0)), ("spacer", 1, plain, (4.0, 4.0))])
    assert (job.tabs.width_in, job.tabs.height_in) == (0.3, 0.0394)
    job = dataclasses.replace(job, parts=(dataclasses.replace(job.parts[0], tabs=True), job.parts[1]))
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
    job = dataclasses.replace(job, parts=(dataclasses.replace(job.parts[0], tabs=True),))
    result = rig.run(job)
    [sheet] = result.sheets
    assert sheet.tap and not sheet.errors
    [fake] = rig.fake.sheets.values()
    assert fake["tabs"] == {"[inner] cutouts - 2 tabs each": 2, "[outer] p01-1": 6} and fake["tab_points"] == {}
    assert any("tabs at points didn't work" in n and "spread evenly by Fusion instead" in n for n in sheet.notes)
