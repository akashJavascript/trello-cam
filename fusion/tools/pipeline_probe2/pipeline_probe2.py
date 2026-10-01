"""pipeline_probe2: the questions pipeline_probe left open, one step at a time.

UNTESTED IN FUSION. Second version (2026-10-01): the first run answered move vs copy, quantity by extra
occurrences, op renaming and op order; it couldn't move the first component ("Pinned component cannot be
arranged") and set choice parameters wrongly (the values already carry their quotes).
It only works in scratch documents it creates itself and closes them without saving.

Part A, Arrange:
  A11 is the first component grounded? Unground everything, then move (isCreateCopies False)
  A12 fallback: arrange copies, then delete the originals; do the copies survive?
  A13 more parts than fit, with isPartialArrangeAllowed (on the envelope) default and True;
      what unusedComponents and the result envelope's occurrences report
  A14 sheet 1 takes what fits, sheet 2 gets unusedComponents; does sheet 1 stay put?
  A15 a plate with a pocket on top: does Arrange flip it? (face-up True / False / component flipped)
Part B, CAM details (uses your smoke template again):
  B5  set bottomHeight_mode to stock bottom from the API, post, lowest Z (want 0)
  B6  outlines p01-1 and p02-1 with a Manual NC Stop between them and a Manual NC pass-through moved to
      the front with moveBefore; post and show the program

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


def read_attrs(obj, names):
    out = {}
    for n in names:
        try:
            value = getattr(obj, n)
            out[n] = "<method>" if callable(value) else value
        except Exception as e:  # noqa: BLE001
            out[n] = f"{type(e).__name__}: {e}"
    return plain(out)


def items(coll):
    """A Fusion collection (count/item) or a plain list, as a list."""
    if coll is None:
        return []
    if hasattr(coll, "count") and hasattr(coll, "item"):
        return [coll.item(i) for i in range(coll.count)]
    return list(coll)


def occ_name(thing):
    occ = getattr(thing, "occurrence", None) or getattr(thing, "occurrenceOrFace", None) or thing
    return getattr(occ, "name", repr(occ))


def scratch_design(app):
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    try:
        design.fusionUnitsManager.distanceDisplayUnits = adsk.fusion.DistanceUnits.InchDistanceUnits
    except Exception:  # noqa: BLE001
        pass
    return doc, design


def collection(things):
    coll = adsk.core.ObjectCollection.create()
    for t in things:
        coll.add(t)
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


def make_pocket_plate(design, name, w, h, t, depth):
    """A plate with an off-center blind pocket cut down from its top face."""
    occ, body = make_plate(design, name, 0, 0, w, h, t)
    comp = occ.component
    plane_in = comp.constructionPlanes.createInput()
    plane_in.setByOffset(comp.xYConstructionPlane, adsk.core.ValueInput.createByString(f"{t} in"))
    plane = comp.constructionPlanes.add(plane_in)
    sketch = comp.sketches.add(plane)
    sketch.sketchCurves.sketchLines.addTwoPointRectangle(point(w * 0.2, h * 0.2), point(w * 0.6, h * 0.5))
    comp.features.extrudeFeatures.addSimple(sketch.profiles.item(0),
                                            adsk.core.ValueInput.createByString(f"-{depth} in"),
                                            adsk.fusion.FeatureOperations.CutFeatureOperation)
    return occ, body


def more_copies(design, occ, n):
    """n more occurrences of occ's component, all at the same place."""
    root = design.rootComponent
    return [root.occurrences.addExistingComponent(occ.component, adsk.core.Matrix3D.create()) for _ in range(n)]


def ground_info(occs):
    return [{"occurrence": o.name, "isGrounded": getattr(o, "isGrounded", "<missing>"),
             "isGroundToParent": getattr(o, "isGroundToParent", "<missing>")} for o in occs]


def unground(occs):
    changed = []
    for o in occs:
        if getattr(o, "isGrounded", False):
            o.isGrounded = False
            changed.append(o.name)
    return changed


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


def pocket_floor_normal_z(body):
    """Normal z of a planar face strictly between the body's bottom and top (a pocket floor), or None."""
    bb = body.boundingBox
    lo, hi = bb.minPoint.z + 1e-4, bb.maxPoint.z - 1e-4
    for f in body.faces:
        if f.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType:
            _, n = f.evaluator.getNormalAtPoint(f.pointOnFace)
            if abs(n.z) > 0.999 and lo < f.pointOnFace.z < hi:
                return round(n.z, 3)
    return None


def local_z_up(occ):
    try:
        _, _, _, z_axis = occ.transform2.getAsCoordinateSystem()
        return round(z_axis.z, 3)
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"


def snapshot(design):
    """Every occurrence that has bodies: where it is, whether it's upside down, its pocket floor normal."""
    out = []
    for occ in design.rootComponent.allOccurrences:
        bodies = items(occ.bRepBodies)
        if not bodies:
            out.append({"occurrence": occ.name, "empty": True})
            continue
        out.append({"occurrence": occ.name, "local_z_up": local_z_up(occ),
                    "bodies": [{"bbox_in": bbox_in(b.boundingBox), "pocket_floor_normal_z": pocket_floor_normal_z(b)}
                               for b in bodies]})
    return out


def overlaps(snap):
    boxes = [(s["occurrence"], b["bbox_in"]) for s in snap for b in s.get("bodies", [])]
    hits = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i][1], boxes[j][1]
            if a[0] < b[2] - 1e-3 and b[0] < a[2] - 1e-3 and a[1] < b[3] - 1e-3 and b[1] < a[3] - 1e-3:
                hits.append(f"{boxes[i][0]} / {boxes[j][0]}")
    return hits


# ------------------------------------------------------------------ Part A: Arrange

def run_arrange(rec, name, design, occs, env_w, env_h, x_off, y_off, copies=False, partial=None,
                face_up=True, flip=False):
    """One Arrange of the given occurrences into one plane envelope. Returns the feature or FAIL."""
    root = design.rootComponent
    feats = root.features.arrangeFeatures
    arr_in = rec.step(f"{name}: createInput", lambda: feats.createInput(
        adsk.fusion.ArrangeSolverTypes.Arrange2DTrueShapeSolverType))
    if arr_in is FAIL:
        return FAIL
    d = arr_in.definition
    rec.step(f"{name}: definition settings", lambda: [
        set_checked(d, "globalRotation", adsk.fusion.ArrangeRotationTypes.AllRotationsArrangeRotationType),
        set_checked(d, "isGlobalDirectionFaceUp", face_up), set_checked(d, "isCreateCopies", copies)])
    for occ in occs:
        comp = rec.step(f"{name}: add {occ.name}", lambda o=occ: arr_in.arrangeComponents.add(o))
        if comp is not FAIL:
            rec.step(f"{name}: component {occ.name} as added", lambda c=comp: read_attrs(
                c, ("upDirection", "isDirectionFlipped", "rotationType", "rotation", "priority", "zeroDirection")))
            if flip:
                rec.step(f"{name}: component {occ.name} isDirectionFlipped = True",
                         lambda c=comp: set_checked(c, "isDirectionFlipped", True))
    env = rec.step(f"{name}: setPlaneEnvelope({env_w} x {env_h} in at {x_off}, {y_off})",
                   lambda: arr_in.setPlaneEnvelope(root.xYConstructionPlane, vi(env_w), vi(env_h)))
    if env is not FAIL:
        rec.step(f"{name}: envelope offsets + spacing", lambda: [
            set_checked(env, "originXOffset", vi(x_off)), set_checked(env, "originYOffset", vi(y_off)),
            set_checked(env, "objectSpacing", vi(0.25))])
        if partial is not None:
            rec.step(f"{name}: envelope.isPartialArrangeAllowed = {partial}",
                     lambda: set_checked(env, "isPartialArrangeAllowed", partial))
        else:
            rec.step(f"{name}: envelope.isPartialArrangeAllowed (default)", lambda: env.isPartialArrangeAllowed)
    feature = rec.step(f"{name}: arrangeFeatures.add", lambda: feats.add(arr_in))
    if feature is not FAIL:
        rec.step(f"{name}: placed per result envelope",
                 lambda: [[occ_name(r) for r in items(e.occurrences)] for e in items(feature.resultEnvelopes)])
        rec.step(f"{name}: unusedComponents", lambda: [occ_name(c) for c in items(feature.unusedComponents)])
        rec.step(f"{name}: health", lambda: {"healthState": str(feature.healthState),
                                              "message": feature.errorOrWarningMessage})
        stats = rec.step(f"{name}: arrangeStatistics", lambda: feature.arrangeStatistics)
        if stats is not FAIL and stats is not None:
            rec.step(f"{name}: arrangeStatistics values", lambda: read_attrs(stats, public_attrs(type(stats))))
    return feature


def after(rec, name, design):
    snap = rec.step(f"{name}: after", lambda: snapshot(design))
    if snap is not FAIL:
        rec.step(f"{name}: overlapping bodies", lambda: overlaps(snap))
    return snap


def a11_unground(app, rec):
    name = "A11 unground, then move"
    doc, design = scratch_design(app)
    try:
        a, _ = make_plate(design, "plate_a", 0, 0, 3, 2, 0.125, hole=0.5)
        b, _ = make_plate(design, "plate_b", 4, 0, 2, 2, 0.125)
        occs = [a] + more_copies(design, a, 1) + [b]
        rec.step(f"{name}: grounded before", lambda: ground_info(occs))
        rec.step(f"{name}: unground", lambda: unground(occs))
        rec.step(f"{name}: grounded after", lambda: ground_info(occs))
        run_arrange(rec, name, design, occs, 20, 12, 10, 2)
        after(rec, name, design)
    finally:
        doc.close(False)


def a12_copies_then_delete(app, rec):
    name = "A12 copies, then delete the originals"
    doc, design = scratch_design(app)
    try:
        a, _ = make_plate(design, "plate_a", 0, 0, 3, 2, 0.125, hole=0.5)
        b, _ = make_plate(design, "plate_b", 4, 0, 2, 2, 0.125)
        feature = run_arrange(rec, name, design, [a, b], 20, 12, 10, 2, copies=True)
        rec.step(f"{name}: before delete", lambda: snapshot(design))
        rec.step(f"{name}: delete originals", lambda: [a.deleteMe(), b.deleteMe()] and "deleted")
        if feature is not FAIL:
            rec.step(f"{name}: health after delete", lambda: {"healthState": str(feature.healthState),
                                                              "message": feature.errorOrWarningMessage})
        after(rec, name, design)
    finally:
        doc.close(False)


def a13_overflow(app, rec, partial):
    name = f"A13 overflow, 6 x 5x5 in into 12x6 in (partial={partial})"
    doc, design = scratch_design(app)
    try:
        a, _ = make_plate(design, "square", 0, 0, 5, 5, 0.125)
        occs = [a] + more_copies(design, a, 5)
        rec.step(f"{name}: unground", lambda: unground(occs))
        run_arrange(rec, name, design, occs, 12, 6, 10, 2, partial=partial)
        after(rec, name, design)
    finally:
        doc.close(False)


def a14_two_sheets(app, rec):
    name = "A14 sheet 2 gets sheet 1's unused parts"
    doc, design = scratch_design(app)
    try:
        a, _ = make_plate(design, "square", 0, 0, 5, 5, 0.125)
        occs = [a] + more_copies(design, a, 3)
        rec.step(f"{name}: unground", lambda: unground(occs))
        sheet1 = run_arrange(rec, f"{name} (sheet 1)", design, occs, 12, 6, 10, 2, partial=True)
        snap1 = rec.step(f"{name}: after sheet 1", lambda: snapshot(design))
        if sheet1 is FAIL:
            return
        unused = []
        for c in items(sheet1.unusedComponents):
            occ = getattr(c, "occurrence", None) or getattr(c, "occurrenceOrFace", None)
            if occ is not None:
                unused.append(occ)
        rec.note(f"{name}: unused after sheet 1", json.dumps([o.name for o in unused]))
        if not unused:
            return
        run_arrange(rec, f"{name} (sheet 2)", design, unused, 12, 6, 40, 2, partial=True)
        snap2 = after(rec, name, design)
        if snap1 is not FAIL and snap2 is not FAIL:
            def moved():
                before = {s["occurrence"]: s.get("bodies") for s in snap1}
                return [s["occurrence"] for s in snap2 if s["occurrence"] in before
                        and s.get("bodies") != before[s["occurrence"]]]
            rec.step(f"{name}: occurrences that moved during sheet 2", moved)
    finally:
        doc.close(False)


def a15_face_up(app, rec, face_up, flip):
    name = f"A15 pocket plate (faceUp={face_up}, component flipped={flip})"
    doc, design = scratch_design(app)
    try:
        occ, body = make_pocket_plate(design, "pocket_plate", 4, 3, 0.25, 0.1)
        rec.step(f"{name}: unground", lambda: unground([occ]))
        rec.step(f"{name}: before", lambda: snapshot(design))
        run_arrange(rec, name, design, [occ], 20, 12, 10, 2, face_up=face_up, flip=flip)
        rec.step(f"{name}: after (pocket_floor_normal_z 1 = pocket opens up)", lambda: snapshot(design))
    finally:
        doc.close(False)


# ------------------------------------------------------------------ Part B: CAM details

def param(obj, name):
    p = obj.parameters.itemByName(name)
    if p is None:
        raise KeyError(f"no parameter {name}")
    return p


def set_choice(obj, pname, display):
    """Pick a choice by its display name. The values getChoices returns already carry their quotes."""
    p = param(obj, pname)
    _, names, values = p.value.getChoices()
    pick = next((v for n, v in zip(names, values) if n.strip().lower() == display), None)
    if pick is None:
        raise ValueError(f"no '{display}' in {list(names)}")
    p.expression = pick if pick.startswith("'") else f"'{pick}'"
    return p.expression


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
    if os.path.join(REPO, "core") not in sys.path:
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

        t_in = adsk.cam.CreateFromCAMTemplateInput.create()
        t_in.camTemplate = adsk.cam.CAMTemplate.createFromFile(template_path)
        setup.createFromCAMTemplate2(t_in)
        outer = next((op for op in items(setup.allOperations) if "[outer]" in op.name.lower()), None)
        if outer is None:
            rec.note("B: stop", "the template has no [outer] op")
            return
        keep = outer.name
        for op in reversed(items(setup.allOperations)):
            if op.name != keep:
                op.deleteMe()

        # B5: bottom height from the API
        rec.step("B5: bottomHeight_mode = Stock bottom", lambda: set_choice(outer, "bottomHeight_mode", "stock bottom"))
        rec.step("B5: bottomHeight_offset = 0", lambda: setattr(param(outer, "bottomHeight_offset"), "expression", "0 in")
                 or param(outer, "bottomHeight_offset").expression)
        select_top(outer, body1, "OnlyOutsideLoops")
        rec.step("B5: generate", lambda: generate(cam))
        lines = rec.step("B5: post", lambda: post(cam, setup, os.path.join(out_dir, "b5"), "b5"))
        if lines is not FAIL:
            rec.step("B5: lowest Z (want 0)", lambda: lowest_z(lines))

        # B6: outlines with a Manual NC Stop between them, and a pass-through moved to the front
        tpl = adsk.cam.CAMTemplate.createFromOperations([outer])

        def add_outer_copy(new_name, body):
            t = adsk.cam.CreateFromCAMTemplateInput.create()
            t.camTemplate = tpl
            setup.createFromCAMTemplate2(t)
            op = setup.allOperations.item(setup.allOperations.count - 1)
            op.name = new_name
            select_top(op, body, "OnlyOutsideLoops")
            return op

        first = add_outer_copy("[outer] p01-1", body1)
        stop = setup.operations.add(setup.operations.createInput("manual"))
        rec.step("B6: Manual NC manualType = Stop", lambda: set_choice(stop, "manualType", "stop"))
        add_outer_copy("[outer] p02-1", body2)
        passthrough = setup.operations.add(setup.operations.createInput("manual"))
        rec.step("B6: Manual NC manualType = Pass through", lambda: set_choice(passthrough, "manualType", "pass through"))
        for pname in ("comment", "action", "message"):
            rec.step(f"B6: pass-through {pname} = PT_{pname.upper()}",
                     lambda n=pname: setattr(param(passthrough, n).value, "value", f"PT_{n.upper()}")
                     or param(passthrough, n).expression)
        rec.step("B6: moveBefore (pass-through ahead of p01-1)", lambda: passthrough.moveBefore(first))
        rec.step("B6: op order", lambda: op_names(setup))
        outer.isSuppressed = True
        rec.step("B6: generate", lambda: generate(cam))
        lines = rec.step("B6: post", lambda: post(cam, setup, os.path.join(out_dir, "b6"), "b6"))
        if lines is not FAIL:
            rec.step("B6: lines with PT_ markers", lambda: [l for l in lines if "PT_" in l] or "none")
            rec.step("B6: lowest Z (want 0)", lambda: lowest_z(lines))
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

        parts = [("A11", lambda: a11_unground(app, rec)),
                 ("A12", lambda: a12_copies_then_delete(app, rec)),
                 ("A13 partial default", lambda: a13_overflow(app, rec, None)),
                 ("A13 partial True", lambda: a13_overflow(app, rec, True)),
                 ("A14", lambda: a14_two_sheets(app, rec)),
                 ("A15 face up", lambda: a15_face_up(app, rec, True, False)),
                 ("A15 face up off", lambda: a15_face_up(app, rec, False, False)),
                 ("A15 flipped", lambda: a15_face_up(app, rec, True, True))]
        if template_path:
            parts.append(("B", lambda: cam_details(app, ui, rec, out_dir, template_path)))
        else:
            rec.note("B", "skipped: no template picked")
        for label, fn in parts:
            try:
                fn()
            except Exception:  # noqa: BLE001
                rec.note(f"{label}: aborted", traceback.format_exc(limit=4))

        data = {"script": "pipeline_probe2", "version": 2, "fusion_version": app.version, "python": sys.version,
                "template": template_path, "steps": rec.steps}
        with open(os.path.join(out_dir, "probe2.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        ok = sum(1 for s in rec.steps if s.get("ok") is True)
        failed = sum(1 for s in rec.steps if s.get("ok") is False)
        ui.messageBox(f"{ok} steps worked, {failed} failed.\nWrote:\n{os.path.join(out_dir, 'probe2.json')}",
                      "pipeline_probe2")
    except Exception:  # noqa: BLE001
        ui.messageBox("pipeline_probe2 failed:\n" + traceback.format_exc(), "pipeline_probe2")
