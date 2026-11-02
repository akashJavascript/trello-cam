"""UNTESTED IN FUSION. Manufacture-side calls: setup, template, selections, outline copies, toolpaths, post.

What pipeline_probe / pipeline_probe2 confirmed is used as confirmed; the rest is listed in UNCONFIRMED and
reported in result.json (worker.untested_steps) whenever a run uses it.
"""

import json
import os

import adsk.cam
import adsk.core

from .adapter import AdapterError, OpState, TemplateOp
from .fx_util import call, collection, items, param, set_expr, to_cm, wait_for

STOCK_BOTTOM = "'from stock bottom'"

UNCONFIRMED = {
    "setup_name": "Setup.name = program name",
    "multi_model_setup": "setup with several part bodies as models",
    "drill_faces": "drill op: holeMode + holeFaces selection",
    "bore_faces": "bore op: holeMode + circularFaces selection",
    "inner_chains": "inner contour: chain selection of single loops",
    "post_properties": "PostProcessInput.postProperties",
    "preview_camera": "Viewport camera framed on one sheet",
    "arrange_flip": "ArrangeComponent.isDirectionFlipped chosen from upDirection",
    "arrange_refuse": "ArrangeComponent.deleteMe for a part that would lie on its side",
    "discard_hide": "Occurrence.isLightBulbOn = False for copies taken out of the job",
    "generate_retry": "CAM.generateToolpath again for ops left with no toolpath and no error",
    "chain_side_type": "ChainSelection.sideType = AlwaysInside for single loops",
    "chain_direction": "ChainSelection.isReverted so the chain runs against the loop's way (cut side follows direction)",
    "outline_chain": "a split top's outline cut as one chain of edges (sheet metal flats; fx_geometry.outline_coedges)",
    "gouge_check": "BRepBody.pointContainment on the posted program's cutting points",
    "team_save": "Document.saveAs into the Fusion Team folder, wait for Application.dataFileComplete, then DataFile.fusionWebURL",
    "tabs": "contour op tabs: group_tabs, tabPositioning = 'tabCount', tabsPerContour",
    "tab_points": "contour op tabs at points: tabPositioning 'distance' 1000 in, tabPositions = sketch points",
    "op_copy": "a copy of the template's [inner] op (CAMTemplate.createFromOperations) for tabbed cutouts",
    "pocket_floors": "[pocket] (2D Adaptive): 'pockets' = CurveSelections.createNewPocketSelection on the floors",
    "pocket_selection": "[pocket]: each floor's outer loop as a chain, inside, when the pocket selection fails",
    "pocket_walls": "[pocketfinish] (2D Contour): each floor's outer loop as a chain, cut on the floor's side",
    # extra_setup (then face_setup) and face_tops ([face] on each faced copy's top outer loop, from the stock top down by the depth)
    # work in Fusion (r029, 2026-11-01); still reported until a sheet with them has been cut.
    "bearing_stock": "[bearing] (Bore): useStockToLeave + stockToLeave = holes.bearing_stock_in",   # works (r031)
    "extra_setup": "another setup on the sheet's stock solid (the FACE program, fit laps)",
    "fit_stock": "fit: useStockToLeave + stockToLeave on the fitted [bore]/[bearing] copy or [outer] ops",
    "face_tops": "[face] (2D Adaptive): each faced copy's top outer loop as a chain, inside; bottom height "
                 "'from stock top' less the depth",
    "scribe_lines": "[scribe]: a copy of [outer] along root sketch lines (one open chain each) with the tool center "
                    "(compensationType 'off'; no leads, ramp, tabs, stock to leave or multiple depths), from the stock "
                    "top down by the depth",
    "scribe_tool": "Operation.tool = Tool.createFromJson(the library's chamfer mill), then its preset's feeds and "
                   "speeds, coolant left disabled",
}


def cam_product(app, doc):
    app.userInterface.workspaces.itemById("CAMEnvironment").activate()
    cam = adsk.cam.CAM.cast(doc.products.itemByProductType("CAMProductType"))
    if cam is None:
        raise AdapterError("no CAM product in the document")
    return cam


def make_setup(cam, name: str, models, stock_body, setup_params) -> object:
    si = cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation)
    si.models = list(models)
    setup = call("setups.add", cam.setups.add, si)
    setup.name = name
    setup.stockMode = adsk.cam.SetupStockModes.SolidStock
    call("Setup.stockSolids", setattr, setup, "stockSolids", collection([stock_body]))
    set_expr(setup, "wcs_origin_mode", setup_params["wcs_origin_mode"])
    set_expr(setup, "wcs_origin_boxPoint", setup_params["wcs_origin_boxPoint"])
    return setup


def ops(setup):
    return items(setup.allOperations)


def op_by_name(setup, name: str):
    found = [o for o in ops(setup) if o.name == name]
    if len(found) != 1:
        raise AdapterError(f"{setup.name}: {len(found)} ops named {name!r}")
    return found[0]


def tool_guid(op):
    try:
        return json.loads(op.tool.toJson()).get("guid")
    except Exception:  # noqa: BLE001 - reported as a GUID mismatch
        return None


def apply_template(setup, path: str):
    template = call("CAMTemplate.createFromFile", adsk.cam.CAMTemplate.createFromFile, path)
    t_in = adsk.cam.CreateFromCAMTemplateInput.create()
    t_in.camTemplate = template
    call("createFromCAMTemplate2", setup.createFromCAMTemplate2, t_in)
    return [TemplateOp(o.name, tool_guid(o)) for o in ops(setup)]


def _choices(op, name: str):
    """(labels, values) Fusion lists for a choice parameter, or (None, why not)."""
    try:
        v = adsk.cam.ChoiceParameterValue.cast(param(op, name).value)
        if v is None:
            return None, "not a choice parameter"
        listed = v.getChoices()
        ok, labels, values = listed if len(listed) == 3 else (True,) + tuple(listed)
        return list(labels), list(values)
    except Exception as e:  # noqa: BLE001 - only for the message
        return None, f"getChoices: {e}"


def _set_choice(op, name: str, wants) -> str:
    """Set choice parameter `name` to the first of its values (or labels) containing one of `wants`; if Fusion
    won't list them, try each of `wants` as the value. Raises AdapterError saying what was listed and tried."""
    labels, values = _choices(op, name)
    if labels is not None:
        candidates = [v for w in wants for lab, v in zip(labels, values) if w in v.lower() or w in lab.lower()]
    else:
        candidates = list(wants)
    tried = []
    for value in dict.fromkeys(candidates):
        try:
            set_expr(op, name, f"'{value}'")
            return value
        except Exception as e:  # noqa: BLE001 - try the choice's own value next
            tried.append(f"{value!r} as expression: {e}")
        try:
            v = adsk.cam.ChoiceParameterValue.cast(param(op, name).value)
            v.value = value
            if v.value == value:
                return value
            tried.append(f"{value!r} as value: reads {v.value!r}")
        except Exception as e:  # noqa: BLE001 - try the next
            tried.append(f"{value!r} as value: {e}")
    listed = (f"{', '.join(f'{lab}={v}' for lab, v in zip(labels, values))}" if labels is not None else values)
    raise AdapterError(f"{name}: nothing like {'/'.join(wants)} took (choices: {listed}; tried: {'; '.join(tried) or 'none'})")


def set_tabs(op, names, per_contour: int) -> None:
    """The op's tabs on (the template's shape, width and height: the API can't set tabShape, any value is an
    'Invalid enumeration value'), this many on each contour, spread evenly ('tabCount')."""
    set_expr(op, names["enabled"], "true")
    set_expr(op, names["positioning"], "'tabCount'")
    set_expr(op, names.get("per_contour", "tabsPerContour"), str(int(per_contour)))   # jobs from before 0.8.0


def _set_length(op, name: str, inches: float) -> None:
    p = param(op, name)
    want = f"{inches:g} in"
    p.expression = want
    if p.expression.replace(" ", "") == want.replace(" ", ""):
        return                     # it took; on an op whose tool is in inches the value isn't in cm (r025, r026)
    got = getattr(p.value, "value", None)
    if got is not None and abs(got - to_cm(inches)) > 1e-4:
        raise AdapterError(f"{name} = {inches:g} in didn't stick (reads {p.expression})")


def set_tab_size(op, names, width_in: float, height_in: float) -> None:
    """The op's tab width and height in inches (0: the template's)."""
    if width_in:
        _set_length(op, names.get("width", "tabWidth"), width_in)
    if height_in:
        _set_length(op, names.get("height", "tabHeight"), height_in)


def set_tab_points(op, sketch, names, points_in) -> None:
    """The op's tabs on (the template's shape, width and height), at sketch points made in `sketch` (on the
    root's X/Y plane) at points_in."""
    set_expr(op, names["enabled"], "true")
    labels, values = _choices(op, names["positioning"])
    if labels is not None and any("point" in v.lower() or "point" in lab.lower() for lab, v in zip(labels, values)):
        _set_choice(op, names["positioning"], ("points", "point"))
    else:
        # This Fusion has no "at points" choice (2705: by distance or number of tabs), but a tabPositions
        # selection: by distance, so far apart that Fusion adds none of its own, plus the points.
        set_expr(op, names["positioning"], "'distance'")
        param(op, names["distance"]).expression = "1000 in"
    made = [sketch.sketchPoints.add(adsk.core.Point3D.create(to_cm(x), to_cm(y), 0)) for x, y in points_in]
    raw = param(op, names.get("manual_positions", "tabPositions")).value
    value = adsk.cam.CadObjectParameterValue.cast(raw)
    if value is None:
        raise AdapterError(f"{names.get('manual_positions', 'tabPositions')} isn't a selection parameter (it's a "
                           f"{getattr(raw, 'objectType', type(raw).__name__)})")
    value.value = made
    if len(list(value.value)) != len(made):
        raise AdapterError(f"tab positions took {len(list(value.value))} of {len(made)} points")


def copy_op(setup, op, name: str) -> None:
    """A copy of op (unfilled, from the template) at the end of the setup, named `name`."""
    tpl = call("CAMTemplate.createFromOperations", adsk.cam.CAMTemplate.createFromOperations, [op])
    t_in = adsk.cam.CreateFromCAMTemplateInput.create()
    t_in.camTemplate = tpl
    call("createFromCAMTemplate2 (op copy)", setup.createFromCAMTemplate2, t_in)
    new = ops(setup)[-1]
    new.name = name
    if new.name != name:
        raise AdapterError(f"renaming the copy of {op.name} to {name!r} didn't stick")


def face_depth(op, depth_in: float) -> None:
    """[face]: from the stock top down to the stock top less the depth (the faced parts' tops are the stock top)."""
    set_expr(op, "topHeight_mode", "'from stock top'")
    _set_length(op, "topHeight_offset", 0.0)
    set_expr(op, "bottomHeight_mode", "'from stock top'")
    _set_length(op, "bottomHeight_offset", -depth_in)


def select_lines(op, curves, contour_param: str) -> None:
    """Each curve (a sketch line) as an open chain of its own: the tool center follows it (compensation off)."""
    value = _contours(op, contour_param)
    sels = value.getCurveSelections()
    sels.clear()
    for curve in curves:
        chain = sels.createNewChainSelection()
        if hasattr(type(chain), "isOpenAllowed"):
            chain.isOpenAllowed = True
        chain.inputGeometry = [curve]
    value.applyCurveSelections(sels)


SCRIBE_OFF = ("doLeadIn", "doLeadOut", "doRamp", "doMultipleDepths", "doMultipleFinishingPasses", "doRoughingPasses",
              "group_tabs", "useStockToLeave", "doChamfer", "useRestMachining")


def scribe_params(op, depth_in: float) -> None:
    """[scribe]: the tool center on the line (no compensation), straight down to the depth and along it once: no
    leads, ramp, tabs, stock to leave or extra passes. From the stock top down by depth_in."""
    _set_choice(op, "compensationType", ("off",))
    for name in SCRIBE_OFF:
        if op.parameters.itemByName(name) is not None:
            set_expr(op, name, "false")
    face_depth(op, depth_in)


_FEEDS = (("tool_spindleSpeed", "n", "rpm"), ("tool_rampSpindleSpeed", "n_ramp", "rpm"),
          ("tool_feedCutting", "v_f", "feed"), ("tool_feedEntry", "v_f_leadIn", "feed"),
          ("tool_feedExit", "v_f_leadOut", "feed"), ("tool_feedPlunge", "v_f_plunge", "feed"),
          ("tool_feedRamp", "v_f_ramp", "feed"), ("tool_feedTransition", "v_f_transition", "feed"))


def set_tool(op, tool_json: str) -> str:
    """The op's tool from its library entry, and that entry's preset feeds and speeds (in the tool's own units);
    coolant stays as the op had it (disabled: mist is the post's). Returns how the feeds were set, for the run
    notes. Raises AdapterError unless the op's tool reads back with the entry's GUID."""
    data = json.loads(tool_json)
    tool = call("Tool.createFromJson", adsk.cam.Tool.createFromJson, tool_json)
    coolant = op.parameters.itemByName("tool_coolant")
    keep = coolant.expression if coolant is not None else None
    call(f"{op.name}: tool", setattr, op, "tool", tool)
    got = tool_guid(op)
    if got != data.get("guid"):
        raise AdapterError(f"{op.name}: the tool reads back as {got or 'unreadable'}, not {data.get('guid')}")
    preset = ((data.get("start-values") or {}).get("presets") or [{}])[0]
    unit = "mm/min" if data.get("unit") == "millimeters" else "in/min"
    done = []
    for name, key, kind in _FEEDS:
        if key in preset and op.parameters.itemByName(name) is not None:
            want = f"{float(preset[key]):g} {'rpm' if kind == 'rpm' else unit}"
            p = param(op, name)
            p.expression = want
            if p.expression.replace(" ", "") != want.replace(" ", ""):
                raise AdapterError(f"{op.name}: {name} = {want} didn't stick (reads {p.expression})")
            done.append(f"{name} {p.expression}")
    if keep is not None:
        set_expr(op, "tool_coolant", keep)
    return f"{op.name}: tool {data.get('description') or data.get('guid')}, feeds set ({', '.join(done)})"


def radial_stock(op, inches: float) -> None:
    """The op leaves this much on the walls (Bore: + a smaller hole, - a bigger one), and nothing on the floor: a 2D
    Contour's axial stock follows the radial by default (r033: 0.002 radial stopped an outline 0.002 above Z0)."""
    set_expr(op, "useStockToLeave", "true")
    _set_length(op, "stockToLeave", inches)
    if op.parameters.itemByName("verticalStockToLeave") is not None:
        set_expr(op, "verticalStockToLeave", "0 in")


def cut_to_stock_bottom(op) -> None:
    set_expr(op, "bottomHeight_mode", STOCK_BOTTOM)
    set_expr(op, "bottomHeight_offset", "0 in")


def _contours(op, name: str):
    return adsk.cam.CadContours2dParameterValue.cast(param(op, name).value)


def select_face_loops(op, faces, loop_type, contour_param: str) -> None:
    value = _contours(op, contour_param)
    sels = value.getCurveSelections()
    sels.clear()
    for face in faces:
        fc = sels.createNewFaceContourSelection()
        fc.loopType = loop_type
        fc.isSelectingSamePlaneFaces = False
        fc.inputGeometry = [face]
    value.applyCurveSelections(sels)


def chain_api() -> str:
    """What this Fusion's chain selection offers, for the run notes (decides how the cut side is set)."""
    sides = getattr(adsk.cam, "SideTypes", None)
    names = sorted(n for n in dir(adsk.cam.ChainSelection) if not n.startswith("_"))
    side_names = sorted(n for n in dir(sides) if n.endswith("SideType")) if sides is not None else []
    return f"ChainSelection: {', '.join(names)}; SideTypes: {', '.join(side_names) or 'none'}"


def _inside(chain, loop, used) -> None:
    """Cut a single loop on the inside of the region it encloses: inside the hole for an inner loop (a cutout),
    on the face for an outer loop (a pocket floor's walls, cut from the pocket). Fusion picks the cut side of a
    chain from the chain's direction, and a chain built from edges runs the way its first edge happens to point.

    Seen in Fusion 2705.1.15 (2026-10-01; ChainSelection has no sideType there), on inner loops:
    - not reverted at all: round cutouts outside, triangles inside (edge directions vary);
    - isReverted = first co-edge isOpposedToEdge (chain runs the loop's way): every cutout outside;
    - so: isReverted = not isOpposedToEdge (chain runs against the loop's way). The cut is on the side away from
      the loop's face; an outer loop's face is the region it encloses, so it runs the loop's way instead.
    The posted program is still tested against the part bodies (pipeline), so a wrong side is never offered."""
    sides = getattr(adsk.cam, "SideTypes", None)
    if sides is not None and hasattr(type(chain), "sideType") and hasattr(sides, "AlwaysInsideSideType"):
        chain.sideType = sides.AlwaysInsideSideType
        used("chain_side_type")
        return
    if hasattr(type(chain), "isReverted"):
        with_loop = loop.coEdges.item(0).isOpposedToEdge
        chain.isReverted = with_loop if loop.isOuter else not with_loop
        used("chain_direction")


def select_loops(op, whole_faces, single_loops, contour_param: str, used=lambda key: None) -> None:
    """whole_faces: faces whose inner loops are all cut; single_loops: BRepLoops cut on their own, on the inside
    of what they enclose (_inside)."""
    value = _contours(op, contour_param)
    sels = value.getCurveSelections()
    sels.clear()
    for face in whole_faces:
        fc = sels.createNewFaceContourSelection()
        fc.loopType = adsk.cam.LoopTypes.OnlyInsideLoops
        fc.isSelectingSamePlaneFaces = False
        fc.inputGeometry = [face]
    for loop in single_loops:
        chain = sels.createNewChainSelection()
        chain.inputGeometry = [co.edge for co in items(loop.coEdges)]   # the whole loop, in order
        _inside(chain, loop, used)
    value.applyCurveSelections(sels)


def select_pockets(op, floors, outer_loops, pocket_param: str, used=lambda key: None) -> str:
    """[pocket]: clear each floor's pocket down to the floor. The floor's inner loops aren't islands: they're
    holes or deeper pockets below it (a raised island sends the part to manual CAM, plate.py), and what's above
    them comes out with the pocket. Fusion's pocket selection on the floors first (what picking a floor in the
    2D Adaptive's Pocket Selections makes); if this Fusion can't, each floor's outer loop as a chain, inside.
    Never a face contour: on a 2D Adaptive that keeps the face and clears everything around it, out to the stock
    (r023, 2026-10-31). Returns which one took."""
    value = _contours(op, pocket_param)
    sels = value.getCurveSelections()
    sels.clear()
    try:
        ps = sels.createNewPocketSelection()
        ps.inputGeometry = list(floors)
        value.applyCurveSelections(sels)
        return "pocket selection"
    except Exception as e:  # noqa: BLE001 - fall back to chains, and say both if they fail too
        first = f"{type(e).__name__}: {e}"
    try:
        select_loops(op, [], outer_loops, pocket_param, used)
    except Exception as e:  # noqa: BLE001
        raise AdapterError(f"pocket selection: {first}; chains: {type(e).__name__}: {e}") from None
    return f"chains (pocket selection: {first})"


def select_hole_faces(op, faces, names) -> None:
    set_expr(op, names["hole_mode"], names["hole_mode_value"])
    value = adsk.cam.CadObjectParameterValue.cast(param(op, names["faces"]).value)
    call(f"{op.name}: {names['faces']}", setattr, value, "value", list(faces))


def outline_copies(setup, template_op, outlines, contour_param: str) -> None:
    """outlines: [(op name, top face)] in cut order. Copies of template_op, appended in that order."""
    cut_to_stock_bottom(template_op)
    tpl = call("CAMTemplate.createFromOperations", adsk.cam.CAMTemplate.createFromOperations, [template_op])
    for name, face in outlines:
        t_in = adsk.cam.CreateFromCAMTemplateInput.create()
        t_in.camTemplate = tpl
        call("createFromCAMTemplate2 (outline copy)", setup.createFromCAMTemplate2, t_in)
        op = ops(setup)[-1]
        op.name = name
        if op.name != name:
            raise AdapterError(f"renaming the outline copy to {name!r} didn't stick")
        if isinstance(face, list):                       # a split top's outline (fx_geometry.outline_coedges)
            select_outline_chain(op, face, contour_param)
        else:
            select_face_loops(op, [face], adsk.cam.LoopTypes.OnlyOutsideLoops, contour_param)
    template_op.deleteMe()


def select_outline_chain(op, coedges, contour_param: str) -> None:
    """A part's outline as one chain of edges, cut outside it. Like a cutout's loop (_inside): the cut side follows
    the chain's direction, and a chain running against the loops' way cuts on the side away from the faces, which
    for an outline is outside. The posted program is still tested against the part bodies, so a wrong side is never
    offered."""
    if not coedges:
        raise AdapterError(f"{op.name}: no outline edges")
    value = _contours(op, contour_param)
    sels = value.getCurveSelections()
    sels.clear()
    chain = sels.createNewChainSelection()
    chain.inputGeometry = [co.edge for co in coedges]
    sides = getattr(adsk.cam, "SideTypes", None)
    if sides is not None and hasattr(type(chain), "sideType") and hasattr(sides, "AlwaysOutsideSideType"):
        chain.sideType = sides.AlwaysOutsideSideType
    elif hasattr(type(chain), "isReverted"):
        chain.isReverted = not coedges[0].isOpposedToEdge
    value.applyCurveSelections(sels)


def generate(cam, setups, timeout_s: float, used=lambda key: None):
    all_ops = [o for s in setups.values() for o in ops(s)]

    def settle(future, what):
        wait_for(lambda: future.isGenerationCompleted, timeout_s, what)
        wait_for(lambda: not any(o.isGenerating for o in all_ops), timeout_s, f"{what} to finish")

    settle(call("generateAllToolpaths", cam.generateAllToolpaths, False), "toolpath generation")
    # Seen in Fusion (2026-10-01, a 1/4 in plate with 29 cutouts): the outline op came back with no toolpath and
    # no error, and generated fine when asked again by hand. Ask once more for any op like that.
    missing = [o for o in all_ops if not o.hasToolpath and not o.hasError]
    if missing:
        used("generate_retry")
        settle(call("generateToolpath (retry)", cam.generateToolpath, collection(missing)), "toolpath regeneration")
    out = {}
    for name, setup in setups.items():
        out[name] = [OpState(o.name, bool(o.hasToolpath), (o.error or "error") if o.hasError else None,
                             (o.warning or "warning") if o.hasWarning else None) for o in ops(setup)]
    return out


def _value_input(v):
    if isinstance(v, bool):
        return adsk.core.ValueInput.createByBoolean(v)
    if isinstance(v, (int, float)):
        return adsk.core.ValueInput.createByReal(float(v))
    return adsk.core.ValueInput.createByString(str(v))


def post(cam, setup, program_name: str, folder: str, post_path: str, properties) -> str:
    if os.listdir(folder):
        raise AdapterError(f"post folder {folder} isn't empty")
    ppi = adsk.cam.PostProcessInput.create(program_name, post_path, folder, adsk.cam.PostOutputUnitOptions.InchesOutput)
    ppi.isOpenInEditor = False
    named = adsk.core.NamedValues.create()
    for key, value in properties.items():
        named.add(key, _value_input(value))
    call("PostProcessInput.postProperties", setattr, ppi, "postProperties", named)
    if not call("CAM.postProcess", cam.postProcess, setup, ppi):
        raise AdapterError("postProcess returned False")
    wait_for(lambda: bool(os.listdir(folder)), 60, "the posted program")
    files = os.listdir(folder)
    if len(files) != 1:
        raise AdapterError(f"post wrote {len(files)} files: {files}")
    return os.path.join(folder, files[0])


def machining_time(cam, setup, rapid_in_per_min: float = 400.0) -> float:
    return call("getMachiningTime", cam.getMachiningTime, setup, 100, to_cm(rapid_in_per_min), 0).machiningTime
