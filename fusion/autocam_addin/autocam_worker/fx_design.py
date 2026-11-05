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
    """The copy's box along the design's X, Y and Z, tight: MeasureManager's oriented box with X and Y as its
    directions. BRepBody.boundingBox is loose once Arrange turns a part by an angle that isn't a right angle: a
    10 x 2 in bar drawn at 30 deg and straightened by Arrange measured about 11.7 x 10.7 in (r080, 2026-11-04), so it
    seemed to cross its area's edge and took more room than it does. That box is the fallback."""
    b = body(occ)
    try:
        mm = adsk.core.Application.get().measureManager
        obb = mm.getOrientedBoundingBox(b, adsk.core.Vector3D.create(1, 0, 0), adsk.core.Vector3D.create(0, 1, 0))
        c, hx, hy, hz = obb.centerPoint, obb.length / 2, obb.width / 2, obb.height / 2
        if hx > 0 and hy > 0 and hz > 0:
            return tuple(to_in(v) for v in (c.x - hx, c.y - hy, c.z - hz, c.x + hx, c.y + hy, c.z + hz))
    except Exception:  # noqa: BLE001 - the loose box below is still a safe (bigger) box
        pass
    bb = b.boundingBox
    return tuple(to_in(v) for v in (bb.minPoint.x, bb.minPoint.y, bb.minPoint.z,
                                    bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z))


def orientation(up, want):
    """Arrange turns a part so its upDirection points +Z. True: flip it (isDirectionFlipped) so `want` points
    up instead; False: leave it; None: upDirection is across `want`, so it would lie on its side."""
    dot = up.dotProduct(want)
    if abs(dot) < 0.99:
        return None
    return dot < 0


PRIORITY_NAMES = {2: "VeryHighArrangePriority", 1: "HighArrangePriority"}


def arrange(design, occs, envelope, spacing_in: float, up_faces, rotation: str, part_in_part: bool,
            priorities=None) -> Arranged:
    """occs: [(copy id, occurrence)]; up_faces: copy id -> face id that must face +Z. priorities: copy id -> 2 or 1
    (ArrangePriorities VeryHigh, High; the rest as Fusion has them). Never isFiller: Fusion refuses it here
    ("Cannot set isFiller for move mode arrange", r077): a filler is a part copied to fill the leftover space."""
    priorities = priorities or {}
    levels = getattr(adsk.fusion, "ArrangePriorities", None)
    notes = []
    if priorities and (levels is None or not all(hasattr(levels, n) for n in PRIORITY_NAMES.values())):
        notes.append("this Fusion has no ArrangePriorities " + "/".join(PRIORITY_NAMES.values())
                     + (f" (it has {', '.join(n for n in dir(levels) if n.endswith('Priority'))})" if levels else "")
                     + ": priorities not set")
    set_to = {}                                      # copy id -> its priority, read back
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
        level = priorities.get(cid)
        if level and levels is not None and hasattr(levels, PRIORITY_NAMES.get(level, "")):
            call(f"priority {cid}", setattr, comp, "priority", getattr(levels, PRIORITY_NAMES[level]))
            set_to[cid] = int(comp.priority)                 # read back: what Fusion has now
        names[occ.name] = cid
    if not names:
        return Arranged((), refused, tuple(notes))
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
    if set_to:
        notes.append("priorities read back: " + ", ".join(f"{c}={v}" for c, v in sorted(set_to.items())))
    return Arranged(tuple(placed), refused, tuple(notes))


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


def make_clamps(design, name: str, origin, zones, z0_in: float, height_in: float):
    """The clamp strips as blocks: each zone (sheet coordinates, the sheet's corner at origin) from z0_in (the stock
    top) up height_in, in a component of their own. Returns (occurrence, bodies in assembly context)."""
    root = design.rootComponent
    occ = call("addNewComponent (clamps)", root.occurrences.addNewComponent, adsk.core.Matrix3D.create())
    comp = occ.component
    comp.name = f"CLAMPS {name}"
    planes = comp.constructionPlanes
    plane_in = planes.createInput()
    plane_in.setByOffset(comp.xYConstructionPlane, vi(z0_in))
    plane = call("clamp plane", planes.add, plane_in)
    x, y = origin
    bodies = []
    for zx0, zy0, zx1, zy1 in zones:
        sketch = comp.sketches.add(plane)
        a = sketch.modelToSketchSpace(adsk.core.Point3D.create(to_cm(x + zx0), to_cm(y + zy0), to_cm(z0_in)))
        b = sketch.modelToSketchSpace(adsk.core.Point3D.create(to_cm(x + zx1), to_cm(y + zy1), to_cm(z0_in)))
        sketch.sketchCurves.sketchLines.addTwoPointRectangle(a, b)
        ext = call("extrude clamp", comp.features.extrudeFeatures.addSimple, sketch.profiles.item(0), vi(height_in),
                   adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        bodies.append(ext.bodies.item(0).createForAssemblyContext(occ))
    return occ, bodies


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
