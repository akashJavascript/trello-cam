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
from autocam_core.tabs import Seg

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
class Arranged:
    placed: Tuple[str, ...]          # copies Arrange put in the envelope
    refused: Dict[str, str]          # copies left out on purpose, and why (e.g. it would lie on its side)


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
                up_faces: Mapping[str, int]) -> Arranged:
        """Nest what fits into one envelope. The rest stay where they were.

        up_faces: for each copy, the face that must end up facing +Z (a pocket's open side). A copy that
        can't be laid that way is refused, not arranged."""
        raise NotImplementedError

    def box(self, copy_id: str) -> Box:
        raise NotImplementedError

    def faces_up(self, copy_id: str, face_id: int) -> bool:
        """After arranging: does that face point +Z?"""
        raise NotImplementedError

    def discard(self, copy_id: str) -> None:
        """Take a copy out of the job. Hidden, not deleted: deleting an occurrence that an Arrange moved
        could make Fusion solve that Arrange again and move the parts already nested."""
        raise NotImplementedError

    # -- CAM
    def make_sheet(self, sheet: str, origin: Tuple[float, float], size_x_in: float, size_y_in: float,
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

    def set_tabs(self, sheet: str, op_name: str, per_contour: int) -> None:
        """Turn on the contour op's tabs (the template's shape, width and height), this many on each of its
        contours, spread evenly."""
        raise NotImplementedError

    def set_tab_size(self, sheet: str, op_name: str, width_in: float, height_in: float) -> None:
        """The op's tab width and height (0: leave the template's)."""
        raise NotImplementedError

    def loop_segments(self, copy_id: str, face_id: int, loop_index: Optional[int]) -> List[Seg]:
        """A loop of a face of a copy as it is now, in the design's X/Y (inches): the outer loop (loop_index
        None) or that inner loop, as straight lines and curves (polylines) in loop order."""
        raise NotImplementedError

    def set_tab_points(self, sheet: str, op_name: str, points: Sequence[Tuple[float, float]]) -> None:
        """Turn on the contour op's tabs (the template's shape, width and height) at these points (design X/Y,
        inches; on its contours)."""
        raise NotImplementedError

    def copy_op(self, sheet: str, op_name: str, new_name: str) -> None:
        """Another op like op_name (from the template, before it's filled), at the end of the setup."""
        raise NotImplementedError

    def delete_op(self, sheet: str, op_name: str) -> None:
        raise NotImplementedError

    def generate(self, sheets: Sequence[str]) -> Dict[str, List[OpState]]:
        raise NotImplementedError

    def post(self, sheet: str, program_name: str, folder: Path, post_path: str,
             properties: Mapping[str, object]) -> Path:
        """Post with the pinned .cps file into an empty folder; returns the program file."""
        raise NotImplementedError

    def cuts_into_parts(self, sheet: str, points: Sequence[Tuple[int, float, float, float]]
                        ) -> List[Tuple[int, float, float]]:
        """points: (line, x, y, z) where the tool center goes below the stock top, in sheet coordinates.
        Returns the ones inside a part on that sheet (the tool center must always be in air)."""
        raise NotImplementedError

    def notes(self) -> Tuple[str, ...]:
        """Anything worth keeping from this run that isn't an error (e.g. API details seen for the first time)."""
        return ()

    def machining_time(self, sheet: str) -> Optional[float]:
        raise NotImplementedError

    # -- outputs
    def preview(self, sheet: str, rect: Rect, path: Path) -> None:
        raise NotImplementedError

    def export_f3d(self, path: Path) -> None:
        raise NotImplementedError

    def save_to_team(self, name: str, project: str, folder: str) -> Tuple[Optional[str], str]:
        """Save the document into a Fusion Team project folder ("a/b" for nested folders) and wait for it to
        reach the cloud. Returns (web link or None, the saved file's name)."""
        raise NotImplementedError

    def finish(self, keep_open: bool) -> None:
        """Close the document without saving (unless keep_open)."""
        raise NotImplementedError
