"""How many tabs a contour gets: one per `distance` along it, at least `least` and at most `most` (the brief:
2 to 6), but no more than fit. Each tab takes about twice its own width plus a cutter diameter of the contour
(the tab, the cutter going up and over it, and room before the next: four cutter widths for a tab one cutter
wide), so a small cutout gets fewer, and one too small for even one gets none. Fusion spreads them evenly along the
contour ('tabCount' positioning, `tabsPerContour`), so a short contour always gets its tabs, where spacing
them by distance gave a cutout shorter than the distance none.

Lengths come from the extractor's face areas: a wall that goes through the plate is its length times the
thickness, so a loop's length is the sum of its walls' areas over their height.

Where they go (place_tabs, the brief's rules, as the user set them on 2026-10-03): spread evenly around the
contour, each snapped to the best spot near its even position. Best is, in order: on a straight edge with the
whole tab at least one cutter diameter from any corner; on a straight edge nearer a corner (when there isn't
room, or not enough tabs fit otherwise); on one curve long enough for the whole tab; across a smooth join (a
tab wider than a small fillet); and only when nothing else is left, across a corner. So a tab stays on one
edge whenever it can: Fusion's stock simulation crashed on 0.3 in triangular tabs (job tsize1, twice), most
likely where one ramped over a small fillet. Tabs stay at least a tab width plus two cutter diameters apart.
The pattern is turned round the contour to whichever start gives the least snapping, which keeps them
roughly opposite each other. Fusion gets the points (its tab positions).
"""

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from .geometry import CYLINDER, PLANE, PartGeometry

Point = Tuple[float, float]

WALL_TOL = 0.02          # a through wall: vertical to within this (normal or axis dot), the full thickness to 2%


def _through_wall(f, t: float) -> bool:
    if f.z_max_in - f.z_min_in < t * 0.98:
        return False
    if f.kind == PLANE:
        return abs(f.normal_dot) < WALL_TOL
    if f.kind == CYLINDER:
        return f.axis_dot > 1 - WALL_TOL
    return True                                  # a spline or other wall that goes through


def walls_length(geom: PartGeometry, wall_ids: Iterable[int]) -> float:
    total = 0.0
    for fid in wall_ids:
        f = geom.face(fid)
        h = f.z_max_in - f.z_min_in
        if h > 1e-6:
            total += f.area_in2 / h
    return total


def outline_length(geom: PartGeometry, inner_walls: Set[int]) -> float:
    """The outer outline's length: every wall through the plate that isn't in an inner loop."""
    return walls_length(geom, [f.id for f in geom.faces
                               if f.id not in inner_walls and _through_wall(f, geom.thickness_in)])


def tab_count(length_in: float, distance_in: float, tool_diameter_in: float, least: int = 2,
              most: int = 6, tab_width_in: float = 0.0) -> int:
    """tab_width_in: 0 means one cutter diameter (the template's own width)."""
    w = tab_width_in or tool_diameter_in
    fit = int(length_in // (2 * (w + tool_diameter_in))) if tool_diameter_in > 0 else most
    want = max(least, min(most, round(length_in / distance_in))) if distance_in > 0 else least
    return max(0, min(want, fit))



# ---------------------------------------------------------------- where they go

CORNER_DEG = 10.0        # a turn sharper than this where two edges meet is a corner


@dataclass(frozen=True)
class Seg:
    """One edge of a contour, in the loop's order: a straight line (start, end) or a curve as a polyline."""
    kind: str                          # "line" or "curve"
    points: Tuple[Point, ...]


@dataclass(frozen=True)
class TabPoint:
    x: float
    y: float
    on_line: bool                      # on a straight edge (else a curve or across a corner)
    clear_of_corners: bool             # the whole tab at least one cutter diameter from any corner


def _dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _angle(u: Point, v: Point) -> float:
    """Degrees between two directions."""
    nu, nv = math.hypot(*u), math.hypot(*v)
    if nu < 1e-12 or nv < 1e-12:
        return 0.0
    c = max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / (nu * nv)))
    return math.degrees(math.acos(c))


class _Contour:
    """The contour as pieces (consecutive points) with their place along it."""

    def __init__(self, segs: Sequence[Seg]):
        self.pieces: List[Tuple[float, float, Point, Point, int]] = []   # (start s, length, a, b, seg index)
        self.seg_span: List[Tuple[float, float]] = []
        s = 0.0
        for i, seg in enumerate(segs):
            start = s
            for a, b in zip(seg.points, seg.points[1:]):
                n = _dist(a, b)
                if n > 1e-9:
                    self.pieces.append((s, n, a, b, i))
                    s += n
            self.seg_span.append((start, s))
        self.length = s
        self.segs = segs
        self.corners: List[float] = []
        live = [i for i, (a, b) in enumerate(self.seg_span) if b - a > 1e-9]
        for k, i in enumerate(live):
            j = live[(k + 1) % len(live)]
            out = self._direction(i, last=True)
            into = self._direction(j, last=False)
            if out and into and _angle(out, into) > CORNER_DEG:
                self.corners.append(self.seg_span[j][0] % self.length if self.length else 0.0)

    def _direction(self, seg: int, last: bool) -> Optional[Point]:
        mine = [p for p in self.pieces if p[4] == seg]
        if not mine:
            return None
        _, _, a, b, _ = mine[-1] if last else mine[0]
        return (b[0] - a[0], b[1] - a[1])

    def at(self, s: float) -> Tuple[Point, int]:
        s %= self.length
        for start, n, a, b, i in self.pieces:
            if s <= start + n + 1e-12:
                t = 0.0 if n <= 0 else (s - start) / n
                return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), i
        _, _, a, b, i = self.pieces[-1]
        return b, i


def _around(a: float, b: float, length: float) -> float:
    """How far apart two places along a closed contour are, the short way."""
    d = abs(a - b) % length
    return min(d, length - d)


def place_tabs(segs: Sequence[Seg], count: int, tool_diameter: float, tab_width: float,
               samples: int = 600, turns: int = 24) -> List[TabPoint]:
    """`count` tab points on a closed contour (segs in loop order), per the module docstring."""
    c = _Contour(segs)
    if count <= 0 or c.length <= 0 or not c.pieces:
        return []
    L = c.length
    half = tab_width / 2
    clear = tool_diameter + half                   # the tab's edge a cutter diameter from the corner
    step = L / max(samples, 1)
    spots = []                                     # (s, tier)
    for k in range(samples):
        s = k * step
        _, i = c.at(s)
        seg_start, seg_end = c.seg_span[i]
        to_corner = min((_around(s, x, L) for x in c.corners), default=L)
        whole = min(s - seg_start, seg_end - s) >= half - 1e-9         # the whole tab on this one edge
        if c.segs[i].kind == "line" and whole:
            tier = 0 if to_corner >= clear - 1e-9 else 1
        elif whole:
            tier = 2                               # on one curve that's long enough
        elif to_corner >= half - 1e-9:
            tier = 3                               # across a smooth join (a small fillet): Fusion crashed on wide
        else:                                      # triangular tabs somewhere like that (tsize1, twice)
            tier = 4
        spots.append((s, tier))
    spacing = L / count
    weight = (0.0, 0.5, 1.0, 3.0, 4.0)            # times half the spacing: a line spot wins unless it's far off
    gap = min(tab_width + 2 * tool_diameter, spacing * 0.6)
    best = None
    for turn in range(turns):
        offset = spacing * turn / turns
        chosen: List[Tuple[float, int]] = []
        cost = 0.0
        n = len(spots)
        for k in range(count):
            ideal = (offset + k * spacing) % L
            centre = int(round(ideal / step)) % n
            pick = None
            for d in range(n // 2 + 1):            # outwards from the even spot, until nothing closer can win
                if pick is not None and d * step - step > pick[0]:
                    break
                for j in {(centre + d) % n, (centre - d) % n}:
                    s, tier = spots[j]
                    if any(_around(s, t, L) < gap for t, _ in chosen):
                        continue
                    v = _around(s, ideal, L) + weight[tier] * spacing / 2
                    if pick is None or v < pick[0]:
                        pick = (v, s, tier)
            if pick is None:
                break
            chosen.append((pick[1], pick[2]))
            cost += pick[0]
        if len(chosen) == count and (best is None or cost < best[0] - 1e-9):
            best = (cost, chosen)
    if best is None:                               # too short for them to stay apart: evenly, wherever
        best = (0.0, [((k * spacing) % L, 2) for k in range(count)])
    out = []
    for s, tier in sorted(best[1]):
        (x, y), _ = c.at(s)
        out.append(TabPoint(round(x, 4), round(y, 4), tier <= 1, tier == 0))
    return out
