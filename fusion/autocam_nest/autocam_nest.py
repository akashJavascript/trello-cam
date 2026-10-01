"""
autocam_nest - Phase 1 of the Trello -> Fusion auto-CAM pipeline.

    job.json in  ->  <job>.f3d, <job>_S<n>.tap per sheet, <job>_nest.png, <job>_result.json

Run from Utilities > Add-Ins > Scripts and Add-Ins; it asks for a job.json.
The Phase 2 add-in will call process_job(job_dict) directly instead.

Conventions this relies on (see job.example.json):
  * Every part is a flat, through-cut plate: one solid body, flat top/bottom,
    vertical walls. Pockets, chamfers, countersinks, engraving and edge fillets
    are rejected to the review list instead of being CAM'd wrong.
  * Operations in your CAM template are tagged in their names:
        [inner]  -> every hole/cutout on every part (inside loops)
        [outer]  -> every part outline (outside loops); put your tabs here
        [all]    -> both
    Untagged template operations are left alone.
"""

import json
import os
import re
import time
import traceback

import adsk.cam
import adsk.core
import adsk.fusion

IN = 2.54   # Fusion's API works in cm
TOL = 1e-6

app = adsk.core.Application.get()
ui = app.userInterface

LOOP_TAGS = {
    '[inner]': adsk.cam.LoopTypes.OnlyInsideLoops,
    '[outer]': adsk.cam.LoopTypes.OnlyOutsideLoops,
    '[all]': adsk.cam.LoopTypes.AllLoops,
}


class JobError(Exception):
    """A problem with the whole job, as opposed to one bad part."""


# --------------------------------------------------------------- helpers

def vi_in(x):
    # Always pass explicit units: the Arrange API preview has a known issue
    # where bare numbers are read in document units instead of cm.
    return adsk.core.ValueInput.createByString(f'{x:.5f} in')


def resolve(job, path):
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(job.get('_base_dir', ''), path))


def wait_for(done, timeout_s, what):
    t0 = time.time()
    while not done():
        adsk.doEvents()
        time.sleep(0.1)
        if time.time() - t0 > timeout_s:
            raise JobError(f'timed out waiting for {what}')


def try_set(obj, attr, value, notes):
    # SWIG objects can silently accept unknown attributes, so check the class.
    if not hasattr(type(obj), attr):
        notes.append(f'{type(obj).__name__} has no {attr}; skipped')
        return
    try:
        setattr(obj, attr, value)
    except Exception as e:
        notes.append(f'could not set {attr}: {e}')


def param(obj, name):
    try:
        return obj.parameters.itemByName(name)
    except Exception:
        return None


def dedupe(msgs):
    return list(dict.fromkeys(msgs))


def outward_normal(face):
    _, n = face.evaluator.getNormalAtPoint(face.pointOnFace)
    n.normalize()
    return n


def is_plane(face):
    return face.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType


def bodies_in(occ):
    found = [b for b in occ.bRepBodies if b.isSolid]
    for child in occ.childOccurrences:
        found += bodies_in(child)
    return found


def component_ids(occ):
    ids = {occ.component.id}
    for child in occ.childOccurrences:
        ids |= component_ids(child)
    return ids


def largest_planar_face(body):
    planar = [f for f in body.faces if is_plane(f)]
    return max(planar, key=lambda f: f.area) if planar else None


def top_face_up(body):
    """After nesting: the flat face pointing +Z at the top of the body."""
    up = adsk.core.Vector3D.create(0, 0, 1)
    cands = [f for f in body.faces
             if is_plane(f) and outward_normal(f).dotProduct(up) > 1 - 1e-4]
    return max(cands, key=lambda f: f.pointOnFace.z) if cands else None


# ------------------------------------------------------------ validation

def is_concave(face, cyl):
    """True if a cylindrical face wraps around empty space (hole / inside radius)."""
    p = face.pointOnFace
    _, n = face.evaluator.getNormalAtPoint(p)
    axis = cyl.axis.copy()
    axis.normalize()
    radial = cyl.origin.vectorTo(p)
    along = axis.copy()
    along.scaleBy(radial.dotProduct(axis))
    radial.subtract(along)
    return n.dotProduct(radial) < 0


def count_sharp_inside_corners(body, n):
    """Edges through the thickness where two flat walls meet concave, with no radius."""
    count = 0
    for edge in body.edges:
        if edge.geometry.objectType != adsk.core.Line3D.classType() or edge.faces.count != 2:
            continue
        a, b = edge.startVertex.geometry, edge.endVertex.geometry
        d = a.vectorTo(b)
        if d.length < TOL:
            continue
        d.normalize()
        if abs(abs(d.dotProduct(n)) - 1) > 1e-4:
            continue
        f1, f2 = edge.faces.item(0), edge.faces.item(1)
        if not (is_plane(f1) and is_plane(f2)):
            continue
        n1, n2 = outward_normal(f1), outward_normal(f2)
        if abs(n1.dotProduct(n2)) > 1 - 1e-4:
            continue
        # Step off the edge along (n1 - n2): that lands in material only at a
        # concave (inside) corner; at a convex corner it lands in air.
        probe = n1.copy()
        probe.subtract(n2)
        probe.normalize()
        probe.scaleBy(0.01)  # 0.1 mm
        q = adsk.core.Point3D.create((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2)
        q.translateBy(probe)
        if body.pointContainment(q) == adsk.fusion.PointContainment.PointInsidePointContainment:
            count += 1
    return count


def check_plate(body, job):
    """Return (errors, warnings) for one imported part body."""
    errors, warnings = [], []
    top = largest_planar_face(body)
    if top is None:
        return ['no flat faces; not a plate'], warnings
    n = outward_normal(top)

    # Thickness = oriented bounding box extent along the plate normal.
    helper = adsk.core.Vector3D.create(1, 0, 0)
    if abs(n.dotProduct(helper)) > 0.9:
        helper = adsk.core.Vector3D.create(0, 1, 0)
    u = n.crossProduct(helper)
    u.normalize()
    w = n.crossProduct(u)
    w.normalize()
    thickness = app.measureManager.getOrientedBoundingBox(body, u, w).height

    if abs(thickness - job['thickness_in'] * IN) > job.get('thickness_tol_in', 0.005) * IN:
        errors.append(f'thickness {thickness / IN:.4f}" does not match job {job["thickness_in"]}"')

    tool_r = job['tool_diameter_in'] * IN / 2
    min_r = tool_r - 0.001 * IN
    top_pt = top.pointOnFace
    for face in body.faces:
        geom = face.geometry
        kind = geom.surfaceType
        if kind == adsk.core.SurfaceTypes.PlaneSurfaceType:
            d = abs(outward_normal(face).dotProduct(n))
            if d > 1 - 1e-4:
                depth = top_pt.vectorTo(face.pointOnFace).dotProduct(n)
                if abs(depth) > 0.001 * IN and abs(depth + thickness) > 0.001 * IN:
                    errors.append(f'floor {-depth / IN:.4f}" deep (pocket, counterbore, or engraving)')
            elif d > 1e-4:
                errors.append('angled flat face (chamfer?)')
        elif kind == adsk.core.SurfaceTypes.CylinderSurfaceType:
            axis = geom.axis.copy()
            axis.normalize()
            if abs(abs(axis.dotProduct(n)) - 1) > 1e-4:
                errors.append('curved face across the thickness (edge fillet?)')
            elif is_concave(face, geom) and geom.radius < min_r:
                msg = (f'hole or inside radius R{geom.radius / IN:.4f}" is smaller than '
                       f'tool radius {tool_r / IN:.4f}"')
                (errors if job.get('strict_inside_radius', True) else warnings).append(msg)
        else:
            errors.append('unsupported face shape (countersink, 3D surface, etc.)')

    sharp = count_sharp_inside_corners(body, n)
    if sharp:
        warnings.append(f'{sharp} sharp inside corner(s); router leaves a {tool_r / IN:.4f}" '
                        f'radius there (add dogbones if something mates)')
    return dedupe(errors), dedupe(warnings)


# ------------------------------------------------------- import and nest

def import_parts(design, job):
    root = design.rootComponent
    parts = []
    for i, spec in enumerate(job['parts']):
        part = {'idx': i, 'name': spec['name'], 'card_id': spec.get('card_id'),
                'qty': int(spec.get('qty', 1)), 'errors': [], 'warnings': [],
                'occ': None, 'comp_ids': set()}
        parts.append(part)

        path = resolve(job, spec['step'])
        if not os.path.isfile(path):
            part['errors'].append(f'STEP file not found: {path}')
            continue

        before = {o.entityToken for o in root.occurrences}
        opts = app.importManager.createSTEPImportOptions(path)
        app.importManager.importToTarget(opts, root)
        new = [o for o in root.occurrences if o.entityToken not in before]
        if len(new) != 1:
            part['errors'].append(f'STEP import made {len(new)} top-level components, expected 1')
            for o in new:
                o.deleteMe()
            continue

        occ = new[0]
        try:
            occ.component.name = spec['name']
        except Exception:
            pass

        bodies = bodies_in(occ)
        if len(bodies) != 1:
            part['errors'].append(f'{len(bodies)} solid bodies, expected exactly 1')
        else:
            errs, warns = check_plate(bodies[0], job)
            part['errors'] += errs
            part['warnings'] += warns

        if part['errors']:
            occ.deleteMe()
        else:
            part['occ'] = occ
            part['comp_ids'] = component_ids(occ)
    return parts


def nest(design, parts, job, notes):
    root = design.rootComponent
    good = [p for p in parts if p['occ']]
    if not good:
        raise JobError('no parts passed validation; nothing to nest')

    # Park the sheets to the right of everything imported, so any part the
    # nest can't fit stays visibly off-sheet instead of overlapping one.
    max_x = max(b.boundingBox.maxPoint.x for p in good for b in bodies_in(p['occ']))

    feats = root.features.arrangeFeatures
    arr_in = feats.createInput(adsk.fusion.ArrangeSolverTypes.Arrange2DTrueShapeSolverType)
    d = arr_in.definition
    d.globalRotation = adsk.fusion.ArrangeRotationTypes.AllRotationsArrangeRotationType
    d.isGlobalDirectionFaceUp = True
    # Off by default: a part nested inside another part's cutout gets freed
    # with that cutout's slug unless the cut order is handled on purpose.
    d.isPartInPartAllowed = bool(job.get('part_in_part', False))
    try_set(d, 'isCreateCopies', True, notes)

    for p in good:
        face = largest_planar_face(bodies_in(p['occ'])[0])  # doubles as the "up" face
        comp = arr_in.arrangeComponents.add(face)
        comp.quantity = p['qty']

    sheet = job['sheet']
    env = arr_in.setPlaneEnvelope(root.xYConstructionPlane,
                                  vi_in(sheet['length_in']), vi_in(sheet['width_in']))
    env.originXOffset = vi_in(max_x / IN + 12)
    env.originYOffset = vi_in(0)
    env.quantity = adsk.core.ValueInput.createByReal(int(job.get('max_sheets', 3)))
    env.objectSpacing = vi_in(job['part_spacing_in'])
    env.envelopeSpacing = vi_in(6)
    try_set(env, 'frameWidth', vi_in(sheet.get('margin_in', 0.5)), notes)
    try_set(env, 'isPartialArrangeAllowed', True, notes)
    return feats.add(arr_in)


def bin_by_sheet(design, arrange, parts):
    """Assign every placed body to a sheet by where it landed.

    Deliberately position-based: the Arrange API preview has a known issue
    reporting which occurrences ended up in which envelope.
    """
    by_comp = {cid: p for p in parts if p['occ'] for cid in p['comp_ids']}
    sheets = []
    for env in arrange.resultEnvelopes:
        env = adsk.fusion.ArrangePlaneResultEnvelope.cast(env)
        if env:
            sheets.append({'bbox': env.boundingBox, 'bodies': [], 'counts': {},
                           'op_errors': [], 'op_warnings': [], 'notes': [], 'tap': None})

    for occ in design.rootComponent.allOccurrences:
        part = by_comp.get(occ.component.id)
        if part is None:
            continue
        for body in occ.bRepBodies:
            bb = body.boundingBox
            cx = (bb.minPoint.x + bb.maxPoint.x) / 2
            cy = (bb.minPoint.y + bb.maxPoint.y) / 2
            for s in sheets:
                sb = s['bbox']
                if (sb.minPoint.x - TOL <= cx <= sb.maxPoint.x + TOL and
                        sb.minPoint.y - TOL <= cy <= sb.maxPoint.y + TOL):
                    s['bodies'].append(body)
                    s['counts'][part['idx']] = s['counts'].get(part['idx'], 0) + 1
                    break

    sheets = [s for s in sheets if s['bodies']]
    for i, s in enumerate(sheets, 1):
        s['index'] = i
    return sheets


def make_stock(design, sheet):
    """A hidden solid the size of the sheet, used as the setup's stock."""
    root = design.rootComponent
    z0 = min(b.boundingBox.minPoint.z for b in sheet['bodies'])
    z1 = max(b.boundingBox.maxPoint.z for b in sheet['bodies'])
    m = adsk.core.Matrix3D.create()
    m.translation = adsk.core.Vector3D.create(0, 0, z0)
    occ = root.occurrences.addNewComponent(m)
    comp = occ.component
    comp.name = f'STOCK sheet {sheet["index"]}'
    sk = comp.sketches.add(comp.xYConstructionPlane)
    bb = sheet['bbox']
    sk.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(bb.minPoint.x, bb.minPoint.y, 0),
        adsk.core.Point3D.create(bb.maxPoint.x, bb.maxPoint.y, 0))
    comp.features.extrudeFeatures.addSimple(
        sk.profiles.item(0), adsk.core.ValueInput.createByReal(z1 - z0),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    occ.isLightBulbOn = False
    return occ.bRepBodies.item(0)


def save_preview(path):
    vp = app.activeViewport
    view = vp.camera
    view.viewOrientation = adsk.core.ViewOrientations.TopViewOrientation
    view.isFitView = True
    vp.camera = view
    adsk.doEvents()
    vp.saveAsImageFile(path, 1600, 1000)


# ------------------------------------------------------------ CAM + post

def assign_geometry(setup, tops, sheet):
    tagged = 0
    for op in setup.allOperations:
        tag = next((t for t in LOOP_TAGS if t in op.name.lower()), None)
        if tag is None:
            continue
        tagged += 1
        prm = param(op, 'contours') or param(op, 'pockets')
        if prm is None:
            sheet['op_errors'].append(f'{op.name}: no contour/pocket selection to fill')
            continue
        value = adsk.cam.CadContours2dParameterValue.cast(prm.value)
        sels = value.getCurveSelections()
        sels.clear()
        fc = sels.createNewFaceContourSelection()
        fc.loopType = LOOP_TAGS[tag]
        fc.isSelectingSamePlaneFaces = False
        fc.inputGeometry = tops
        value.applyCurveSelections(sels)
    if tagged == 0:
        sheet['op_errors'].append('template has no operations tagged [inner], [outer] or [all]')


def build_cam(doc, sheets, job):
    ui.workspaces.itemById('CAMEnvironment').activate()
    cam = adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType'))
    if cam is None:
        raise JobError('could not open the Manufacture workspace for the new document')

    template = adsk.cam.CAMTemplate.createFromFile(resolve(job, job['cam_template']))
    for s in sheets:
        si = cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation)
        si.models = s['bodies']
        setup = cam.setups.add(si)
        setup.name = f'{job["job_name"]} sheet {s["index"]}'

        if hasattr(type(setup), 'stockSolids'):
            setup.stockMode = adsk.cam.SetupStockModes.SolidStock
            setup.stockSolids = [s['stock']]
        else:
            s['op_errors'].append('could not set sheet stock from solid; set stock by hand')

        for name, expr in job.get('setup_params', {}).items():
            prm = param(setup, name)
            if prm:
                prm.expression = expr
            else:
                s['notes'].append(f'setup parameter {name} not found')

        t_in = adsk.cam.CreateFromCAMTemplateInput.create()
        t_in.camTemplate = template
        setup.createFromCAMTemplate2(t_in)

        tops = [top_face_up(b) for b in s['bodies']]
        if not all(tops):
            s['op_errors'].append('could not find the top face of every part after nesting')
        assign_geometry(setup, [t for t in tops if t], s)
        s['setup'] = setup

    future = cam.generateAllToolpaths(False)
    wait_for(lambda: future.isGenerationCompleted, 900, 'toolpath generation')

    for s in sheets:
        for op in s['setup'].allOperations:
            if getattr(op, 'hasError', False):
                s['op_errors'].append(f'{op.name}: {getattr(op, "error", "error")}')
            elif not op.hasToolpath:
                s['op_errors'].append(f'{op.name}: no toolpath generated')
            elif getattr(op, 'hasWarning', False):
                s['op_warnings'].append(f'{op.name}: {getattr(op, "warning", "warning")}')
    return cam


def walk_post_urls(lib, folder, depth):
    for url in lib.childAssetURLs(folder):
        yield url
    if depth > 0:
        for sub in lib.childFolderURLs(folder):
            yield from walk_post_urls(lib, sub, depth - 1)


def find_post(job):
    """Your own post first. Autodesk's library only if the job allows it,
    so a missing custom post never silently falls back to a generic one."""
    lib = adsk.cam.CAMManager.get().libraryManager.postLibrary
    want = job['post'].lower()
    for loc in (adsk.cam.LibraryLocations.LocalLibraryLocation,
                adsk.cam.LibraryLocations.CloudLibraryLocation):
        try:
            for url in walk_post_urls(lib, lib.urlByLocation(loc), depth=2):
                cfg = lib.postConfigurationAtURL(url)
                # Match on the post's description or its file name.
                if cfg and (want in (cfg.description or '').lower() or want in url.toString().lower()):
                    return cfg, url.toString()
        except Exception:
            continue
    if job.get('allow_fusion_library_post', False):
        query = lib.createQuery(adsk.cam.LibraryLocations.Fusion360LibraryLocation)
        for cfg in query.execute():
            if want in cfg.description.lower():
                return cfg, f'Fusion library: {cfg.description}'
    raise JobError(f'no post matching "{job["post"]}" in your local or cloud post library')


Z_WORD = re.compile(r'Z\s*(-?\d*\.?\d+)', re.IGNORECASE)


def check_tap_floor(tap_path, floor_in):
    """Spoilboard guard: scan the posted program and return (min_z_in, offending_lines).

    Z0 is the stock bottom (spoilboard surface), so no motion may go below floor_in.
    Skips comments ([...]) and G53 machine-coordinate lines. Handles inch (G20) and metric programs.
    """
    with open(tap_path, 'r', errors='replace') as f:
        lines = f.read().splitlines()
    metric = not any(re.match(r'\s*G20\b', ln) for ln in lines)
    scale = 1 / 25.4 if metric else 1.0
    min_z, bad = None, []
    for n, raw in enumerate(lines, 1):
        ln = re.sub(r'\[[^\]]*\]', '', raw).strip()
        if not ln or re.search(r'\bG53\b', ln, re.IGNORECASE):
            continue
        for m in Z_WORD.finditer(ln):
            z = float(m.group(1)) * scale
            min_z = z if min_z is None else min(min_z, z)
            if z < floor_in - 1e-4:
                bad.append(f'line {n}: {raw.strip()}')
    return min_z, bad


def post_sheets(cam, sheets, job, out_dir):
    cfg, post_used = find_post(job)
    ext = cfg.extension or '.tap'
    if not ext.startswith('.'):
        ext = '.' + ext

    for s in sheets:
        if s['op_errors'] and not job.get('post_with_errors', False):
            continue
        name = f'{job["job_name"]}_S{s["index"]}'
        nc_in = cam.ncPrograms.createInput()
        nc_in.displayName = name
        prms = nc_in.parameters
        prms.itemByName('nc_program_filename').value.value = name
        prms.itemByName('nc_program_output_folder').value.value = out_dir.replace('\\', '/')
        prms.itemByName('nc_program_openInEditor').value.value = False
        ext_prm = prms.itemByName('nc_program_nc_extension')
        if ext_prm:
            ext_prm.value.value = ext
        nc_in.operations = [s['setup']]

        prog = cam.ncPrograms.add(nc_in)
        prog.postConfiguration = cfg
        post_prms = prog.postParameters
        for k, v in job.get('post_params', {}).items():
            prm = post_prms.itemByName(k)
            if prm:
                prm.value.value = v
            else:
                s['notes'].append(f'post parameter {k} not found')
        prog.updatePostParameters(post_prms)
        prog.postProcess(adsk.cam.NCProgramPostProcessOptions.create())

        tap = os.path.join(out_dir, name + ext)
        try:
            wait_for(lambda: os.path.isfile(tap), 120, f'{os.path.basename(tap)}')
        except JobError:
            s['op_errors'].append(f'post ran but {tap} was not written')
            continue

        # Spoilboard guard: never hand out a program that goes below Z0 (stock bottom).
        floor_in = float(job.get('z_floor_in', 0.0))
        min_z, bad = check_tap_floor(tap, floor_in)
        if bad:
            rejected = tap[:-len(ext)] + '.REJECTED' + ext
            os.replace(tap, rejected)
            s['op_errors'].append(
                f'program goes below Z{floor_in:.4f} (lowest Z {min_z:.4f} in) and would cut the '
                f'spoilboard; saved as {os.path.basename(rejected)}. First offenders: ' + '; '.join(bad[:3]))
        else:
            s['tap'] = tap
            s['notes'].append(f'lowest Z in program: {min_z:.4f} in' if min_z is not None else 'no Z moves found')
    return post_used


# ------------------------------------------------------------ the job

def summarize(job, parts, sheets, notes, stem, post_used):
    placed = {}
    for s in sheets:
        for idx, n in s['counts'].items():
            placed[idx] = placed.get(idx, 0) + n

    part_rows = []
    for p in parts:
        got = placed.get(p['idx'], 0)
        errors = list(p['errors'])
        if not errors and got < p['qty']:
            errors.append(f'only {got} of {p["qty"]} fit on the sheets')
        part_rows.append({
            'name': p['name'], 'card_id': p['card_id'], 'qty': p['qty'], 'placed': got,
            'sheets': [s['index'] for s in sheets if p['idx'] in s['counts']],
            'errors': errors, 'warnings': p['warnings'],
        })

    sheet_rows = [{
        'index': s['index'], 'tap': s['tap'],
        'parts': {parts[i]['name']: n for i, n in s['counts'].items()},
        'errors': s['op_errors'], 'warnings': s['op_warnings'], 'notes': s['notes'],
    } for s in sheets]

    clean = (all(not r['errors'] and not r['warnings'] for r in part_rows) and
             all(r['tap'] and not r['errors'] and not r['warnings'] for r in sheet_rows))
    return {
        'job_name': job['job_name'],
        'status': 'ok' if clean else 'needs_review',
        'f3d': stem + '.f3d',
        'preview_png': stem + '_nest.png',
        'post': post_used,
        'sheets': sheet_rows,
        'parts': part_rows,
        'notes': notes,
    }


def process_job(job):
    for key in ('job_name', 'output_dir', 'thickness_in', 'tool_diameter_in', 'sheet',
                'part_spacing_in', 'cam_template', 'post', 'parts'):
        if key not in job:
            raise JobError(f'job is missing "{key}"')

    out_dir = resolve(job, job['output_dir'])
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, job['job_name'])
    notes = []

    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    design.designType = adsk.fusion.DesignTypes.ParametricDesignType

    parts = import_parts(design, job)
    arrange = nest(design, parts, job, notes)
    sheets = bin_by_sheet(design, arrange, parts)
    if not sheets:
        raise JobError('the nest placed nothing on any sheet')
    for s in sheets:
        s['stock'] = make_stock(design, s)
    save_preview(stem + '_nest.png')

    cam = build_cam(doc, sheets, job)
    try:
        post_used = post_sheets(cam, sheets, job, out_dir)
    except JobError as e:
        notes.append(str(e))
        post_used = None

    em = design.exportManager
    em.execute(em.createFusionArchiveExportOptions(stem + '.f3d'))

    result = summarize(job, parts, sheets, notes, stem, post_used)
    with open(stem + '_result.json', 'w') as f:
        json.dump(result, f, indent=2)
    return result


def run(context):
    try:
        dlg = ui.createFileDialog()
        dlg.title = 'Pick a job.json'
        dlg.filter = 'Job file (*.json)'
        if dlg.showOpen() != adsk.core.DialogResults.DialogOK:
            return
        with open(dlg.filename) as f:
            job = json.load(f)
        job['_base_dir'] = os.path.dirname(dlg.filename)

        res = process_job(job)

        lines = [f'Status: {res["status"]}']
        for s in res['sheets']:
            tap = os.path.basename(s['tap']) if s['tap'] else 'NOT POSTED'
            lines.append(f'Sheet {s["index"]}: {sum(s["parts"].values())} parts -> {tap}')
        for p in res['parts']:
            if p['errors']:
                lines.append(f'{p["name"]}: {p["errors"][0]}')
        lines.append(f'Details in {res["job_name"]}_result.json')
        ui.messageBox('\n'.join(lines), 'AutoCAM + nest')
    except JobError as e:
        ui.messageBox(f'Job stopped: {e}', 'AutoCAM + nest')
    except Exception:
        ui.messageBox('Failed:\n' + traceback.format_exc(), 'AutoCAM + nest')
