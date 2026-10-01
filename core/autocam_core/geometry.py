"""What the Fusion extractor reports about one imported part, in inches.

The extractor (the only code that walks BRep) picks a plate axis: the normal of the largest
flat face. It measures every face along that axis, with 0 = the bottom of the part's oriented
bounding box and thickness_in = the top. Everything else (is it a plate, which side is up,
what are the holes, which tool) is decided offline in plate.py, holes.py and tooling.py.
"""

from dataclasses import asdict, dataclass, replace
from typing import Any, Dict, Tuple

PLANE = "plane"
CYLINDER = "cylinder"


@dataclass(frozen=True)
class Face:
    id: int
    kind: str                       # "plane", "cylinder", or Fusion's name for anything else ("cone", "torus", ...)
    z_min_in: float                 # extent along the plate axis (0 = bottom of the part)
    z_max_in: float
    area_in2: float = 0.0
    normal_dot: float = 0.0         # planes: dot(outward normal, plate axis); +1 faces up, -1 faces down, 0 = wall
    radius_in: float = 0.0          # cylinders
    axis_dot: float = 0.0           # cylinders: |dot(axis, plate axis)|; 1 = a vertical wall
    concave: bool = False           # cylinders: wraps around empty space (a hole or an inside corner)
    center_in: Tuple[float, float] = (0.0, 0.0)  # cylinders: where the axis crosses the plate plane
    sweep_deg: float = 0.0          # cylinders: how far the face goes around its axis (STEP often splits holes in two)
    inner_loops: Tuple[Tuple[int, ...], ...] = ()  # planes: for each inner loop, the ids of its wall faces

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Face":
        data = dict(data)
        data["center_in"] = tuple(data.get("center_in", (0.0, 0.0)))
        data["inner_loops"] = tuple(tuple(loop) for loop in data.get("inner_loops", ()))
        return cls(**data)


@dataclass(frozen=True)
class PartGeometry:
    name: str
    solid_bodies: int
    thickness_in: float             # oriented bounding box extent along the plate axis
    faces: Tuple[Face, ...]
    sharp_inside_corners: int = 0   # through-thickness concave edges with no radius (counted by the extractor)

    def flipped(self) -> "PartGeometry":
        """The same part turned over: heights measured from the other side, normals reversed.

        Positions in the plate plane are left alone; they're only compared with each other.
        """
        t = self.thickness_in
        faces = tuple(
            replace(f, z_min_in=t - f.z_max_in, z_max_in=t - f.z_min_in, normal_dot=-f.normal_dot)
            for f in self.faces)
        return replace(self, faces=faces)

    def face(self, face_id: int) -> Face:
        for f in self.faces:
            if f.id == face_id:
                return f
        raise KeyError(f"{self.name}: no face {face_id}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PartGeometry":
        return cls(
            name=data["name"], solid_bodies=int(data["solid_bodies"]), thickness_in=float(data["thickness_in"]),
            faces=tuple(Face.from_dict(f) for f in data["faces"]),
            sharp_inside_corners=int(data.get("sharp_inside_corners", 0)),
        )
