"""Just enough of adsk.core / adsk.fusion BRep to run fx_geometry.extract offline (cm, like Fusion).

plate(): a w x h x t inch box on Z0 with round through-holes, each split into two half cylinders the way
STEP files usually arrive. It checks the extractor's arithmetic and bookkeeping (axis, heights, loops,
hole grouping); it says nothing about how real Fusion objects behave.
"""

import math
import sys
import types

IN = 2.54


class Vector3D:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)

    @staticmethod
    def create(x=0.0, y=0.0, z=0.0):
        return Vector3D(x, y, z)

    def dotProduct(self, o):
        return self.x * o.x + self.y * o.y + self.z * o.z

    def crossProduct(self, o):
        return Vector3D(self.y * o.z - self.z * o.y, self.z * o.x - self.x * o.z, self.x * o.y - self.y * o.x)

    @property
    def length(self):
        return math.sqrt(self.dotProduct(self))

    def normalize(self):
        n = self.length
        self.x, self.y, self.z = self.x / n, self.y / n, self.z / n
        return True

    def copy(self):
        return Vector3D(self.x, self.y, self.z)

    def scaleBy(self, s):
        self.x, self.y, self.z = self.x * s, self.y * s, self.z * s

    def subtract(self, o):
        self.x, self.y, self.z = self.x - o.x, self.y - o.y, self.z - o.z


class Point3D(Vector3D):
    @staticmethod
    def create(x=0.0, y=0.0, z=0.0):
        return Point3D(x, y, z)

    def vectorTo(self, o):
        return Vector3D(o.x - self.x, o.y - self.y, o.z - self.z)

    def translateBy(self, v):
        self.x, self.y, self.z = self.x + v.x, self.y + v.y, self.z + v.z


class Collection(list):
    @property
    def count(self):
        return len(self)

    def item(self, i):
        return self[i]


PLANE, CYLINDER = 0, 1
LINE, ARC = "adsk::core::Line3D", "adsk::core::Arc3D"
INSIDE, OUTSIDE = 0, 1


class Vertex:
    def __init__(self, p):
        self.geometry = p


class Edge:
    def __init__(self, kind, a, b, mid):
        self.geometry = types.SimpleNamespace(objectType=kind)
        self.startVertex, self.endVertex = Vertex(a), Vertex(b)
        self.pointOnEdge = mid
        self.faces = Collection()


class Loop:
    def __init__(self, outer, edges):
        self.isOuter = outer
        self.edges = Collection(edges)


class Face:
    def __init__(self, temp_id, surface, point, normal_fn, area, edges, loops=()):
        self.tempId = temp_id
        self.geometry = surface
        self.pointOnFace = point
        self.evaluator = types.SimpleNamespace(getNormalAtPoint=lambda p: (True, normal_fn(p)))
        self.area = area
        self.edges = Collection(edges)
        self.loops = Collection(loops)
        verts = []
        for e in edges:
            for v in (e.startVertex, e.endVertex):
                if all((v.geometry.x, v.geometry.y, v.geometry.z) != (u.geometry.x, u.geometry.y, u.geometry.z)
                       for u in verts):
                    verts.append(v)
        self.vertices = Collection(verts)
        for e in edges:
            e.faces.append(self)


def plate(w, h, t, holes=()):
    """holes: (diameter, (cx, cy)) in inches. Returns an occurrence-like object."""
    W, H, T = w * IN, h * IN, t * IN
    P = Point3D
    c = [P(0, 0, 0), P(W, 0, 0), P(W, H, 0), P(0, H, 0)]
    ct = [P(p.x, p.y, T) for p in c]

    def line(a, b):
        return Edge(LINE, a, b, P((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2))

    bottom_edges = [line(c[i], c[(i + 1) % 4]) for i in range(4)]
    top_edges = [line(ct[i], ct[(i + 1) % 4]) for i in range(4)]
    vertical = [line(c[i], ct[i]) for i in range(4)]
    faces = []
    tid = iter(range(1000, 2000))
    up, down = (lambda p: Vector3D(0, 0, 1)), (lambda p: Vector3D(0, 0, -1))

    hole_top_loops, hole_bottom_loops, cylinders, hole_area = [], [], [], 0.0
    for d, (cx, cy) in holes:
        r, X, Y = d / 2 * IN, cx * IN, cy * IN
        s0, s1 = P(X + r, Y, 0), P(X - r, Y, 0)
        t0, t1 = P(X + r, Y, T), P(X - r, Y, T)
        top_arcs = [Edge(ARC, t0, t1, P(X, Y + r, T)), Edge(ARC, t1, t0, P(X, Y - r, T))]
        bottom_arcs = [Edge(ARC, s0, s1, P(X, Y + r, 0)), Edge(ARC, s1, s0, P(X, Y - r, 0))]
        seams = [line(s0, t0), line(s1, t1)]
        hole_top_loops.append(Loop(False, top_arcs))
        hole_bottom_loops.append(Loop(False, bottom_arcs))
        cyl = types.SimpleNamespace(surfaceType=CYLINDER, origin=P(X, Y, 0), axis=Vector3D(0, 0, 1), radius=r)

        def inward(p, X=X, Y=Y):
            v = Vector3D(X - p.x, Y - p.y, 0)    # a hole's wall faces into the hole
            v.normalize()
            return v

        for half, mid in ((0, P(X, Y + r, T / 2)), (1, P(X, Y - r, T / 2))):
            cylinders.append((cyl, mid, inward, math.pi * r * T, [top_arcs[half], bottom_arcs[half]] + seams))
        hole_area += math.pi * r * r

    faces.append(Face(next(tid), types.SimpleNamespace(surfaceType=PLANE), P(W / 2, H / 2, T), up,
                      W * H - hole_area, top_edges + [e for lp in hole_top_loops for e in lp.edges],
                      [Loop(True, top_edges)] + hole_top_loops))
    faces.append(Face(next(tid), types.SimpleNamespace(surfaceType=PLANE), P(W / 2, H / 2, 0), down,
                      W * H - hole_area, bottom_edges + [e for lp in hole_bottom_loops for e in lp.edges],
                      [Loop(True, bottom_edges)] + hole_bottom_loops))
    sides = [(Vector3D(0, -1, 0), W), (Vector3D(1, 0, 0), H), (Vector3D(0, 1, 0), W), (Vector3D(-1, 0, 0), H)]
    for i, (n, length) in enumerate(sides):
        a, b = c[i], c[(i + 1) % 4]
        faces.append(Face(next(tid), types.SimpleNamespace(surfaceType=PLANE), P((a.x + b.x) / 2, (a.y + b.y) / 2, T / 2),
                          lambda p, n=n: n.copy(), length * T,
                          [bottom_edges[i], top_edges[i], vertical[i], vertical[(i + 1) % 4]]))
    for cyl, mid, fn, area, edges in cylinders:
        faces.append(Face(next(tid), cyl, mid, fn, area, edges))

    def contains(q):
        if not (0 < q.x < W and 0 < q.y < H and 0 < q.z < T):
            return OUTSIDE
        for d, (cx, cy) in holes:
            if math.hypot(q.x - cx * IN, q.y - cy * IN) < d / 2 * IN:
                return OUTSIDE
        return INSIDE

    edges = []
    for f in faces:
        for e in f.edges:
            if e not in edges:
                edges.append(e)
    verts = []
    for e in edges:
        for v in (e.startVertex, e.endVertex):
            if v not in verts:
                verts.append(v)
    box = types.SimpleNamespace(minPoint=P(0, 0, 0), maxPoint=P(W, H, T))
    body = types.SimpleNamespace(isSolid=True, faces=Collection(faces), edges=Collection(edges),
                                 vertices=Collection(verts), pointContainment=contains, boundingBox=box)
    return types.SimpleNamespace(name="plate:1", bRepBodies=Collection([body]), childOccurrences=Collection())


def install():
    adsk = types.ModuleType("adsk")
    core = types.ModuleType("adsk.core")
    fusion = types.ModuleType("adsk.fusion")
    cam = types.ModuleType("adsk.cam")
    core.Vector3D, core.Point3D = Vector3D, Point3D
    core.SurfaceTypes = types.SimpleNamespace(PlaneSurfaceType=PLANE, CylinderSurfaceType=CYLINDER,
                                              ConeSurfaceType=2, SphereSurfaceType=3, TorusSurfaceType=4,
                                              NurbsSurfaceType=7)
    core.Line3D = types.SimpleNamespace(classType=lambda: LINE)
    core.DataEventHandler = core.CustomEventHandler = object
    fusion.PointContainment = types.SimpleNamespace(PointInsidePointContainment=INSIDE)
    adsk.core, adsk.fusion, adsk.cam = core, fusion, cam
    sys.modules.update({"adsk": adsk, "adsk.core": core, "adsk.fusion": fusion, "adsk.cam": cam})


def uninstall():
    for name in list(sys.modules):
        if name == "adsk" or name.startswith("adsk.") or name.startswith("autocam_worker.fx_"):
            del sys.modules[name]
