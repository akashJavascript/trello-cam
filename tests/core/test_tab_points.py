"""Where tabs go on a contour (autocam_core/tabs.place_tabs)."""

import math

from autocam_core.tabs import Seg, TabPoint, place_tabs

CUTTER = 4 / 25.4
TAB = CUTTER                               # the template's tab width: the cutter's diameter


def lines(*pts):
    pts = list(pts) + [pts[0]]
    return [Seg("line", (a, b)) for a, b in zip(pts, pts[1:])]


def arc(cx, cy, r, a0, a1, n=24):
    return Seg("curve", tuple((cx + r * math.cos(a0 + (a1 - a0) * k / n), cy + r * math.sin(a0 + (a1 - a0) * k / n))
                              for k in range(n + 1)))


def stadium(length, r):
    """Two straight sides joined by half circles: the lines are (0,0)-(length,0) and (length,2r)-(0,2r)."""
    return [Seg("line", ((0.0, 0.0), (length, 0.0))), arc(length, r, r, -math.pi / 2, math.pi / 2),
            Seg("line", ((length, 2 * r), (0.0, 2 * r))), arc(0.0, r, r, math.pi / 2, 3 * math.pi / 2)]


def far_from_corners(p, corners, d):
    return all(math.hypot(p.x - cx, p.y - cy) >= d - 1e-6 for cx, cy in corners)


def test_a_square_gets_one_tab_on_each_side_clear_of_the_corners():
    sq = [(0, 0), (4, 0), (4, 4), (0, 4)]
    tabs = place_tabs(lines(*sq), 4, CUTTER, TAB)
    assert len(tabs) == 4 and all(t.on_line and t.clear_of_corners for t in tabs)
    assert all(far_from_corners(t, sq, CUTTER + TAB / 2) for t in tabs)
    sides = {("x0" if t.x < 1e-6 else "x4" if t.x > 4 - 1e-6 else "y0" if t.y < 1e-6 else "y4") for t in tabs}
    assert sides == {"x0", "x4", "y0", "y4"}


def test_two_tabs_go_roughly_opposite():
    tabs = place_tabs(lines((0, 0), (6, 0), (6, 2), (0, 2)), 2, CUTTER, TAB)
    a, b = tabs
    assert math.hypot(a.x - b.x, a.y - b.y) > 2.0           # across the part, not side by side


def test_lines_win_over_curves():
    tabs = place_tabs(stadium(3.0, 1.0), 2, CUTTER, TAB)
    assert all(t.on_line and t.clear_of_corners for t in tabs)
    assert {round(t.y, 3) for t in tabs} == {0.0, 2.0}       # one on each straight side


def test_curves_take_tabs_when_the_lines_cant_hold_them_all():
    tabs = place_tabs(stadium(0.6, 1.0), 6, CUTTER, TAB)    # short straights, long curves
    assert len(tabs) == 6
    assert 0 < sum(t.on_line for t in tabs) < 6              # some on the straights, the rest on the curves


def test_near_a_corner_is_fine_when_the_edges_are_short():
    sq = [(0, 0), (0.4, 0), (0.4, 0.4), (0, 0.4)]          # a small cutout: no spot is a cutter clear of corners
    tabs = place_tabs(lines(*sq), 2, CUTTER, TAB)
    assert len(tabs) == 2 and all(t.on_line for t in tabs) and not any(t.clear_of_corners for t in tabs)


def test_a_circle_gets_evenly_spread_tabs():
    tabs = place_tabs([arc(0, 0, 1.0, 0, 2 * math.pi, 72)], 3, CUTTER, TAB)
    assert len(tabs) == 3 and not any(t.on_line for t in tabs)
    angles = sorted(math.degrees(math.atan2(t.y, t.x)) % 360 for t in tabs)
    gaps = [(b - a) for a, b in zip(angles, angles[1:] + [angles[0] + 360])]
    assert all(abs(g - 120) < 6 for g in gaps)


def test_tabs_stay_apart_and_none_means_none():
    tabs = place_tabs(lines((0, 0), (10, 0), (10, 0.5), (0, 0.5)), 6, CUTTER, TAB)
    pts = [(t.x, t.y) for t in tabs]
    assert len(tabs) == 6 and all(math.hypot(a[0] - b[0], a[1] - b[1]) >= TAB + 2 * CUTTER - 1e-6
                                  for i, a in enumerate(pts) for b in pts[i + 1:])
    assert place_tabs(lines((0, 0), (1, 0), (1, 1)), 0, CUTTER, TAB) == []


def test_a_wide_tab_stays_off_small_fillets():
    # A plate with 0.1 in fillets at its corners (each quarter arc 0.16 in long) and 0.3 in tabs: every tab sits
    # wholly on a straight side, never across a fillet.
    r, w, h = 0.1, 3.0, 2.0
    segs = []
    corners = [((w - r, 0), (w, r), (w - r, r), -math.pi / 2), ((w, h - r), (w - r, h), (w - r, h - r), 0.0),
               ((r, h), (0, h - r), (r, h - r), math.pi / 2), ((0, r), (r, 0), (r, r), math.pi)]
    start = (r, 0)
    for a, b, centre, a0 in corners:
        segs.append(Seg("line", (start, a)))
        segs.append(arc(centre[0], centre[1], r, a0, a0 + math.pi / 2, 6))
        start = b
    tabs = place_tabs(segs, 6, CUTTER, 0.3)
    assert len(tabs) == 6 and all(t.on_line for t in tabs)
    for t in tabs:                                       # 0.15 in (half a tab) clear of every fillet
        assert min(abs(t.x - r), abs(t.x - (w - r)), abs(t.y - r), abs(t.y - (h - r))) >= 0.15 - 1e-6 or \
            (r + 0.15 <= t.x <= w - r - 0.15 and r + 0.15 <= t.y <= h - r - 0.15) or \
            (t.y in (0.0, h) and r + 0.15 - 1e-6 <= t.x <= w - r - 0.15 + 1e-6) or \
            (t.x in (0.0, w) and r + 0.15 - 1e-6 <= t.y <= h - r - 0.15 + 1e-6)
