"""UNTESTED IN FUSION. Design-side calls: document, STEP import, copies, Arrange, stock, preview, .f3d.

Arrange is driven the way pipeline_probe2 v3 showed works without the Manufacturing Extension: move (not copy)
occurrences, clear isGroundToParent first, one plane envelope per Arrange with partial arrange on, and
isDirectionFlipped where a part's upDirection points away from the face that must face up.
"""

import adsk.core
import adsk.fusion

from autocam_core.preview import preview_size, preview_view

from .adapter import AdapterError, Arranged
from .fx_util import call, items, normal, to_cm, to_in, vec, vi, wait_for


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


def make_stock(design, name: str, origin, size_x_in: float, size_y_in: float, thickness_in: float):
    """A hidden solid the size of the sheet (size_x across, size_y along its length), bottom at Z0, corner at origin."""
    root = design.rootComponent
    occ = call("addNewComponent (stock)", root.occurrences.addNewComponent, adsk.core.Matrix3D.create())
    comp = occ.component
    comp.name = f"STOCK {name}"
    sketch = comp.sketches.add(comp.xYConstructionPlane)
    x, y = origin
    sketch.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(to_cm(x), to_cm(y), 0),
        adsk.core.Point3D.create(to_cm(x + size_x_in), to_cm(y + size_y_in), 0))
    ext = call("extrude stock", comp.features.extrudeFeatures.addSimple, sketch.profiles.item(0), vi(thickness_in),
               adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    occ.isLightBulbOn = False
    try:   # the sheet's edge, for the preview and the review (r015: with the stock hidden nothing showed it)
        edge = root.sketches.add(root.xYConstructionPlane)
        edge.name = f"SHEET {name}"
        edge.sketchCurves.sketchLines.addTwoPointRectangle(
            adsk.core.Point3D.create(to_cm(x), to_cm(y), 0),
            adsk.core.Point3D.create(to_cm(x + size_x_in), to_cm(y + size_y_in), 0))
    except Exception:  # noqa: BLE001 - only cosmetic
        pass
    return ext.bodies.item(0).createForAssemblyContext(occ)


def preview(app, rect, path: str) -> None:
    """Top view framed on one sheet, as seen from the front of the machine (X across, Y away from you). An
    upright sheet gets an upright picture. viewExtents is what shows across the picture's shorter side (r014:
    with viewExtents = the sheet's length, a 1000 x 1600 picture showed about 50 in across, so the sheet
    filled less than half of it); it's set so the sheet fits both ways, with 8% to spare."""
    width, height = preview_size(rect)               # the same framing the labels are placed with
    cx, cy, extent = (to_cm(v) for v in preview_view(rect, (width, height)))
    vp = app.activeViewport
    cam = vp.camera
    cam.cameraType = adsk.core.CameraTypes.OrthographicCameraType
    cam.target = adsk.core.Point3D.create(cx, cy, 0)
    cam.eye = adsk.core.Point3D.create(cx, cy, 500)
    cam.upVector = adsk.core.Vector3D.create(0, 1, 0)
    cam.isFitView = False
    cam.viewExtents = extent
    vp.camera = cam
    adsk.doEvents()
    if not vp.saveAsImageFile(path, width, height):
        raise AdapterError("saveAsImageFile returned False")


def export_f3d(design, path: str) -> None:
    em = design.exportManager
    if not call("export .f3d", em.execute, em.createFusionArchiveExportOptions(path)):
        raise AdapterError("export returned False")


def _find_project(app, name: str):
    hubs = [app.data.activeHub] + [h for h in items(app.data.dataHubs)]
    seen = []
    for hub in hubs:
        if hub is None:
            continue
        for project in items(hub.dataProjects):
            if project.name == name:
                return project
            seen.append(project.name)
    raise AdapterError(f"no Fusion Team project named {name!r} (found: {', '.join(sorted(set(seen))) or 'none'})")


def _find_folder(project, path: str):
    folder = project.rootFolder
    for part in [p for p in path.replace("\\", "/").split("/") if p]:
        sub = folder.dataFolders.itemByName(part)
        if sub is None:
            names = [f.name for f in items(folder.dataFolders)]
            raise AdapterError(f"no folder {part!r} in {folder.name!r} (found: {', '.join(names) or 'none'})")
        folder = sub
    return folder


def link_kind(url: str) -> str:
    """'file', 'folder' or 'unknown': the last part of a Fusion Team link is a base64 URN."""
    import base64
    tail = url.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
    try:
        urn = base64.urlsafe_b64decode(tail + "=" * (-len(tail) % 4)).decode("ascii", errors="replace")
    except (ValueError, TypeError):
        return "unknown"
    if "fs.folder" in urn:
        return "folder"
    if "fs.file" in urn or "dm.lineage" in urn:
        return "file"
    return "unknown"


class _UploadDone(adsk.core.DataEventHandler):
    """Application.dataFileComplete: the saved file has finished uploading."""

    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.file = None

    def notify(self, args):
        try:
            f = args.file
            if f is not None and f.name.startswith(self.name):
                self.file = f
        except Exception:  # noqa: BLE001 - never raise into Fusion
            pass


def save_to_team(app, doc, name: str, project: str, folder_path: str, timeout_s: float = 300.0):
    """Save into the team folder, wait until Fusion says the upload is complete, then read the file's link.

    Seen 2026-10-01: reading doc.dataFile right after saveAs gave a link to the *folder* (a placeholder while
    the upload ran). Returns (link or None, name, note); a link that still isn't a file's is not returned."""
    folder = _find_folder(_find_project(app, project), folder_path)
    done = _UploadDone(name)
    app.dataFileComplete.add(done)
    try:
        if not call("Document.saveAs", doc.saveAs, name, folder, "auto-CAM job", ""):
            raise AdapterError("Document.saveAs returned False")
        wait_for(lambda: done.file is not None, timeout_s, "the upload to Fusion Team to finish")
    finally:
        app.dataFileComplete.remove(done)
    url = done.file.fusionWebURL
    kind = link_kind(url) if url else "none"
    note = f"Fusion Team: {done.file.name} uploaded; link kind {kind}: {url}"
    return (url if kind != "folder" else None), done.file.name, note
