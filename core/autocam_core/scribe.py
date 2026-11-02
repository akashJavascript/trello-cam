"""Scribing a bent part's bend lines (core 0.18.0): a shallow line down the middle of each bend, on the part's top,
to line it up in the brake. Two ways, chosen per part (PartSpec.scribe):

- "endmill": the sheet's own cutter, an op of the sheet's program ([scribe], before the outlines): no tool change,
  a line as wide as the cutter, ScribeSpec.depth_in deep;
- "chamfer": the 45 degree chamfer mill, in a SCRIBE program of the sheet's own, run after its FACE program and
  before its main one: a fine line, twice ScribeSpec.chamfer_depth_in wide.

Either way it goes that deep below the part's top: a faced part's top is the face depth lower.

Beside each line, a U or D says which way it bends: U, the flange comes up toward the scribed face (that face ends
up inside the bend); D, away from it. Onshape's bend table says "up" or "down" from the flat pattern's top, its STEP's
+Z side (checked on Part 13, 2026-11-01: its top is outside its "down" Bend D: the holes off-center across the bend
mirror between the flat seen from +Z and the bent part seen from inside the bend, on both flanges). The scribed face
is the one up on the machine; geometry.PartGeometry.axis_z says whether that's the flat's top. A letter goes where its
box fits on the part clear of holes, edges and every bend; none fits, or two bends alike go opposite ways (which strip
is which can't be told): no letter, and the sheet card's bend list still says.

Where the bends are: Onshape splits a flat pattern's top along each bend into the bend zone (a strip between the
two lines where the bend starts and ends) and the flats either side (read off a real flat, 2026-11-01: Part 13's
one 90 degree bend, R 0.05 in on 1/8, is a strip 0.1669 in wide, k = 0.45). A bend zone is a top face with exactly
two edges shared with the part's other top faces, both straight and parallel, as far apart as one of the part's
bends can be: its angle x (radius + k x thickness), k from 0 to 1. Its centerline runs midway between them. A
part where that doesn't find exactly one strip per bend isn't scribed (its bends are marked by hand).
"""

import json
import math
import re
from collections import Counter
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from .tapguard import parse_code

SCRIBE_WAYS = ("endmill", "chamfer")
Edge = Tuple[str, Tuple[float, float], Tuple[float, float], bool]   # (edge key, start, end, straight), inches
Line = Tuple[float, float, float, float]                             # x0, y0, x1, y1, inches
Point = Tuple[float, float]
Path = Tuple[Point, ...]                                             # a polyline the cutter follows, one chain
Strip = Tuple[Line, float]                                           # a bend's centerline and its zone's width

LENGTH_TOL_IN = 1e-4
PARALLEL_TOL = 1e-3          # sin of the angle between the strip's two edges
WIDTH_TOL_IN = 0.002
ON_LINE_TOL_IN = 0.002       # a scribing point must be this close to a bend line (the post rounds to 4 decimals)

_SCRIBE_LINE = re.compile(r"^\[scribe( [^\[\]]*)?\]$")
_COMMENT_ONLY = re.compile(r"^\[[^\[\]]*\]$")


def _sub(a, b):
    return a[0] - b[0], a[1] - b[1]


def _cross(a, b) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1]


def zone_widths(bends: Sequence[Tuple[float, float]], thickness_in: float) -> List[Tuple[float, float]]:
    """(narrowest, widest) a bend zone can be for each (angle in degrees, inside radius in inches)."""
    out = []
    for angle, radius in bends:
        theta = math.radians(abs(angle))
        out.append((theta * max(radius, 0.0) - WIDTH_TOL_IN, theta * (max(radius, 0.0) + thickness_in) + WIDTH_TOL_IN))
    return out


def bend_centerlines(faces: Mapping[Any, Sequence[Edge]], bends: Sequence[Tuple[float, float]],
                     thickness_in: float) -> Tuple[Tuple[Line, ...], str]:
    """faces: each top face's outer loop as edges. bends: (angle in degrees, inside radius in inches) from the bend
    table. Returns (one centerline per bend zone, "") or ((), why the part can't be scribed)."""
    strips, why = bend_strips(faces, bends, thickness_in)
    return tuple(line for line, _ in strips), why


def bend_strips(faces: Mapping[Any, Sequence[Edge]], bends: Sequence[Tuple[float, float]],
                thickness_in: float) -> Tuple[Tuple[Strip, ...], str]:
    """As bend_centerlines, each with its zone's width."""
    if not bends:
        return (), "it has no bends"
    if any(abs(a) < 1e-6 for a, _ in bends):
        return (), "its bend table couldn't be read"
    if len(faces) < 2:
        return (), "its top isn't split along its bends"
    widths = zone_widths(bends, thickness_in)
    seen = Counter(key for edges in faces.values() for key in {e[0] for e in edges})
    lines: List[Strip] = []
    for edges in faces.values():
        shared = list({e[0]: e for e in edges if seen[e[0]] > 1}.values())
        if len(shared) != 2 or not all(e[3] for e in shared):
            continue
        (_, a0, a1, _), (_, b0, b1, _) = shared
        da, db = _sub(a1, a0), _sub(b1, b0)
        la, lb = math.hypot(*da), math.hypot(*db)
        if la < LENGTH_TOL_IN or lb < LENGTH_TOL_IN or abs(_cross(da, db)) / (la * lb) > PARALLEL_TOL:
            continue
        mid_b = ((b0[0] + b1[0]) / 2, (b0[1] + b1[1]) / 2)
        width = abs(_cross(da, _sub(mid_b, a0))) / la
        if not any(lo <= width <= hi for lo, hi in widths):
            continue
        if _dot(da, db) < 0:
            b0, b1 = b1, b0
        lines.append((((a0[0] + b0[0]) / 2, (a0[1] + b0[1]) / 2, (a1[0] + b1[0]) / 2, (a1[1] + b1[1]) / 2), width))
    if len(lines) != len(bends):
        return (), (f"{len(lines)} bend zone{'s' if len(lines) != 1 else ''} found on its top for its "
                    f"{len(bends)} bend{'s' if len(bends) != 1 else ''}")
    return tuple(sorted(lines)), ""


def tip_radius_in(tool_json: str) -> float:
    """A chamfer mill's tip radius (inches) from its library entry: what Fusion offsets a 2D contour by with it, the
    cone's tip being at the bottom height (r046: minus its full radius as stock was "Failed to write Job File")."""
    try:
        data = json.loads(tool_json)
        tip = float((data.get("geometry") or {}).get("tip-diameter") or 0.0) / 2
    except (ValueError, TypeError, AttributeError):
        return 0.0
    return tip / 25.4 if data.get("unit") == "millimeters" else tip


def distance_to_line(x: float, y: float, line: Line) -> float:
    x0, y0, x1, y1 = line
    d = (x1 - x0, y1 - y0)
    length2 = _dot(d, d)
    t = 0.0 if length2 < 1e-12 else max(0.0, min(1.0, _dot((x - x0, y - y0), d) / length2))
    return math.hypot(x - (x0 + t * d[0]), y - (y0 + t * d[1]))


def off_the_lines(points: Sequence[Tuple[int, float, float, float]], lines: Sequence[Line],
                  tol_in: float = ON_LINE_TOL_IN) -> List[Tuple[int, float, float]]:
    """The scribing points (line, x, y, z) that aren't on any of the bend lines (same coordinates)."""
    return [(n, x, y) for n, x, y, _ in points if not any(distance_to_line(x, y, ln) <= tol_in for ln in lines)]


def scribe_sections(text: str) -> List[Tuple[int, int]]:
    """Each [scribe ...] op in a posted program: (its comment line, its last line), 1-based. An op runs from its
    comment line to the next comment-only line or the end (as sheetcheck.outer_depth_problems reads [outer ...])."""
    out: List[Tuple[int, int]] = []
    start: Optional[int] = None
    lines = [l.strip() for l in text.splitlines()]
    for n, line in enumerate(lines, 1):
        if _COMMENT_ONLY.match(line):
            if start is not None:
                out.append((start, n - 1))
                start = None
            if _SCRIBE_LINE.match(line):
                start = n
    if start is not None:
        out.append((start, len(lines)))
    return out


def in_sections(line: int, sections: Sequence[Tuple[int, int]]) -> bool:
    return any(a <= line <= b for a, b in sections)


def lowest_z(text: str, section: Tuple[int, int]) -> Optional[float]:
    lowest: Optional[float] = None
    lines = text.splitlines()
    for code in lines[section[0]:section[1]]:
        try:
            words = parse_code(code.strip())
        except ValueError:
            continue                    # the guard rejects unreadable lines
        if ("G", 53.0) in words:
            continue
        for letter, value in words:
            if letter == "Z" and value is not None:
                lowest = value if lowest is None else min(lowest, value)
    return lowest


# ------------------------------------------------------------------ which way: U or D beside the line

# Each letter in a box 1 wide and 1 tall, (0, 0) its bottom left, as one polyline (one chain for the cutter).
STROKES = {
    "U": ((0.0, 1.0), (0.0, 0.3), (0.15, 0.05), (0.35, 0.0), (0.65, 0.0), (0.85, 0.05), (1.0, 0.3), (1.0, 1.0)),
    "D": ((0.0, 0.0), (0.0, 1.0), (0.5, 1.0), (0.85, 0.85), (1.0, 0.6), (1.0, 0.4), (0.85, 0.15), (0.5, 0.0),
          (0.0, 0.0)),
}
MARK_SPOTS = (0.5, 0.3, 0.7, 0.15, 0.85)        # along the line, from its start
MARK_GAP_IN = 0.06                                # between the bend zone and the letter's box, past the clearance


def bend_letter(width: float, bends: Sequence[Tuple[float, float, str]], thickness_in: float,
                scribed_is_top: Optional[bool]) -> Optional[str]:
    """U or D for the strip this wide: from the bends (angle, radius, "up"/"down") it can be. None when the scribed
    side isn't known, no bend fits, or the ones that fit go opposite ways."""
    if scribed_is_top is None:
        return None
    ways = {d for (lo, hi), (_, _, d) in zip(zone_widths([(a, r) for a, r, _ in bends], thickness_in), bends)
            if lo <= width <= hi}
    if len(ways) != 1 or not ways <= {"up", "down"}:
        return None
    return "U" if (ways == {"up"}) == scribed_is_top else "D"


def letter_path(ch: str, center: Point, up: Point, w: float, h: float) -> Path:
    """The letter in a w x h box at center, its top toward `up` (a unit vector), read from the top of the part."""
    right = (up[1], -up[0])
    return tuple((center[0] + (u - 0.5) * w * right[0] + (v - 0.5) * h * up[0],
                  center[1] + (u - 0.5) * w * right[1] + (v - 0.5) * h * up[1]) for u, v in STROKES[ch])


STROKE_GAP_IN = 0.002      # each stroke stops this short of the next: lines of their own, never one chain of
                           # connected sketch lines (r048: that came back "missing selections" and broke the next op)


def letter_strokes(ch: str, center: Point, up: Point, w: float, h: float) -> List[Path]:
    """The letter (letter_path) as separate straight strokes, each a path of its own, not quite touching."""
    pts = letter_path(ch, center, up, w, h)
    out = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        if length <= 4 * STROKE_GAP_IN:
            continue
        g = STROKE_GAP_IN / length
        out.append(((x0 + g * (x1 - x0), y0 + g * (y1 - y0)), (x1 - g * (x1 - x0), y1 - g * (y1 - y0))))
    return out


def letter_box(center: Point, up: Point, w: float, h: float) -> List[Point]:
    """The w x h box at center, its top toward `up`, as a polygon."""
    right = (up[1], -up[0])
    return [(center[0] + a * w / 2 * right[0] + b * h / 2 * up[0], center[1] + a * w / 2 * right[1] + b * h / 2 * up[1])
            for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


def _inside(x: float, y: float, poly: Sequence[Point]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def mark_spot(line: Line, half_width: float, w: float, h: float, clear: float, outline: Sequence[Point],
              holes: Sequence[Sequence[Point]], strips: Sequence[Strip],
              taken: Sequence[Sequence[Point]] = ()) -> Optional[Tuple[Point, Point]]:
    """Where a w x h letter goes beside a bend line: (its center, its up direction), its box (grown by `clear`, the
    cutter's radius and a margin) on the part (inside its outline, clear of every hole, every bend zone and the letters
    already placed), at the first of MARK_SPOTS along the line that fits, either side. None if nowhere fits."""
    x0, y0, x1, y1 = line
    length = math.hypot(x1 - x0, y1 - y0)
    if length < LENGTH_TOL_IN:
        return None
    d = ((x1 - x0) / length, (y1 - y0) / length)
    for t in MARK_SPOTS:
        px, py = x0 + t * (x1 - x0), y0 + t * (y1 - y0)
        for side in (1.0, -1.0):
            up = (-d[1] * side, d[0] * side)
            off = half_width + clear + MARK_GAP_IN + h / 2
            center = (px + up[0] * off, py + up[1] * off)
            right = (up[1], -up[0])
            hw, hh = w / 2 + clear, h / 2 + clear
            box = [(center[0] + a * hw * right[0] + b * hh * up[0], center[1] + a * hw * right[1] + b * hh * up[1])
                   for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            grid = [(center[0] + a * hw * right[0] + b * hh * up[0], center[1] + a * hw * right[1] + b * hh * up[1])
                    for a in (-1, -0.5, 0, 0.5, 1) for b in (-1, -0.5, 0, 0.5, 1)]
            if not all(_inside(x, y, outline) for x, y in grid):
                continue
            if any(_inside(x, y, poly) for poly in list(holes) + list(taken) for x, y in grid):
                continue
            if any(_inside(x, y, box) for poly in list(holes) + [outline] + list(taken) for x, y in poly):
                continue
            if any(distance_to_line(x, y, ln) < wd / 2 for ln, wd in strips for x, y in grid):
                continue
            return center, up
    return None


def path_segments(paths: Sequence[Path]) -> List[Line]:
    return [(a[0], a[1], b[0], b[1]) for p in paths for a, b in zip(p, p[1:])]
