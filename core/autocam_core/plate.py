"""Is this part a plate we can cut, which side is up, and what features does it have?

Rules (brief, "Existing code" and "Not implemented yet"):
- exactly one solid body; thickness within tolerance of one of the material's stock thicknesses;
- flat top and bottom, vertical walls; no chamfers/countersinks, edge fillets or 3D faces;
- pockets are fine if they all open to one side; that side is cut facing up. Features from both
  sides are rejected (two-sided parts are out of scope);
- sharp inside corners are a warning (the router leaves its radius there).
Tool-dependent rules (hole sizes, inside radii) are in tooling.py.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from . import errors as E
from .errors import Issue
from .geometry import CYLINDER, PLANE, PartGeometry
from .holes import Hole, group_cylinders

HEIGHT_TOL_IN = 0.001
_FLAT = 1 - 1e-4      # |normal_dot| above this: faces up or down
_WALL = 1e-4          # |normal_dot| below this: a vertical wall


@dataclass(frozen=True)
class Loop:
    """An inner loop of an up-facing flat face (the top, or a pocket floor) that goes through the plate."""
    face_id: int
    index: int
    wall_face_ids: Tuple[int, ...]
    hole: Optional[int] = None      # index into PlateAnalysis.through_holes if the loop is one round hole


@dataclass(frozen=True)
class PlateAnalysis:
    name: str
    errors: Tuple[Issue, ...]
    warnings: Tuple[Issue, ...]
    measured_thickness_in: float
    stock_thickness_in: Optional[float]
    flipped: bool = False                       # cut with the extractor's axis pointing down
    geometry: Optional[PartGeometry] = None     # oriented so the plate axis points up (pocket side up)
    top_face_ids: Tuple[int, ...] = ()
    pocket_floor_ids: Tuple[int, ...] = ()
    through_holes: Tuple[Hole, ...] = ()
    inside_radii_in: Tuple[float, ...] = ()     # inside corners and round pockets; must suit the tool
    through_loops: Tuple[Loop, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def up_face_id(self) -> Optional[int]:
        """The face Arrange should lay face-up: the largest top face."""
        if not self.top_face_ids or self.geometry is None:
            return None
        return max(self.top_face_ids, key=lambda i: self.geometry.face(i).area_in2)


def snap_thickness(measured_in: float, stock_in: Sequence[float], tol_in: float) -> Optional[float]:
    matches = [t for t in stock_in if abs(measured_in - t) <= tol_in + 1e-9]
    return min(matches, key=lambda t: abs(measured_in - t)) if matches else None


def _fmt_list(values: Sequence[float]) -> str:
    return ", ".join(f"{v:g}" for v in values)


def analyze(geom: PartGeometry, thicknesses_in: Sequence[float], thickness_tol_in: float) -> PlateAnalysis:
    return _analyze(geom, thicknesses_in, thickness_tol_in, flipped=False)


def _analyze(geom: PartGeometry, thicknesses_in, thickness_tol_in, flipped: bool) -> PlateAnalysis:
    errors: List[Issue] = []
    warnings: List[Issue] = []
    t = geom.thickness_in

    if geom.solid_bodies != 1:
        return PlateAnalysis(geom.name, (Issue(E.BODY_COUNT, f"{geom.solid_bodies} solid bodies, expected exactly 1"),),
                             (), t, None)

    stock = snap_thickness(t, thicknesses_in, thickness_tol_in)
    if stock is None:
        errors.append(Issue(E.THICKNESS_NOT_STOCK,
                            f'thickness {t:.4f}" is not a stock thickness ({_fmt_list(thicknesses_in)} in)'))

    tops, bottoms, up_floors, down_floors = [], [], [], []
    seen_codes = set()

    def once(code: str, msg: str) -> None:
        if code not in seen_codes:
            seen_codes.add(code)
            errors.append(Issue(code, msg))

    for f in geom.faces:
        if f.kind == PLANE:
            nd = f.normal_dot
            if abs(nd) >= _FLAT:
                z = (f.z_min_in + f.z_max_in) / 2
                if nd > 0:
                    if z >= t - HEIGHT_TOL_IN:
                        tops.append(f.id)
                    elif z > HEIGHT_TOL_IN:
                        up_floors.append(f.id)
                    else:
                        once(E.NOT_A_PLATE, "a flat face points up from the bottom of the part")
                else:
                    if z <= HEIGHT_TOL_IN:
                        bottoms.append(f.id)
                    elif z < t - HEIGHT_TOL_IN:
                        down_floors.append(f.id)
                    else:
                        once(E.NOT_A_PLATE, "a flat face points down from the top of the part")
            elif abs(nd) > _WALL:
                once(E.CHAMFER, "angled flat face (chamfer or countersink?)")
        elif f.kind == CYLINDER:
            if f.axis_dot < 1 - 1e-4:
                once(E.EDGE_FILLET, "curved face across the thickness (edge fillet?)")
        else:
            once(E.UNSUPPORTED_FACE, f"{f.kind} face (countersink, 3D surface, ...); only flat and vertical faces can be cut")

    if not tops or not bottoms:
        once(E.NOT_A_PLATE, "no flat top and bottom; not a plate")
    if up_floors and down_floors:
        once(E.TWO_SIDED, "pockets or counterbores from both sides; two-sided parts can't be cut")
    elif down_floors and not flipped:
        # Every floor opens downward from the extractor's axis: cut it the other way up.
        return _analyze(geom.flipped(), thicknesses_in, thickness_tol_in, flipped=True)

    if geom.sharp_inside_corners:
        warnings.append(Issue(E.SHARP_INSIDE_CORNERS,
                              f"{geom.sharp_inside_corners} sharp inside corner(s); the router leaves its radius "
                              "there (add dogbones if something mates)"))

    holes = group_cylinders(geom.faces)
    through = tuple(h for h in holes if h.full and h.z_min_in <= HEIGHT_TOL_IN)
    inside_radii = tuple(sorted(h.diameter_in / 2 for h in holes if not h.full or h.z_min_in > HEIGHT_TOL_IN))

    loops: List[Loop] = []
    by_faces = {frozenset(h.face_ids): i for i, h in enumerate(through)}
    for face_id in tops + up_floors:
        for index, walls in enumerate(geom.face(face_id).inner_loops):
            if all(geom.face(w).z_min_in <= HEIGHT_TOL_IN for w in walls):
                loops.append(Loop(face_id, index, tuple(walls), by_faces.get(frozenset(walls))))

    return PlateAnalysis(
        name=geom.name, errors=tuple(errors), warnings=tuple(warnings), measured_thickness_in=t,
        stock_thickness_in=stock, flipped=flipped, geometry=geom, top_face_ids=tuple(tops),
        pocket_floor_ids=tuple(up_floors), through_holes=through, inside_radii_in=inside_radii,
        through_loops=tuple(loops),
    )
