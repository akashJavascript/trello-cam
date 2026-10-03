"""Offcuts in the pipeline: a job's partly used sheets are filled before new sheets (offcuts.py, pipeline)."""

import dataclasses

from autocam_core.schema_job import OffcutSpec
from rig import Rig, plate


def job_with(rig, parts, offcuts):
    return dataclasses.replace(rig.job(parts), offcuts=tuple(offcuts))


def stretches(rig):
    """Each Arrange's area along the length (Y)."""
    return [(y0, y1) for _, y0, _, y1 in rig.fake.arrange_envelopes]


def test_parts_that_fit_go_on_the_offcut_the_same_way_round_as_before(tmp_path):
    rig = Rig(tmp_path)
    # Cut once with end A at the front, 7.5 in used: the same way round, 8 to 39.5 in along Y is free.
    job = job_with(rig, [("plate", 4, plate(name="plate"), (10.0, 5.0))], [OffcutSpec("off1", 0.125, ((0.0, 7.5),))])
    result = rig.run(job)
    [sheet] = result.sheets
    assert sheet.offcut_id == "off1" and not sheet.offcut_turned                    # no spinning the sheet round
    assert sheet.used_y_in[0] >= 8.0


def test_spinning_an_offcut_round_wins_only_when_it_saves_a_new_sheet(tmp_path):
    rig = Rig(tmp_path)
    # 25 in used from end A. The same way round: 25.5 to 39.5 in (4 of these fit). Spun round: 0.5 to 22.5 in (8 fit).
    job = job_with(rig, [("plate", 6, plate(name="plate"), (10.0, 5.0))], [OffcutSpec("off1", 0.125, ((0.0, 25.0),))])
    result = rig.run(job)
    [sheet] = result.sheets
    assert sheet.offcut_id == "off1" and sheet.offcut_turned                        # all 6 on it, no new sheet
    widths = sorted({round(x1 - x0, 2) for x0, x1 in stretches(rig)})
    assert 13.5 in widths and 21.5 in widths                                        # both ways round were tried
    assert any("offcuts spun round beat the listed order" in n for n in result.notes)


def test_what_doesnt_fit_on_the_offcut_goes_on_a_new_sheet(tmp_path):
    rig = Rig(tmp_path)
    job = job_with(rig, [("plate", 12, plate(name="plate"), (10.0, 5.0))], [OffcutSpec("off1", 0.125, ((0.0, 25.0),))])
    result = rig.run(job)
    assert [s.offcut_id for s in result.sheets] == ["off1", None]
    assert not result.sheets[0].offcut_turned          # a new sheet either way, so no point spinning the offcut
    assert sum(p.count for s in result.sheets for p in s.parts) == 12


def test_offcuts_of_another_thickness_or_without_room_are_left_alone(tmp_path):
    rig = Rig(tmp_path)
    offcuts = [OffcutSpec("thick", 0.25, ()),                        # 1/4 in: these parts are 1/8
               OffcutSpec("full", 0.125, ((0.0, 20.0), (24.0, 48.0)))]   # only 3 in free in the middle
    result = rig.run(job_with(rig, [("plate", 2, plate(name="plate"), (10.0, 5.0))], offcuts))
    assert [s.offcut_id for s in result.sheets] == [None]


def test_an_offcut_that_takes_nothing_doesnt_stop_the_nest(tmp_path):
    rig = Rig(tmp_path)
    # 31.5 in used: either way round, at most 0.5 to 16 in is free, too short for a part 20 in long.
    job = job_with(rig, [("long", 1, plate(name="long"), (5.0, 20.0))], [OffcutSpec("short", 0.125, ((0.0, 31.5),))])
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
