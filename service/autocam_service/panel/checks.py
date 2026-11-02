"""The panel's design checks: AutoCAM's own plate rules (plate.py, tooling.py) on the part's shape from Onshape
(geometry.py), said the way a student can act on.

They're the rules a run applies after the STEP export, so a part that passes here passes there (the run still has the
last word: nesting, clamps and each program's own checks come later). A feature that only means the 1/8 in endmill
isn't a problem: the panel says which cutter the part gets and why, and never asks for a change (the user's choice,
2026-11-01).
"""

from typing import Any, Dict, List, Optional, Tuple

from autocam_core import errors as E
from autocam_core.geometry import PartGeometry
from autocam_core.holes import CONTOUR_ROOM_IN, HoleRules, ToolProfile
from autocam_core.plate import PlateAnalysis, analyze
from autocam_core.tooling import part_tool, plan_features

from ..config import Config, Tool

NOT_PLATE_MARGIN_IN = 0.25      # this much thicker than the thickest stock: not a plate at all (a block, a printed part)
FRACTIONS = {0.0625: "1/16", 0.125: "1/8", 0.1875: "3/16", 0.25: "1/4", 0.375: "3/8", 0.5: "1/2"}
FEATURE_WORDS = (("drill", "drilled"), ("bore", "bored"), ("bearing", "bearing"), ("inner", "cut out"),
                 ("pockets", "pocket"))


def inches(x: float) -> str:
    """3/16 in for stock sizes, 0.210 in otherwise."""
    for value, text in FRACTIONS.items():
        if abs(x - value) < 0.0005:
            return f"{text} in"
    return f"{x:.3f} in"


def hole_rules(cfg: Config) -> HoleRules:
    h = cfg.holes
    return HoleRules(h.drill_tol_in, h.bore_min_in, h.bore_min_tol_in, h.bore_max_in, tuple(h.bearing_sizes_in),
                     h.bearing_tol_in, h.bearing_stock_in)


def _profile(cfg: Config, tool: Tool) -> ToolProfile:
    return ToolProfile(tool.key, tool.diameter_in, tool.min_inside_radius_in, tuple(cfg.holes.drill_sizes_in[tool.key]),
                       tool.cutter_label)


def _ready(cfg: Config, tool: Tool) -> bool:
    """A tool runs only with its template exported (as jobs._tool_spec decides)."""
    template = next((t for t in cfg.templates.values() if t.tool == tool.key), None)
    return tool.available and template is not None and template.file.is_file()


def cutter_name(tool: Tool) -> str:
    """4 mm endmill, 1/8 in endmill: what students call them."""
    mm = tool.diameter_in * 25.4
    if abs(mm - round(mm)) < 0.02:
        return f"{round(mm)} mm endmill"
    return f"{inches(tool.diameter_in)} endmill"


def _why_small(reason: str) -> str:
    """part_tool's reason the 4 mm isn't enough, said as a plain fact (no advice)."""
    if "hole" in reason:
        size = reason.split('"', 1)[0]
        try:
            return f"a {float(size):.3f} in hole needs it"
        except ValueError:
            return "a small hole needs it"
    if "inside corner" in reason:
        return "a tight inside corner needs it"
    return ""


def _number(text: str) -> Optional[float]:
    try:
        return float(text.split('"', 1)[0].split()[-1].lstrip("R"))
    except (ValueError, IndexError):
        return None


def explain(issue, thickness: float, stock: Tuple[float, ...], material: str, small: Optional[Tool]) -> Dict[str, str]:
    """A problem the run would send the card back for: what it is, and how to fix it."""
    code, msg = issue.code, issue.msg
    if code == E.THICKNESS_NOT_STOCK:
        nearest = min(stock, key=lambda s: abs(s - thickness)) if stock else None
        sizes = ", ".join(inches(s).replace(" in", "") for s in stock)
        return {"title": f"It's {thickness:.3f} in thick, which isn't a stock size.",
                "fix": f"{material} comes in {sizes} in." + (f" Make it {inches(nearest)}." if nearest else "")}
    if code == E.BODY_COUNT:
        return {"title": "It's more than one solid.", "fix": "Make it one part, or send each part on its own."}
    if code == E.CHAMFER:
        return {"title": "It has a chamfer or countersink.",
                "fix": "The router can't cut angled faces. Take it off and deburr or countersink by hand after."}
    if code == E.EDGE_FILLET:
        return {"title": "It has a rounded top or bottom edge.",
                "fix": "The router can't cut those. Take the fillet off (round corners in the sketch are fine)."}
    if code == E.UNSUPPORTED_FACE:
        return {"title": "It has a curved 3D face.", "fix": "Only flat faces and straight walls can be cut."}
    if code == E.TWO_SIDED:
        return {"title": "It has pockets or counterbores on both sides.",
                "fix": "The router only cuts from the top. Put every pocket on the same side."}
    if code == E.NOT_A_PLATE:
        return {"title": "It isn't a flat plate.",
                "fix": "AutoCAM cuts plates: a flat top and bottom with straight walls, pockets from one side."}
    if code == E.FEATURE_TOO_SMALL:
        size = _number(msg)
        least = small.diameter_in + 2 * CONTOUR_ROOM_IN if small else None
        return {"title": f"A {size:.3f} in hole is too small for any cutter." if size else "A hole is too small to cut.",
                "fix": (f"Make it at least {least:.3f} in, or drill it by hand after." if least
                        else "Make it bigger, or drill it by hand after.")}
    if code == E.INSIDE_RADIUS_TOO_SMALL:
        return {"title": "An inside corner is tighter than any cutter can make.",
                "fix": f"Give it a radius of at least {small.min_inside_radius_in:.4f} in." if small
                else "Give it a bigger radius."}
    if code == E.NEEDS_MANUAL_CAM:
        if "island" in msg:
            return {"title": "A pocket has a raised island in it.",
                    "fix": "AutoCAM can't cut around islands yet: split the pocket, or it gets CAM'd by hand."}
        if "split" in msg:
            return {"title": "Its top is split into several faces.",
                    "fix": "There's no single outline to cut. Make it one plate (no split lines or touching bodies)."}
        if "poly" in msg:
            return {"title": "It needs the 1/8 in endmill, which isn't set up for polycarbonate yet.",
                    "fix": "It gets CAM'd by hand."}
        return {"title": "It needs CAM by hand.", "fix": msg}
    return {"title": msg[:1].upper() + msg[1:] + ".", "fix": ""}


def note(issue) -> str:
    """A warning: cut anyway, but worth knowing."""
    if issue.code == E.SHARP_INSIDE_CORNERS:
        n = issue.msg.split(" ", 1)[0]
        return (f"{n} sharp inside corner{'s' if n != '1' else ''}: the cutter leaves a small radius there. Add "
                "dogbones if something fits into them.")
    if issue.code == E.HOLE_CONTOURED:
        size = _number(issue.msg)
        return f"The {size:.3f} in hole is contoured: check its fit." if size else "A hole is contoured: check its fit."
    return issue.msg[:1].upper() + issue.msg[1:] + "."


def _not_a_plate(thickness: float, stock: Tuple[float, ...]) -> Optional[List[Dict[str, str]]]:
    """A part far thicker than any stock isn't a plate with a mistake in it: one line, not a list of its faces."""
    if stock and thickness > max(stock) + NOT_PLATE_MARGIN_IN:
        return [{"title": f"This isn't a plate ({thickness:.3f} in thick).",
                 "fix": f"AutoCAM cuts flat plates up to {inches(max(stock))} thick. This part gets made another way."}]
    return None


def check(geom: PartGeometry, cfg: Config, material_key: Optional[str]) -> Dict[str, Any]:
    """{"ok", "problems": [{"title", "fix"}], "notes": [...], "thickness", "cutter", "cutterWhy", "features",
    "needMaterial"}. Without a material the shape is checked against every stock size and the cutter waits."""
    problems: List[Dict[str, str]] = []
    notes: List[str] = []
    if material_key is None:
        stock = tuple(sorted({t for m in cfg.materials.values() for t in m.thicknesses_in}))
        a = analyze(geom, stock, cfg.plate.thickness_tol_in)
        problems = _not_a_plate(a.measured_thickness_in, stock) or \
            [explain(i, a.measured_thickness_in, stock, "Stock", None) for i in a.errors]
        return {"ok": not problems, "problems": problems, "notes": [note(i) for i in a.warnings],
                "thickness": inches(a.stock_thickness_in or a.measured_thickness_in), "cutter": "", "cutterWhy": "",
                "features": "", "needMaterial": True}

    m = cfg.materials[material_key]
    tooling = cfg.tooling[m.family]
    default, small = cfg.tools[tooling.default], cfg.tools.get(tooling.small_features)
    small_ready = small if small is not None and _ready(cfg, small) else None
    stock = tuple(m.thicknesses_in)
    name = f"{m.name} aluminum" if m.family == "aluminum" else "Polycarbonate" if m.family == "polycarbonate" \
        else m.name
    a: PlateAnalysis = analyze(geom, stock, cfg.plate.thickness_tol_in)
    problems += _not_a_plate(a.measured_thickness_in, stock) or \
        [explain(i, a.measured_thickness_in, stock, name, small) for i in a.errors]
    notes += [note(i) for i in a.warnings]
    if not a.errors and len(a.top_face_ids) > 1:
        problems.append(explain(E.Issue(E.NEEDS_MANUAL_CAM, "split"), 0, stock, name, small))
    out: Dict[str, Any] = {"thickness": inches(a.stock_thickness_in or a.measured_thickness_in), "cutter": "",
                           "cutterWhy": "", "features": "", "needMaterial": False}
    if not problems:
        rules = hole_rules(cfg)
        need = part_tool(a, family=m.family, default=_profile(cfg, default),
                         small=_profile(cfg, small_ready) if small_ready else None, rules=rules, force_small=False,
                         strict_inside_radius=cfg.plate.strict_inside_radius)
        problems += [explain(i, a.measured_thickness_in, stock, name, small) for i in need.errors]
        notes += [note(i) for i in need.warnings]
        if need.tool is not None:
            tool = cfg.tools[need.tool]
            out["cutter"] = cutter_name(tool)
            if need.tool != default.key and need.reasons:
                out["cutterWhy"] = _why_small(need.reasons[0])
            plan = plan_features(a, _profile(cfg, tool), rules)
            problems += [explain(i, a.measured_thickness_in, stock, name, small) for i in plan.errors]
            notes += [note(i) for i in plan.warnings]
            counts = plan.counts()
            out["features"] = ", ".join(f"{counts[k]} {word}" for k, word in FEATURE_WORDS if counts.get(k))
    out.update(ok=not problems, problems=problems, notes=list(dict.fromkeys(notes)))
    return out
