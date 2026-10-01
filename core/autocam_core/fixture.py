"""The standard sheet fixture (decisions 5, 15, 20).

Sheet coordinates = work coordinates in the posted program: origin at the bottom front-left
corner of the sheet (the corner facing the operator, at the bed end), X along the sheet's 48 in
length, Y across its 24 in width, Z0 = stock bottom. The far end of the sheet overhangs the bed.

- The stock is the full sheet, so simulation and the preview match reality.
- Parts are nested only in the reachable area, minus edge margins and clamp keep-out strips.
- Each clamped edge gets a keep-out strip along its full length: clamp reach + clearance.
"""

from dataclasses import dataclass
from typing import Sequence, Tuple

Rect = Tuple[float, float, float, float]  # x0, y0, x1, y1

FRONT, BACK, ZERO_END = "front", "back", "zero_end"


@dataclass(frozen=True)
class Fixture:
    sheet_length_in: float
    sheet_width_in: float
    nest_region_in: Rect
    clamp_zones_in: Tuple[Rect, ...]
    clamp_height_in: float

    @property
    def nest_size_in(self) -> Tuple[float, float]:
        r = self.nest_region_in
        return r[2] - r[0], r[3] - r[1]


def build(*, sheet_length_in: float, sheet_width_in: float, reach_x_in: float, edge_margin_in: float,
          reach_margin_in: float, clamp_edges: Sequence[str], clamp_reach_in: float,
          clamp_clearance_in: float, clamp_height_in: float) -> Fixture:
    strip = clamp_reach_in + clamp_clearance_in
    edges = set(clamp_edges)
    unknown = edges - {FRONT, BACK, ZERO_END}
    if unknown:
        raise ValueError(f"unknown clamp edges: {', '.join(sorted(unknown))}")
    length, width = sheet_length_in, sheet_width_in
    region = (
        strip if ZERO_END in edges else edge_margin_in,
        strip if FRONT in edges else edge_margin_in,
        min(reach_x_in, length) - reach_margin_in,
        width - (strip if BACK in edges else edge_margin_in),
    )
    if region[2] <= region[0] or region[3] <= region[1]:
        raise ValueError(f"no room left to nest: region {region}")
    zones = []
    if FRONT in edges:
        zones.append((0.0, 0.0, length, strip))
    if BACK in edges:
        zones.append((0.0, width - strip, length, width))
    if ZERO_END in edges:
        zones.append((0.0, 0.0, strip, width))
    return Fixture(length, width, region, tuple(zones), clamp_height_in)


def clamp_clear_z(thickness_in: float, clamp_height_in: float, margin_in: float) -> float:
    """Lowest Z allowed over a clamp zone: clamp tops are clamp_height above the plate top."""
    return thickness_in + clamp_height_in + margin_in


def min_clearance_height(thickness_in: float, min_clear_above_stock_in: float) -> float:
    """Lowest template clearance height (Z0 = stock bottom). The post doesn't retract between ops."""
    return thickness_in + min_clear_above_stock_in


def sheet_origin(envelope_in: Rect, nest_region_in: Rect) -> Tuple[float, float]:
    """Where a sheet's (0, 0) lands in the nesting document, given the envelope Arrange filled."""
    return envelope_in[0] - nest_region_in[0], envelope_in[1] - nest_region_in[1]


def contains(outer: Rect, inner: Rect, tol: float = 1e-6) -> bool:
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)
