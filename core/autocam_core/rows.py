"""Straight rows: a sheet's parts laid square across it, one row behind the other (core 0.27.0).

Fusion's Arrange turns parts to any angle, and for a few long parts it can stack them on a slant (the user's p0201,
2026-11-05: three 15 x 2 in bars at 12 degrees took 7.7 in of the sheet's length and left no room beside them). Laid
straight, the same three take 7 in and leave a 5 x 7 in room beside them that the next nest fills.

So after Fusion nests a sheet, the pipeline also lays its parts in straight rows and keeps whichever leaves the
better offcut. The rows are planned here: each part with its long side across the sheet, deepest parts first, each
row filled left to right before the next one starts (first fit). A part's sides are its smallest rectangle at any
angle (`min_rect`, from its outline as it lies after Fusion's Arrange), not its box in CAD. Fusion's Arrange still places each row, in an
area only as deep as the row's deepest part, so it can't tilt them; the pipeline compares the two by `used_up`:
the sheet's length the parts take, times its width, less the room beside them big enough to keep.
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .offcuts import Rect, free_rooms

Item = Tuple[str, float, float]       # (id, long side, short side), inches


@dataclass(frozen=True)
class Row:
    depth: float                      # along the sheet's length: its deepest part's short side
    items: Tuple[Item, ...]           # left to right

    @property
    def across(self) -> float:        # the long sides only (no spacing)
        return sum(i[1] for i in self.items)


def min_rect(points: Sequence[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    """(long side, short side) of the smallest rectangle around the points, at any angle: one of its sides lies
    along an edge of their convex hull (rotating calipers). None for fewer than 3 points or none with any area. A part
    drawn at an angle in CAD has a box much bigger than this (p0201 at 11 deg: 4.7 in deep for a 1.6 in bar)."""
    hull = _hull(points)
    if len(hull) < 3:
        return None
    best: Optional[Tuple[float, float, float]] = None          # (area, long, short)
    for (ax, ay), (bx, by) in zip(hull, hull[1:] + hull[:1]):
        dx, dy = bx - ax, by - ay
        n = math.hypot(dx, dy)
        if n < 1e-12:
            continue
        ux, uy = dx / n, dy / n
        us = [x * ux + y * uy for x, y in hull]
        vs = [-x * uy + y * ux for x, y in hull]
        w, h = max(us) - min(us), max(vs) - min(vs)
        if best is None or w * h < best[0] - 1e-12:
            best = (w * h, max(w, h), min(w, h))
    if best is None or best[2] <= 1e-9:
        return None
    return round(best[1], 4), round(best[2], 4)


def _hull(points: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Convex hull, counterclockwise (Andrew's monotone chain)."""
    pts = sorted(set((round(x, 6), round(y, 6)) for x, y in points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower: List[Tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: List[Tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def straight_rows(items: Sequence[Item], width: float, spacing: float,
                  rank: Optional[Mapping[str, int]] = None) -> Optional[List[Row]]:
    """The rows, front first; None if a part's long side doesn't fit across `width`. rank: id -> 0, 1, ...: lower
    ranks are placed first (robot parts before spares); within a rank, deepest first, then longest, then as given."""
    if any(i[1] > width + 1e-9 or i[1] <= 0 or i[2] <= 0 for i in items):
        return None
    rank = rank or {}
    order = sorted(enumerate(items), key=lambda e: (rank.get(e[1][0], 0), -e[1][2], -e[1][1], e[0]))
    rows: List[List[Item]] = []
    for _, item in order:
        for row in rows:
            if sum(i[1] for i in row) + spacing * len(row) + item[1] <= width + 1e-9:
                row.append(item)
                break
        else:
            rows.append([item])
    return [Row(max(i[2] for i in r), tuple(r)) for r in rows]


def rows_depth(rows: Sequence[Row], spacing: float) -> float:
    """How far the rows reach along the length, with `spacing` between them."""
    return sum(r.depth for r in rows) + spacing * max(0, len(rows) - 1)


def row_boxes(rows: Sequence[Row], x0: float, y0: float, spacing: float) -> Dict[str, Rect]:
    """Where each part would be, the rows starting at (x0, y0): left to right, against each row's front."""
    out: Dict[str, Rect] = {}
    y = y0
    for r in rows:
        x = x0
        for cid, across, along in r.items:
            out[cid] = (x, y, x + across, y + along)
            x += across + spacing
        y += r.depth + spacing
    return out


@dataclass(frozen=True)
class Use:
    length: float                     # along the sheet's length, from the area's front to the parts' back plus margin
    room: float                       # the room beside the parts big enough to keep, in2
    used: float                       # length x width, less that room, in2 (smaller leaves more)


def used_up(boxes: Sequence[Rect], x0: float, x1: float, y0: float, margin: float, clear: float,
            min_side: float) -> Use:
    """What a layout uses of a band across the sheet (x0 to x1) from its front y0: up to the parts' back edge plus
    margin (the cutter's path), less every empty rectangle among the parts at least `clear` from them and
    min_side both ways (offcuts.free_rooms: the rooms the offcut keeps)."""
    if not boxes:
        return Use(0.0, 0.0, 0.0)
    top = max(b[3] for b in boxes) + margin
    rooms = free_rooms(boxes, (x0, y0, x1, top), clear, min_side)
    room = sum((r[2] - r[0]) * (r[3] - r[1]) for r in rooms)
    length = top - y0
    return Use(round(length, 3), round(room, 2), round(length * (x1 - x0) - room, 2))
