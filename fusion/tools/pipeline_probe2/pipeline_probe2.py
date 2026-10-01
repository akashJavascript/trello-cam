"""pipeline_probe2: the Arrange questions still open, one step at a time.

UNTESTED IN FUSION. Version 3 (2026-10-01). Earlier runs settled the CAM side (bottom height from the API,
op copies and renames, Manual NC) and showed that the first component created can't be moved by Arrange:
"Pinned component cannot be arranged". It isn't `isGrounded` (that was already False); that component's
occurrences report `isGroundToParent = True`.
It only works in scratch documents it creates itself and closes them without saving.

  A16 clear isGroundToParent, then move (isCreateCopies False)
  A17 the real path: plates exported as STEP, imported one by one, extra copies added, arranged
  A13 more parts than fit, isPartialArrangeAllowed default and True: what gets placed, what the statistics say,
      and whether unusedComponents can be read after rolling the timeline back
  A14 sheet 1 takes what fits, sheet 2 gets the rest: does sheet 1 stay put?
  A15 a plate with a pocket on top: which way up does it land, and which setting keeps the pocket up?

Output: fusion/tools/pipeline_probe2/out/<time>/probe2.json (+ the STEP files it made)
"""

import json
import os
import sys
import time
import traceback

import adsk.core
import adsk.fusion

HERE = os.path.dirname(os.path.abspath(__file__))
IN = 2.54
FAIL = object()


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


def vec(v):
    return [round(v.x, 3), round(v.y, 3), round(v.z, 3)]


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


def make_plate(design, name, x, y, w, h, t, hole=None):
    """A w x h x t inch plate as its own component. Returns the occurrence."""
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
    comp.features.extrudeFeatures.addSimple(profile, adsk.core.ValueInput.createByString(f"{t} in"),
                                            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    return occ


def make_pocket_plate(design, name, w, h, t, depth):
    """A plate with an off-center blind pocket cut down from its top face."""
    occ = make_plate(design, name, 0, 0, w, h, t)
    comp = occ.component
    plane_in = comp.constructionPlanes.createInput()
    plane_in.setByOffset(comp.xYConstructionPlane, adsk.core.ValueInput.createByString(f"{t} in"))
    sketch = comp.sketches.add(comp.constructionPlanes.add(plane_in))
    sketch.sketchCurves.sketchLines.addTwoPointRectangle(point(w * 0.2, h * 0.2), point(w * 0.6, h * 0.5))
    comp.features.extrudeFeatures.addSimple(sketch.profiles.item(0),
                                            adsk.core.ValueInput.createByString(f"-{depth} in"),
                                            adsk.fusion.FeatureOperations.CutFeatureOperation)
    return occ


def more_copies(design, occ, n):
    """n more occurrences of occ's component, all at the same place."""
    root = design.rootComponent
    return [root.occurrences.addExistingComponent(occ.component, adsk.core.Matrix3D.create()) for _ in range(n)]


def ground_info(occs):
    return [{"occurrence": o.name, "isGrounded": getattr(o, "isGrounded", "<missing>"),
             "isGroundToParent": getattr(o, "isGroundToParent", "<missing>")} for o in occs]


def clear_ground(occs):
    changed = []
    for o in occs:
        for attr in ("isGroundToParent", "isGrounded"):
            if getattr(o, attr, False):
                setattr(o, attr, False)
                changed.append(f"{o.name}.{attr}")
    return changed


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


def stats_summary(feature):
    data = json.loads(feature.arrangeStatistics)
    totals = data.get("statistics", {})
    return {k: totals[k].get("value") for k in ("Number of Components", "Components Arranged",
                                                 "Components Unarranged", "Envelopes Used") if k in totals}


def unused_after_rollback(design, feature):
    out = {}
    for before in (True, False):
        try:
            feature.timelineObject.rollTo(before)
            out[f"rollTo({before})"] = [occ_name(c) for c in items(feature.unusedComponents)]
        except Exception as e:  # noqa: BLE001
            out[f"rollTo({before})"] = f"{type(e).__name__}: {e}"
        finally:
            design.timeline.moveToEnd()
    return out


def run_arrange(rec, name, design, occs, env_w, env_h, x_off, y_off, partial=None, face_up=True,
                component_setup=None):
    """One Arrange (moving, not copying) of the given occurrences into one plane envelope.

    Returns (feature or FAIL, names placed in the envelope)."""
    root = design.rootComponent
    feats = root.features.arrangeFeatures
    arr_in = rec.step(f"{name}: createInput", lambda: feats.createInput(
        adsk.fusion.ArrangeSolverTypes.Arrange2DTrueShapeSolverType))
    if arr_in is FAIL:
        return FAIL, []
    d = arr_in.definition
    rec.step(f"{name}: definition settings", lambda: [
        set_checked(d, "globalRotation", adsk.fusion.ArrangeRotationTypes.AllRotationsArrangeRotationType),
        set_checked(d, "isGlobalDirectionFaceUp", face_up), set_checked(d, "isCreateCopies", False)])
    for occ in occs:
        comp = rec.step(f"{name}: add {occ.name}", lambda o=occ: arr_in.arrangeComponents.add(o))
        if comp is not FAIL:
            rec.step(f"{name}: {occ.name} as added", lambda c=comp: {
                "upDirection": vec(c.upDirection), "zeroDirection": vec(c.zeroDirection),
                "isDirectionFlipped": c.isDirectionFlipped})
            if component_setup:
                label, fn = component_setup
                rec.step(f"{name}: {occ.name} {label}", lambda c=comp: fn(c))
    env = rec.step(f"{name}: setPlaneEnvelope({env_w} x {env_h} in at {x_off}, {y_off})",
                   lambda: arr_in.setPlaneEnvelope(root.xYConstructionPlane, vi(env_w), vi(env_h)))
    if env is not FAIL:
        rec.step(f"{name}: envelope offsets + spacing", lambda: [
            set_checked(env, "originXOffset", vi(x_off)), set_checked(env, "originYOffset", vi(y_off)),
            set_checked(env, "objectSpacing", vi(0.25))])
        if partial is not None:
            rec.step(f"{name}: envelope.isPartialArrangeAllowed = {partial}",
                     lambda: set_checked(env, "isPartialArrangeAllowed", partial))
    feature = rec.step(f"{name}: arrangeFeatures.add", lambda: feats.add(arr_in))
    if feature is FAIL:
        return FAIL, []
    placed = rec.step(f"{name}: placed", lambda: [occ_name(r) for e in items(feature.resultEnvelopes)
                                                  for r in items(e.occurrences)])
    rec.step(f"{name}: statistics", lambda: stats_summary(feature))
    rec.step(f"{name}: health", lambda: {"healthState": str(feature.healthState),
                                          "message": feature.errorOrWarningMessage})
    return feature, ([] if placed is FAIL else placed)


def after(rec, name, design):
    snap = rec.step(f"{name}: after", lambda: snapshot(design))
    if snap is not FAIL:
        rec.step(f"{name}: overlapping bodies", lambda: overlaps(snap) or "none")
    return snap


def a16_ground_to_parent(app, rec):
    name = "A16 clear isGroundToParent, then move"
    doc, design = scratch_design(app)
    try:
        a = make_plate(design, "plate_a", 0, 0, 3, 2, 0.125, hole=0.5)
        b = make_plate(design, "plate_b", 4, 0, 2, 2, 0.125)
        occs = [a] + more_copies(design, a, 1) + [b]
        rec.step(f"{name}: before", lambda: ground_info(occs))
        rec.step(f"{name}: clear", lambda: clear_ground(occs))
        rec.step(f"{name}: after clearing", lambda: ground_info(occs))
        run_arrange(rec, name, design, occs, 20, 12, 10, 2)
        after(rec, name, design)
    finally:
        doc.close(False)


def export_steps(app, rec, folder):
    """Three plates exported as STEP files, the way Onshape exports will arrive."""
    os.makedirs(folder, exist_ok=True)
    doc, design = scratch_design(app)
    try:
        occs = [make_plate(design, "step_a", 0, 0, 3, 2, 0.125, hole=0.5),
                make_plate(design, "step_b", 4, 0, 2, 2, 0.125),
                make_pocket_plate(design, "step_pocket", 4, 3, 0.25, 0.1)]
        paths = []
        for occ in occs:
            path = os.path.join(folder, occ.component.name + ".step")
            em = design.exportManager
            if rec.step(f"A17: export {occ.component.name}.step",
                        lambda p=path, o=occ: em.execute(em.createSTEPExportOptions(p, o.component))) is not FAIL:
                paths.append(path)
        return paths
    finally:
        doc.close(False)


def a17_step_import(app, rec, folder):
    name = "A17 STEP import"
    paths = export_steps(app, rec, folder)
    if not paths:
        return
    doc, design = scratch_design(app)
    try:
        root = design.rootComponent
        parts = []
        for path in paths:
            def do_import(p=path):
                before = {o.entityToken for o in items(root.occurrences)}
                app.importManager.importToTarget(app.importManager.createSTEPImportOptions(p), root)
                return [o for o in items(root.occurrences) if o.entityToken not in before]
            new = rec.step(f"{name}: import {os.path.basename(path)}", do_import)
            if new is not FAIL:
                rec.note(f"{name}: {os.path.basename(path)} made", json.dumps([o.name for o in new]))
                parts += new
        if not parts:
            return
        occs = parts + more_copies(design, parts[0], 1)
        rec.step(f"{name}: ground before", lambda: ground_info(occs))
        rec.step(f"{name}: clear", lambda: clear_ground(occs))
        rec.step(f"{name}: before", lambda: snapshot(design))
        run_arrange(rec, name, design, occs, 20, 12, 10, 2, partial=True)
        after(rec, name, design)
    finally:
        doc.close(False)


def a13_overflow(app, rec, partial):
    name = f"A13 overflow, 6 x 5x5 in into 12x6 in (partial={partial})"
    doc, design = scratch_design(app)
    try:
        a = make_plate(design, "square", 0, 0, 5, 5, 0.125)
        occs = [a] + more_copies(design, a, 5)
        rec.step(f"{name}: clear", lambda: clear_ground(occs))
        feature, _ = run_arrange(rec, name, design, occs, 12, 6, 10, 2, partial=partial)
        if feature is not FAIL:
            rec.step(f"{name}: unusedComponents after rolling back", lambda: unused_after_rollback(design, feature))
        after(rec, name, design)
    finally:
        doc.close(False)


def a14_two_sheets(app, rec):
    name = "A14 sheet 2 gets what sheet 1 couldn't fit"
    doc, design = scratch_design(app)
    try:
        a = make_plate(design, "square", 0, 0, 5, 5, 0.125)
        occs = [a] + more_copies(design, a, 3)
        rec.step(f"{name}: clear", lambda: clear_ground(occs))
        feature, placed = run_arrange(rec, f"{name} (sheet 1)", design, occs, 12, 6, 10, 2, partial=True)
        snap1 = rec.step(f"{name}: after sheet 1", lambda: snapshot(design))
        if feature is FAIL:
            return
        rest = [o for o in occs if o.name not in placed]
        rec.note(f"{name}: left for sheet 2", json.dumps([o.name for o in rest]))
        if not rest:
            return
        run_arrange(rec, f"{name} (sheet 2)", design, rest, 12, 6, 40, 2, partial=True)
        snap2 = after(rec, name, design)
        if snap1 is not FAIL and snap2 is not FAIL:
            def moved():
                before = {s["occurrence"]: s.get("bodies") for s in snap1}
                return [s["occurrence"] for s in snap2 if s["occurrence"] in placed
                        and s.get("bodies") != before.get(s["occurrence"])] or "none"
            rec.step(f"{name}: sheet 1 parts that moved during sheet 2", moved)
    finally:
        doc.close(False)


A15_VARIANTS = [
    ("face-up True", True, None),
    ("face-up False", False, None),
    ("isDirectionFlipped True", True, ("isDirectionFlipped = True", lambda c: set_checked(c, "isDirectionFlipped", True))),
    ("upDirection +Z", True, ("upDirection = (0, 0, 1)",
                              lambda c: setattr(c, "upDirection", adsk.core.Vector3D.create(0, 0, 1)) or vec(c.upDirection))),
    ("upDirection -Z", True, ("upDirection = (0, 0, -1)",
                              lambda c: setattr(c, "upDirection", adsk.core.Vector3D.create(0, 0, -1)) or vec(c.upDirection))),
]


def a15_face_up(app, rec, label, face_up, component_setup):
    name = f"A15 pocket plate, {label}"
    doc, design = scratch_design(app)
    try:
        make_plate(design, "first", 0, 6, 1, 1, 0.125)  # takes the first-component slot
        occ = make_pocket_plate(design, "pocket_plate", 4, 3, 0.25, 0.1)
        rec.step(f"{name}: clear", lambda: clear_ground([occ]))
        run_arrange(rec, name, design, [occ], 20, 12, 10, 2, face_up=face_up, component_setup=component_setup)
        rec.step(f"{name}: after (pocket_floor_normal_z 1 = pocket opens up)",
                 lambda: [s for s in snapshot(design) if s["occurrence"].startswith("pocket_plate")])
    finally:
        doc.close(False)


def run(context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    rec = Recorder()
    out_dir = os.path.join(HERE, "out", time.strftime("%Y%m%d-%H%M%S"))
    try:
        os.makedirs(out_dir, exist_ok=True)
        parts = [("A16", lambda: a16_ground_to_parent(app, rec)),
                 ("A17", lambda: a17_step_import(app, rec, os.path.join(out_dir, "steps"))),
                 ("A13 partial default", lambda: a13_overflow(app, rec, None)),
                 ("A13 partial True", lambda: a13_overflow(app, rec, True)),
                 ("A14", lambda: a14_two_sheets(app, rec))]
        parts += [(f"A15 {label}", lambda v=(label, up, setup): a15_face_up(app, rec, *v))
                  for label, up, setup in A15_VARIANTS]
        for label, fn in parts:
            try:
                fn()
            except Exception:  # noqa: BLE001
                rec.note(f"{label}: aborted", traceback.format_exc(limit=4))

        data = {"script": "pipeline_probe2", "version": 3, "fusion_version": app.version, "python": sys.version,
                "steps": rec.steps}
        with open(os.path.join(out_dir, "probe2.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        ok = sum(1 for s in rec.steps if s.get("ok") is True)
        failed = sum(1 for s in rec.steps if s.get("ok") is False)
        ui.messageBox(f"Version 3: {ok} steps worked, {failed} failed.\nWrote:\n{os.path.join(out_dir, 'probe2.json')}",
                      "pipeline_probe2 v3")
    except Exception:  # noqa: BLE001
        ui.messageBox("pipeline_probe2 v3 failed:\n" + traceback.format_exc(), "pipeline_probe2 v3")
