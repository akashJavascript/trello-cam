"""UNTESTED IN FUSION. BRep -> autocam_core.geometry.PartGeometry. The only code that walks BRep.

The plate axis is the outward normal of the largest flat face. Every face is measured along it (0 = the
part's lowest point). Face ids are 1-based positions in body.faces, so the same id finds the same face on
every copy of the part (fx_design.face_by_id).
"""

import math

import adsk.core
import adsk.fusion

from autocam_core.geometry import CYLINDER, PLANE, Face, PartGeometry

from .fx_util import IN, items, normal, to_in

TOL_CM = 1e-6

_KINDS = {}
for _name in dir(adsk.core.SurfaceTypes):
    if _name.endswith("SurfaceType"):
        _KINDS[getattr(adsk.core.SurfaceTypes, _name)] = _name[:-len("SurfaceType")].lower()
_KINDS[adsk.core.SurfaceTypes.PlaneSurfaceType] = PLANE
_KINDS[adsk.core.SurfaceTypes.CylinderSurfaceType] = CYLINDER


def solid_bodies(occ):
    found = [b for b in items(occ.bRepBodies) if b.isSolid]
    for child in items(occ.childOccurrences):
        found += solid_bodies(child)
    return found


def _basis(axis):
    helper = adsk.core.Vector3D.create(1, 0, 0)
    if abs(axis.dotProduct(helper)) > 0.9:
        helper = adsk.core.Vector3D.create(0, 1, 0)
    u = axis.crossProduct(helper)
    u.normalize()
    w = axis.crossProduct(u)
    w.normalize()
    return u, w


def _along(p, v) -> float:
    return p.x * v.x + p.y * v.y + p.z * v.z


def _face_points(face):
    pts = [v.geometry for v in items(face.vertices)]
    pts += [e.pointOnEdge for e in items(face.edges)]
    pts.append(face.pointOnFace)
    return pts


def _is_concave(face, cyl) -> bool:
    """A cylindrical face that wraps around empty space (a hole or an inside corner)."""
    p = face.pointOnFace
    _, n = face.evaluator.getNormalAtPoint(p)
    axis = cyl.axis.copy()
    axis.normalize()
    radial = cyl.origin.vectorTo(p)
    along = axis.copy()
    along.scaleBy(radial.dotProduct(axis))
    radial.subtract(along)
    return n.dotProduct(radial) < 0


def _sharp_inside_corners(body, axis) -> int:
    """Edges through the thickness where two flat walls meet concave, with no radius."""
    count = 0
    for edge in items(body.edges):
        if edge.geometry.objectType != adsk.core.Line3D.classType() or edge.faces.count != 2:
            continue
        a, b = edge.startVertex.geometry, edge.endVertex.geometry
        d = a.vectorTo(b)
        if d.length < TOL_CM:
            continue
        d.normalize()
        if abs(abs(d.dotProduct(axis)) - 1) > 1e-4:
            continue
        f1, f2 = edge.faces.item(0), edge.faces.item(1)
        if not all(f.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType for f in (f1, f2)):
            continue
        n1, n2 = normal(f1), normal(f2)
        if abs(n1.dotProduct(n2)) > 1 - 1e-4:
            continue
        # Stepping off the edge along (n1 - n2) lands in material only at an inside corner.
        probe = n1.copy()
        probe.subtract(n2)
        probe.normalize()
        probe.scaleBy(0.01)
        q = adsk.core.Point3D.create((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2)
        q.translateBy(probe)
        if body.pointContainment(q) == adsk.fusion.PointContainment.PointInsidePointContainment:
            count += 1
    return count


def inner_loops(face):
    return [lp for lp in items(face.loops) if not lp.isOuter]


def extract(name: str, occ) -> PartGeometry:
    bodies = solid_bodies(occ)
    if len(bodies) != 1:
        return PartGeometry(name, len(bodies), 0.0, ())
    body = bodies[0]
    faces = items(body.faces)
    planes = [f for f in faces if f.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType]
    if not planes:
        return PartGeometry(name, 1, 0.0, ())
    axis = normal(max(planes, key=lambda f: f.area))
    u, w = _basis(axis)
    z0 = min(_along(v.geometry, axis) for v in items(body.vertices))
    z1 = max(_along(v.geometry, axis) for v in items(body.vertices))
    ids = {f.tempId: i for i, f in enumerate(faces, 1)}

    out = []
    for fid, f in enumerate(faces, 1):
        geom = f.geometry
        kind = _KINDS.get(geom.surfaceType, str(geom.surfaceType))
        zs = [_along(p, axis) - z0 for p in _face_points(f)]
        lo, hi = min(zs), max(zs)
        data = dict(id=fid, kind=kind, z_min_in=to_in(lo), z_max_in=to_in(hi), area_in2=f.area / (IN * IN))
        if kind == PLANE:
            data["normal_dot"] = normal(f).dotProduct(axis)
            loops = []
            for lp in inner_loops(f):
                walls = []
                for e in items(lp.edges):
                    for other in items(e.faces):
                        oid = ids.get(other.tempId)
                        if oid is not None and oid != fid and oid not in walls:
                            walls.append(oid)
                loops.append(tuple(walls))
            data["inner_loops"] = tuple(loops)
        elif kind == CYLINDER:
            cax = geom.axis.copy()
            cax.normalize()
            r = geom.radius
            height = hi - lo
            data.update(radius_in=to_in(r), axis_dot=abs(cax.dotProduct(axis)), concave=_is_concave(f, geom),
                        center_in=(to_in(_along(geom.origin, u)), to_in(_along(geom.origin, w))),
                        sweep_deg=min(360.0, math.degrees(f.area / (r * height))) if r > 0 and height > TOL_CM else 0.0)
        out.append(Face(**data))
    return PartGeometry(name, 1, to_in(z1 - z0), tuple(out), _sharp_inside_corners(body, axis))
