"""Where the tool center goes below the stock top, read back from a posted program (inches, sheet coordinates).

The Fusion worker tests these points against the part bodies: the tool center must never be inside a part.
That catches a cutout or an outline cut on the wrong side of its line (seen 2026-10-01: round cutouts a tool
width too big) whatever the cause, using the program that would actually run.

Points: the end and the middle of every feed move (G1/G2/G3) that dips below `below_z`, arcs by their swept
midpoint, and every drilling-cycle point. The tapguard has already accepted the program; unreadable lines are
skipped here.
"""

import math
from typing import List, Optional, Tuple

from .tapguard import parse_code

Point = Tuple[int, float, float, float]   # (line number, x, y, z)


def _arc_mid(x0, y0, x1, y1, cx, cy, clockwise: bool) -> Tuple[float, float]:
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    if math.hypot(x1 - x0, y1 - y0) < 1e-6:
        sweep = 2 * math.pi
    else:
        sweep = (a1 - a0) % (2 * math.pi)
    if clockwise:
        sweep = -((a0 - a1) % (2 * math.pi) or 2 * math.pi)
    r = math.hypot(x0 - cx, y0 - cy)
    mid = a0 + sweep / 2
    return cx + r * math.cos(mid), cy + r * math.sin(mid)


def cutting_points(text: str, below_z: float) -> List[Point]:
    x: Optional[float] = None
    y: Optional[float] = None
    z = math.inf                       # after G53 Z: the machine top
    motion: Optional[int] = None
    cycle_z: Optional[float] = None
    in_cycle = False
    out: List[Point] = []
    for n, raw in enumerate(text.splitlines(), 1):
        try:
            words = parse_code(raw)
        except ValueError:
            continue
        if not words:
            continue
        gs = [int(v) for l, v in words if l == "G" and v is not None and float(v).is_integer()]
        vals = {l: v for l, v in words if l not in ("G", "M")}
        if 53 in gs:
            if ("Z", None) in words:
                z = math.inf
            else:
                x = y = None           # parked
            continue
        if 4 in gs:
            continue
        if 80 in gs:
            in_cycle = False
        for g in gs:
            if g in (0, 1, 2, 3):
                motion, in_cycle = g, False
            elif g in (73, 81, 82, 83):
                in_cycle = True
        if in_cycle and vals.get("Z") is not None:
            cycle_z = vals["Z"]
        if not any(k in vals for k in ("X", "Y", "Z")):
            continue
        nx = vals["X"] if vals.get("X") is not None else x
        ny = vals["Y"] if vals.get("Y") is not None else y
        if in_cycle and not any(g in (0, 1, 2, 3) for g in gs):
            if nx is not None and ny is not None and cycle_z is not None and cycle_z < below_z:
                out.append((n, nx, ny, cycle_z))
            x, y = nx, ny
            continue
        nz = vals["Z"] if vals.get("Z") is not None else z
        if motion in (1, 2, 3) and None not in (x, y, nx, ny) and min(z, nz) < below_z:
            out.append((n, nx, ny, nz))
            if motion == 1:
                mx, my = (x + nx) / 2, (y + ny) / 2
            elif "I" in vals and "J" in vals:
                mx, my = _arc_mid(x, y, nx, ny, x + vals["I"], y + vals["J"], clockwise=motion == 2)
            else:
                mx, my = nx, ny
            out.append((n, mx, my, nz if math.isinf(z) else (z + nz) / 2))
        x, y, z = nx, ny, nz
    return out
