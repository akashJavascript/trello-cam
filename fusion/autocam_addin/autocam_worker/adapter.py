"""Every Fusion call the pipeline makes, as one interface.

pipeline.py talks only to this, so the whole job flow is tested offline against a fake
(tests/fusion/fakeadapter.py). FusionAdapter (fx_adapter.py) is the real one and the only code that
imports adsk.

- Units: inches everywhere here. The Fusion adapter converts to and from cm at its edge.
- Names: the pipeline names every part copy ("p03.2") and every sheet (its program name). The adapter
  keeps the Fusion objects behind those names.
- Face ids are the ids in the PartGeometry that extract() returned for that copy's part. Every copy of
  a part has the same ids.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from autocam_core.geometry import PartGeometry

Rect = Tuple[float, float, float, float]   # x0, y0, x1, y1

# Op tags, in template order (fusion/templates/README.md).
DRILL, BORE, BEARING, POCKET, INNER, OUTER = "drill", "bore", "bearing", "pocket", "inner", "outer"
TAGS = (DRILL, BORE, BEARING, POCKET, INNER, OUTER)


class AdapterError(Exception):
    """A Fusion call failed. The message says which call and what happened."""


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    z0: float
    x1: float
    y1: float
    z1: float

    @property
    def xy(self) -> Rect:
        return self.x0, self.y0, self.x1, self.y1


@dataclass(frozen=True)
class TemplateOp:
    name: str
    tool_guid: Optional[str]          # None if the tool couldn't be read


@dataclass(frozen=True)
class OpState:
    name: str
    has_toolpath: bool
    error: Optional[str]
    warning: Optional[str]


@dataclass(frozen=True)
class OpFill:
    """What one tagged op on one sheet cuts."""
    tag: str
    holes: Tuple[Tuple[str, Tuple[int, ...]], ...] = ()     # drill/bore/bearing: (copy id, the hole's face ids)
    loops: Tuple[Tuple[str, int, int], ...] = ()            # inner: (copy id, face id, inner loop index)
    floors: Tuple[Tuple[str, int], ...] = ()                # pocket: (copy id, floor face id)


class Adapter:
    """The interface. Methods raise AdapterError when Fusion refuses."""

    # Op tags this adapter can fill. A part needing anything else is sent back for manual CAM.
    capabilities: frozenset = frozenset()

    def versions(self) -> Tuple[str, str]:
        """(Fusion version, Python version)."""
        raise NotImplementedError

    def untested(self) -> Tuple[str, ...]:
        """Steps this run used that haven't been seen working in Fusion yet."""
        raise NotImplementedError

    # -- design
    def begin(self, job_id: str) -> None:
        """A new, empty design document in inches."""
        raise NotImplementedError

    def import_step(self, copy_id: str, path: str) -> None:
        """Import one STEP file as one occurrence named copy_id."""
        raise NotImplementedError

    def extract(self, copy_id: str) -> PartGeometry:
        raise NotImplementedError

    def add_copy(self, source_id: str, copy_id: str) -> None:
        """Another occurrence of source_id's part, at the same place."""
        raise NotImplementedError

    def arrange(self, copy_ids: Sequence[str], envelope: Rect, spacing_in: float,
                up_faces: Mapping[str, int]) -> List[str]:
        """Nest what fits into one envelope; returns the copies placed. The rest stay where they were.

        up_faces: for each copy, the face that must end up facing +Z (a pocket's open side)."""
        raise NotImplementedError

    def box(self, copy_id: str) -> Box:
        raise NotImplementedError

    def faces_up(self, copy_id: str, face_id: int) -> bool:
        """After arranging: does that face point +Z?"""
        raise NotImplementedError

    def delete(self, copy_id: str) -> None:
        raise NotImplementedError

    # -- CAM
    def make_sheet(self, sheet: str, origin: Tuple[float, float], length_in: float, width_in: float,
                   thickness_in: float, copy_ids: Sequence[str]) -> None:
        """Stock solid for the full sheet at origin, and a setup with the copies as models, stock from that
        solid, and the work origin at the stock's bottom front-left corner."""
        raise NotImplementedError

    def apply_template(self, sheet: str, template_path: str) -> List[TemplateOp]:
        raise NotImplementedError

    def fill(self, sheet: str, op_name: str, fill: OpFill) -> None:
        """Select what the op cuts. Inner and pocket ops also get bottom height = stock bottom where that
        applies (inner: yes, pocket: no)."""
        raise NotImplementedError

    def make_outer_ops(self, sheet: str, template_op: str, outlines: Sequence[Tuple[str, str, int]]) -> None:
        """One copy of the template's outline op per (new op name, copy id, top face id), in that order,
        each cutting to stock bottom; then delete the template op."""
        raise NotImplementedError

    def delete_op(self, sheet: str, op_name: str) -> None:
        raise NotImplementedError

    def generate(self, sheets: Sequence[str]) -> Dict[str, List[OpState]]:
        raise NotImplementedError

    def post(self, sheet: str, program_name: str, folder: Path, post_path: str,
             properties: Mapping[str, object]) -> Path:
        """Post with the pinned .cps file into an empty folder; returns the program file."""
        raise NotImplementedError

    def machining_time(self, sheet: str) -> Optional[float]:
        raise NotImplementedError

    # -- outputs
    def preview(self, sheet: str, rect: Rect, path: Path) -> None:
        raise NotImplementedError

    def export_f3d(self, path: Path) -> None:
        raise NotImplementedError

    def finish(self, keep_open: bool) -> None:
        """Close the document without saving (unless keep_open)."""
        raise NotImplementedError
