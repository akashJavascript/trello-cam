"""A fake Fusion for the pipeline tests: the Adapter interface over plain Python state.

- Parts: tests register a PartGeometry and a footprint (w, h) per STEP path.
- Arrange packs copies in shelves from the envelope's corner, part spacing apart, and leaves what doesn't
  fit where it was (like Arrange with partial arrange on).
- Templates: the template file holds a JSON list of [op name, tool GUID].
- Posting writes a program shaped like the pinned post's output (tests/fixtures/taps/fusion_*.tap): op
  comments, the spindle start after the first op, rapids at the clearance height, a G81 per drilled
  hole, and outlines offset by the tool radius plus a lead-in, so the clamp-strip check sees realistic
  tool paths.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from autocam_core.geometry import PartGeometry
from autocam_core.names import comment_line

from autocam_worker.adapter import (
    BEARING, BORE, DRILL, INNER, OUTER, TAGS, Adapter, AdapterError, Arranged, Box, OpFill, OpState, TemplateOp,
)

OUTLINE_OFFSET_IN = 0.0787 + 0.05     # 4 mm tool radius + lead-in


def _tag(name: str) -> Optional[str]:
    found = [t for t in TAGS if f"[{t}]" in name.lower()]
    return found[0] if len(found) == 1 else None


class FakeAdapter(Adapter):
    capabilities = frozenset({DRILL, BORE, BEARING, INNER, OUTER})

    def __init__(self):
        self.parts: Dict[str, Tuple[PartGeometry, Tuple[float, float]]] = {}
        self.copies: Dict[str, str] = {}               # copy id -> STEP path
        self.boxes: Dict[str, Box] = {}
        self.sheets: Dict[str, dict] = {}
        self.calls: List[str] = []
        self.upside_down: Set[str] = set()             # part keys Arrange turns over
        self.below_floor: Set[str] = set()             # sheet names whose program dips below Z0
        self.fail_import: Set[str] = set()             # STEP paths that won't import
        self.refuse: Set[str] = set()                  # part keys Arrange won't lay flat
        self.arrange_error: Optional[str] = None       # every Arrange raises this
        self.bad_z: Set[str] = set()                   # copy ids Arrange leaves floating
        self.move_on_discard: Dict[str, Tuple[float, float]] = {}    # copy id -> shift when anything is discarded
        self.move_on_generate: Dict[str, Tuple[float, float]] = {}   # copy id -> shift during toolpath generation
        self.fail_make_sheet: Set[str] = set()         # sheet names
        self.op_states: Dict[str, Tuple[bool, Optional[str], Optional[str]]] = {}  # op name -> state
        self.fail_post: Set[str] = set()               # sheet names
        self.gouges: Dict[str, List[Tuple[int, float, float]]] = {}   # sheet -> points reported inside a part
        self.points_checked: Dict[str, int] = {}
        self.team_error: Optional[str] = None
        self.saved_to_team: List[Tuple[str, str, str]] = []
        self.arrange_envelopes: List[Tuple[float, float, float, float]] = []
        self.finished: Optional[bool] = None

    def register(self, step: Path, geometry: PartGeometry, size: Tuple[float, float]) -> None:
        self.parts[str(step)] = (geometry, size)

    # -- Adapter
    def versions(self):
        return "fake", "3"

    def untested(self):
        return ("fake adapter",)

    def begin(self, job_id):
        self.calls.append(f"begin {job_id}")

    def import_step(self, copy_id, path):
        if path in self.fail_import or path not in self.parts:
            raise AdapterError(f"importToTarget failed for {path}")
        self.copies[copy_id] = path
        geometry, (w, h) = self.parts[path]
        self.boxes[copy_id] = Box(0.0, 0.0, 0.0, w, h, geometry.thickness_in)

    def extract(self, copy_id):
        return self.parts[self.copies[copy_id]][0]

    def add_copy(self, source_id, copy_id):
        self.copies[copy_id] = self.copies[source_id]
        self.boxes[copy_id] = self.boxes[source_id]

    def arrange(self, copy_ids, envelope, spacing_in, up_faces):
        self.arrange_envelopes.append(envelope)
        if self.arrange_error:
            raise AdapterError(self.arrange_error)
        x0, y0, x1, y1 = envelope
        x, y, row = x0, y0, 0.0
        placed, refused = [], {}
        for cid in copy_ids:
            if cid.rsplit(".", 1)[0] in self.refuse:
                refused[cid] = "upDirection (0, 1, 0) is across the top face"
                continue
            geometry, (w, h) = self.parts[self.copies[cid]]
            px, py, prow = x, y, row
            if px + w > x1 + 1e-9:
                px, py, prow = x0, y + row + spacing_in, 0.0
            if px + w > x1 + 1e-9 or py + h > y1 + 1e-9:
                continue                      # doesn't fit; the next one might
            z0 = 0.5 if cid in self.bad_z else 0.0
            self.boxes[cid] = Box(px, py, z0, px + w, py + h, z0 + geometry.thickness_in)
            placed.append(cid)
            x, y, row = px + w + spacing_in, py, max(prow, h)
        return Arranged(tuple(placed), refused)

    def box(self, copy_id):
        return self.boxes[copy_id]

    def faces_up(self, copy_id, face_id):
        return copy_id.rsplit(".", 1)[0] not in self.upside_down

    def _shift(self, moves):
        for cid, (dx, dy) in moves.items():
            b = self.boxes[cid]
            self.boxes[cid] = Box(b.x0 + dx, b.y0 + dy, b.z0, b.x1 + dx, b.y1 + dy, b.z1)
        moves.clear()

    def discard(self, copy_id):
        self.calls.append(f"discard {copy_id}")
        self._shift(self.move_on_discard)
        self.copies.pop(copy_id, None)
        self.boxes.pop(copy_id, None)

    def make_sheet(self, sheet, origin, length_in, width_in, thickness_in, copy_ids):
        if sheet in self.fail_make_sheet:
            raise AdapterError("setups.add: RuntimeError: 3 : something broke")
        self.sheets[sheet] = {"origin": origin, "size": (length_in, width_in), "t": thickness_in,
                              "copies": list(copy_ids), "ops": [], "fills": {}, "outer": [], "deleted": []}

    def apply_template(self, sheet, template_path):
        ops = [TemplateOp(name, guid) for name, guid in json.loads(Path(template_path).read_text())]
        self.sheets[sheet]["ops"] = [o.name for o in ops]
        return ops

    def fill(self, sheet, op_name, fill: OpFill):
        self.sheets[sheet]["fills"][op_name] = fill

    def make_outer_ops(self, sheet, template_op, outlines):
        s = self.sheets[sheet]
        s["ops"].remove(template_op)
        s["ops"] += [name for name, _, _ in outlines]
        s["outer"] = list(outlines)

    def delete_op(self, sheet, op_name):
        s = self.sheets[sheet]
        s["ops"].remove(op_name)
        s["deleted"].append(op_name)

    def generate(self, sheets):
        self._shift(self.move_on_generate)
        return {name: [OpState(op, *self.op_states.get(op, (True, None, None))) for op in self.sheets[name]["ops"]]
                for name in sheets}

    def _center(self, s: dict, cid: str) -> Tuple[float, float]:
        b = self.boxes[cid]
        return (b.x0 + b.x1) / 2 - s["origin"][0], (b.y0 + b.y1) / 2 - s["origin"][1]

    def post(self, sheet, program_name, folder, post_path, properties):
        if sheet in self.fail_post:
            raise AdapterError("CAM.postProcess: RuntimeError: 3 : post failed")
        s = self.sheets[sheet]
        t = s["t"]
        clear, retract = t + 2.0, t + 0.2
        mist = bool(properties.get("useMist"))
        lines = [f"[{program_name}]"] + (["M11 C8"] if mist else []) + ["G90", "G20", "G53 Z"]
        outer = {name: cid for name, cid, _ in s["outer"]}
        for i, op in enumerate(s["ops"]):
            lines.append(comment_line(op))
            if i == 0:
                lines += ["S18000", "M3", "G4 X4."]
            tag = _tag(op)
            if tag == OUTER:
                b = self.boxes[outer[op]]
                ox, oy = s["origin"]
                o = OUTLINE_OFFSET_IN
                xa, ya, xb, yb = b.x0 - ox - o, b.y0 - oy - o, b.x1 - ox + o, b.y1 - oy + o
                lines += [f"G0 X{xa:.4f} Y{ya:.4f}", f"Z{retract:g}", "G1 Z0. F20.", f"G1 X{xb:.4f} F60.",
                          f"G1 Y{yb:.4f}", f"G1 X{xa:.4f}", f"G1 Y{ya:.4f}", f"G0 Z{clear:g}"]
                continue
            fill = s["fills"][op]
            for cid in [c for c, _ in fill.holes] + [c for c, _, _ in fill.loops]:
                x, y = self._center(s, cid)
                if tag == DRILL:
                    lines += [f"G0 X{x:.4f} Y{y:.4f}", f"Z{retract:g}", f"G81 X{x:.4f} Y{y:.4f} Z0. R{retract:g} F20.",
                              "G80", f"G0 Z{clear:g}"]
                else:
                    lines += [f"G0 X{x:.4f} Y{y:.4f}", f"Z{retract:g}", "G1 Z0. F20.", f"G1 X{x + 0.05:.4f} F60.",
                              f"G0 Z{clear:g}"]
        lines += (["M12 C8"] if mist else []) + ["G53 Z", "M5", "G53 P10"]
        text = "\r\n".join(lines) + "\r\n"
        if sheet in self.below_floor:
            text = text.replace("G1 Z0. F20.", "G1 Z-0.02 F20.", 1)
        folder.mkdir(parents=True, exist_ok=True)
        if any(folder.iterdir()):
            raise AdapterError("post folder isn't empty")
        path = folder / f"{program_name}.tap"
        path.write_bytes(text.encode("ascii"))
        return path

    def cuts_into_parts(self, sheet, points):
        self.points_checked[sheet] = len(points)
        return self.gouges.get(sheet, [])

    def machining_time(self, sheet):
        return 600.0

    def preview(self, sheet, rect, path):
        path.write_bytes(b"\x89PNG fake")

    def export_f3d(self, path):
        path.write_bytes(b"fake f3d")

    def save_to_team(self, name, project, folder):
        if self.team_error:
            raise AdapterError(self.team_error)
        self.saved_to_team.append((name, project, folder))
        return f"https://team.example/{project}/{name}", name

    def finish(self, keep_open):
        self.finished = keep_open


def placed_copies(fake: FakeAdapter, sheet: str) -> Sequence[str]:
    return fake.sheets[sheet]["copies"]
