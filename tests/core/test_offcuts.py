"""Offcuts: where the next nest can go on a partly used sheet (offcuts.py)."""

from autocam_core.offcuts import (
    add_used, as_loaded, beside_as_loaded, best_placement, compact, cut_away, free_length, free_stretch, rect_minus,
    free_rooms, room_beside, to_own, turn_rect,
)

L, LO, HI, GAP = 48.0, 0.5, 39.5, 0.5     # sheet length, the nest region along X, the gap from old cuts


def test_a_short_strip_used_turns_the_sheet_round_for_a_whole_sheets_worth():
    used = [(0.0, 7.5)]                    # r006: the first 7.5 in from the front end
    as_was = free_stretch(used, L, False, LO, HI, GAP)
    assert as_was == (8.0, 39.5)
    turned = best_placement(used, L, LO, HI, GAP, 6.0)
    assert turned.turned and (turned.x0, turned.x1) == (0.5, 39.5)   # the strip is now past the reach


def test_a_long_strip_leaves_the_rest_either_way():
    used = [(0.0, 25.0)]
    p = best_placement(used, L, LO, HI, GAP, 6.0)
    assert p.turned and (p.x0, p.x1) == (0.5, 22.5)       # 48 - 25 - 0.5
    assert free_stretch(used, L, False, LO, HI, GAP) == (25.5, 39.5)


def test_used_at_both_ends_leaves_the_middle():
    used = [(0.0, 7.5), (30.0, 48.0)]      # cut once, then again turned round
    p = best_placement(used, L, LO, HI, GAP, 6.0)
    assert (p.turned, p.x0, p.x1) == (False, 8.0, 29.5)
    assert best_placement(used, L, LO, HI, GAP, 30.0) is None
    assert free_length(used, L, LO, HI, GAP) == 21.5


def test_recording_a_cut_in_the_sheets_own_coordinates():
    used = add_used([(0.0, 7.5)], (0.5, 20.0), L, turned=True)      # nested turned round, from the front
    assert used == ((0.0, 7.5), (28.0, 47.5))
    assert as_loaded(used, L, True) == [(0.5, 20.0), (40.5, 48.0)]
    assert add_used(used, (5.0, 10.0), L, turned=False) == ((0.0, 10.0), (28.0, 47.5))   # overlaps merge


def test_a_fresh_sheet_and_ties():
    p = best_placement([], L, LO, HI, GAP, 6.0)
    assert (p.turned, p.x0, p.x1) == (False, 0.5, 39.5)                # ties keep it the way it was


def test_stretches_read_back_from_json_are_lists():
    assert add_used([[0.0, 7.5]], (10.0, 12.0), L, turned=False) == ((0.0, 7.5), (10.0, 12.0))


# ---- room beside earlier cuts (X across the 24 in width, Y along the 48 in length)

W = 24.0
REGION = (1.25, 0.5, 22.75, 39.5)       # inside the clamp strips (X) and the reach (Y)


def test_room_beside_a_single_part():
    # A 6 x 3 in part at the front left: its band is 0.5 to 4 in; the rest of the band's width is kept, from
    # 0.75 in right of the part (cutter path 0.25 + loading slack 0.5) to the edge of the nest region.
    assert room_beside([(1.5, 0.75, 7.5, 3.75)], (0.5, 4.0), 22.75, 0.25, 0.5, 3.0) == (8.25, 0.5, 22.75, 4.0)
    # Parts across nearly the whole width, or a band under 3 in deep, leave nothing worth keeping.
    assert room_beside([(1.5, 0.75, 20.0, 3.75)], (0.5, 4.0), 22.75, 0.25, 0.5, 3.0) is None
    assert room_beside([(1.5, 0.75, 7.5, 2.0)], (0.5, 2.5), 22.75, 0.25, 0.5, 3.0) is None
    assert room_beside([], (0.5, 4.0), 22.75, 0.25, 0.5, 3.0) is None


def test_turning_a_sheet_end_for_end_moves_its_room_to_the_other_corner():
    r = (8.25, 0.5, 22.75, 4.0)                       # front right, end A at the front
    assert turn_rect(r, W, L) == (1.25, 44.0, 15.75, 47.5)    # back left, past the reach
    assert turn_rect(turn_rect(r, W, L), W, L) == r
    assert to_own(turn_rect(r, W, L), W, L, True) == r and to_own(r, W, L, False) == r


def test_room_beside_as_loaded_is_cut_to_what_the_machine_reaches():
    beside = [(8.25, 0.5, 22.75, 4.0), (10.0, 20.0, 22.75, 34.0)]
    assert beside_as_loaded(beside, W, L, False, REGION, 3.0) == [(0, beside[0]), (1, beside[1])]
    # Turned round, the first is past the reach; the second lands 14 to 28 in from the front, still in reach.
    assert beside_as_loaded(beside, W, L, True, REGION, 3.0) == [(1, (1.25, 14.0, 14.0, 28.0))]
    # Cut down to the reach, what's left must still be 3 in deep.
    assert beside_as_loaded([(10.0, 37.0, 22.75, 46.0)], W, L, False, REGION, 3.0) == []
    assert beside_as_loaded([(10.0, 35.0, 22.75, 46.0)], W, L, False, REGION, 3.0) == [(0, (10.0, 35.0, 22.75, 39.5))]


# ---- parts cut out by hand (cut_away)

ACROSS, MARGIN, MIN_SIDE = (1.25, 22.75), 0.5, 3.0


def test_a_hand_cut_in_the_free_stretch_keeps_the_room_either_side_of_it():
    # a 5 x 6 in part cut 6 to 11 across, 12 to 18 along, from a sheet used 0.5 to 7.5 along
    used, beside = cut_away([(0.5, 7.5)], [], (6.0, 12.0, 11.0, 18.0), L, ACROSS, MARGIN, GAP, MIN_SIDE)
    assert used == ((0.5, 7.5), (11.5, 18.5))
    assert beside == [(1.25, 11.5, 5.0, 18.5), (12.0, 11.5, 22.75, 18.5)]   # 1 in clear of the cut both sides
    used, beside = cut_away([(0.5, 7.5)], [], (5.0, 12.0, 10.0, 18.0), L, ACROSS, MARGIN, GAP, MIN_SIDE)
    assert beside == [(11.0, 11.5, 22.75, 18.5)]       # 1.25 to 4 in on its left is under 3 in: no room there


def test_a_hand_cut_across_the_whole_width_leaves_no_room_beside_it():
    used, beside = cut_away([], [], (0.0, 20.0, 24.0, 26.0), L, ACROSS, MARGIN, GAP, MIN_SIDE)
    assert used == ((19.5, 26.5),) and beside == []


def test_a_hand_cut_in_room_beside_earlier_cuts_takes_its_piece_of_that_room():
    room = (8.0, 0.5, 22.75, 7.5)                       # beside a part cut at the front-left
    used, beside = cut_away([(0.5, 7.5)], [room], (15.0, 1.0, 18.0, 4.0), L, ACROSS, MARGIN, GAP, MIN_SIDE)
    assert used == ((0.5, 7.5),)                       # inside the used band already
    assert beside == [(8.0, 0.5, 14.0, 7.5), (19.0, 0.5, 22.75, 7.5)]   # above it, 14 to 19 x 5 to 7.5, is too small


def test_a_hand_cut_overlapping_a_used_band_only_adds_room_where_the_sheet_was_free():
    used, beside = cut_away([(0.5, 14.0)], [], (6.0, 12.0, 11.0, 22.0), L, ACROSS, MARGIN, GAP, MIN_SIDE)
    assert used == ((0.5, 22.5),)
    assert beside == [(1.25, 14.0, 5.0, 22.5), (12.0, 14.0, 22.75, 22.5)]   # not over the old band


def test_rect_minus_leaves_rectangles_that_dont_overlap():
    pieces = rect_minus((0.0, 0.0, 10.0, 10.0), (3.0, 4.0, 6.0, 7.0))
    assert pieces == [(0.0, 0.0, 3.0, 10.0), (6.0, 0.0, 10.0, 10.0), (3.0, 0.0, 6.0, 4.0), (3.0, 7.0, 6.0, 10.0)]
    assert sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in pieces) == 100 - 9
    assert rect_minus((0.0, 0.0, 1.0, 1.0), (2.0, 2.0, 3.0, 3.0)) == [(0.0, 0.0, 1.0, 1.0)]


def test_a_room_the_hand_cut_takes_whole_keeps_its_place_until_compacted():
    rooms = [(8.0, 0.5, 12.0, 7.5), (14.0, 0.5, 22.75, 7.5)]        # a sheet may hold room 1 by its index
    _, beside = cut_away([(0.5, 7.5)], rooms, (8.0, 1.0, 12.0, 7.0), L, ACROSS, MARGIN, GAP, MIN_SIDE)
    assert beside[0] == (8.0, 0.5, 8.0, 0.5) and beside[1] == (14.0, 0.5, 22.75, 7.5)
    assert compact(beside) == [(14.0, 0.5, 22.75, 7.5)]


# ---------------------------------------------------------------- every empty corner (2026-11-03)

def test_every_empty_corner_among_the_parts_is_kept():
    # Like r068: a part at the front left and a long one behind it on the right, in a band 0.5 to 26.55 in. 1 in
    # clear of each (0.5 spacing + 0.5 loading slack). The room behind the front part (left) and the corner in front
    # of the long one (right) are both kept, biggest first; the slivers beside them aren't.
    boxes = [(1.8, 1.0, 11.4, 14.0), (11.9, 8.8, 21.6, 26.0)]
    assert free_rooms(boxes, (0.5, 0.5, 23.5, 26.55), 1.0, 3.0) == [(0.5, 15.0, 10.9, 26.55), (12.4, 0.5, 23.5, 7.8)]


def test_a_single_part_keeps_just_the_room_to_its_right_as_before():
    assert free_rooms([(1.5, 0.75, 7.5, 3.75)], (0.5, 0.5, 22.75, 4.0), 0.75, 3.0) == [(8.25, 0.5, 22.75, 4.0)]
    assert free_rooms([], (0.5, 0.5, 22.75, 4.0), 0.75, 3.0) == []


def test_rooms_never_overlap_and_stay_clear_of_every_part():
    import random
    rnd = random.Random(5940)
    area, clear, least = (0.5, 0.5, 23.5, 39.5), 1.0, 3.0
    for _ in range(200):
        boxes = []
        for _ in range(rnd.randint(1, 8)):
            x, y = rnd.uniform(0.5, 20), rnd.uniform(0.5, 36)
            boxes.append((x, y, min(23.5, x + rnd.uniform(1, 8)), min(39.5, y + rnd.uniform(1, 10))))
        rooms = free_rooms(boxes, area, clear, least)
        assert len(rooms) <= 6
        for i, r in enumerate(rooms):
            assert r[2] - r[0] >= least - 0.002 and r[3] - r[1] >= least - 0.002
            assert area[0] - 1e-6 <= r[0] and r[2] <= area[2] + 1e-6 and area[1] - 1e-6 <= r[1] and r[3] <= area[3] + 1e-6
            for b in boxes:                              # at least `clear` from every part
                assert r[2] <= b[0] - clear + 0.002 or r[0] >= b[2] + clear - 0.002 or \
                    r[3] <= b[1] - clear + 0.002 or r[1] >= b[3] + clear - 0.002
            for q in rooms[i + 1:]:                      # rooms never overlap
                assert r[2] <= q[0] + 1e-6 or q[2] <= r[0] + 1e-6 or r[3] <= q[1] + 1e-6 or q[3] <= r[1] + 1e-6
        areas = [(r[2] - r[0]) * (r[3] - r[1]) for r in rooms]
        assert areas == sorted(areas, reverse=True)      # biggest first
