"""Offcuts: where the next nest can go on a partly used sheet (offcuts.py)."""

from autocam_core.offcuts import (
    add_used, as_loaded, beside_as_loaded, best_placement, free_length, free_stretch, room_beside, to_own,
    turn_rect,
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
