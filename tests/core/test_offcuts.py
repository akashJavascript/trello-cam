"""Offcuts: where the next nest can go on a partly used sheet (offcuts.py)."""

from autocam_core.offcuts import add_used, as_loaded, best_placement, free_length, free_stretch

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
