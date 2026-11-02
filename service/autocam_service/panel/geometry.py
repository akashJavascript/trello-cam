"""Onshape's body details -> autocam_core.geometry.PartGeometry: the same model fx_geometry.py makes from Fusion's
BRep, so the panel runs AutoCAM's own plate checks (plate.py, tooling.py) while the part is still being designed.

The response (GET /parts/d/{did}/{wvm}/{wvmid}/e/{eid}/partid/{pid}/bodydetails) lists the body's faces (surface
type and parameters, area, loops of coedges), edges (curve, end and mid points) and vertices, in meters.
Conventions, read off P-2011's real response (2026-11-01) and checked against Fusion's analysis of the same version
(run r004: 3/16 in, 2 drilled, 20 bored, 2 bearing, 25 cut out):
- a face's outward normal is its surface's normal, reversed when the face's `orientation` is false;
- a cylinder face that's reversed points at its axis: it wraps around empty space (a hole or an inside corner);
- loops run counterclockwise seen from outside (material on the left), so at a straight edge between two flat faces,
  (n1 x n2) . t > 0 is a convex edge, t being the edge's direction as the first face's loop runs it.

Measured like fx_geometry: the plate axis is the outward normal of the largest flat face; heights along it from the
part's lowest point; a cylinder's sweep from its area (area / (radius x height)), since STEP and Onshape both split
holes into pieces.
"""

import hashlib
import json
import math
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from autocam_core.geometry import CYLINDER, PLANE, Face, PartGeometry
from autocam_core.holes import group_cylinders

M_PER_IN = 0.0254
TOL_M = 1e-8
Vec = Tuple[float, float, float]


def _v(d: Optional[Dict[str, Any]]) -> Optional[Vec]:
    if not isinstance(d, dict):
        return None
    return (float(d.get("x", 0.0)), float(d.get("y", 0.0)), float(d.get("z", 0.0)))


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a: Vec) -> Vec:
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n) if n > 0 else (0.0, 0.0, 0.0)


def _scale(a: Vec, k: float) -> Vec:
    return (a[0] * k, a[1] * k, a[2] * k)


def _basis(axis: Vec) -> Tuple[Vec, Vec]:
    helper = (1.0, 0.0, 0.0) if abs(axis[0]) <= 0.9 else (0.0, 1.0, 0.0)
    u = _unit(_cross(axis, helper))
    return u, _unit(_cross(axis, u))


def _kind(face: Dict[str, Any]) -> str:
    t = str(((face.get("surface") or {}).get("type") or "unknown")).lower()
    return {"plane": PLANE, "cylinder": CYLINDER}.get(t, t)


def outward_normal(face: Dict[str, Any]) -> Vec:
    n = _unit(_v((face.get("surface") or {}).get("normal")) or (0.0, 0.0, 0.0))
    return n if face.get("orientation", True) else _scale(n, -1.0)


def _coedges(face: Dict[str, Any]) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
    for loop in face.get("loops") or []:
        for co in loop.get("coedges") or []:
            yield loop, co


def solid_body(details: Dict[str, Any], part_id: str) -> Optional[Dict[str, Any]]:
    bodies = details.get("bodies") if isinstance(details, dict) else None
    for b in bodies or []:
        if b.get("id") == part_id:
            return b
    solids = [b for b in bodies or [] if str(b.get("type", "")).upper() == "SOLID"]
    return solids[0] if len(solids) == 1 else None


def body_geometry(body: Dict[str, Any], name: str) -> PartGeometry:
    return read_body(body, name)[0]


def read_body(body: Dict[str, Any], name: str) -> Tuple[PartGeometry, Dict[str, Any]]:
    """The part model, and what the panel needs beside it: "face_ids" (Onshape's id of each model face, in model id
    order), "outline_faces" (the walls of the outline: picking one means its outline), "outline_area_in2" and
    "box_in" (for the cost and sheet use)."""
    extra: Dict[str, Any] = {"face_ids": [], "outline_faces": [], "outline_area_in2": 0.0, "box_in": [0.0, 0.0]}
    if str(body.get("type", "SOLID")).upper() != "SOLID":
        return PartGeometry(name, 0, 0.0, ()), extra
    faces: List[Dict[str, Any]] = [f for f in body.get("faces") or [] if f.get("id")]
    planes = [f for f in faces if _kind(f) == PLANE]
    if not planes:
        return PartGeometry(name, 1, 0.0, ()), extra
    largest = max(planes, key=lambda f: float(f.get("area") or 0.0))
    axis = outward_normal(largest)
    u, w = _basis(axis)

    vertices = {v.get("id"): _v(v.get("point")) for v in body.get("vertices") or []}
    edges = {e.get("id"): e for e in body.get("edges") or []}

    def edge_points(eid: str) -> List[Vec]:
        e = edges.get(eid) or {}
        pts = [vertices.get(vid) for vid in e.get("vertices") or []]
        g = e.get("geometry") or {}
        pts += [_v(g.get(k)) for k in ("startPoint", "endPoint", "midPoint", "quarterPoint")]
        return [p for p in pts if p is not None]

    every = [p for p in vertices.values() if p is not None] + [p for eid in edges for p in edge_points(eid)]
    if not every:
        return PartGeometry(name, 1, 0.0, ()), extra
    heights = [_dot(p, axis) for p in every]
    z0, z1 = min(heights), max(heights)

    ids = {f["id"]: i for i, f in enumerate(faces, 1)}
    faces_of_edge: Dict[str, List[str]] = {}
    for f in faces:
        for _, co in _coedges(f):
            faces_of_edge.setdefault(co.get("edgeId"), [])
            if f["id"] not in faces_of_edge[co.get("edgeId")]:
                faces_of_edge[co.get("edgeId")].append(f["id"])

    out = []
    for f in faces:
        fid, kind = ids[f["id"]], _kind(f)
        pts = [p for _, co in _coedges(f) for p in edge_points(co.get("edgeId"))]
        zs = [_dot(p, axis) - z0 for p in pts] or [0.0]
        lo, hi = min(zs), max(zs)
        area = float(f.get("area") or 0.0)
        data: Dict[str, Any] = dict(id=fid, kind=kind, z_min_in=lo / M_PER_IN, z_max_in=hi / M_PER_IN,
                                    area_in2=area / (M_PER_IN * M_PER_IN))
        surface = f.get("surface") or {}
        if kind == PLANE:
            data["normal_dot"] = _dot(outward_normal(f), axis)
            loops = []
            for loop in f.get("loops") or []:
                if loop.get("isOuter") or not loop.get("isInner", True):
                    continue
                walls: List[int] = []
                for co in loop.get("coedges") or []:
                    for other in faces_of_edge.get(co.get("edgeId"), []):
                        oid = ids.get(other)
                        if oid is not None and oid != fid and oid not in walls:
                            walls.append(oid)
                loops.append(tuple(walls))
            data["inner_loops"] = tuple(loops)
        elif kind == CYLINDER:
            cax = _unit(_v(surface.get("axis")) or (0.0, 0.0, 1.0))
            r = float(surface.get("radius") or 0.0)
            origin = _v(surface.get("origin")) or (0.0, 0.0, 0.0)
            height = hi - lo
            data.update(radius_in=r / M_PER_IN, axis_dot=abs(_dot(cax, axis)), concave=not f.get("orientation", True),
                        center_in=(_dot(origin, u) / M_PER_IN, _dot(origin, w) / M_PER_IN),
                        sweep_deg=min(360.0, math.degrees(area / (r * height))) if r > 0 and height > TOL_M else 0.0)
        out.append(Face(**data))
    geom = PartGeometry(name, 1, (z1 - z0) / M_PER_IN, tuple(out), _sharp_inside_corners(faces, edges, axis))

    us, ws = [_dot(p, u) for p in every], [_dot(p, w) for p in every]
    extra["box_in"] = [round((max(us) - min(us)) / M_PER_IN, 3), round((max(ws) - min(ws)) / M_PER_IN, 3)]
    extra["face_ids"] = [f["id"] for f in faces]
    # The outline: the top's outer loop, or for a top split into faces in one plane (a sheet metal flat pattern is
    # split along its bends) the outer loops' co-edges that aren't shared between two of those faces, in order.
    level = _dot(_v((largest.get("surface") or {}).get("origin")) or (0.0, 0.0, 0.0), axis)
    tops = [f for f in planes if _dot(outward_normal(f), axis) > 0.9999
            and abs(_dot(_v((f.get("surface") or {}).get("origin")) or (0.0, 0.0, 0.0), axis) - level) < 1e-6]
    top_ids = {f["id"] for f in tops}
    boundary = []
    for f in tops:
        outer = next((lp for lp in f.get("loops") or [] if lp.get("isOuter")), None)
        for co in (outer or {}).get("coedges") or []:
            if sum(1 for o in faces_of_edge.get(co.get("edgeId"), []) if o in top_ids) > 1:
                continue                                   # between two top faces: inside the outline
            boundary.append((f["id"], co))
    if boundary:
        def ends(co):
            g = (edges.get(co.get("edgeId")) or {}).get("geometry") or {}
            a, b = _v(g.get("startPoint")), _v(g.get("endPoint"))
            return (b, a) if not co.get("orientation", True) else (a, b)
        ordered, left = [boundary[0]], boundary[1:]
        while left:
            end = ends(ordered[-1][1])[1]
            nxt = min(left, key=lambda fc: sum((x - y) ** 2 for x, y in zip(end or (0, 0, 0),
                                                                            ends(fc[1])[0] or (0, 0, 0))))
            ordered.append(nxt)
            left.remove(nxt)
        walls: List[str] = []
        ring: List[Tuple[float, float]] = []
        for fid, co in ordered:
            for other in faces_of_edge.get(co.get("edgeId"), []):
                if other not in top_ids and other not in walls:
                    walls.append(other)
            g = (edges.get(co.get("edgeId")) or {}).get("geometry") or {}
            pts = [_v(g.get(k)) for k in ("startPoint", "quarterPoint", "midPoint", "endPoint")]
            pts = [p for p in pts if p is not None]
            if not co.get("orientation", True):
                pts.reverse()
            ring += [(_dot(p, u) / M_PER_IN, _dot(p, w) / M_PER_IN) for p in pts]
        extra["outline_faces"] = walls
        extra["outline_area_in2"] = round(abs(sum(ring[i][0] * ring[(i + 1) % len(ring)][1]
                                                  - ring[(i + 1) % len(ring)][0] * ring[i][1]
                                                  for i in range(len(ring)))) / 2, 2) if len(ring) > 2 else 0.0
    return geom, extra


def _sharp_inside_corners(faces: Sequence[Dict[str, Any]], edges: Dict[str, Dict[str, Any]], axis: Vec) -> int:
    """Straight edges through the thickness where two flat walls meet concave, with no radius."""
    by_id = {f["id"]: f for f in faces}
    runs: Dict[str, List[Tuple[str, bool]]] = {}           # edge -> [(face, coedge runs along the edge)]
    for f in faces:
        for _, co in _coedges(f):
            runs.setdefault(co.get("edgeId"), []).append((f["id"], bool(co.get("orientation", True))))
    count = 0
    for eid, sides in runs.items():
        e = edges.get(eid) or {}
        if len(sides) != 2 or str((e.get("curve") or {}).get("type", "")).upper() != "LINE":
            continue
        g = e.get("geometry") or {}
        a, b = _v(g.get("startPoint")), _v(g.get("endPoint"))
        if a is None or b is None:
            continue
        d = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        if math.sqrt(_dot(d, d)) < TOL_M:
            continue
        d = _unit(d)
        if abs(abs(_dot(d, axis)) - 1) > 1e-4:
            continue
        (f1, along), (f2, _) = sides
        if _kind(by_id[f1]) != PLANE or _kind(by_id[f2]) != PLANE:
            continue
        n1, n2 = outward_normal(by_id[f1]), outward_normal(by_id[f2])
        if abs(_dot(n1, n2)) > 1 - 1e-4:
            continue
        t = d if along else _scale(d, -1.0)
        if _dot(_cross(n1, n2), t) < 0:
            count += 1
    return count


def _holes(geom: PartGeometry):
    return [h for h in group_cylinders(geom.faces) if h.full]


def shape_key(geom: PartGeometry) -> str:
    """The same for the same shape wherever it sits in its Part Studio: the thickness, every face's kind, size and
    height, and the distances between round holes (so a moved hole counts)."""
    faces = sorted((f.kind, round(f.z_min_in, 3), round(f.z_max_in, 3), round(f.area_in2, 2), round(f.radius_in, 4))
                   for f in geom.faces)
    centers = [h.center_in for h in _holes(geom)][:60]
    gaps = sorted(round(math.dist(a, b), 2) for i, a in enumerate(centers) for b in centers[i + 1:])
    data = json.dumps([round(geom.thickness_in, 4), faces, gaps])
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def differences(then: PartGeometry, now: PartGeometry, then_area: float = 0.0, now_area: float = 0.0) -> List[str]:
    """What changed between two states of a part, in words; [] when it's the same shape."""
    if shape_key(then) == shape_key(now):
        return []
    out: List[str] = []
    if abs(then.thickness_in - now.thickness_in) > 0.0005:
        from .checks import inches                     # here: checks reads the config, this module doesn't
        out.append(f"thickness {inches(then.thickness_in)} to {inches(now.thickness_in)}")
    a = Counter(round(h.diameter_in, 3) for h in _holes(then))
    b = Counter(round(h.diameter_in, 3) for h in _holes(now))
    added, removed = sum((b - a).values()), sum((a - b).values())
    if added:
        out.append(f"{added} hole{'s' if added != 1 else ''} added")
    if removed:
        out.append(f"{removed} hole{'s' if removed != 1 else ''} removed")
    if then_area and now_area and abs(then_area - now_area) > 0.01:
        out.append("the outline changed")
    return out or ["the shape changed"]
