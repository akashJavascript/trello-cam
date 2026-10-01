"""Which tool cuts a part, and which tool cuts a sheet (decisions 12, 13, 21).

- 4 mm by default. A part needs the 1/8 in tool if the 4 mm can't make one of its features (a
  hole that isn't a 4 mm drill size and is too small to contour, or an inside radius under 2 mm),
  or if its card has the `Tool 1/8` label.
- Polycarbonate never uses the aluminum 1/8 in (T12). If the poly 1/8 in tool or its template isn't
  set up, the part goes to Needs fixing with the exact comment from decision 13.
- One tool per sheet (the post allows one tool per program): if any part on a sheet needs the
  1/8 in tool, the whole sheet uses it.
"""

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from . import errors as E
from .errors import Issue
from .holes import BEARING, BORE, CONTOUR_WARN, DRILL, INNER, TOO_SMALL, HoleRules, ToolProfile, classify
from .plate import Loop, PlateAnalysis

RADIUS_TOL_IN = 1e-4


@dataclass(frozen=True)
class ToolNeed:
    tool: Optional[str]              # tool key, or None if the part can't be cut
    reasons: Tuple[str, ...] = ()    # why the default tool isn't enough (empty when it is)
    errors: Tuple[Issue, ...] = ()
    warnings: Tuple[Issue, ...] = ()


@dataclass(frozen=True)
class FeaturePlan:
    """How one part's features are cut with its sheet's tool."""
    drill: Tuple[int, ...]           # indexes into PlateAnalysis.through_holes
    bearing: Tuple[int, ...]
    bore: Tuple[int, ...]
    inner_loops: Tuple[Loop, ...]    # through cutouts, big holes and contoured small holes
    pocket_floor_ids: Tuple[int, ...]
    contoured_holes: Tuple[int, ...]  # holes in inner_loops that got a fit warning
    errors: Tuple[Issue, ...] = ()
    warnings: Tuple[Issue, ...] = ()

    def counts(self) -> Dict[str, int]:
        return {"drill": len(self.drill), "bore": len(self.bore), "bearing": len(self.bearing),
                "inner": len(self.inner_loops), "contour_warn": len(self.contoured_holes),
                "pockets": len(self.pocket_floor_ids)}


def _blockers(analysis: PlateAnalysis, tool: ToolProfile, rules: HoleRules) -> Tuple[List[str], List[str]]:
    holes, radii = [], []
    name = tool.label or tool.key
    for hole in analysis.through_holes:
        if classify(hole.diameter_in, tool, rules) == TOO_SMALL:
            msg = f'{hole.diameter_in:.4f}" hole is too small for the {name}'
            if msg not in holes:
                holes.append(msg)
    for r in analysis.inside_radii_in:
        if r < tool.min_inside_radius_in - RADIUS_TOL_IN:
            msg = f'R{r:.4f}" inside corner is tighter than the {name} can cut'
            if msg not in radii:
                radii.append(msg)
    return holes, radii


def part_tool(analysis: PlateAnalysis, *, family: str, default: ToolProfile, small: Optional[ToolProfile],
              rules: HoleRules, force_small: bool, small_label_name: str = "Tool 1/8",
              strict_inside_radius: bool = True) -> ToolNeed:
    """`small` is None when the small-feature tool (or its template) isn't set up for this family."""
    if analysis.errors:
        return ToolNeed(None)
    holes, radii = _blockers(analysis, default, rules)
    if not holes and not radii and not force_small:
        return ToolNeed(default.key)
    reasons = tuple(holes + radii) or (f'"{small_label_name}" label on the card',)
    if small is None:
        msg = (E.POLY_EIGHTH_MISSING_MSG if family == "polycarbonate"
               else f"needs manual CAM: no small-feature tool is set up for {family}")
        return ToolNeed(None, reasons, (Issue(E.NEEDS_MANUAL_CAM, msg),))
    holes, radii = _blockers(analysis, small, rules)
    errors = [Issue(E.FEATURE_TOO_SMALL, m) for m in holes]
    warnings = []
    for m in radii:
        if strict_inside_radius:
            errors.append(Issue(E.INSIDE_RADIUS_TOO_SMALL, m))
        else:
            warnings.append(Issue(E.SMALL_INSIDE_RADIUS, m))
    return ToolNeed(None if errors else small.key, reasons, tuple(errors), tuple(warnings))


def sheet_tool(needs: Mapping[str, ToolNeed], default_key: str,
               small_key: Optional[str]) -> Tuple[str, Tuple[Tuple[str, str], ...]]:
    """The one tool for a sheet, plus (part_key, reason) for every part that forced the small tool."""
    forced = tuple((key, need.reasons[0]) for key, need in needs.items()
                   if small_key is not None and need.tool == small_key)
    return (small_key if forced else default_key), forced


def plan_features(analysis: PlateAnalysis, tool: ToolProfile, rules: HoleRules) -> FeaturePlan:
    kinds = [classify(h.diameter_in, tool, rules) for h in analysis.through_holes]
    errors, warnings = [], []
    for h, kind in zip(analysis.through_holes, kinds):
        if kind == TOO_SMALL:
            errors.append(Issue(E.FEATURE_TOO_SMALL, f'{h.diameter_in:.4f}" hole is too small for the '
                                                     f"{tool.label or tool.key}"))
        elif kind == CONTOUR_WARN:
            warnings.append(Issue(E.HOLE_CONTOURED, f'{h.diameter_in:.4f}" hole is contoured with the '
                                                    f"{tool.label or tool.key}; check the fit"))
    contoured = tuple(i for i, k in enumerate(kinds) if k == CONTOUR_WARN)
    inner_holes = {i for i, k in enumerate(kinds) if k in (INNER, CONTOUR_WARN)}
    inner_loops = tuple(l for l in analysis.through_loops if l.hole is None or l.hole in inner_holes)
    looped = {l.hole for l in inner_loops if l.hole is not None}
    for i in sorted(inner_holes - looped):
        errors.append(Issue(E.NOT_A_PLATE, f'{analysis.through_holes[i].diameter_in:.4f}" hole has no loop to contour'))
    return FeaturePlan(
        drill=tuple(i for i, k in enumerate(kinds) if k == DRILL),
        bearing=tuple(i for i, k in enumerate(kinds) if k == BEARING),
        bore=tuple(i for i, k in enumerate(kinds) if k == BORE),
        inner_loops=inner_loops,
        pocket_floor_ids=analysis.pocket_floor_ids,
        contoured_holes=contoured,
        errors=tuple(errors), warnings=tuple(warnings),
    )
