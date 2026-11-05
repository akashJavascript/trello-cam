"""Straight rows (rows.py, core 0.27.0): the plan, and how much of the sheet a layout uses."""

from autocam_core.rows import Use, row_boxes, rows_depth, straight_rows, used_up


def test_long_parts_go_one_behind_the_other_long_side_across():
    rows = straight_rows([("a", 15.0, 2.0), ("b", 15.0, 2.0), ("c", 15.0, 2.0)], 21.0, 0.5)
    assert [[i[0] for i in r.items] for r in rows] == [["a"], ["b"], ["c"]]
    assert rows_depth(rows, 0.5) == 7.0


def test_rows_fill_left_to_right_deepest_first():
    items = [("small", 4.0, 2.0), ("deep", 10.0, 6.0), ("mid", 8.0, 3.0), ("small2", 4.0, 2.0)]
    rows = straight_rows(items, 21.0, 0.5)
    # deep (10) + mid (8) + 2 spacings = 18.5: small (4) doesn't fit beside them; the two smalls make the next row.
    assert [[i[0] for i in r.items] for r in rows] == [["deep", "mid"], ["small", "small2"]]
    assert [r.depth for r in rows] == [6.0, 2.0]
    assert row_boxes(rows, 1.0, 2.0, 0.5) == {"deep": (1.0, 2.0, 11.0, 8.0), "mid": (11.5, 2.0, 19.5, 5.0),
                                              "small": (1.0, 8.5, 5.0, 10.5), "small2": (5.5, 8.5, 9.5, 10.5)}


def test_spares_come_after_the_robot_parts():
    items = [("spare", 6.0, 5.0), ("robot", 6.0, 2.0)]
    rows = straight_rows(items, 21.0, 0.5, {"spare": 1})
    assert [i[0] for i in rows[0].items] == ["robot", "spare"] and rows[0].depth == 5.0


def test_a_part_longer_than_the_width_or_of_unknown_size_is_not_planned():
    assert straight_rows([("a", 22.0, 2.0)], 21.0, 0.5) is None
    assert straight_rows([("a", 0.0, 0.0)], 21.0, 0.5) is None


def test_used_up_counts_the_room_beside_as_kept():
    # Three 15 x 2 bars in rows across a 21.5 in band from y 0: 7 in long with margins (0.25 + 6.5 + 0.25)...
    bars = [(0.25, 0.25 + n * 2.25, 15.25, 2.25 + n * 2.25) for n in range(3)]
    straight = used_up(bars, 0.0, 21.5, 0.0, 0.25, 0.75, 3.0)
    assert straight.length == 7.0
    # ...and the room right of them: from 15.25 + 0.75 to 21.5, the whole band deep (7 in).
    assert straight.room == round(5.5 * 7.0, 2) and straight.used == round(7.0 * 21.5 - 5.5 * 7.0, 2)
    # The same bars on a slant, as their boxes: 7.7 in long, nothing beside wide enough to keep.
    slant = [(1.5, 0.25, 16.5, 4.5), (3.0, 1.9, 20.5, 6.5), (4.5, 3.5, 20.0, 7.7)]
    tilted = used_up(slant, 0.0, 21.5, 0.0, 0.25, 0.75, 3.0)
    assert tilted.length == 7.95 and tilted.room == 0.0
    assert straight.used < tilted.used - 0.25 * 21.5


def test_nothing_placed_uses_nothing():
    assert used_up([], 0.0, 21.5, 0.0, 0.25, 0.75, 3.0) == Use(0.0, 0.0, 0.0)
