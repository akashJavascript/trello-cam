"""UNTESTED IN FUSION. Design-side calls: document, STEP import, copies, Arrange, stock, preview, .f3d.

Arrange is driven the way pipeline_probe2 v3 showed works without the Manufacturing Extension: move (not copy)
occurrences, clear isGroundToParent first, one plane envelope per Arrange with partial arrange on, and
isDirectionFlipped where a part's upDirection points away from the face that must face up.
"""

import adsk.core
import adsk.fusion

from .adapter import AdapterError, Arranged
from .fx_util import call, items, normal, to_cm, to_in, vec, vi


def new_design(app):
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    design.fusionUnitsManager.distanceDisplayUnits = adsk.fusion.DistanceUnits.InchDistanceUnits
    return doc, design


def import_step(app, design, path: str, name: str):
    root = design.rootComponent
    # Names, not entity tokens: tokens of one entity can differ between reads.
    before = {o.name for o in items(root.occurrences)}
    opts = call("createSTEPImportOptions", app.importManager.createSTEPImportOptions, path)
    call("importToTarget", app.importManager.importToTarget, opts, root)
    new = [o for o in items(root.occurrences) if o.name not in before]
    if len(new) != 1:
        for o in new:
            o.deleteMe()
        raise AdapterError(f"the STEP file made {len(new)} top-level components, expected 1")
    occ = new[0]
    try:
        occ.component.name = name
    except Exception:  # noqa: BLE001 - only cosmetic
        pass
    return occ


def add_copy(design, occ):
    return call("addExistingComponent", design.rootComponent.occurrences.addExistingComponent,
                occ.component, occ.transform2)


def body(occ):
    from .fx_geometry import solid_bodies
    bodies = solid_bodies(occ)
    if len(bodies) != 1:
        raise AdapterError(f"{occ.name} has {len(bodies)} solid bodies")
    return bodies[0]


def face_by_id(occ, face_id: int):
    faces = body(occ).faces
    if not 1 <= face_id <= faces.count:
        raise AdapterError(f"{occ.name} has no face {face_id}")
    return faces.item(face_id - 1)


def box_in(occ):
    bb = body(occ).boundingBox
    return tuple(to_in(v) for v in (bb.minPoint.x, bb.minPoint.y, bb.minPoint.z,
                                    bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z))


def orientation(up, want):
    """Arrange turns a part so its upDirection points +Z. True: flip it (isDirectionFlipped) so `want` points
    up instead; False: leave it; None: upDirection is across `want`, so it would lie on its side."""
    dot = up.dotProduct(want)
    if abs(dot) < 0.99:
        return None
    return dot < 0


def arrange(design, occs, envelope, spacing_in: float, up_faces, rotation: str, part_in_part: bool) -> Arranged:
    """occs: [(copy id, occurrence)]; up_faces: copy id -> face id that must face +Z."""
    root = design.rootComponent
    for _, occ in occs:
        for attr in ("isGroundToParent", "isGrounded"):
            if getattr(occ, attr, False):
                call(f"{attr} = False", setattr, occ, attr, False)
    feats = root.features.arrangeFeatures
    arr_in = call("arrangeFeatures.createInput", feats.createInput,
                  adsk.fusion.ArrangeSolverTypes.Arrange2DTrueShapeSolverType)
    d = arr_in.definition
    rot = adsk.fusion.ArrangeRotationTypes
    d.globalRotation = rot.AllRotationsArrangeRotationType if rotation == "all" else rot.NoneArrangeRotationType
    d.isGlobalDirectionFaceUp = True
    d.isPartInPartAllowed = bool(part_in_part)
    d.isCreateCopies = False
    names, refused = {}, {}
    for cid, occ in occs:
        comp = call(f"arrangeComponents.add({cid})", arr_in.arrangeComponents.add, occ)
        want = normal(face_by_id(occ, up_faces[cid]))
        flip = orientation(comp.upDirection, want)
        if flip is None:
            refused[cid] = f"upDirection {vec(comp.upDirection)} is across the top face {vec(want)}"
            call(f"take {cid} out of the Arrange", comp.deleteMe)
            continue
        if flip:
            comp.isDirectionFlipped = True
        names[occ.name] = cid
    if not names:
        return Arranged((), refused)
    x0, y0, x1, y1 = envelope
    env = call("setPlaneEnvelope", arr_in.setPlaneEnvelope, root.xYConstructionPlane, vi(x1 - x0), vi(y1 - y0))
    env.originXOffset = vi(x0)
    env.originYOffset = vi(y0)
    env.objectSpacing = vi(spacing_in)
    env.isPartialArrangeAllowed = True
    feature = call("arrangeFeatures.add", feats.add, arr_in)
    placed = []
    for result_env in items(feature.resultEnvelopes):
        for r in items(result_env.occurrences):
            cid = names.get(r.occurrence.name)
            if cid is not None:
                placed.append(cid)
    return Arranged(tuple(placed), refused)


def faces_up(occ, face_id: int) -> bool:
    return normal(face_by_id(occ, face_id)).z > 0.999


def make_stock(design, name: str, origin, length_in: float, width_in: float, thickness_in: float):
    """A hidden solid the size of the sheet, bottom at Z0, corner at origin."""
    root = design.rootComponent
    occ = call("addNewComponent (stock)", root.occurrences.addNewComponent, adsk.core.Matrix3D.create())
    comp = occ.component
    comp.name = f"STOCK {name}"
    sketch = comp.sketches.add(comp.xYConstructionPlane)
    x, y = origin
    sketch.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(to_cm(x), to_cm(y), 0),
        adsk.core.Point3D.create(to_cm(x + length_in), to_cm(y + width_in), 0))
    ext = call("extrude stock", comp.features.extrudeFeatures.addSimple, sketch.profiles.item(0), vi(thickness_in),
               adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    occ.isLightBulbOn = False
    return ext.bodies.item(0).createForAssemblyContext(occ)


def preview(app, rect, path: str) -> None:
    """Top view framed on one sheet."""
    x0, y0, x1, y1 = (to_cm(v) for v in rect)
    vp = app.activeViewport
    cam = vp.camera
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    cam.cameraType = adsk.core.CameraTypes.OrthographicCameraType
    cam.target = adsk.core.Point3D.create(cx, cy, 0)
    cam.eye = adsk.core.Point3D.create(cx, cy, 500)
    cam.upVector = adsk.core.Vector3D.create(0, 1, 0)
    cam.isFitView = False
    cam.viewExtents = max(x1 - x0, y1 - y0) * 0.55
    vp.camera = cam
    adsk.doEvents()
    if not vp.saveAsImageFile(path, 1600, 900):
        raise AdapterError("saveAsImageFile returned False")


def export_f3d(design, path: str) -> None:
    em = design.exportManager
    if not call("export .f3d", em.execute, em.createFusionArchiveExportOptions(path)):
        raise AdapterError("export returned False")
