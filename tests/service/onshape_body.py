"""A plate as Onshape's bodydetails describes one (meters; faces, loops of coedges, edges, vertices), for tests of
panel/geometry.py and the design checks. Conventions as in the real response (geometry.py's docstring): a face's
outward normal is its surface normal, reversed when `orientation` is false; loops run counterclockwise seen from
outside; a hole is two half cylinders that point at their axis."""

import math

M = 0.0254


def _v(p):
    return {"x": p[0], "y": p[1], "z": p[2]}


def plate_body(outline, thickness, holes=(), pid="JHD", microversion="m1"):
    """outline: (x, y) corners in inches, counterclockwise; holes: (x, y, diameter) in inches."""
    t = thickness * M
    pts = [(x * M, y * M) for x, y in outline]
    n = len(pts)
    verts, edges, faces = {}, {}, []

    def edge(eid, a, b, curve=None, mid=None):
        pa, pb = verts[a], verts[b]
        mid = mid or tuple((pa[i] + pb[i]) / 2 for i in range(3))
        if curve is None:
            d = [pb[i] - pa[i] for i in range(3)]
            length = math.sqrt(sum(x * x for x in d))
            curve = {"type": "LINE", "origin": _v(pa), "direction": _v([x / length for x in d])}
        edges[eid] = {"id": eid, "curve": curve, "vertices": [a, b],
                      "geometry": {"startPoint": _v(pa), "endPoint": _v(pb), "midPoint": _v(mid)}}

    def co(eid, start):
        return {"edgeId": eid, "orientation": edges[eid]["vertices"][0] == start}

    for i, (x, y) in enumerate(pts):
        verts[f"b{i}"] = (x, y, 0.0)
        verts[f"t{i}"] = (x, y, t)
    for i in range(n):
        j = (i + 1) % n
        edge(f"et{i}", f"t{i}", f"t{j}")
        edge(f"eb{i}", f"b{i}", f"b{j}")
        edge(f"ev{i}", f"b{i}", f"t{i}")

    top_inner, bottom_inner, hole_area = [], [], 0.0
    for k, (cx, cy, d) in enumerate(holes):
        cx, cy, r = cx * M, cy * M, d / 2 * M
        hole_area += math.pi * r * r
        for z, tag in ((t, "t"), (0.0, "b")):
            verts[f"h{k}{tag}0"] = (cx + r, cy, z)
            verts[f"h{k}{tag}1"] = (cx - r, cy, z)
            circle = {"type": "CIRCLE", "radius": r, "origin": _v((cx, cy, z)), "normal": _v((0, 0, 1))}
            edge(f"h{k}{tag}a", f"h{k}{tag}0", f"h{k}{tag}1", circle, (cx, cy + r, z))
            edge(f"h{k}{tag}b", f"h{k}{tag}1", f"h{k}{tag}0", circle, (cx, cy - r, z))
        edge(f"h{k}s0", f"h{k}b0", f"h{k}t0")
        edge(f"h{k}s1", f"h{k}b1", f"h{k}t1")
        top_inner.append({"isOuter": False, "isInner": True,
                          "coedges": [co(f"h{k}ta", f"h{k}t1"), co(f"h{k}tb", f"h{k}t0")]})
        bottom_inner.append({"isOuter": False, "isInner": True,
                             "coedges": [co(f"h{k}ba", f"h{k}b0"), co(f"h{k}bb", f"h{k}b1")]})
        for half, (a, b) in enumerate((("a", "s1"), ("b", "s0"))):
            faces.append({"id": f"H{k}{half}", "area": math.pi * r * t, "orientation": False,
                          "surface": {"type": "CYLINDER", "axis": _v((0, 0, 1)), "radius": r, "origin": _v((cx, cy, 0))},
                          "loops": [{"isOuter": True, "isInner": False, "coedges": [
                              co(f"h{k}t{a}", f"h{k}t0"), co(f"h{k}{b}", f"h{k}t1"),
                              co(f"h{k}b{a}", f"h{k}b1"), co(f"h{k}s{half}", f"h{k}b0")]}]})

    shoelace = abs(sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))) / 2
    faces.insert(0, {"id": "TOP", "area": shoelace - hole_area, "orientation": True,
                     "surface": {"type": "PLANE", "normal": _v((0, 0, 1)), "origin": _v((0, 0, t))},
                     "loops": [{"isOuter": True, "isInner": False,
                                "coedges": [co(f"et{i}", f"t{i}") for i in range(n)]}] + top_inner})
    faces.insert(1, {"id": "BOTTOM", "area": shoelace - hole_area, "orientation": False,
                     "surface": {"type": "PLANE", "normal": _v((0, 0, 1)), "origin": _v((0, 0, 0))},
                     "loops": [{"isOuter": True, "isInner": False,
                                "coedges": [co(f"eb{i}", f"b{(i + 1) % n}") for i in reversed(range(n))]}]
                     + bottom_inner})
    for i in range(n):
        j = (i + 1) % n
        dx, dy = pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]
        length = math.hypot(dx, dy)
        faces.append({"id": f"S{i}", "area": length * t, "orientation": True,
                      "surface": {"type": "PLANE", "normal": _v((dy / length, -dx / length, 0)),
                                  "origin": _v((pts[i][0], pts[i][1], 0))},
                      "loops": [{"isOuter": True, "isInner": False, "coedges": [
                          co(f"eb{i}", f"b{i}"), co(f"ev{j}", f"b{j}"), co(f"et{i}", f"t{j}"), co(f"ev{i}", f"t{i}")]}]})
    return {"microversionId": microversion, "bodies": [{
        "id": pid, "type": "SOLID", "faces": faces, "edges": list(edges.values()),
        "vertices": [{"id": k, "point": _v(p)} for k, p in verts.items()]}]}


def rectangle(w, h):
    return [(0, 0), (w, 0), (w, h), (0, h)]
