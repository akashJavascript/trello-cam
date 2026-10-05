"""The sheet preview: how it's framed, where a point in the design lands in the picture, and where a part's label
goes (on its material, as far from any edge as possible).

The framing is the one Fusion's camera is given (fx_design.preview): a top view centred on the sheet, its
viewExtents being what shows across the picture's shorter side (found on r014/r015), set so the sheet fits both
ways with 8% to spare. The labels are drawn on that picture by the service (labels.py), from the pixel positions
worked out here.
"""

import math
from typing import Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]
Rect = Tuple[float, float, float, float]

MARGIN = 1.08


def preview_size(rect: Rect) -> Tuple[int, int]:
    """Picture size: upright for an upright sheet."""
    x0, y0, x1, y1 = rect
    return (1000, 1600) if (y1 - y0) > (x1 - x0) else (1600, 900)


def preview_view(rect: Rect, size: Tuple[int, int]) -> Tuple[float, float, float]:
    """(centre x, centre y, viewExtents): viewExtents in the rect's units, across the picture's shorter side."""
    x0, y0, x1, y1 = rect
    w, h = size
    short = min(w, h)
    return (x0 + x1) / 2, (y0 + y1) / 2, max((x1 - x0) * short / w, (y1 - y0) * short / h) * MARGIN


def to_pixels(x: float, y: float, rect: Rect, size: Tuple[int, int]) -> Point:
    """Where design point (x, y) lands in the picture (pixels from its top-left corner)."""
    cx, cy, extent = preview_view(rect, size)
    scale = pixels_per_unit(rect, size)
    w, h = size
    return w / 2 + (x - cx) * scale, h / 2 - (y - cy) * scale


def pixels_per_unit(rect: Rect, size: Tuple[int, int]) -> float:
    return min(size) / preview_view(rect, size)[2]


def flatten(segs) -> List[Point]:
    """A loop's Segs (tabs.Seg) as one closed polyline."""
    pts: List[Point] = []
    for seg in segs:
        for p in seg.points:
            if not pts or (abs(p[0] - pts[-1][0]) > 1e-9 or abs(p[1] - pts[-1][1]) > 1e-9):
                pts.append((float(p[0]), float(p[1])))
    return pts


def _inside(x: float, y: float, poly: Sequence[Point]) -> bool:
    n, hit = len(poly), False
    for i in range(n):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            hit = not hit
    return hit


def _edges(polys: Iterable[Sequence[Point]]) -> List[Tuple[float, float, float, float]]:
    out = []
    for poly in polys:
        n = len(poly)
        for i in range(n):
            (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
            out.append((ax, ay, bx, by))
    return out


def _clearance(x: float, y: float, edges, best: float) -> float:
    """Distance to the nearest edge (stops early once it's below `best`)."""
    d2 = math.inf
    floor = best * best
    for ax, ay, bx, by in edges:
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        t = 0.0 if L <= 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L))
        ex, ey = ax + t * dx - x, ay + t * dy - y
        e = ex * ex + ey * ey
        if e < d2:
            d2 = e
            if d2 <= floor:
                break
    return math.sqrt(d2)


def label_spot(outer: Sequence[Point], holes: Sequence[Sequence[Point]] = (), grid: int = 24) -> Tuple[float, float, float]:
    """(x, y, clearance): the point inside the outline and outside every cutout that's furthest from any edge,
    and that distance. A grid over the part's box, then a finer one round the best point. A part that's all
    edge (or a bad outline) gets its box centre and 0."""
    if len(outer) < 3:
        xs = [p[0] for p in outer] or [0.0]
        ys = [p[1] for p in outer] or [0.0]
        return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, 0.0
    x0, x1 = min(p[0] for p in outer), max(p[0] for p in outer)
    y0, y1 = min(p[1] for p in outer), max(p[1] for p in outer)
    edges = _edges([outer] + [h for h in holes if len(h) >= 3])
    solid = [h for h in holes if len(h) >= 3]

    def material(x: float, y: float) -> bool:
        return _inside(x, y, outer) and not any(_inside(x, y, h) for h in solid)

    best: Optional[Tuple[float, float, float]] = None
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    tie = max(x1 - x0, y1 - y0) * 1e-3              # as good as: then the one nearer the middle wins

    def look(xa: float, ya: float, xb: float, yb: float, n: int) -> None:
        nonlocal best
        for i in range(n):
            for j in range(n):
                x = xa + (xb - xa) * (i + 0.5) / n
                y = ya + (yb - ya) * (j + 0.5) / n
                if not material(x, y):
                    continue
                d = _clearance(x, y, edges, best[2] - tie if best else 0.0)
                if best is None or d > best[2] + tie or (
                        d > best[2] - tie and math.hypot(x - mx, y - my) < math.hypot(best[0] - mx, best[1] - my)):
                    best = (x, y, d)

    look(x0, y0, x1, y1, grid)
    if best is None:
        return (x0 + x1) / 2, (y0 + y1) / 2, 0.0
    sx, sy = (x1 - x0) / grid, (y1 - y0) / grid
    bx, by, _ = best
    look(bx - sx, by - sy, bx + sx, by + sy, 8)
    return best
