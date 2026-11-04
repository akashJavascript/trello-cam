"""Where a sheet's program really cuts, on a fine grid, and the empty rectangles left among the parts
(2026-11-04, the user's pick: "use each part's real outline instead of its box").

Offcuts keep the empty rooms among the parts cut (offcuts.free_rooms), but that counted each part's bounding box as
used: an L-shaped part's box covers the empty corner inside the L, and a long part's arm covers the room under it
(r068 S1 lost the room behind its front part that way). This reads the posted program instead:

- cutting_segments: every move that goes below the stock top (feed moves, arcs as short chords, drilled holes as
  points), in sheet coordinates. Rapids above the stock cut nothing and don't count.
- CutMap: a grid of `cell` squares over the sheet. A cell is used if any point of it is within `reach` of a cutting
  move's path (the cutter's centre line), or if a toolpath encloses it (the part inside its outline, found as what
  can't be reached from the sheet's edge without crossing a cut). A cell is empty only if every point in it is clear.
- CutMap.rooms: the biggest empty rectangles in an area, like free_rooms (biggest first, never overlapping, each at
  least min_side both ways, at most `most`). Still rectangles: each one is an Arrange envelope in Fusion, as before.

Anything it can't read (a G91 move, an arc given by R) raises CutMapError, and the caller goes back to boxes.
"""

import math
from collections import deque
from typing import List, Optional, Sequence, Tuple

from .tapguard import parse_code

CELL_IN = 0.125
ARC_STEP = math.radians(5)               # chords of an arc: at most 0.1% of its radius inside the arc
SLACK_IN = 0.01                          # covers the chords and rounding
Rect = Tuple[float, float, float, float]
Segment = Tuple[float, float, float, float]

_MOTION = (0.0, 1.0, 2.0, 3.0)
_CYCLES = (73.0, 81.0, 82.0, 83.0)


class CutMapError(ValueError):
    pass


def _arc_points(x0: float, y0: float, x1: float, y1: float, cx: float, cy: float, clockwise: bool) -> List[Tuple[float, float]]:
    r = math.hypot(x0 - cx, y0 - cy)
    a0, a1 = math.atan2(y0 - cy, x0 - cx), math.atan2(y1 - cy, x1 - cx)
    sweep = (a0 - a1) % (2 * math.pi) if clockwise else (a1 - a0) % (2 * math.pi)
    if math.hypot(x1 - x0, y1 - y0) < 1e-6:
        sweep = 2 * math.pi                  # start == end: a full circle
    n = max(1, int(math.ceil(sweep / ARC_STEP)))
    sign = -1.0 if clockwise else 1.0
    pts = [(cx + r * math.cos(a0 + sign * sweep * k / n), cy + r * math.sin(a0 + sign * sweep * k / n))
           for k in range(1, n)]
    return pts + [(x1, y1)]


def cutting_segments(text: str, stock_top_in: float) -> List[Segment]:
    """The XY path of every move below the stock top, as segments (x0, y0, x1, y1); a drilled hole or a plunge is a
    zero-length segment at its point. Work coordinates (the sheet's front-left corner, as loaded)."""
    x = y = z = None
    motion: Optional[float] = None
    cycle: Optional[float] = None
    cycle_z: Optional[float] = None
    out: List[Segment] = []
    for line in text.splitlines():
        try:
            words = parse_code(line)
        except ValueError as e:
            raise CutMapError(f"unreadable line {line!r}: {e}") from None
        if not words:
            continue
        gs = [v for letter, v in words if letter == "G"]
        if 91.0 in gs:
            raise CutMapError("relative moves (G91)")
        if 53.0 in gs:                       # machine coordinates: G53 Z goes to the top, the park leaves the sheet
            if any(letter == "Z" for letter, _ in words):
                z = math.inf
            if any(letter == "P" for letter, _ in words):
                x = y = None
            continue
        if 4.0 in gs:
            continue                         # a dwell (its X is seconds) or a stop
        vals = {letter: v for letter, v in words if letter not in ("G", "M")}
        for g in gs:
            if g in _MOTION:
                motion, cycle = g, None
            elif g in _CYCLES:
                cycle = g
            elif g == 80.0:
                cycle = None
        if "R" in vals and cycle is None and motion in (2.0, 3.0):
            raise CutMapError("an arc given by its radius (R)")
        if not any(k in vals for k in "XYZ"):
            continue
        nx = vals.get("X", x)
        ny = vals.get("Y", y)
        if cycle is not None:                # a drilling cycle: down to its Z at this point, back up to R
            if "Z" in vals:
                cycle_z = vals["Z"]
            if nx is not None and ny is not None and cycle_z is not None and cycle_z < stock_top_in:
                out.append((nx, ny, nx, ny))
            x, y = nx, ny
            z = vals.get("R", z)
            continue
        nz = vals.get("Z", z)
        if nx is None or ny is None:
            x, y, z = nx, ny, nz
            continue
        low = min(v for v in (z, nz) if v is not None) if (z is not None or nz is not None) else None
        cuts = low is not None and low < stock_top_in
        if x is None or y is None:
            if cuts:
                out.append((nx, ny, nx, ny))
        elif motion in (2.0, 3.0):
            cx, cy = x + vals.get("I", 0.0), y + vals.get("J", 0.0)
            if cuts:
                px, py = x, y
                for qx, qy in _arc_points(x, y, nx, ny, cx, cy, motion == 2.0):
                    out.append((px, py, qx, qy))
                    px, py = qx, qy
        elif cuts:
            out.append((x, y, nx, ny))
        x, y, z = nx, ny, nz
    return out


def _distance(px: float, py: float, s: Segment) -> float:
    x0, y0, x1, y1 = s
    dx, dy = x1 - x0, y1 - y0
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 < 1e-12 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / length2))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


class CutMap:
    """sheet: (x0, y0, x1, y1), the grid's extent (the whole sheet, so every outline is closed inside it).
    reach: how far from a cutting move's centre line nothing may go (cutter radius plus clearance)."""

    def __init__(self, segments: Sequence[Segment], sheet: Rect, reach: float, cell: float = CELL_IN):
        self.sheet, self.cell = sheet, cell
        self.cols = max(1, int(math.ceil((sheet[2] - sheet[0]) / cell - 1e-9)))
        self.rows = max(1, int(math.ceil((sheet[3] - sheet[1]) / cell - 1e-9)))
        used = [bytearray(self.cols) for _ in range(self.rows)]
        d = reach + cell * math.sqrt(2) / 2 + SLACK_IN     # from a cell's centre: covers its corners too
        sx, sy = sheet[0], sheet[1]
        for s in set((round(a, 4), round(b, 4), round(c, 4), round(e, 4)) for a, b, c, e in segments):
            c0 = max(0, int((min(s[0], s[2]) - d - sx) / cell))
            c1 = min(self.cols - 1, int((max(s[0], s[2]) + d - sx) / cell))
            r0 = max(0, int((min(s[1], s[3]) - d - sy) / cell))
            r1 = min(self.rows - 1, int((max(s[1], s[3]) + d - sy) / cell))
            for r in range(r0, r1 + 1):
                row, cy = used[r], sy + (r + 0.5) * cell
                for c in range(c0, c1 + 1):
                    if not row[c] and _distance(sx + (c + 0.5) * cell, cy, s) < d:
                        row[c] = 1
        # Inside a closed toolpath (the part itself): whatever can't be reached from the sheet's edge.
        outside = [bytearray(self.cols) for _ in range(self.rows)]
        todo = deque((r, c) for r in range(self.rows) for c in range(self.cols)
                     if (r in (0, self.rows - 1) or c in (0, self.cols - 1)) and not used[r][c])
        for r, c in todo:
            outside[r][c] = 1
        while todo:
            r, c = todo.popleft()
            for rr, cc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                if 0 <= rr < self.rows and 0 <= cc < self.cols and not used[rr][cc] and not outside[rr][cc]:
                    outside[rr][cc] = 1
                    todo.append((rr, cc))
        self.free = outside                  # 1: empty and reachable from the edge

    def is_free(self, x: float, y: float) -> bool:
        c, r = int((x - self.sheet[0]) / self.cell), int((y - self.sheet[1]) / self.cell)
        return 0 <= r < self.rows and 0 <= c < self.cols and bool(self.free[r][c])

    def rooms(self, area: Rect, min_side: float, most: int = 6) -> List[Rect]:
        """The empty rectangles in `area`: biggest first, never overlapping, each at least min_side both ways, at
        most `most`. Cells at the area's edge count clipped to it."""
        ax0, ay0, ax1, ay1 = area
        sx, sy, cell = self.sheet[0], self.sheet[1], self.cell
        c0 = max(0, int(math.floor((ax0 - sx) / cell + 1e-9)))
        c1 = min(self.cols, int(math.ceil((ax1 - sx) / cell - 1e-9)))
        r0 = max(0, int(math.floor((ay0 - sy) / cell + 1e-9)))
        r1 = min(self.rows, int(math.ceil((ay1 - sy) / cell - 1e-9)))
        if c1 <= c0 or r1 <= r0:
            return []
        grid = [bytearray(self.free[r][c0:c1]) for r in range(r0, r1)]

        def inches(ca: int, cb: int, ra: int, rb: int) -> Rect:
            return (max(ax0, sx + (c0 + ca) * cell), max(ay0, sy + (r0 + ra) * cell),
                    min(ax1, sx + (c0 + cb) * cell), min(ay1, sy + (r0 + rb) * cell))

        found: List[Rect] = []
        while len(found) < most:
            best, best_area, best_cells = None, 0.0, None
            heights = [0] * (c1 - c0)
            for r, row in enumerate(grid):
                for c, v in enumerate(row):
                    heights[c] = heights[c] + 1 if v else 0
                stack: List[int] = []
                for c in range(len(heights) + 1):
                    h = heights[c] if c < len(heights) else 0
                    while stack and heights[stack[-1]] >= h:
                        top = stack.pop()
                        height = heights[top]
                        if height:
                            left = stack[-1] + 1 if stack else 0
                            rect = inches(left, c, r - height + 1, r + 1)
                            w, l = rect[2] - rect[0], rect[3] - rect[1]
                            if w >= min_side - 1e-9 and l >= min_side - 1e-9 and w * l > best_area + 1e-9:
                                best, best_area, best_cells = rect, w * l, (left, c, r - height + 1, r + 1)
                    stack.append(c)
            if best is None:
                break
            found.append(tuple(round(v, 3) for v in best))
            ca, cb, ra, rb = best_cells
            for rr in range(ra, rb):
                for cc in range(ca, cb):
                    grid[rr][cc] = 0
        return found


def cut_map(text: str, sheet: Rect, stock_top_in: float, tool_radius_in: float, clear_in: float,
            cell: float = CELL_IN) -> CutMap:
    """The cut map of a posted program. clear_in: how far an empty room stays from a part's edge (the part
    spacing plus the loading slack, as for boxes); the cutter's path runs tool_radius_in outside that edge, so the
    reach from the path's centre line is clear_in - tool_radius_in (never less than the cutter's own radius)."""
    segments = cutting_segments(text, stock_top_in)
    if not segments:
        raise CutMapError("the program cuts nothing")
    return CutMap(segments, sheet, max(clear_in - tool_radius_in, tool_radius_in), cell)
