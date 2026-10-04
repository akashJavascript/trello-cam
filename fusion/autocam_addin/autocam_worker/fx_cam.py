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
    "gouge_check": "BRepBody.pointContainment on the posted program's cutting points",
    "team_save": "Document.saveAs into the Fusion Team folder, wait for Application.dataFileComplete, then DataFile.fusionWebURL",
    "tabs": "contour op tabs: group_tabs, tabPositioning = 'tabCount', tabsPerContour",
    "tab_points": "contour op tabs at points: tabPositioning 'distance' 1000 in, tabPositions = sketch points",
    "op_copy": "a copy of the template's [inner] op (CAMTemplate.createFromOperations) for tabbed cutouts",
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
    p.expression = f"{inches:g} in"
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
    """Cut a single loop on its inside. Fusion picks the cut side of a chain from the chain's direction, and a
    chain built from edges runs the way its first edge happens to point.

    Seen in Fusion 2705.1.15 (2026-10-01; ChainSelection has no sideType there):
    - not reverted at all: round cutouts outside, triangles inside (edge directions vary);
    - isReverted = first co-edge isOpposedToEdge (chain runs the loop's way): every cutout outside;
    - so: isReverted = not isOpposedToEdge (chain runs against the loop's way).
    The posted program is still tested against the part bodies (pipeline), so a wrong side is never offered."""
    sides = getattr(adsk.cam, "SideTypes", None)
    if sides is not None and hasattr(type(chain), "sideType") and hasattr(sides, "AlwaysInsideSideType"):
        chain.sideType = sides.AlwaysInsideSideType
        used("chain_side_type")
        return
    if hasattr(type(chain), "isReverted"):
        chain.isReverted = not loop.coEdges.item(0).isOpposedToEdge
        used("chain_direction")


def select_loops(op, whole_faces, single_loops, contour_param: str, used=lambda key: None) -> None:
    """whole_faces: faces whose inner loops are all cut; single_loops: BRepLoops cut on their own."""
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
        select_face_loops(op, [face], adsk.cam.LoopTypes.OnlyOutsideLoops, contour_param)
    template_op.deleteMe()


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
