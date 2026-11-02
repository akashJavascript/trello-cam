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
from autocam_core.holes import CONTOUR_ROOM_IN, DRILL, HoleRules, ToolProfile, classify
from autocam_core.plate import PlateAnalysis, analyze
from autocam_core.tooling import Split, part_tool, plan_features

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


def _split_why(a: PlateAnalysis, split: Split, small: Tool) -> str:
    """What goes on the small endmill first when the part is split (core 0.19.0's "more than one cutter")."""
    sizes = sorted({round(a.through_holes[i].diameter_in, 3) for i in split.holes})
    bits = []
    if split.holes:
        n = len(split.holes)
        bits.append(f"the {sizes[0]:.3f} in hole" if n == 1 else
                    f"{n} {sizes[0]:.3f} in holes" if len(sizes) == 1 else f"{n} small holes")
    if split.loops:
        n = len(split.loops)
        bits.append("a tight-cornered cutout" if n == 1 else f"{n} tight-cornered cutouts")
    return f"{' and '.join(bits)} first on the {cutter_name(small)}"


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


def use(cfg: Config, material_key: str, thickness: Optional[float], extra: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """How much of a sheet one part takes and what that costs: its outline's area, plus its share of the gaps
    ([costs] nest_efficiency), at the sheet's price ([costs] sheet_prices). None without a price or an outline."""
    area = float(extra.get("outline_area_in2") or 0.0)
    prices = cfg.costs.sheet_prices.get(material_key) or {}
    price = next((v for t, v in prices.items() if thickness is not None and abs(t - thickness) < 1e-6), None)
    if area <= 0:
        return None
    share = area / cfg.costs.nest_efficiency / (cfg.sheet.length_in * cfg.sheet.width_in)
    return {"area_in2": round(area, 1), "sheet_share": share, "each": round(price * share, 2) if price else None,
            "box_in": extra.get("box_in")}


def picks(a: PlateAnalysis, cfg: Config, tool: Optional[Tool], extra: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Onshape face or edge id -> the fit picking it means: a round hole that isn't drilled (by its size), or the
    outline. The panel turns a click on that face, or on one of its edges, in the CAD into its Fit (fits.py)."""
    ids = extra.get("face_ids") or []
    edges = extra.get("face_edges") or {}
    out: Dict[str, Dict[str, Any]] = {}

    def mark(fid: str, pick: Dict[str, Any]) -> None:
        out[fid] = pick
        for eid in edges.get(fid) or []:
            out.setdefault(eid, pick)          # an edge between a hole and the outline wall (none on a plate) stays first

    for fid in extra.get("outline_faces") or []:
        mark(fid, {"kind": "outline"})
    if tool is None:
        return out
    rules, profile = hole_rules(cfg), _profile(cfg, tool)
    for hole in a.through_holes:
        if classify(hole.diameter_in, profile, rules) == DRILL:
            continue
        for face_id in hole.face_ids:
            if 0 < face_id <= len(ids):
                fid = ids[face_id - 1]
                pick = {"kind": "hole", "size": round(hole.diameter_in, 4)}
                out[fid] = pick
                for eid in edges.get(fid) or []:
                    out[eid] = pick            # a hole's rim edge is the hole, even where it meets an outline wall
    return out


def bends_line(bends, material) -> str:
    """"Cut flat, then 1 bend: Bend A 90 up (R 0.09 in)", and a word on 6061 (it can crack at a tight bend)."""
    known = [b for b in bends if b.get("direction")]
    if not known:
        return "Cut flat, then bent (its bends weren't read: see the part in Onshape)."
    each = ", then ".join(f"{b['name']} {b['angle_deg']:g}\u00b0 {b['direction']}" for b in known)
    radii = sorted({round(b["radius_in"], 3) for b in known if b.get("radius_in")})
    text = (f"Cut flat, then {len(known)} bend{'s' if len(known) != 1 else ''}: {each}"
            + (f" (R {', '.join(f'{r:g}' for r in radii)} in)" if radii else "") + ".")
    if material is not None and material.name == "6061":
        text += " 6061 can crack at a tight bend; 5052 bends cleanly."
    return text


def check(geom: PartGeometry, cfg: Config, material_key: Optional[str],
          extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """{"ok", "problems": [{"title", "fix"}], "notes": [...], "thickness", "cutter", "cutterWhy", "features",
    "needMaterial", "use", "pick"}. Without a material the shape is checked against every stock size and the cutter
    waits."""
    extra = extra or {}
    problems: List[Dict[str, str]] = []
    notes: List[str] = []
    if material_key is None:
        stock = tuple(sorted({t for m in cfg.materials.values() for t in m.thicknesses_in}))
        a = analyze(geom, stock, cfg.plate.thickness_tol_in)
        problems = _not_a_plate(a.measured_thickness_in, stock) or \
            [explain(i, a.measured_thickness_in, stock, "Stock", None) for i in a.errors]
        return {"ok": not problems, "problems": problems, "notes": [note(i) for i in a.warnings],
                "thickness": inches(a.stock_thickness_in or a.measured_thickness_in), "cutter": "", "cutterWhy": "",
                "features": "", "needMaterial": True, "use": None, "pick": picks(a, cfg, None, extra)}

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
    out: Dict[str, Any] = {"thickness": inches(a.stock_thickness_in or a.measured_thickness_in), "stock_in": a.stock_thickness_in,
                           "cutter": "",
                           "cutterWhy": "", "features": "", "needMaterial": False,
                           "use": use(cfg, material_key, a.stock_thickness_in, extra), "pick": picks(a, cfg, None, extra)}
    if not problems:
        rules = hole_rules(cfg)
        need = part_tool(a, family=m.family, default=_profile(cfg, default),
                         small=_profile(cfg, small_ready) if small_ready else None, rules=rules, force_small=False,
                         strict_inside_radius=cfg.plate.strict_inside_radius, split=tooling.split)
        problems += [explain(i, a.measured_thickness_in, stock, name, small) for i in need.errors]
        notes += [note(i) for i in need.warnings]
        if need.tool is not None:
            tool = cfg.tools[need.tool]
            out["cutter"] = cutter_name(tool)
            out["pick"] = picks(a, cfg, tool, extra)
            if need.split is not None and small_ready is not None:
                # what a run does ([tooling] split): the sheet stays on the 4 mm; only these go in a short
                # 1/8 in program run first
                out["cutterWhy"] = _split_why(a, need.split, small_ready)
                plans = [plan_features(a, _profile(cfg, tool), rules, without=need.split),
                         plan_features(a, _profile(cfg, small_ready), rules, only=need.split)]
            else:
                if need.tool != default.key and need.reasons:
                    out["cutterWhy"] = _why_small(need.reasons[0])
                plans = [plan_features(a, _profile(cfg, tool), rules)]
            counts: Dict[str, int] = {}
            for plan in plans:
                problems += [explain(i, a.measured_thickness_in, stock, name, small) for i in plan.errors]
                notes += [note(i) for i in plan.warnings]
                for k, n in plan.counts().items():
                    counts[k] = counts.get(k, 0) + n
            out["features"] = ", ".join(f"{counts[k]} {word}" for k, word in FEATURE_WORDS if counts.get(k))
    out.update(ok=not problems, problems=problems, notes=list(dict.fromkeys(notes)))
    return out
