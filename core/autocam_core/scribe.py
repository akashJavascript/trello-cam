"""Scribing a bent part's bend lines (core 0.18.0): a shallow line down the middle of each bend, on the part's top,
to line it up in the brake. Two ways, chosen per part (PartSpec.scribe):

- "endmill": the sheet's own cutter, an op of the sheet's program ([scribe], before the outlines): no tool change,
  a line as wide as the cutter, ScribeSpec.depth_in deep;
- "chamfer": the 45 degree chamfer mill, in a SCRIBE program of the sheet's own, run after its FACE program and
  before its main one: a fine line, twice ScribeSpec.chamfer_depth_in wide.

Either way it goes that deep below the part's top: a faced part's top is the face depth lower.

Where the bends are: Onshape splits a flat pattern's top along each bend into the bend zone (a strip between the
two lines where the bend starts and ends) and the flats either side (read off a real flat, 2026-11-01: Part 13's
one 90 degree bend, R 0.05 in on 1/8, is a strip 0.1669 in wide, k = 0.45). A bend zone is a top face with exactly
two edges shared with the part's other top faces, both straight and parallel, as far apart as one of the part's
bends can be: its angle x (radius + k x thickness), k from 0 to 1. Its centerline runs midway between them. A
part where that doesn't find exactly one strip per bend isn't scribed (its bends are marked by hand).
"""

import math
import re
from collections import Counter
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from .tapguard import parse_code

SCRIBE_WAYS = ("endmill", "chamfer")
Edge = Tuple[str, Tuple[float, float], Tuple[float, float], bool]   # (edge key, start, end, straight), inches
Line = Tuple[float, float, float, float]                             # x0, y0, x1, y1, inches

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
    if not bends:
        return (), "it has no bends"
    if any(abs(a) < 1e-6 for a, _ in bends):
        return (), "its bend table couldn't be read"
    if len(faces) < 2:
        return (), "its top isn't split along its bends"
    widths = zone_widths(bends, thickness_in)
    seen = Counter(key for edges in faces.values() for key in {e[0] for e in edges})
    lines: List[Line] = []
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
        lines.append(((a0[0] + b0[0]) / 2, (a0[1] + b0[1]) / 2, (a1[0] + b1[0]) / 2, (a1[1] + b1[1]) / 2))
    if len(lines) != len(bends):
        return (), (f"{len(lines)} bend zone{'s' if len(lines) != 1 else ''} found on its top for its "
                    f"{len(bends)} bend{'s' if len(bends) != 1 else ''}")
    return tuple(sorted(lines)), ""


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
