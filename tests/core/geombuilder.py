"""Build PartGeometry the way the Fusion extractor will report it, for offline tests."""

from typing import Dict, List, Tuple

from autocam_core.geometry import Face, PartGeometry


class PlateBuilder:
    """A rectangular plate; add holes, slots, pockets and odd faces, then build()."""

    def __init__(self, name: str = "plate", thickness: float = 0.125, axis_up: bool = True):
        self.name = name
        self.t = thickness
        self.sign = 1.0 if axis_up else -1.0     # axis_up=False: the extractor's axis points at the pocket floor side
        self.faces: List[Face] = []
        self.loops: Dict[int, List[Tuple[int, ...]]] = {}
        self.bodies = 1
        self.sharp = 0
        self.top = self._face(kind="plane", z0=self.t, z1=self.t, normal_dot=1.0, area=48.0)
        self.bottom = self._face(kind="plane", z0=0.0, z1=0.0, normal_dot=-1.0, area=48.0)
        for _ in range(4):
            self._face(kind="plane", z0=0.0, z1=self.t, normal_dot=0.0, area=1.0)

    # -- low level
    def _face(self, kind: str, z0: float, z1: float, **kw) -> int:
        fid = len(self.faces) + 1
        if kw.pop("flip_me", True) and self.sign < 0:
            z0, z1 = self.t - z1, self.t - z0
            if "normal_dot" in kw:
                kw["normal_dot"] = -kw["normal_dot"]
        area = kw.pop("area", 0.0)
        self.faces.append(Face(id=fid, kind=kind, z_min_in=z0, z_max_in=z1, area_in2=area, **kw))
        return fid

    def _cyl(self, r: float, center, sweep: float, z0: float, z1: float, concave: bool = True) -> int:
        return self._face(kind="cylinder", z0=z0, z1=z1, radius_in=r, axis_dot=1.0, concave=concave,
                          center_in=tuple(center), sweep_deg=sweep)

    def _loop(self, face_id: int, walls) -> None:
        self.loops.setdefault(face_id, []).append(tuple(walls))

    def _top_id(self) -> int:
        # With the axis flipped, the face built as "top" is the extractor's bottom; loops still go on it.
        return self.top

    # -- features
    def hole(self, d: float, center=(1.0, 1.0), split: bool = True, on: int = None, z0: float = 0.0):
        z1 = self.t if on is None else self._z_of(on)
        if split:
            walls = (self._cyl(d / 2, center, 180.0, z0, z1), self._cyl(d / 2, center, 180.0, z0, z1))
        else:
            walls = (self._cyl(d / 2, center, 360.0, z0, z1),)
        self._loop(on if on is not None else self._top_id(), walls)
        return walls

    def inside_corner(self, r: float, center=(5.0, 5.0)) -> int:
        return self._cyl(r, center, 90.0, 0.0, self.t)

    def outside_corner(self, r: float, center=(0.0, 0.0)) -> int:
        return self._cyl(r, center, 90.0, 0.0, self.t, concave=False)

    def slot(self, width: float = 0.25, center=(3.0, 3.0)):
        r = width / 2
        walls = (self._face(kind="plane", z0=0.0, z1=self.t, normal_dot=0.0),
                 self._face(kind="plane", z0=0.0, z1=self.t, normal_dot=0.0),
                 self._cyl(r, center, 180.0, 0.0, self.t),
                 self._cyl(r, (center[0] + 1.0, center[1]), 180.0, 0.0, self.t))
        self._loop(self._top_id(), walls)
        return walls

    def pocket(self, depth: float, corner_r: float = 0.1, center=(8.0, 8.0)) -> int:
        """A rectangular pocket opening on the top side; returns the floor face id."""
        zf = self.t - depth
        floor = self._face(kind="plane", z0=zf, z1=zf, normal_dot=1.0, area=4.0)
        walls = [self._face(kind="plane", z0=zf, z1=self.t, normal_dot=0.0) for _ in range(4)]
        corners = [self._cyl(corner_r, (center[0] + dx, center[1] + dy), 90.0, zf, self.t)
                   for dx, dy in ((0, 0), (2, 0), (0, 2), (2, 2))]
        self._loop(self._top_id(), walls + corners)
        self._floor_z = getattr(self, "_floor_z", {})
        self._floor_z[floor] = zf
        return floor

    def counterbore(self, d_big: float, depth: float, d_small: float, center=(12.0, 2.0)):
        zf = self.t - depth
        big = self._cyl(d_big / 2, center, 360.0, zf, self.t)
        floor = self._face(kind="plane", z0=zf, z1=zf, normal_dot=1.0, area=0.2)
        self._loop(self._top_id(), (big,))
        small = (self._cyl(d_small / 2, center, 180.0, 0.0, zf), self._cyl(d_small / 2, center, 180.0, 0.0, zf))
        self._loop(floor, small)
        return floor

    def chamfer(self) -> int:
        return self._face(kind="plane", z0=self.t - 0.03, z1=self.t, normal_dot=0.7)

    def edge_fillet(self) -> int:
        return self._face(kind="cylinder", z0=self.t - 0.03, z1=self.t, radius_in=0.03, axis_dot=0.0,
                          concave=False, sweep_deg=90.0)

    def cone(self) -> int:
        return self._face(kind="cone", z0=self.t - 0.05, z1=self.t)

    def bottom_floor(self, depth: float) -> int:
        """A pocket floor opening on the bottom side (relative to the builder's top)."""
        return self._face(kind="plane", z0=depth, z1=depth, normal_dot=-1.0, area=1.0)

    def _z_of(self, face_id: int) -> float:
        return self._floor_z[face_id]

    def build(self) -> PartGeometry:
        faces = tuple(
            Face(**{**f.__dict__, "inner_loops": tuple(self.loops.get(f.id, ()))}) for f in self.faces)
        return PartGeometry(self.name, self.bodies, self.t, faces, self.sharp)
