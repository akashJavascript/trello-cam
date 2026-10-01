"""UNTESTED IN FUSION. FusionAdapter: the Adapter interface (adapter.py) over the real Fusion API.

Keeps the Fusion objects behind the pipeline's names (copy ids, sheet names). Occurrences are looked up by
entity token, so a reference that went stale after a timeline change is found again.
"""

import functools
import sys
from pathlib import Path
from typing import Dict, List

from autocam_core.schema_job import Job

from . import fx_cam, fx_design, fx_geometry
from .adapter import BEARING, BORE, DRILL, INNER, OUTER, Adapter, AdapterError, Box
from .fx_cam import UNCONFIRMED
from .fx_util import call


class FusionAdapter(Adapter):
    capabilities = frozenset({DRILL, BORE, BEARING, INNER, OUTER})   # pocket: selection name still unknown

    def __init__(self, app, job: Job, generate_timeout_s: float = 900.0):
        self.app = app
        self.job = job
        self.timeout = generate_timeout_s
        self.doc = self.design = self.cam = None
        self.tokens: Dict[str, str] = {}       # copy id -> occurrence entity token
        self.setups: Dict[str, object] = {}     # sheet -> setup
        self.used: List[str] = []

    def _used(self, key: str) -> None:
        if UNCONFIRMED[key] not in self.used:
            self.used.append(UNCONFIRMED[key])

    def versions(self):
        return self.app.version, sys.version.split()[0]

    def untested(self):
        return tuple(self.used)

    # -- design
    def begin(self, job_id):
        self.doc, self.design = call("new design document", fx_design.new_design, self.app)

    def _occ(self, copy_id):
        token = self.tokens.get(copy_id)
        if token is None:
            raise AdapterError(f"no occurrence for {copy_id}")
        found = self.design.findEntityByToken(token)
        if not found:
            raise AdapterError(f"occurrence for {copy_id} is gone")
        return found[0]

    def import_step(self, copy_id, path):
        occ = fx_design.import_step(self.app, self.design, path, copy_id.rsplit(".", 1)[0])
        self.tokens[copy_id] = occ.entityToken

    def extract(self, copy_id):
        return call("extract geometry", fx_geometry.extract, copy_id.rsplit(".", 1)[0], self._occ(copy_id))

    def add_copy(self, source_id, copy_id):
        self.tokens[copy_id] = fx_design.add_copy(self.design, self._occ(source_id)).entityToken

    def arrange(self, copy_ids, envelope, spacing_in, up_faces):
        self._used("arrange_flip")
        nest = self.job.nest
        result = fx_design.arrange(self.design, [(c, self._occ(c)) for c in copy_ids], envelope, spacing_in,
                                   up_faces, nest.rotation, nest.part_in_part)
        if result.refused:
            self._used("arrange_refuse")
        return result

    def box(self, copy_id):
        return Box(*call("bounding box", fx_design.box_in, self._occ(copy_id)))

    def faces_up(self, copy_id, face_id):
        return call("face normal", fx_design.faces_up, self._occ(copy_id), face_id)

    def discard(self, copy_id):
        self._used("discard_hide")
        self._occ(copy_id).isLightBulbOn = False

    # -- CAM
    def _setup(self, sheet):
        if sheet not in self.setups:
            raise AdapterError(f"no setup for {sheet}")
        return self.setups[sheet]

    def make_sheet(self, sheet, origin, length_in, width_in, thickness_in, copy_ids):
        stock = fx_design.make_stock(self.design, sheet, origin, length_in, width_in, thickness_in)
        if self.cam is None:
            self.cam = call("open Manufacture", fx_cam.cam_product, self.app, self.doc)
        models = [fx_design.body(self._occ(c)) for c in copy_ids]
        self._used("setup_name")
        if len(models) > 1:
            self._used("multi_model_setup")
        self.setups[sheet] = fx_cam.make_setup(self.cam, sheet, models, stock, self.job.fusion_params["setup"])

    def apply_template(self, sheet, template_path):
        return fx_cam.apply_template(self._setup(sheet), template_path)

    def fill(self, sheet, op_name, fill):
        op = fx_cam.op_by_name(self._setup(sheet), op_name)
        params = self.job.fusion_params
        if fill.tag in (DRILL, BORE, BEARING):
            names = params["drill"] if fill.tag == DRILL else params["bore"]
            self._used("drill_faces" if fill.tag == DRILL else "bore_faces")
            faces = [fx_design.face_by_id(self._occ(cid), fid) for cid, ids in fill.holes for fid in ids]
            call(f"{op_name}: select holes", fx_cam.select_hole_faces, op, faces, names)
        elif fill.tag == INNER:
            whole, single = self._inner_selection(fill.loops)
            if single:
                self._used("inner_chains")
            call(f"{op_name}: select loops", fx_cam.select_loops, op, whole, single, params["selections"]["contour"])
            fx_cam.cut_to_stock_bottom(op)
        else:
            raise AdapterError(f"[{fill.tag}] ops aren't automated yet")

    def _inner_selection(self, loops):
        """Faces whose inner loops are all wanted are selected whole (confirmed); other loops one by one."""
        wanted: Dict[tuple, set] = {}
        for cid, fid, index in loops:
            wanted.setdefault((cid, fid), set()).add(index)
        whole, single = [], []
        for (cid, fid), indexes in wanted.items():
            face = fx_design.face_by_id(self._occ(cid), fid)
            face_loops = fx_geometry.inner_loops(face)
            if indexes == set(range(len(face_loops))):
                whole.append(face)
            else:
                single += [face_loops[i] for i in sorted(indexes)]
        return whole, single

    def make_outer_ops(self, sheet, template_op, outlines):
        setup = self._setup(sheet)
        faces = [(name, fx_design.face_by_id(self._occ(cid), fid)) for name, cid, fid in outlines]
        fx_cam.outline_copies(setup, fx_cam.op_by_name(setup, template_op), faces,
                              self.job.fusion_params["selections"]["contour"])

    def delete_op(self, sheet, op_name):
        call(f"delete op {op_name}", fx_cam.op_by_name(self._setup(sheet), op_name).deleteMe)

    def generate(self, sheets):
        return fx_cam.generate(self.cam, {s: self._setup(s) for s in sheets}, self.timeout)

    def post(self, sheet, program_name, folder, post_path, properties):
        self._used("post_properties")
        return Path(fx_cam.post(self.cam, self._setup(sheet), program_name, str(folder), post_path, properties))

    def machining_time(self, sheet):
        return fx_cam.machining_time(self.cam, self._setup(sheet))

    # -- outputs
    def preview(self, sheet, rect, path):
        self._used("preview_camera")
        call("preview", fx_design.preview, self.app, rect, str(path))

    def export_f3d(self, path):
        fx_design.export_f3d(self.design, str(path))

    def finish(self, keep_open):
        if self.doc is not None and not keep_open:
            call("close document", self.doc.close, False)


def _as_adapter_error(name, method):
    """Any Fusion exception becomes an AdapterError naming the step, so one part or one sheet fails, not the job."""
    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except AdapterError:
            raise
        except Exception as e:  # noqa: BLE001 - Fusion raises RuntimeError and friends
            raise AdapterError(f"{name}: {type(e).__name__}: {e}") from None
    return wrapper


for _name in ("begin", "import_step", "extract", "add_copy", "arrange", "box", "faces_up", "discard", "make_sheet",
              "apply_template", "fill", "make_outer_ops", "delete_op", "generate", "post", "machining_time",
              "preview", "export_f3d", "finish"):
    setattr(FusionAdapter, _name, _as_adapter_error(_name, getattr(FusionAdapter, _name)))
