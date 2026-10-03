"""Tabs: the template's tabs turned on for the outlines of parts that ask for them (PartSpec.tabs)."""

import dataclasses

from autocam_core.schema_job import TabSpec
from rig import Rig, plate


def test_only_the_parts_that_ask_get_tabs(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("small", 2, plate(name="small"), (3.0, 3.0)), ("big", 1, plate(name="big"), (10.0, 5.0))])
    job = dataclasses.replace(job, parts=(dataclasses.replace(job.parts[0], tabs=True), job.parts[1]),
                              tabs=TabSpec(distance_in=2.0))
    result = rig.run(job)
    [sheet] = result.sheets
    assert sheet.tap and not sheet.errors
    [fake] = rig.fake.sheets.values()
    assert fake["tabs"] == {"[outer] p01-1": 2.0, "[outer] p01-2": 2.0}             # not p02's outline
    program = (rig.out / sheet.tap).read_text()
    assert program.count("G1 Z0.04") == 2                                          # the program passed its checks


def test_no_tabs_by_default(tmp_path):
    rig = Rig(tmp_path)
    result = rig.run(rig.job([("small", 2, plate(name="small"), (3.0, 3.0))]))
    assert result.sheets[0].tap
    [fake] = rig.fake.sheets.values()
    assert fake["tabs"] == {}
