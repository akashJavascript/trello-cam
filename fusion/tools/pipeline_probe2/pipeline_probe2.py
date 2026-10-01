"""pipeline_probe2: the questions pipeline_probe left open, one step at a time.

UNTESTED IN FUSION: written from the pipeline_probe results (2026-10-01).
It only works in scratch documents it creates itself and closes them without saving.

Part A, Arrange (adding occurrences, the way that worked):
  A0  every public attribute of the Arrange classes, so no option is guessed
  A7  does Arrange move the occurrences or copy them? (isCreateCopies False, then True)
  A8  quantity without component.quantity: 3 occurrences of one component + 2 of another
  A9  more parts than fit: what happens to the ones that don't (with and without partial arrange)
  A10 two Arranges in a row (sheet 1, then sheet 2 further along X): does the second move the first?
Part B, CAM details (uses your smoke template again):
  B1  bottom height: list the choices, set 'stock bottom' from the API, post, read the lowest Z
  B2  op rename: copy the outline op, rename it '[outer] p01-1', check the posted comment line
  B3  Manual NC: list its parameters and choices, make it a Stop between two outlines, post, find the M0
  B4  op order: are new ops (template copies, Manual NC) always added at the end of the setup?

Output: fusion/tools/pipeline_probe2/out/<time>/probe2.json (+ posted .tap files)
"""

import json
import os
import sys
import time
import traceback

import adsk.cam
import adsk.core
import adsk.fusion

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
IN = 2.54
FAIL = object()
POST_FILE = os.path.join(REPO, "fusion", "posts", "shopsabre_automatic_mist.cps")


class Recorder:
    def __init__(self):
        self.steps = []

    def step(self, name, fn):
        t0 = time.time()
        try:
            value = fn()
        except Exception as e:  # noqa: BLE001 - record and keep going
            self.steps.append({"step": name, "ok": False, "error": f"{type(e).__name__}: {e}",
                               "trace": traceback.format_exc(limit=4)})
            return FAIL
        self.steps.append({"step": name, "ok": True, "result": plain(value), "s": round(time.time() - t0, 2)})
        return value

    def note(self, name, text):
        self.steps.append({"step": name, "ok": None, "note": text})


def plain(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    return f"<{type(value).__name__}>"


def vi(inches):
    return adsk.core.ValueInput.createByString(f"{inches:.5f} in")


def point(x_in, y_in):
    return adsk.core.Point3D.create(x_in * IN, y_in * IN, 0)


def set_checked(obj, attr, value):
    if not hasattr(type(obj), attr):
        raise AttributeError(f"{type(obj).__name__} has no attribute {attr}")
    setattr(obj, attr, value)
    return repr(getattr(obj, attr))


def public_attrs(cls):
    return sorted(n for n in dir(cls) if not n.startswith("_") and n not in ("cast", "classType", "this", "thisown"))


def scratch_design(app):
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    try:
        design.fusionUnitsManager.distanceDisplayUnits = adsk.fusion.DistanceUnits.InchDistanceUnits
    except Exception:  # noqa: BLE001
        pass
    return doc, design


def collection(items):
    coll = adsk.core.ObjectCollection.create()
    for item in items:
        coll.add(item)
    return coll


def make_plate(design, name, x, y, w, h, t, hole=None):
    """A w x h x t inch plate as its own component. Returns (occurrence, body proxy)."""
    root = design.rootComponent
    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    comp = occ.component
    comp.name = name
    sketch = comp.sketches.add(comp.xYConstructionPlane)
    sketch.sketchCurves.sketchLines.addTwoPointRectangle(point(x, y), point(x + w, y + h))
    if hole:
        sketch.sketchCurves.sketchCircles.addByCenterRadius(point(x + w / 2, y + h / 2), hole / 2 * IN)
    profiles = [sketch.profiles.item(i) for i in range(sketch.profiles.count)]
    profile = max(profiles, key=lambda p: p.areaProperties().area)
    ext = comp.features.extrudeFeatures.addSimple(profile, adsk.core.ValueInput.createByString(f"{t} in"),
                                                  adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    return occ, ext.bodies.item(0).createForAssemblyContext(occ)


def more_copies(design, occ, n):
    """n more occurrences of occ's component, all at the same place."""
    root = design.rootComponent
    return [root.occurrences.addExistingComponent(occ.component, adsk.core.Matrix3D.create()) for _ in range(n)]


def wait_for(done, timeout_s):
    t0 = time.time()
    while not done():
        adsk.doEvents()
        time.sleep(0.1)
        if time.time() - t0 > timeout_s:
            raise TimeoutError(f"still not done after {timeout_s} s")
    return round(time.time() - t0, 1)


def bbox_in(bb):
    return [round(v / IN, 3) for v in (bb.minPoint.x, bb.minPoint.y, bb.maxPoint.x, bb.maxPoint.y)]


def top_normal_z(body):
    best = None
    for f in body.faces:
        if f.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType:
            _, n = f.evaluator.getNormalAtPoint(f.pointOnFace)
            if abs(n.z) > 0.999 and (best is None or f.pointOnFace.z > best[0]):
                best = (f.pointOnFace.z, n.z)
    return None if best is None else round(best[1], 3)


def snapshot(design):
    """Every occurrence: name, translation, body boxes and which way its top face points."""
    out = []
    for occ in design.rootComponent.allOccurrences:
        t = occ.transform2.translation if hasattr(occ, "transform2") else occ.transform.translation
        out.append({
            "occurrence": occ.name, "component": occ.component.name,
            "translation_in": [round(t.x / IN, 3), round(t.y / IN, 3), round(t.z / IN, 3)],
            "bodies": [{"bbox_in": bbox_in(b.boundingBox), "top_normal_z": top_normal_z(b)} for b in occ.bRepBodies],
        })
    out.append({"root bodies": [bbox_in(b.boundingBox) for b in design.rootComponent.bRepBodies]})
    return out


def overlaps(snap):
    boxes = [(s["occurrence"], b["bbox_in"]) for s in snap if "bodies" in s for b in s["bodies"]]
    hits = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i][1], boxes[j][1]
            if a[0] < b[2] - 1e-3 and b[0] < a[2] - 1e-3 and a[1] < b[3] - 1e-3 and b[1] < a[3] - 1e-3:
                hits.append([boxes[i][0], boxes[j][0]])
    return hits


# ------------------------------------------------------------------ Part A: Arrange

def arrange_classes(rec):
    names = sorted(n for n in dir(adsk.fusion) if "Arrange" in n and isinstance(getattr(adsk.fusion, n), type))
    rec.step("A0: Arrange classes and their attributes",
             lambda: {n: public_attrs(getattr(adsk.fusion, n)) for n in names})


def run_arrange(rec, name, design, occs, env_w, env_h, x_off, y_off, copies=None, partial=None):
    """One Arrange of the given occurrences into one plane envelope. Returns the feature or FAIL."""
    root = design.rootComponent
    feats = root.features.arrangeFeatures
    arr_in = rec.step(f"{name}: createInput", lambda: feats.createInput(
        adsk.fusion.ArrangeSolverTypes.Arrange2DTrueShapeSolverType))
    if arr_in is FAIL:
        return FAIL
    d = arr_in.definition
    rec.step(f"{name}: definition.globalRotation = all",
             lambda: set_checked(d, "globalRotation", adsk.fusion.ArrangeRotationTypes.AllRotationsArrangeRotationType))
    rec.step(f"{name}: definition.isGlobalDirectionFaceUp", lambda: set_checked(d, "isGlobalDirectionFaceUp", True))
    if copies is not None:
        rec.step(f"{name}: definition.isCreateCopies = {copies}", lambda: set_checked(d, "isCreateCopies", copies))
    if partial is not None:
        for owner, label in ((arr_in, "input"), (d, "definition")):
            if hasattr(type(owner), "isPartialArrangeAllowed"):
                rec.step(f"{name}: {label}.isPartialArrangeAllowed = {partial}",
                         lambda o=owner: set_checked(o, "isPartialArrangeAllowed", partial))
                break
        else:
            rec.note(f"{name}: isPartialArrangeAllowed", "not found on the input or the definition")
    for occ in occs:
        rec.step(f"{name}: add {occ.name}", lambda o=occ: arr_in.arrangeComponents.add(o))
    env = rec.step(f"{name}: setPlaneEnvelope({env_w} x {env_h} in)",
                   lambda: arr_in.setPlaneEnvelope(root.xYConstructionPlane, vi(env_w), vi(env_h)))
    if env is not FAIL:
        rec.step(f"{name}: envelope attributes", lambda: public_attrs(type(env)))
        rec.step(f"{name}: envelope.originXOffset = {x_off}", lambda: set_checked(env, "originXOffset", vi(x_off)))
        rec.step(f"{name}: envelope.originYOffset = {y_off}", lambda: set_checked(env, "originYOffset", vi(y_off)))
        rec.step(f"{name}: envelope.objectSpacing = 0.25", lambda: set_checked(env, "objectSpacing", vi(0.25)))
    feature = rec.step(f"{name}: arrangeFeatures.add", lambda: feats.add(arr_in))
    if feature is not FAIL:
        def envelopes():
            envs = feature.resultEnvelopes
            return [bbox_in(envs.item(i).boundingBox) for i in range(envs.count)]
        rec.step(f"{name}: resultEnvelopes", envelopes)
        rec.step(f"{name}: feature attributes", lambda: public_attrs(type(feature)))
    return feature


def a7_move_or_copy(app, rec, copies):
    name = f"A7 move or copy (isCreateCopies={copies})"
    doc, design = scratch_design(app)
    try:
        a, _ = make_plate(design, "plate_a", 0, 0, 3, 2, 0.125, hole=0.5)
        b, _ = make_plate(design, "plate_b", 4, 0, 2, 2, 0.125)
        rec.step(f"{name}: before", lambda: snapshot(design))
        run_arrange(rec, name, design, [a, b], 20, 12, 10, 2, copies=copies)
        rec.step(f"{name}: after", lambda: snapshot(design))
    finally:
        doc.close(False)


def a8_quantity(app, rec):
    name = "A8 qty by extra occurrences (3 x plate_a, 2 x plate_b)"
    doc, design = scratch_design(app)
    try:
        a, _ = make_plate(design, "plate_a", 0, 0, 3, 2, 0.125, hole=0.5)
        b, _ = make_plate(design, "plate_b", 4, 0, 2, 2, 0.125)
        occs = rec.step(f"{name}: addExistingComponent copies", lambda: [a] + more_copies(design, a, 2) + [b] + more_copies(design, b, 1))
        if occs is FAIL:
            return
        run_arrange(rec, name, design, occs, 20, 12, 10, 2, copies=False)
        snap = rec.step(f"{name}: after", lambda: snapshot(design))
        if snap is not FAIL:
            rec.step(f"{name}: overlapping bodies", lambda: overlaps(snap))
    finally:
        doc.close(False)


def a9_overflow(app, rec, partial):
    name = f"A9 overflow, 6 x 5x5 in into 12x6 in (partial={partial})"
    doc, design = scratch_design(app)
    try:
        a, _ = make_plate(design, "square", 0, 0, 5, 5, 0.125)
        occs = [a] + more_copies(design, a, 5)
        run_arrange(rec, name, design, occs, 12, 6, 10, 2, copies=False, partial=partial)
        snap = rec.step(f"{name}: after", lambda: snapshot(design))
        if snap is not FAIL:
            rec.step(f"{name}: overlapping bodies", lambda: overlaps(snap))
    finally:
        doc.close(False)


def a10_two_sheets(app, rec):
    name = "A10 two Arranges in a row"
    doc, design = scratch_design(app)
    try:
        a, _ = make_plate(design, "square", 0, 0, 5, 5, 0.125)
        occs = [a] + more_copies(design, a, 3)
        run_arrange(rec, f"{name} (sheet 1)", design, occs[:2], 12, 6, 10, 2, copies=False)
        rec.step(f"{name}: after sheet 1", lambda: snapshot(design))
        run_arrange(rec, f"{name} (sheet 2)", design, occs[2:], 12, 6, 40, 2, copies=False)
        snap = rec.step(f"{name}: after sheet 2", lambda: snapshot(design))
        if snap is not FAIL:
            rec.step(f"{name}: overlapping bodies", lambda: overlaps(snap))
    finally:
        doc.close(False)


# ------------------------------------------------------------------ Part B: CAM details

def param(obj, name):
    p = obj.parameters.itemByName(name)
    if p is None:
        raise KeyError(f"no parameter {name}")
    return p


def describe_params(obj, only=None):
    out = []
    prms = obj.parameters
    for i in range(prms.count):
        p = prms.item(i)
        if only and not any(s in p.name.lower() for s in only):
            continue
        info = {"name": p.name, "title": getattr(p, "title", None), "expression": p.expression,
                "value_type": type(p.value).__name__}
        if hasattr(p.value, "getChoices"):
            try:
                info["choices"] = repr(p.value.getChoices())[:600]
            except Exception as e:  # noqa: BLE001
                info["choices"] = f"{type(e).__name__}: {e}"
        out.append(info)
    return out


def post(cam, setup, folder, name):
    os.makedirs(folder, exist_ok=True)
    ppi = adsk.cam.PostProcessInput.create(name, POST_FILE, folder, adsk.cam.PostOutputUnitOptions.InchesOutput)
    ppi.isOpenInEditor = False
    if not cam.postProcess(setup, ppi):
        raise RuntimeError("postProcess returned False")
    path = os.path.join(folder, name + ".tap")
    wait_for(lambda: os.path.exists(path), 60)
    with open(path, "rb") as f:
        return f.read().decode("ascii", errors="replace").splitlines()


def lowest_z(lines):
    sys.path.insert(0, os.path.join(REPO, "core"))
    from autocam_core.tapguard import parse_code
    zs = []
    for line in lines:
        try:
            words = parse_code(line)
        except ValueError:
            continue
        if ("G", 53.0) not in words:
            zs += [v for l, v in words if l == "Z" and v is not None]
    return min(zs) if zs else None


def select_top(op, body, loop):
    top = None
    for f in body.faces:
        if f.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType:
            _, n = f.evaluator.getNormalAtPoint(f.pointOnFace)
            if n.z > 0.999 and (top is None or f.pointOnFace.z > top.pointOnFace.z):
                top = f
    value = adsk.cam.CadContours2dParameterValue.cast(param(op, "contours").value)
    sels = value.getCurveSelections()
    sels.clear()
    fc = sels.createNewFaceContourSelection()
    fc.loopType = getattr(adsk.cam.LoopTypes, loop)
    fc.isSelectingSamePlaneFaces = False
    fc.inputGeometry = [top]
    value.applyCurveSelections(sels)


def generate(cam):
    future = cam.generateAllToolpaths(False)
    return wait_for(lambda: future.isGenerationCompleted, 300)


def op_names(setup):
    return [setup.allOperations.item(i).name for i in range(setup.allOperations.count)]


def cam_details(app, ui, rec, out_dir, template_path):
    doc, design = scratch_design(app)
    try:
        _, body1 = make_plate(design, "plate_1", 1, 1, 4, 3, 0.125, hole=0.5)
        _, body2 = make_plate(design, "plate_2", 7, 1, 4, 3, 0.125)
        _, stock = make_plate(design, "stock", 0, 0, 12, 5, 0.125)
        stock.isLightBulbOn = False
        ui.workspaces.itemById("CAMEnvironment").activate()
        cam = adsk.cam.CAM.cast(doc.products.itemByProductType("CAMProductType"))
        si = cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation)
        si.models = [body1, body2]
        setup = cam.setups.add(si)
        setup.stockMode = adsk.cam.SetupStockModes.SolidStock
        setup.stockSolids = collection([stock])
        param(setup, "wcs_origin_mode").expression = "'stockPoint'"
        param(setup, "wcs_origin_boxPoint").expression = "'bottom 1'"

        template = adsk.cam.CAMTemplate.createFromFile(template_path)
        t_in = adsk.cam.CreateFromCAMTemplateInput.create()
        t_in.camTemplate = template
        setup.createFromCAMTemplate2(t_in)
        rec.step("B0: ops from the smoke template", lambda: op_names(setup))
        outer = next((setup.allOperations.item(i) for i in range(setup.allOperations.count)
                      if "[outer]" in setup.allOperations.item(i).name.lower()), None)
        if outer is None:
            rec.note("B: stop", "the template has no [outer] op")
            return
        keep = outer.name
        for i in range(setup.allOperations.count - 1, -1, -1):
            op = setup.allOperations.item(i)
            if op.name != keep:
                op.deleteMe()
        rec.step("B0: kept only the outline op", lambda: op_names(setup))

        # B1: bottom height
        rec.step("B1: outline op height parameters",
                 lambda: describe_params(outer, only=("bottomheight", "topheight", "clearanceheight")))

        def set_stock_bottom():
            p = param(outer, "bottomHeight_mode")
            ok, names, values = p.value.getChoices()
            pick = next((v for n, v in zip(names, values) if "stock bottom" in (n + " " + v).lower()), None)
            if pick is None:
                raise ValueError(f"no stock-bottom choice in {list(zip(names, values))}")
            p.expression = f"'{pick}'"
            param(outer, "bottomHeight_offset").expression = "0 in"
            return {"bottomHeight_mode": p.expression, "bottomHeight_offset": param(outer, "bottomHeight_offset").expression}
        rec.step("B1: set bottomHeight_mode to stock bottom, offset 0", set_stock_bottom)
        select_top(outer, body1, "OnlyOutsideLoops")
        rec.step("B1: generate", lambda: generate(cam))
        lines = rec.step("B1: post", lambda: post(cam, setup, os.path.join(out_dir, "b1"), "b1"))
        if lines is not FAIL:
            rec.step("B1: lowest Z (want 0)", lambda: lowest_z(lines))

        # B2 + B4: copy the outline op, rename it, check order and the posted comment
        tpl = adsk.cam.CAMTemplate.createFromOperations([outer])

        def add_outer_copy(new_name, body):
            t = adsk.cam.CreateFromCAMTemplateInput.create()
            t.camTemplate = tpl
            setup.createFromCAMTemplate2(t)
            op = setup.allOperations.item(setup.allOperations.count - 1)
            op.name = new_name
            select_top(op, body, "OnlyOutsideLoops")
            return op
        rec.step("B2: copy + rename '[outer] p01-1'", lambda: add_outer_copy("[outer] p01-1", body1).name)

        # B3: Manual NC
        manual = rec.step("B3: Manual NC via createInput('manual')",
                          lambda: setup.operations.add(setup.operations.createInput("manual")))
        if manual is not FAIL:
            rec.step("B3: Manual NC parameters", lambda: describe_params(manual))
            rec.step("B3: Manual NC attributes", lambda: public_attrs(type(manual)))

            def make_stop():
                prms = [manual.parameters.item(i) for i in range(manual.parameters.count)]
                prms.sort(key=lambda p: 0 if ("type" in p.name.lower() or "manual" in p.name.lower()) else 1)
                for p in prms:
                    if hasattr(p.value, "getChoices"):
                        ok, names, values = p.value.getChoices()
                        pick = next((v for n, v in zip(names, values) if "stop" in (n + " " + v).lower()
                                     and "optional" not in (n + " " + v).lower()), None)
                        if pick is not None:
                            p.expression = f"'{pick}'"
                            return {p.name: p.expression}
                raise ValueError("no choice parameter offers a stop")
            rec.step("B3: set the Manual NC type to Stop", make_stop)
        rec.step("B2: copy + rename '[outer] p02-1'", lambda: add_outer_copy("[outer] p02-1", body2).name)
        rec.step("B4: op order (want outline, p01-1, Manual NC, p02-1)", lambda: op_names(setup))
        outer.isSuppressed = True
        rec.step("B2/B3: generate", lambda: generate(cam))
        lines = rec.step("B2/B3: post", lambda: post(cam, setup, os.path.join(out_dir, "b3"), "b3"))
        if lines is not FAIL:
            rec.step("B2: comment lines in the program", lambda: [l for l in lines if l.startswith("[")])

            def around_m0():
                hits = [i for i, l in enumerate(lines) if l.strip().split(" ")[0] == "M0"]
                return [lines[max(0, i - 6): i + 6] for i in hits] or "no M0 line"
            rec.step("B3: lines around each M0", around_m0)
            rec.step("B2/B3: lowest Z", lambda: lowest_z(lines))
    finally:
        doc.close(False)


def run(context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    rec = Recorder()
    out_dir = os.path.join(HERE, "out", time.strftime("%Y%m%d-%H%M%S"))
    try:
        os.makedirs(out_dir, exist_ok=True)
        dlg = ui.createFileDialog()
        dlg.title = "Pick your smoke template (.f3dhsm-template), or Cancel to skip Part B"
        dlg.filter = "CAM template (*.f3dhsm-template)"
        template_path = dlg.filename if dlg.showOpen() == adsk.core.DialogResults.DialogOK else None

        parts = [("A0", lambda: arrange_classes(rec)),
                 ("A7 copies False", lambda: a7_move_or_copy(app, rec, False)),
                 ("A7 copies True", lambda: a7_move_or_copy(app, rec, True)),
                 ("A8", lambda: a8_quantity(app, rec)),
                 ("A9 partial default", lambda: a9_overflow(app, rec, None)),
                 ("A9 partial True", lambda: a9_overflow(app, rec, True)),
                 ("A10", lambda: a10_two_sheets(app, rec))]
        if template_path:
            parts.append(("B", lambda: cam_details(app, ui, rec, out_dir, template_path)))
        else:
            rec.note("B", "skipped: no template picked")
        for label, fn in parts:
            try:
                fn()
            except Exception:  # noqa: BLE001
                rec.note(f"{label}: aborted", traceback.format_exc(limit=4))

        data = {"script": "pipeline_probe2", "fusion_version": app.version, "python": sys.version,
                "template": template_path, "steps": rec.steps}
        with open(os.path.join(out_dir, "probe2.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        ok = sum(1 for s in rec.steps if s.get("ok") is True)
        failed = sum(1 for s in rec.steps if s.get("ok") is False)
        ui.messageBox(f"{ok} steps worked, {failed} failed.\nWrote:\n{os.path.join(out_dir, 'probe2.json')}",
                      "pipeline_probe2")
    except Exception:  # noqa: BLE001
        ui.messageBox("pipeline_probe2 failed:\n" + traceback.format_exc(), "pipeline_probe2")
