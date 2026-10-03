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


# ---- room beside earlier cuts

BESIDE = (8.25, 0.5, 22.75, 4.0)        # what a 6 x 3 in part at the front left leaves (sheet coordinates)


def test_a_single_part_leaves_the_room_beside_it(tmp_path):
    rig = Rig(tmp_path)
    result = rig.run(rig.job([("gusset", 1, plate(name="gusset"), (6.0, 3.0))]))
    [sheet] = result.sheets
    # At the front left of the nest area (1.5, 0.75): its band along the length, and the rest of the band's width.
    assert sheet.used_y_in == (0.5, 4.0)
    assert sheet.beside_left_in == (BESIDE,) and sheet.beside_used == ()


def test_room_beside_earlier_cuts_is_filled_first_on_the_same_sheet(tmp_path):
    rig = Rig(tmp_path)
    offcut = OffcutSpec("off1", 0.125, ((0.5, 4.0),), beside_in=(BESIDE,))
    job = job_with(rig, [("small", 2, plate(name="small"), (3.0, 3.0)), ("big", 1, plate(name="big"), (10.0, 5.0))],
                   [offcut])
    result = rig.run(job)
    [sheet] = result.sheets                                          # one sheet: the offcut, two Arranges
    assert sheet.offcut_id == "off1" and sorted((p.part_key, p.count) for p in sheet.parts) == [("p01", 2), ("p02", 1)]
    x0, y0, x1, y1 = rig.fake.arrange_envelopes[0]                  # the first Arrange: the room beside the cut
    assert (round(x1 - x0, 3), y0, y1) == (14.0, 0.75, 3.75)         # 8.25 to 22.75 in, less the part spacing
    # The small plates went beside the old cut (3 in deep: the big one doesn't fit), the big one in the free stretch.
    assert sheet.beside_used == (0,)
    assert sheet.used_y_in == (4.5, 10.0)                            # only the free stretch's part: 4.75 to 9.75
    # Left over: right of the two small plates in the old band, and right of the big plate in its new band.
    assert sheet.beside_left_in == ((15.5, 0.5, 22.75, 4.0), (12.25, 4.5, 22.75, 10.0))
    assert all(p.placed == p.qty for p in result.parts)


def test_an_offcut_with_only_room_beside_cuts_still_takes_parts(tmp_path):
    rig = Rig(tmp_path)
    # The whole length is used (no free stretch either way round), but the room beside the first cut is free.
    offcut = OffcutSpec("off1", 0.125, ((0.0, 48.0),), beside_in=((10.0, 0.5, 22.75, 8.0),))
    result = rig.run(job_with(rig, [("tab", 1, plate(name="tab"), (4.0, 4.0))], [offcut]))
    [sheet] = result.sheets
    assert sheet.offcut_id == "off1" and not sheet.offcut_turned
    assert sheet.beside_used == (0,) and sheet.used_y_in is None and sheet.free_length_in is None
    assert sheet.beside_left_in == ((15.0, 0.5, 22.75, 8.0),)        # right of the tab: 10.25 + 4 + 0.75


def test_room_beside_cuts_too_small_for_anything_is_skipped(tmp_path):
    rig = Rig(tmp_path)
    offcut = OffcutSpec("off1", 0.125, ((0.5, 4.0),), beside_in=(BESIDE,))
    result = rig.run(job_with(rig, [("big", 1, plate(name="big"), (10.0, 5.0))], [offcut]))
    [sheet] = result.sheets
    assert sheet.offcut_id == "off1" and sheet.beside_used == () and sheet.used_y_in == (4.5, 10.0)


def test_squeezing_a_sheet_with_parts_beside_cuts_keeps_it_one_sheet(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.columns = True                               # like Fusion: down the left edge first
    offcut = OffcutSpec("off1", 0.125, ((0.5, 4.0),), beside_in=(BESIDE,))
    job = job_with(rig, [("tab", 1, plate(name="tab"), (3.0, 3.0)), ("gusset", 4, plate(name="gusset"), (6.0, 4.0))],
                   [offcut])
    result = rig.run(job)
    [sheet] = result.sheets
    assert sheet.offcut_id == "off1" and sheet.beside_used == (0,)
    assert sorted((p.part_key, p.count) for p in sheet.parts) == [("p01", 1), ("p02", 4)]
    # The gussets ran 16.75 in down the free stretch; squeezed, two columns of two take 8.25 in.
    assert sheet.used_y_in == (4.5, 4.75 + 8.25 + 0.25)
    assert not rig.fake.late                              # every copy was made before the first Arrange


def test_the_offcut_with_the_least_room_that_fits_is_used_first(tmp_path):
    rig = Rig(tmp_path)
    # Listed first: 31.5 in free. Second: a 9 in scrap at the back (loaded as before). The plate fits either.
    offcuts = [OffcutSpec("big", 0.125, ((0.0, 7.5),)), OffcutSpec("scrap", 0.125, ((0.0, 30.0),))]
    result = rig.run(job_with(rig, [("plate", 1, plate(name="plate"), (10.0, 5.0))], offcuts))
    [sheet] = result.sheets
    assert sheet.offcut_id == "scrap" and not sheet.offcut_turned


def test_parts_too_big_for_the_scrap_go_on_the_bigger_offcut(tmp_path):
    rig = Rig(tmp_path)
    offcuts = [OffcutSpec("big", 0.125, ((0.0, 7.5),)), OffcutSpec("scrap", 0.125, ((0.0, 30.0),))]
    result = rig.run(job_with(rig, [("long", 1, plate(name="long"), (10.0, 20.0))], offcuts))
    assert [s.offcut_id for s in result.sheets] == ["big"]


def test_a_scrap_is_never_turned_round(tmp_path):
    rig = Rig(tmp_path)
    # A 20 in piece (the rest of the length is missing). Turned round it would sit at the back, past its own
    # front edge, so only the way it's loaded counts, even though turned round would hold more.
    scrap = OffcutSpec("scrap", 0.125, ((20.0, 48.0),), can_turn=False)
    result = rig.run(job_with(rig, [("plate", 4, plate(name="plate"), (10.0, 5.0))], [scrap]))
    assert [(s.offcut_id, s.offcut_turned) for s in result.sheets] == [("scrap", False)]
    assert max(y1 for _, _, _, y1 in rig.fake.arrange_envelopes) <= 19.25 + 1e-6    # only ever its front 19.5 in
