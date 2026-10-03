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
    assert fake["tabs"] == {"[outer] p01-1": 4, "[outer] p01-2": 4}                 # 32 in / 8 in; not p02's
    program = (rig.out / sheet.tap).read_text()
    assert program.count("G1 Z0.04") == 2                                          # the program passed its checks


def test_no_tabs_without_the_box(tmp_path):
    rig = Rig(tmp_path)
    result = rig.run(rig.job([("small", 2, plate(name="small"), (3.0, 3.0))]))
    assert result.sheets[0].tap
    [fake] = rig.fake.sheets.values()
    assert fake["tabs"] == {}


def test_every_cutout_gets_tabs_by_its_length_however_short(tmp_path):
    rig = Rig(tmp_path)
    # Cutouts 20, 5, 2 and 1.6 in around, and one of 0.5 in: one per 2.5 in, 2 to 6, as many as fit (one per
    # 4 cutter widths, 0.63 in with the 4 mm cutter).
    tabbed = with_cutouts("bracket", [20.0, 5.0, 2.0, 1.6, 0.5])
    plain = with_cutouts("spacer", [5.0])
    job = rig.job([("bracket", 1, tabbed, (8.0, 6.0)), ("spacer", 1, plain, (4.0, 4.0))])
    job = dataclasses.replace(job, parts=(dataclasses.replace(job.parts[0], tabs=True), job.parts[1]))
    result = rig.run(job)
    [sheet] = result.sheets
    assert sheet.tap and not sheet.errors
    [fake] = rig.fake.sheets.values()
    inner = [op for op in fake["ops"] if op.startswith("[inner]")]
    assert inner == ["[inner] cutouts", "[inner] cutouts - 2 tabs each", "[inner] cutouts - 6 tabs each"]
    assert fake["ops"].index(inner[-1]) < min(i for i, op in enumerate(fake["ops"]) if op.startswith("[outer]"))
    assert fake["tabs"]["[inner] cutouts - 6 tabs each"] == 6 and fake["tabs"]["[inner] cutouts - 2 tabs each"] == 2
    lengths = {op: len(fake["fills"][op].loops) for op in inner}
    # 20 in -> 6; 5 in -> 2; 2 in -> 3 fit but 1 wanted -> 2 (the minimum); 1.6 in -> 2 fit -> 2;
    # 0.5 in: none fit, so it's cut with the spacer's cutout, without tabs.
    assert lengths == {"[inner] cutouts": 2, "[inner] cutouts - 2 tabs each": 3, "[inner] cutouts - 6 tabs each": 1}
    assert fake["tabs"]["[outer] p01-1"] == 6 and "[outer] p02-1" not in fake["tabs"]
