"""Straight rows: a sheet's parts laid square across it, one row behind the other (core 0.27.0).

Fusion's Arrange turns parts to any angle, and for a few long parts it can stack them on a slant (the user's p0201,
2026-11-05: three 15 x 2 in bars at 12 degrees took 7.7 in of the sheet's length and left no room beside them). Laid
straight, the same three take 7 in and leave a 5 x 7 in room beside them that the next nest fills.

So after Fusion nests a sheet, the pipeline also lays its parts in straight rows and keeps whichever leaves the
better offcut. The rows are planned here: each part with its long side across the sheet, deepest parts first, each
row filled left to right before the next one starts (first fit). Fusion's Arrange still places each row, in an
area only as deep as the row's deepest part, so it can't tilt them; the pipeline compares the two by `used_up`:
the sheet's length the parts take, times its width, less the room beside them big enough to keep.
"""

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
