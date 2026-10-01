"""pipeline_probe: try every Fusion API call the pipeline needs, one step at a time, and record what works.

UNTESTED IN FUSION: written after the Phase 1 smoke test failed at Arrange (2026-10-01).
It only works in scratch documents it creates itself and closes them without saving.

Part A tries Arrange several ways on two small boxes (face vs occurrence, settings, envelope order,
quantity). Part B runs the CAM chain on one test plate: setup, stock from a solid, work origin, your
smoke template, contour selections, toolpaths, posting (library post and pinned post by path), the
Z-floor guard on the posted .tap, Manual NC, op copies, machining time, .f3d export and a preview.

Every step is recorded on its own, so one failure doesn't hide the rest. Output:
    fusion/tools/pipeline_probe/out/<time>/probe.json  (+ the posted .tap files, preview, .f3d)
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
POST_DESCRIPTION = "ShopSabre with automatic mist"
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


def scratch_design(app):
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    try:  # programs must post in inches; new documents may default to mm
        design.fusionUnitsManager.distanceDisplayUnits = adsk.fusion.DistanceUnits.InchDistanceUnits
    except Exception:  # noqa: BLE001 - recorded later by the CAM steps if it matters
        pass
    return doc, design


def collection(items):
    coll = adsk.core.ObjectCollection.create()
    for item in items:
        coll.add(item)
    return coll


def make_plate(design, name, x, y, w, h, t, hole=None):
    """A w x h x t inch plate as its own component (like an imported STEP part). Returns (occ, body proxy)."""
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


def planar_up_faces(body):
    faces = []
    for f in body.faces:
        if f.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType:
            _, n = f.evaluator.getNormalAtPoint(f.pointOnFace)
            if n.z > 0.999:
                faces.append(f)
    return faces


def top_face(body):
    faces = planar_up_faces(body)
    return max(faces, key=lambda f: f.pointOnFace.z) if faces else None


def largest_planar_face(body):
    faces = [f for f in body.faces if f.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType]
    return max(faces, key=lambda f: f.area) if faces else None


def wait_for(done, timeout_s):
    t0 = time.time()
    while not done():
        adsk.doEvents()
        time.sleep(0.1)
        if time.time() - t0 > timeout_s:
            raise TimeoutError(f"still not done after {timeout_s} s")
    return round(time.time() - t0, 1)


# ------------------------------------------------------------------ Part A: Arrange

ARRANGE_VARIANTS = [
    # name, what to add, apply Phase 1 definition settings, envelope before components, quantity
    ("A1 occurrence, defaults", "occ", False, False, 1),
    ("A2 face, defaults", "face", False, False, 1),
    ("A3 occurrence, Phase 1 settings", "occ", True, False, 1),
    ("A4 face, Phase 1 settings", "face", True, False, 1),
    ("A5 face, envelope first", "face", False, True, 1),
    ("A6 occurrence, qty 3, copies", "occ", True, False, 3),
]


def describe_arrange(design, feature):
    out = {"envelopes": [], "bodies": []}
    envs = feature.resultEnvelopes
    for i in range(envs.count):
        env = envs.item(i)
        cast = getattr(adsk.fusion, "ArrangePlaneResultEnvelope", None)
        env = cast.cast(env) if cast else env
        bb = env.boundingBox
        out["envelopes"].append([round(v / IN, 3) for v in (bb.minPoint.x, bb.minPoint.y, bb.maxPoint.x, bb.maxPoint.y)])
    for occ in design.rootComponent.allOccurrences:
        for b in occ.bRepBodies:
            bb = b.boundingBox
            out["bodies"].append({"component": occ.component.name,
                                  "center_in": [round((bb.minPoint.x + bb.maxPoint.x) / 2 / IN, 3),
                                                round((bb.minPoint.y + bb.maxPoint.y) / 2 / IN, 3)]})
    return out


def arrange_variant(app, rec, name, target, settings, envelope_first, qty):
    doc, design = scratch_design(app)
    try:
        root = design.rootComponent
        parts = [make_plate(design, "plate_a", 0, 0, 3, 2, 0.125, hole=0.5),
                 make_plate(design, "plate_b", 4, 0, 2, 2, 0.125)]
        feats = root.features.arrangeFeatures
        solver = adsk.fusion.ArrangeSolverTypes.Arrange2DTrueShapeSolverType
        arr_in = rec.step(f"{name}: createInput(Arrange2DTrueShapeSolverType)", lambda: feats.createInput(solver))
        if arr_in is FAIL:
            return

        def envelope():
            env = rec.step(f"{name}: setPlaneEnvelope(XY, 20 in, 12 in)",
                           lambda: arr_in.setPlaneEnvelope(root.xYConstructionPlane, vi(20), vi(12)))
            if env is FAIL:
                return
            rec.step(f"{name}: envelope.originXOffset", lambda: set_checked(env, "originXOffset", vi(10)))
            rec.step(f"{name}: envelope.objectSpacing", lambda: set_checked(env, "objectSpacing", vi(0.25)))
            rec.step(f"{name}: envelope.quantity", lambda: set_checked(env, "quantity", adsk.core.ValueInput.createByReal(2)))
            rec.step(f"{name}: envelope.envelopeSpacing", lambda: set_checked(env, "envelopeSpacing", vi(6)))

        if envelope_first:
            envelope()
        if settings:
            d = arr_in.definition
            rec.step(f"{name}: definition type", lambda: f"{type(d).__name__} / {d.objectType}")
            for attr, value in (("globalRotation", adsk.fusion.ArrangeRotationTypes.AllRotationsArrangeRotationType),
                                ("isGlobalDirectionFaceUp", True), ("isPartInPartAllowed", False),
                                ("isCreateCopies", True)):
                rec.step(f"{name}: definition.{attr}", lambda a=attr, v=value: set_checked(d, a, v))
        for occ, body in parts:
            item = occ if target == "occ" else largest_planar_face(body)
            comp = rec.step(f"{name}: arrangeComponents.add({target}) for {occ.component.name}",
                            lambda i=item: arr_in.arrangeComponents.add(i))
            if comp is not FAIL and qty > 1:
                rec.step(f"{name}: component.quantity = {qty}", lambda c=comp: set_checked(c, "quantity", qty))
        if not envelope_first:
            envelope()
        feature = rec.step(f"{name}: arrangeFeatures.add", lambda: feats.add(arr_in))
        if feature is not FAIL:
            rec.step(f"{name}: result", lambda: describe_arrange(design, feature))
    finally:
        doc.close(False)


# ------------------------------------------------------------------ Part B: CAM chain

def find_post(rec):
    lib = adsk.cam.CAMManager.get().libraryManager.postLibrary
    found = []

    def walk(url, depth):
        for child in lib.childAssetURLs(url):
            cfg = lib.postConfigurationAtURL(child)
            if cfg and POST_DESCRIPTION.lower() in (cfg.description or "").lower():
                found.append((cfg, child.toString()))
        if depth > 0:
            for sub in lib.childFolderURLs(url):
                walk(sub, depth - 1)

    for loc_name in ("LocalLibraryLocation", "CloudLibraryLocation"):
        loc = getattr(adsk.cam.LibraryLocations, loc_name)
        rec.step(f"B: post library walk ({loc_name})", lambda l=loc: walk(lib.urlByLocation(l), 2) or len(found))
    return found


def cam_chain(app, ui, rec, out_dir, template_path):
    doc, design = scratch_design(app)
    try:
        occ, body = make_plate(design, "probe_plate", 1, 1, 6, 4, 0.125, hole=0.5)
        _, stock_body = make_plate(design, "probe_stock", 0, 0, 8, 6, 0.125)
        rec.step("B: hide stock body", lambda: set_checked(stock_body, "isLightBulbOn", False))

        rec.step("B: activate Manufacture", lambda: ui.workspaces.itemById("CAMEnvironment").activate() or "ok")
        cam = rec.step("B: get CAM product",
                       lambda: adsk.cam.CAM.cast(doc.products.itemByProductType("CAMProductType")))
        if cam is FAIL or cam is None:
            rec.note("B: stop", "no CAM product")
            return

        def param(obj, name):
            p = obj.parameters.itemByName(name)
            if p is None:
                raise KeyError(f"no parameter {name}")
            return p

        def new_setup(models):
            si = cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation)
            si.models = models
            return cam.setups.add(si)

        setup = rec.step("B: setups.add (models = [plate] as a list)", lambda: new_setup([body]))
        if setup is FAIL:
            setup = rec.step("B: setups.add (models as ObjectCollection)", lambda: new_setup(collection([body])))
        if setup is FAIL:
            return
        rec.step("B: Setup.stockMode = SolidStock",
                 lambda: set_checked(setup, "stockMode", adsk.cam.SetupStockModes.SolidStock))
        solids = rec.step("B: Setup.stockSolids = [stock body] (list)",
                          lambda: set_checked(setup, "stockSolids", [stock_body]))
        if solids is FAIL:
            solids = rec.step("B: Setup.stockSolids = ObjectCollection",
                              lambda: set_checked(setup, "stockSolids", collection([stock_body])))
        if solids is FAIL:
            def stock_by_params():
                param(setup, "job_stockMode").expression = "'solid'"
                param(setup, "job_stockSolid").value.value = [stock_body]
                return param(setup, "job_stockMode").expression
            rec.step("B: stock from solid via job_stockMode / job_stockSolid params", stock_by_params)

        for pname, expr in (("wcs_origin_mode", "'stockPoint'"), ("wcs_origin_boxPoint", "'bottom 1'")):
            rec.step(f"B: setup param {pname} = {expr}",
                     lambda n=pname, e=expr: setattr(param(setup, n), "expression", e) or param(setup, n).expression)
        rec.step("B: read back job_stockMode", lambda: param(setup, "job_stockMode").expression)

        ops_before = setup.allOperations.count
        if template_path:
            template = rec.step("B: CAMTemplate.createFromFile", lambda: adsk.cam.CAMTemplate.createFromFile(template_path))

            def apply_template():
                t_in = adsk.cam.CreateFromCAMTemplateInput.create()
                t_in.camTemplate = template
                setup.createFromCAMTemplate2(t_in)
                return [setup.allOperations.item(i).name for i in range(setup.allOperations.count)]

            if template is not FAIL:
                rec.step("B: setup.createFromCAMTemplate2", apply_template)
        else:
            rec.note("B: template", "skipped: no template file picked")

        ops = [setup.allOperations.item(i) for i in range(setup.allOperations.count)]
        top = top_face(body)
        loop_types = {"[inner]": "OnlyInsideLoops", "[outer]": "OnlyOutsideLoops"}
        for op in ops:
            raw = rec.step(f"B: op '{op.name}' Tool.toJson", lambda o=op: o.tool.toJson())
            if raw is not FAIL:
                rec.note(f"B: op '{op.name}' tool", json.dumps({k: json.loads(raw).get(k) for k in ("guid", "description")}))
            tag = next((t for t in loop_types if t in op.name.lower()), None)
            if tag is None:
                continue

            def select(o=op, loop=loop_types[tag]):
                value = adsk.cam.CadContours2dParameterValue.cast(param(o, "contours").value)
                sels = value.getCurveSelections()
                sels.clear()
                fc = sels.createNewFaceContourSelection()
                fc.loopType = getattr(adsk.cam.LoopTypes, loop)
                fc.isSelectingSamePlaneFaces = False
                fc.inputGeometry = [top]
                value.applyCurveSelections(sels)
                return loop

            rec.step(f"B: op '{op.name}' contours = face contour {loop_types[tag]}", select)

        future = rec.step("B: generateAllToolpaths", lambda: cam.generateAllToolpaths(False))
        if future is not FAIL:
            rec.step("B: wait for toolpaths", lambda: wait_for(lambda: future.isGenerationCompleted, 300))
        for op in ops:
            def state(o=op):
                return {a: getattr(o, a, "<missing>") for a in ("hasToolpath", "hasError", "hasWarning", "isSuppressed")}
            rec.step(f"B: op '{op.name}' state", state)

        posts = find_post(rec)
        rec.note("B: posts found", json.dumps([url for _, url in posts]))
        nc_dir = os.path.join(out_dir, "nc_program")
        os.makedirs(nc_dir, exist_ok=True)
        if posts:
            cfg = posts[0][0]

            def nc_program():
                nc_in = cam.ncPrograms.createInput()
                nc_in.displayName = "probe_nc"
                prms = nc_in.parameters
                prms.itemByName("nc_program_filename").value.value = "probe_nc"
                prms.itemByName("nc_program_output_folder").value.value = nc_dir.replace("\\", "/")
                prms.itemByName("nc_program_openInEditor").value.value = False
                nc_in.operations = [setup]
                prog = cam.ncPrograms.add(nc_in)
                prog.postConfiguration = cfg
                post_prms = prog.postParameters
                names = [post_prms.item(i).name for i in range(post_prms.count)]
                prog.updatePostParameters(post_prms)
                ok = prog.postProcess(adsk.cam.NCProgramPostProcessOptions.create())
                return {"postProcess returned": ok, "post parameters": names}

            rec.step("B: NC program + postProcess (library post)", nc_program)
            rec.step("B: wait for library-post .tap",
                     lambda: wait_for(lambda: any(f.endswith(".tap") for f in os.listdir(nc_dir)), 60))

        path_dir = os.path.join(out_dir, "post_by_path")
        os.makedirs(path_dir, exist_ok=True)

        def post_by_path():
            ppi = adsk.cam.PostProcessInput.create("probe_path", POST_FILE, path_dir,
                                                   adsk.cam.PostOutputUnitOptions.InchesOutput)
            ppi.isOpenInEditor = False
            return cam.postProcess(setup, ppi)

        rec.step("B: CAM.postProcess with the pinned .cps path", post_by_path)
        rec.step("B: wait for post-by-path .tap",
                 lambda: wait_for(lambda: any(f.endswith(".tap") for f in os.listdir(path_dir)), 60))

        sys.path.insert(0, os.path.join(REPO, "core"))
        for folder in (nc_dir, path_dir):
            for name in sorted(os.listdir(folder)):
                if name.endswith(".tap"):
                    def guard(p=os.path.join(folder, name)):
                        from autocam_core.tapguard import GuardSpec, check_program
                        with open(p, "rb") as f:
                            report = check_program(f.read(), GuardSpec())
                        return {"passed": report.passed, "min_z_in": report.min_z_in,
                                "problems": list(report.problems[:5]), "offenders": list(report.offenders[:5])}
                    rec.step(f"B: tapguard on {os.path.basename(folder)}/{name}", guard)

        rec.step("B: getMachiningTime", lambda: cam.getMachiningTime(setup, 100, 400 * IN, 0).machiningTime)

        outer = next((op for op in ops if "[outer]" in op.name.lower()), None)
        if outer is not None:
            copy_tpl = rec.step("B: CAMTemplate.createFromOperations([outer op])",
                                lambda: adsk.cam.CAMTemplate.createFromOperations([outer]))
            if copy_tpl is not FAIL:
                def apply_copy():
                    before = setup.allOperations.count
                    t_in = adsk.cam.CreateFromCAMTemplateInput.create()
                    t_in.camTemplate = copy_tpl
                    setup.createFromCAMTemplate2(t_in)
                    added = setup.allOperations.item(setup.allOperations.count - 1)
                    result = {"ops before": before, "ops after": setup.allOperations.count, "new op": added.name}
                    added.deleteMe()
                    return result
                rec.step("B: apply the copied-op template, then deleteMe", apply_copy)
            rec.step("B: op.isSuppressed toggle", lambda: set_checked(outer, "isSuppressed", True)
                     and set_checked(outer, "isSuppressed", False))

        for strategy in ("manual_nc", "manualnc", "manual"):
            def manual(s=strategy):
                op_in = setup.operations.createInput(s)
                op = setup.operations.add(op_in)
                name = op.name
                op.deleteMe()
                return name
            rec.step(f"B: Manual NC via operations.createInput('{strategy}')", manual)

        rec.step("B: export .f3d", lambda: design.exportManager.execute(
            design.exportManager.createFusionArchiveExportOptions(os.path.join(out_dir, "probe.f3d"))))

        def preview():
            vp = app.activeViewport
            cam_view = vp.camera
            cam_view.viewOrientation = adsk.core.ViewOrientations.TopViewOrientation
            cam_view.isFitView = True
            vp.camera = cam_view
            adsk.doEvents()
            return vp.saveAsImageFile(os.path.join(out_dir, "preview.png"), 800, 500)

        rec.step("B: Viewport.saveAsImageFile", preview)
        rec.note("B: ops before template", str(ops_before))
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
        dlg.title = "Pick your smoke template (.f3dhsm-template), or Cancel to skip the template steps"
        dlg.filter = "CAM template (*.f3dhsm-template)"
        template_path = dlg.filename if dlg.showOpen() == adsk.core.DialogResults.DialogOK else None

        for variant in ARRANGE_VARIANTS:
            try:
                arrange_variant(app, rec, *variant)
            except Exception as e:  # noqa: BLE001
                rec.note(f"{variant[0]}: aborted", f"{type(e).__name__}: {e}")
        try:
            cam_chain(app, ui, rec, out_dir, template_path)
        except Exception:  # noqa: BLE001
            rec.note("B: aborted", traceback.format_exc(limit=4))

        data = {"script": "pipeline_probe", "fusion_version": app.version, "python": sys.version,
                "template": template_path, "steps": rec.steps}
        with open(os.path.join(out_dir, "probe.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        ok = sum(1 for s in rec.steps if s.get("ok") is True)
        failed = sum(1 for s in rec.steps if s.get("ok") is False)
        ui.messageBox(f"{ok} steps worked, {failed} failed.\nWrote:\n{os.path.join(out_dir, 'probe.json')}",
                      "pipeline_probe")
    except Exception:  # noqa: BLE001
        ui.messageBox("pipeline_probe failed:\n" + traceback.format_exc(), "pipeline_probe")
