"""Round holes: find them in split STEP faces and decide how each one is cut (decision 7).

Precedence for a full round through-hole of diameter d, with the sheet's tool:
1. `drill`   d matches one of the tool's drill sizes (exact list, +/- drill_tol). Straight plunge.
2. `bearing` d matches a bearing size (+/- bearing_tol). Wins over bore: those sizes are in the bore range.
3. `bore`    bore_min - bore_min_tol <= d <= bore_max (inclusive). Helical bore to the modeled diameter.
4. `inner`   d > bore_max: contoured as a cutout.
5. `contour_warn` anything else the tool can still contour (between the drill sizes and 5 mm):
             contoured, with a warning so someone checks the fit.
6. `too_small` the tool can't make it; the part needs the smaller tool (decision 13).
"""

import math
from dataclasses import dataclass
from typing import Iterable, List, Tuple

from .geometry import CYLINDER, Face

DRILL = "drill"
BEARING = "bearing"
BORE = "bore"
INNER = "inner"
CONTOUR_WARN = "contour_warn"
TOO_SMALL = "too_small"

# Which template op cuts each kind of hole.
OP_TAG = {DRILL: "drill", BEARING: "bearing", BORE: "bore", INNER: "inner", CONTOUR_WARN: "inner"}

CONTOUR_ROOM_IN = 0.001   # radial room a contour needs beyond the tool radius
GROUP_TOL_IN = 0.0005     # split faces of one hole share an axis and radius within this
FULL_CIRCLE_DEG = 359.0
_VERTICAL = 1 - 1e-4


@dataclass(frozen=True)
class HoleRules:
    drill_tol_in: float
    bore_min_in: float
    bore_min_tol_in: float
    bore_max_in: float
    bearing_sizes_in: Tuple[float, ...]
    bearing_tol_in: float


@dataclass(frozen=True)
class ToolProfile:
    """The bits of a tool that decide what it can cut. Tools are still identified by GUID elsewhere."""
    key: str
    diameter_in: float
    min_inside_radius_in: float
    drill_sizes_in: Tuple[float, ...]
    label: str = ""


@dataclass(frozen=True)
class Hole:
    diameter_in: float
    center_in: Tuple[float, float]
    face_ids: Tuple[int, ...]
    z_min_in: float
    z_max_in: float
    sweep_deg: float

    @property
    def full(self) -> bool:
        return self.sweep_deg >= FULL_CIRCLE_DEG


def classify(diameter_in: float, tool: ToolProfile, rules: HoleRules) -> str:
    if any(abs(diameter_in - s) <= rules.drill_tol_in + 1e-9 for s in tool.drill_sizes_in):
        return DRILL
    if any(abs(diameter_in - b) <= rules.bearing_tol_in + 1e-9 for b in rules.bearing_sizes_in):
        return BEARING
    if rules.bore_min_in - rules.bore_min_tol_in - 1e-9 <= diameter_in <= rules.bore_max_in + 1e-6:
        return BORE
    if diameter_in > rules.bore_max_in:
        return INNER
    if diameter_in / 2 >= tool.diameter_in / 2 + CONTOUR_ROOM_IN:
        return CONTOUR_WARN
    return TOO_SMALL


def group_cylinders(faces: Iterable[Face]) -> List[Hole]:
    """Concave vertical cylinders grouped by shared axis and radius (one hole = one group)."""
    groups: List[List[Face]] = []
    for f in faces:
        if f.kind != CYLINDER or not f.concave or f.axis_dot < _VERTICAL:
            continue
        for g in groups:
            ref = g[0]
            if (abs(ref.radius_in - f.radius_in) <= GROUP_TOL_IN
                    and math.dist(ref.center_in, f.center_in) <= GROUP_TOL_IN):
                g.append(f)
                break
        else:
            groups.append([f])
    holes = []
    for g in groups:
        holes.append(Hole(
            diameter_in=2 * sum(f.radius_in for f in g) / len(g),
            center_in=g[0].center_in,
            face_ids=tuple(f.id for f in g),
            z_min_in=min(f.z_min_in for f in g),
            z_max_in=max(f.z_max_in for f in g),
            sweep_deg=sum(f.sweep_deg for f in g),
        ))
    return holes
