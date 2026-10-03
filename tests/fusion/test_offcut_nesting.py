"""Offcuts in the pipeline: a job's partly used sheets are filled before new sheets (offcuts.py, pipeline)."""

import dataclasses

from autocam_core.schema_job import OffcutSpec
from rig import Rig, plate


def job_with(rig, parts, offcuts):
    return dataclasses.replace(rig.job(parts), offcuts=tuple(offcuts))


def test_an_offcut_is_filled_turned_round_before_any_new_sheet(tmp_path):
    rig = Rig(tmp_path)
    # 25 in used from end A: turned round, the free stretch is 0.5 to 22.5 in (gap 0.5 from the old cuts).
    job = job_with(rig, [("plate", 6, plate(name="plate"), (10.0, 5.0))], [OffcutSpec("off1", 0.125, ((0.0, 25.0),))])
    result = rig.run(job)
    [sheet] = result.sheets
    assert sheet.offcut_id == "off1" and sheet.offcut_turned
    x0, y0, x1, y1 = rig.fake.arrange_envelopes[0]
    origin_x = x0 - 0.75
    assert (round(x1 - origin_x, 3), round(x0 - origin_x, 3)) == (22.25, 0.75)     # free stretch less the spacing
    assert sheet.used_x_in[0] >= 0.5 and sheet.used_x_in[1] <= 22.5
    assert sheet.free_length_in is not None


def test_what_doesnt_fit_on_the_offcut_goes_on_a_new_sheet(tmp_path):
    rig = Rig(tmp_path)
    job = job_with(rig, [("plate", 12, plate(name="plate"), (10.0, 5.0))], [OffcutSpec("off1", 0.125, ((0.0, 25.0),))])
    result = rig.run(job)
    assert [s.offcut_id for s in result.sheets] == ["off1", None]
    assert sum(p.count for s in result.sheets for p in s.parts) == 12


def test_offcuts_of_another_thickness_or_without_room_are_left_alone(tmp_path):
    rig = Rig(tmp_path)
    offcuts = [OffcutSpec("thick", 0.25, ()),                        # 1/4 in: these parts are 1/8
               OffcutSpec("full", 0.125, ((0.0, 20.0), (24.0, 48.0)))]   # only 3 in free in the middle
    result = rig.run(job_with(rig, [("plate", 2, plate(name="plate"), (10.0, 5.0))], offcuts))
    assert [s.offcut_id for s in result.sheets] == [None]


def test_an_offcut_that_takes_nothing_doesnt_stop_the_nest(tmp_path):
    rig = Rig(tmp_path)
    # 31.5 in used: turned round, 0.5 to 16 in is free, too short for a 20 in part.
    job = job_with(rig, [("long", 1, plate(name="long"), (20.0, 5.0))], [OffcutSpec("short", 0.125, ((0.0, 31.5),))])
    result = rig.run(job)
    assert [s.offcut_id for s in result.sheets] == [None] and result.parts[0].placed == 1


def test_offcuts_in_the_job_are_checked(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("plate", 1, plate(name="plate"), (10.0, 5.0))])
    ok = dataclasses.replace(job, offcuts=(OffcutSpec("o1", 0.125, ((0.0, 7.5),)),))
    assert ok.validate() == []
    bad = dataclasses.replace(job, offcuts=(OffcutSpec("o1", 0.125, ((0.0, 60.0),)), OffcutSpec("o1", 0.125, ())))
    problems = bad.validate()
    assert any("duplicate id" in e for e in problems) and any("inside the sheet" in e for e in problems)
