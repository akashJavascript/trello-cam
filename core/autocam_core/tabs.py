"""How many tabs a contour gets: one per `distance` along it, at least `least` and at most `most` (the brief:
2 to 6), but no more than fit. Each tab takes about four cutter widths of the contour (its own width, the
template's `tool_diameter`, the cutter going up and over it, and room before the next), so a small cutout gets
fewer, and one too small for even one gets none (its slug is little more than chips). Fusion spreads them evenly along the
contour ('tabCount' positioning, `tabsPerContour`), so a short contour always gets its tabs, where spacing
them by distance gave a cutout shorter than the distance none.

Lengths come from the extractor's face areas: a wall that goes through the plate is its length times the
thickness, so a loop's length is the sum of its walls' areas over their height.
"""

from typing import Iterable, Set

from .geometry import CYLINDER, PLANE, PartGeometry

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
              most: int = 6) -> int:
    fit = int(length_in // (4 * tool_diameter_in)) if tool_diameter_in > 0 else most
    want = max(least, min(most, round(length_in / distance_in))) if distance_in > 0 else least
    return max(0, min(want, fit))

