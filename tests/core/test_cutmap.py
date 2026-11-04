"""Rooms from where a program really cuts (cutmap.py, 2026-11-04): the space inside an L or under an arm counts,
and no room ever comes closer than its clearance to a cut."""

import math
import random

import pytest

from autocam_core.cutmap import CutMapError, cut_map, cutting_segments
from autocam_core.offcuts import free_rooms

T = 0.125          # stock
R = 0.0787         # 4 mm cutter
CLEAR = 0.75       # part spacing + offcut slack
SHEET = (0.0, 0.0, 24.0, 48.0)


def program(*outlines, drill=()):
    """Each outline: its corner points, cut once around at Z0 with the cutter outside it (offset by R)."""
    lines = ["[TEST]", "G90", "G20", "G53 Z", "[outer]", "S18000", "M3", "G4 X4."]
    for pts in outlines:
        path = _offset(pts, R)
        lines += [f"G0 X{path[0][0]:.4f} Y{path[0][1]:.4f}", "Z0.6", "G0 Z0.2", "G1 Z0. F20."]
        lines += [f"G1 X{x:.4f} Y{y:.4f} F100." for x, y in path[1:] + path[:1]] + ["G0 Z0.6"]
    for x, y in drill:
        lines += [f"G81 X{x} Y{y} Z0. R0.2 F20.", "G80"]
    lines += ["G53 Z", "M5", "G53 P10"]
    return "\r\n".join(lines) + "\r\n"


def _offset(pts, d):
    """A rectilinear polygon (counterclockwise) grown by d: the cutter's centre line round it."""
    out = []
    n = len(pts)
    for i in range(n):
        (ax, ay), (bx, by), (cx, cy) = pts[i - 1], pts[i], pts[(i + 1) % n]
        n1 = _normal(ax, ay, bx, by)
        n2 = _normal(bx, by, cx, cy)
        out.append((bx + d * (n1[0] + n2[0]), by + d * (n1[1] + n2[1])))
    return out


def _normal(ax, ay, bx, by):
    length = math.hypot(bx - ax, by - ay)
    return (by - ay) / length, -(bx - ax) / length          # outward for a counterclockwise polygon


def ell(x, y, w=10.0, h=10.0, t=3.0):
    """An L: an arm along X and an arm along Y, its empty corner up and to the right."""
    return [(x, y), (x + w, y), (x + w, y + t), (x + t, y + t), (x + t, y + h), (x, y + h)]


def inside(px, py, pts):
    hit = False
    for i in range(len(pts)):
        (ax, ay), (bx, by) = pts[i - 1], pts[i]
        if (ay > py) != (by > py) and px < ax + (py - ay) * (bx - ax) / (by - ay):
            hit = not hit
    return hit


def seg_distance(px, py, s):
    x0, y0, x1, y1 = s
    dx, dy = x1 - x0, y1 - y0
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / l2))
    return math.hypot(px - x0 - t * dx, py - y0 - t * dy)


# ---------------------------------------------------------------- reading the program

def test_only_moves_into_the_stock_count():
    segs = cutting_segments(program([(2, 2), (6, 2), (6, 5), (2, 5)]), T)
    assert segs and all(min(s[1], s[3]) > 1.9 and max(s[0], s[2]) < 6.1 for s in segs)     # no rapids, no park
    assert len({x for x in segs if x[:2] != x[2:]}) == 4                # the four sides (plunge and lift: points)


def test_arcs_become_chords_on_their_circle():
    text = "G90\r\nG20\r\nG0 X5. Y2.\r\nG0 Z0.2\r\nG1 Z0. F20.\r\nG3 X2. Y5. I-3. J0. F60.\r\nG2 X5. Y2. I0. J-3.\r\n"
    segs = [s for s in cutting_segments(text, T) if s[:2] != s[2:]]
    assert len(segs) > 30
    for s in segs:
        for x, y in (s[:2], s[2:]):
            assert math.hypot(x - 2, y - 2) == pytest.approx(3, abs=1e-6)
    ccw = segs[:len(segs) // 2]
    assert all(x >= 2 - 1e-6 and y >= 2 - 1e-6 for s in ccw for x, y in (s[:2], s[2:]))   # the short way round


def test_drilled_holes_are_points_and_unreadable_programs_are_refused():
    segs = cutting_segments(program(drill=[(10, 10), (12, 10)]), T)
    assert (10.0, 10.0, 10.0, 10.0) in segs and (12.0, 10.0, 12.0, 10.0) in segs
    with pytest.raises(CutMapError):
        cutting_segments("G91\r\nG1 X1. Z-0.1\r\n", T)
    with pytest.raises(CutMapError):
        cutting_segments("G90\r\nG0 X0 Y0\r\nG1 Z0. F20.\r\nG2 X2. Y0. R1.\r\n", T)
    with pytest.raises(CutMapError):
        cut_map("G90\r\nG20\r\nG0 X1. Y1.\r\n", SHEET, T, R, CLEAR)          # cuts nothing


# ---------------------------------------------------------------- rooms

def test_the_corner_inside_an_l_is_a_room_its_box_hides():
    l_part = ell(2, 2, w=16, h=16, t=4)                                  # an 16 x 16 in L, arms 4 in wide
    area = (1.25, 0.5, 22.75, 20.0)
    by_box = free_rooms([(2, 2, 18, 18)], area, CLEAR, 3.0)
    rooms = cut_map(program(l_part), SHEET, T, R, CLEAR).rooms(area, 3.0)
    big = max(rooms, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
    assert big[0] >= 6 + CLEAR - 1e-9 and big[1] >= 6 + CLEAR - 1e-9      # up and right of the arms...
    assert big[2] - big[0] > 10 and big[3] - big[1] > 10                  # ...about 11 x 11 in of it
    assert sum((r[2] - r[0]) * (r[3] - r[1]) for r in rooms) > 2 * sum((r[2] - r[0]) * (r[3] - r[1]) for r in by_box)


def test_a_part_alone_leaves_the_same_room_beside_it_within_a_cell():
    rooms = cut_map(program([(2, 1), (8, 1), (8, 4), (2, 4)]), SHEET, T, R, CLEAR).rooms((1.25, 0.5, 22.75, 4.5), 3.0)
    [room] = rooms
    assert room[1:] == (0.5, 22.75, 4.5) and 8.75 <= room[0] <= 8.75 + 0.25   # 8 + 0.75, give or take a cell


def test_rooms_never_come_near_a_cut_or_inside_a_part():
    rng = random.Random(5940)
    for _ in range(25):
        parts = []
        for _ in range(rng.randint(1, 4)):
            x, y = rng.uniform(1.5, 14), rng.uniform(1, 30)
            parts.append(ell(x, y, w=rng.uniform(4, 8), h=rng.uniform(4, 12), t=rng.uniform(1, 3))
                         if rng.random() < 0.6 else
                         [(x, y), (x + rng.uniform(2, 8), y), (x + 8, y + 4), (x, y + rng.uniform(2, 6))][:2] +
                         [(x + 8, y + 4), (x, y + 4)])
        text = program(*parts)
        segs = cutting_segments(text, T)
        area = (1.25, 0.5, 22.75, 40.0)
        for room in cut_map(text, SHEET, T, R, CLEAR).rooms(area, 2.0):
            assert area[0] <= room[0] < room[2] <= area[2] and area[1] <= room[1] < room[3] <= area[3]
            for i in range(9):
                for j in range(9):
                    px = room[0] + (room[2] - room[0]) * i / 8
                    py = room[1] + (room[3] - room[1]) * j / 8
                    assert min(seg_distance(px, py, s) for s in segs) >= CLEAR - R - 1e-6, (room, px, py)
                    assert not any(inside(px, py, p) for p in parts), (room, px, py)


def test_rooms_dont_overlap_and_stop_at_most():
    text = program(ell(2, 2), ell(14, 20), [(3, 30), (6, 30), (6, 33), (3, 33)])
    rooms = cut_map(text, SHEET, T, R, CLEAR).rooms((1.25, 0.5, 22.75, 40.0), 2.0, most=4)
    assert len(rooms) == 4
    for i, a in enumerate(rooms):
        for b in rooms[i + 1:]:
            assert a[2] <= b[0] + 1e-9 or b[2] <= a[0] + 1e-9 or a[3] <= b[1] + 1e-9 or b[3] <= a[1] + 1e-9
