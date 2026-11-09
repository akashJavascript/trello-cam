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


def test_an_offcut_on_the_rack_goes_the_way_round_with_the_longer_free_stretch(tmp_path):
    rig = Rig(tmp_path)
    # As before, all 4 fit either way round: on the bed it stays as it was; on the rack (core 0.28.0) turning it is no
    # extra work, so it's loaded the way with more room.
    for on_bed, turned in ((True, False), (False, True)):
        job = job_with(rig, [("plate", 4, plate(name="plate"), (10.0, 5.0))],
                       [OffcutSpec("off1", 0.125, ((0.0, 25.0),), on_bed=on_bed)])
        [sheet] = rig.run(job).sheets
        assert sheet.offcut_id == "off1" and sheet.offcut_turned is turned


def test_the_smaller_offcut_on_the_rack_takes_the_parts_turned_round(tmp_path):
    rig = Rig(tmp_path)
    # Offcuts #3 and #4 on 2026-11-05 (r099): the small one has 9 in free the way it was cut (30.5 to 39.5) and 17.5
    # in turned round; the big one 34 in. The 4 plates take 10.25 in. On the rack, the small one takes them turned
    # round, and the big one is kept big.
    offcuts = [OffcutSpec("big", 0.125, ((0.0, 5.0),), on_bed=False),
               OffcutSpec("small", 0.125, ((0.0, 30.0),), on_bed=False)]
    [sheet] = rig.run(job_with(rig, [("plate", 4, plate(name="plate"), (10.0, 5.0))], offcuts)).sheets
    assert sheet.offcut_id == "small" and sheet.offcut_turned


def test_turning_the_offcut_on_the_bed_still_counts_against_it(tmp_path):
    rig = Rig(tmp_path)
    # The same, but the small one is still on the bed the way it was cut: it isn't turned round to save using a
    # second offcut (only to save a new sheet); the plates that don't fit it go on the big one, on the rack.
    offcuts = [OffcutSpec("big", 0.125, ((0.0, 5.0),), on_bed=False), OffcutSpec("small", 0.125, ((0.0, 30.0),))]
    result = rig.run(job_with(rig, [("plate", 4, plate(name="plate"), (10.0, 5.0))], offcuts))
    assert [(s.offcut_id, s.offcut_turned) for s in result.sheets] == [("small", False), ("big", True)]


def test_a_part_fits_beside_an_earlier_cut_by_reaching_into_the_clean_sheet_past_it(tmp_path):
    # Offcut #4 (2026-11-05): a second p0209 beside the first only fit once the room counted the clean 0.75 in past
    # r101's band. Here a 3.75 x 26 in plate: 26.1 in of room by the band, 26.9 in with the clean sheet behind it.
    rig = Rig(tmp_path)
    off = OffcutSpec("off4", 0.125, ((0.25, 12.38), (12.63, 38.755)), beside_in=((6.25, 12.63, 22.75, 38.755),))
    result = rig.run(job_with(rig, [("p0209", 1, plate(name="p0209"), (3.75, 26.0))], [off]))
    [sheet] = result.sheets
    assert sheet.offcut_id == "off4" and not sheet.offcut_turned and sheet.beside_used == (0,)
    [(a, b)] = sheet.beside_along_in
    assert b > 38.755                                     # past the band: the service records it as used


def test_ties_go_to_the_smallest_offcut():
    from autocam_worker.adapter import AdapterError
    import autocam_worker.pipeline as pipeline

    class NoBoxes:
        def box(self, cid):
            raise AdapterError("no")

    def on(offcut_id):
        t = pipeline._Try("as listed", {})
        t.envelopes, t.placed, t.areas = [((0.0, 0.0), (0, 0, 1, 1))], [("p01.1",)], [0]
        t.stock = [(offcut_id, pipeline._Way(False, None))]
        return pipeline._score(NoBoxes(), t, {}, {"small": 200.0, "big": 700.0})
    assert on("small") < on("big")


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
    # right of the tab (10.25 + 4 + 0.75), and behind it: 5 x 2.5 in, kept since the minimum went to 2 in (2026-11-09)
    assert sheet.beside_left_in == ((15.0, 0.5, 22.75, 8.0), (10.0, 5.5, 15.0, 8.0))


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


SHOP = {"part_spacing_in": 0.5, "offcut_gap_in": 0.25, "offcut_bed_gap_in": 0.0, "beside_cut_in": 0.25}


def test_an_offcut_still_clamped_takes_parts_right_past_its_cuts(tmp_path):
    # core 0.31.0, with the shop's settings: still clamped as it was cut, the next parts start 0.25 in past the used
    # stretch (only their cutter's room); put back on (here a scrap that only goes one way), 0.25 in of slack more.
    for on_bed, start in ((True, 7.75), (False, 8.0)):
        (tmp_path / str(on_bed)).mkdir()
        rig = Rig(tmp_path / str(on_bed), **SHOP)
        off = OffcutSpec("off1", 0.125, ((0.0, 7.5),), on_bed=on_bed, can_turn=False)
        [sheet] = rig.run(job_with(rig, [("plate", 2, plate(name="plate"), (10.0, 5.0))], [off])).sheets
        assert sheet.offcut_id == "off1"
        [(y0, y1)] = stretches(rig)
        assert y0 == start and y1 == 39.0                  # the back is the nest region's edge: the part spacing


def test_a_second_p0209_fits_beside_the_first_with_room_to_spare(tmp_path):
    # Offcut #4 on 2026-11-05 with the shop's settings: the room beside r101's p0209 used to leave Arrange 25.125 in
    # for a 25.12 in part (Fusion wouldn't take it). Now, the piece still clamped, the room reaches back to H2's used
    # stretch (no slack) and the part stays 0.25 in from it; beside the first p0209 the slack comes back too:
    # 26.37 in of length, 16 in across.
    rig = Rig(tmp_path, **SHOP)
    off = OffcutSpec("off4", 0.125, ((0.25, 12.38), (12.63, 38.755)), beside_in=((6.25, 12.63, 22.75, 38.755),))
    [sheet] = rig.run(job_with(rig, [("p0209", 1, plate(name="p0209"), (3.75, 25.12))], [off])).sheets
    assert sheet.offcut_id == "off4" and sheet.beside_used == (0,)
    room = rig.fake.arrange_envelopes[0]
    assert (round(room[2] - room[0], 3), room[1], room[3]) == (16.0, 12.63, 39.0)   # core 0.30.0: 15.5 x 25.87 in


def test_a_long_part_takes_the_room_and_the_rest_go_in_the_free_stretch_when_that_keeps_the_room_whole(tmp_path):
    # core 0.31.0 (r110 on offcut #4: Arrange put the p0202s beside the second p0209 in the room by r101's cut and left
    # a 3.7 in strip). The bar only fits the room; the strips fit either. Rooms first, they crowd in with the bar;
    # with the free stretch first, the room keeps a 12 x 29.5 in rectangle beside the bar, and that's kept.
    rig = Rig(tmp_path)
    rig.fake.columns = True                               # like Fusion: down the left edge first
    off = OffcutSpec("off", 0.125, ((0.0, 30.5),), beside_in=((6.0, 0.5, 22.75, 30.0),), can_turn=False)
    parts = [("bar", 1, plate(name="bar"), (3.75, 24.0)), ("strip", 2, plate(name="strip"), (12.0, 2.0))]
    result = rig.run(job_with(rig, parts, [off]))
    [sheet] = result.sheets
    assert sheet.beside_used == (0,) and sheet.used_y_in is not None              # the room and the free stretch
    assert (10.75, 0.5, 22.75, 30.0) in sheet.beside_left_in
    assert any("free stretch before rooms beat the listed order" in n for n in result.notes)
    # its free stretch is still the sheet's last area, so the straight rows look at it
    assert "the last sheet stays as Fusion nested it" in (rig.out / "worker.log").read_text()


def test_small_parts_still_go_in_the_room_beside_cuts_first(tmp_path):
    # The free stretch is the bigger rectangle: a part that fits the room goes there and the stretch stays whole.
    rig = Rig(tmp_path)
    off = OffcutSpec("off", 0.125, ((0.0, 8.5),), beside_in=((8.0, 0.5, 22.75, 8.0),), can_turn=False)
    [sheet] = rig.run(job_with(rig, [("plate", 1, plate(name="plate"), (5.0, 5.0))], [off])).sheets
    assert sheet.beside_used == (0,) and sheet.used_y_in is None


def test_an_offcut_on_the_rack_goes_the_shorter_way_round_when_that_keeps_more_in_one_piece(tmp_path):
    # On the rack, both ways round are the same work. Turned round, the free stretch is longer (33 in) but the room by
    # the earlier cuts is out of reach; as before, the part fits that room and the 25 in stretch stays whole.
    rig = Rig(tmp_path)
    off = OffcutSpec("off", 0.125, ((0.0, 14.0),), beside_in=((8.0, 0.5, 22.75, 13.5),), on_bed=False)
    [sheet] = rig.run(job_with(rig, [("plate", 1, plate(name="plate"), (12.0, 11.0))], [off])).sheets
    assert not sheet.offcut_turned and sheet.beside_used == (0,) and sheet.used_y_in is None
