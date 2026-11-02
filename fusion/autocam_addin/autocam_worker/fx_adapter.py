"""UNTESTED IN FUSION. FusionAdapter: the Adapter interface (adapter.py) over the real Fusion API.

Keeps the Fusion objects behind the pipeline's names (copy ids, sheet names). Occurrences are looked up by
entity token, so a reference that went stale after a timeline change is found again.
"""

import functools
import sys
import time

import adsk.core
import adsk.fusion
from pathlib import Path
from typing import Dict, List

from autocam_core.schema_job import Job

from . import fx_cam, fx_design, fx_geometry
from .adapter import (BEARING, BORE, DRILL, FACE, INNER, OUTER, POCKET, POCKETFINISH, SCRIBE, Adapter, AdapterError,
                      Box)
from .fx_cam import UNCONFIRMED
from .fx_util import call, to_cm


class FusionAdapter(Adapter):
    capabilities = frozenset({DRILL, BORE, BEARING, INNER, OUTER, POCKET, POCKETFINISH, FACE, SCRIBE})

    def __init__(self, app, job: Job, generate_timeout_s: float = 900.0):
        self.app = app
        self.job = job
        self.timeout = generate_timeout_s
        self.doc = self.design = self.cam = None
        self.tokens: Dict[str, str] = {}       # copy id -> occurrence entity token
        self.setups: Dict[str, object] = {}     # sheet -> setup
        self.tab_sketch: Dict[str, object] = {}  # sheet -> the sketch holding its tab points
        self.scribe_sketch: Dict[str, object] = {}   # sheet -> the sketch holding its bend lines
        self.sheet_info: Dict[str, tuple] = {}  # sheet -> (origin, thickness, copy ids)
        self.stocks: Dict[str, object] = {}     # sheet -> its stock solid
        self.used: List[str] = []
        self.run_notes: List[str] = []
        self.trace: List[str] = []              # every adapter call, timed (the job folder's fusion_calls.log)

    def _used(self, key: str) -> None:
        if UNCONFIRMED[key] not in self.used:
            self.used.append(UNCONFIRMED[key])

    def versions(self):
        return self.app.version, sys.version.split()[0]

    def untested(self):
        return tuple(self.used)

    def notes(self):
        return tuple(self.run_notes)

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

    def make_sheet(self, sheet, origin, size_x_in, size_y_in, thickness_in, copy_ids):
        stock = fx_design.make_stock(self.design, sheet, origin, size_x_in, size_y_in, thickness_in)
        if self.cam is None:
            self.cam = call("open Manufacture", fx_cam.cam_product, self.app, self.doc)
        models = [fx_design.body(self._occ(c)) for c in copy_ids]
        self._used("setup_name")
        if len(models) > 1:
            self._used("multi_model_setup")
        self.setups[sheet] = fx_cam.make_setup(self.cam, sheet, models, stock, self.job.fusion_params["setup"])
        self.sheet_info[sheet] = (origin, thickness_in, list(copy_ids))
        self.stocks[sheet] = stock

    def make_extra_setup(self, sheet, name, copy_ids):
        self._used("extra_setup")
        if sheet not in self.stocks:
            raise AdapterError(f"no stock for {sheet}")
        models = [fx_design.body(self._occ(c)) for c in copy_ids]
        self.setups[name] = fx_cam.make_setup(self.cam, name, models, self.stocks[sheet], self.job.fusion_params["setup"])
        self.sheet_info[name] = self.sheet_info[sheet]          # the gouge check: every copy on the sheet
        self.stocks[name] = self.stocks[sheet]

    def op_names(self, sheet):
        return [o.name for o in fx_cam.ops(self._setup(sheet))]

    def set_radial_stock(self, sheet, op_name, stock_in):
        self._used("fit_stock")
        call(f"{op_name}: radial stock", fx_cam.radial_stock, fx_cam.op_by_name(self._setup(sheet), op_name), stock_in)

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
            if fill.tag == BEARING and fill.stock_in is not None:
                self._used("bearing_stock")
                call(f"{op_name}: radial stock", fx_cam.radial_stock, op, fill.stock_in)
        elif fill.tag == INNER:
            whole, single = self._inner_selection(fill.loops)
            if single:
                self._used("inner_chains")
                note = fx_cam.chain_api()
                if note not in self.run_notes:
                    self.run_notes.append(note)
            call(f"{op_name}: select loops", fx_cam.select_loops, op, whole, single, params["selections"]["contour"],
                 self._used)
            fx_cam.cut_to_stock_bottom(op)
        elif fill.tag == POCKET:
            self._used("pocket_floors")
            floors = [fx_design.face_by_id(self._occ(cid), fid) for cid, fid in fill.floors]
            how = call(f"{op_name}: select pockets", fx_cam.select_pockets, op, floors,
                       [fx_geometry.outer_loop(f) for f in floors], params["selections"]["pocket"], self._used)
            if how != "pocket selection":
                self._used("pocket_selection")
            self.run_notes.append(f"{op_name}: pockets selected as {how}")
        elif fill.tag == FACE:
            self._used("face_tops")
            loops = [fx_geometry.outer_loop(fx_design.face_by_id(self._occ(cid), f)) for cid, fid in fill.tops
                     for f in (fid if isinstance(fid, (tuple, list)) else (fid,))]
            selections = params["selections"]
            call(f"{op_name}: select tops", fx_cam.select_loops, op, [], loops,
                 selections.get("face", selections["pocket"]), self._used)
            call(f"{op_name}: face depth", fx_cam.face_depth, op, fill.depth_in)
        elif fill.tag == SCRIBE:
            self._used("scribe_lines")
            if sheet not in self.scribe_sketch:
                root = self.design.rootComponent
                sk = call("bend lines sketch", root.sketches.add, root.xYConstructionPlane)
                sk.name = f"BEND LINES {sheet}"
                self.scribe_sketch[sheet] = sk
            lines = self.scribe_sketch[sheet].sketchCurves.sketchLines
            made = [call("bend line", lines.addByTwoPoints, adsk.core.Point3D.create(to_cm(x0), to_cm(y0), 0),
                         adsk.core.Point3D.create(to_cm(x1), to_cm(y1), 0)) for x0, y0, x1, y1 in fill.lines]
            call(f"{op_name}: select bend lines", fx_cam.select_lines, op, made, params["selections"]["contour"])
            call(f"{op_name}: scribe", fx_cam.scribe_params, op, fill.depth_in)
        elif fill.tag == POCKETFINISH:
            self._used("pocket_walls")
            loops = [fx_geometry.outer_loop(fx_design.face_by_id(self._occ(cid), fid)) for cid, fid in fill.floors]
            note = fx_cam.chain_api()
            if note not in self.run_notes:
                self.run_notes.append(note)
            call(f"{op_name}: select pocket walls", fx_cam.select_loops, op, [], loops,
                 params["selections"]["contour"], self._used)
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
        faces = []
        for name, cid, fid in outlines:
            if isinstance(fid, (tuple, list)):          # a split top: its outline's co-edges, cut as a chain
                self._used("outline_chain")
                tops = [fx_design.face_by_id(self._occ(cid), f) for f in fid]
                faces.append((name, fx_geometry.outline_coedges(tops)))
            else:
                faces.append((name, fx_design.face_by_id(self._occ(cid), fid)))
        fx_cam.outline_copies(setup, fx_cam.op_by_name(setup, template_op), faces,
                              self.job.fusion_params["selections"]["contour"])

    def set_tabs(self, sheet, op_name, per_contour):
        self._used("tabs")
        call(f"{op_name}: tabs", fx_cam.set_tabs, fx_cam.op_by_name(self._setup(sheet), op_name),
             self.job.fusion_params["tabs"], per_contour)

    def set_tab_size(self, sheet, op_name, width_in, height_in):
        call(f"{op_name}: tab size", fx_cam.set_tab_size, fx_cam.op_by_name(self._setup(sheet), op_name),
             self.job.fusion_params["tabs"], width_in, height_in)

    def loop_segments(self, copy_id, face_id, loop_index):
        if isinstance(face_id, (tuple, list)):          # a split top: the outline of all its faces together
            faces = [fx_design.face_by_id(self._occ(copy_id), f) for f in face_id]
            return call("outline edges", lambda: fx_geometry.coedge_segments(fx_geometry.outline_coedges(faces)))
        return call("loop edges", fx_geometry.loop_segments, fx_design.face_by_id(self._occ(copy_id), face_id),
                    loop_index)

    def top_edges(self, copy_id, face_ids):
        occ = self._occ(copy_id)
        return {fid: fx_geometry.face_edges(fx_design.face_by_id(occ, fid)) for fid in face_ids}

    def set_tool(self, sheet, op_name, tool_json):
        self._used("scribe_tool")
        note = fx_cam.set_tool(fx_cam.op_by_name(self._setup(sheet), op_name), tool_json)
        self.run_notes.append(note)

    def set_tab_points(self, sheet, op_name, points):
        self._used("tab_points")
        if sheet not in self.tab_sketch:
            root = self.design.rootComponent
            sk = call("tab points sketch", root.sketches.add, root.xYConstructionPlane)
            sk.name = f"TABS {sheet}"
            self.tab_sketch[sheet] = sk
        call(f"{op_name}: tabs at points", fx_cam.set_tab_points, fx_cam.op_by_name(self._setup(sheet), op_name),
             self.tab_sketch[sheet], self.job.fusion_params["tabs"], points)

    def copy_op(self, sheet, op_name, new_name):
        self._used("op_copy")
        setup = self._setup(sheet)
        fx_cam.copy_op(setup, fx_cam.op_by_name(setup, op_name), new_name)

    def delete_op(self, sheet, op_name):
        call(f"delete op {op_name}", fx_cam.op_by_name(self._setup(sheet), op_name).deleteMe)

    def generate(self, sheets):
        return fx_cam.generate(self.cam, {s: self._setup(s) for s in sheets}, self.timeout, self._used)

    def post(self, sheet, program_name, folder, post_path, properties):
        self._used("post_properties")
        return Path(fx_cam.post(self.cam, self._setup(sheet), program_name, str(folder), post_path, properties))

    def cuts_into_parts(self, sheet, points):
        self._used("gouge_check")
        (ox, oy), t, copy_ids = self.sheet_info[sheet]
        bodies = [(fx_design.body(self._occ(c)), fx_design.box_in(self._occ(c))) for c in copy_ids]
        inside = adsk.fusion.PointContainment.PointInsidePointContainment
        hits, seen = [], set()
        for line, x, y, z in points:
            X, Y = x + ox, y + oy
            Z = min(max(z + 0.01, 0.01), t - 0.01)       # just above the cut, within the plate
            key = (round(X, 3), round(Y, 3), round(Z, 2))
            if key in seen:
                continue
            seen.add(key)
            for b, (x0, y0, _, x1, y1, _) in bodies:
                if x0 <= X <= x1 and y0 <= Y <= y1 and b.pointContainment(
                        adsk.core.Point3D.create(to_cm(X), to_cm(Y), to_cm(Z))) == inside:
                    hits.append((line, x, y))
                    break
            if len(hits) >= 20:
                break
        return hits

    def machining_time(self, sheet):
        return fx_cam.machining_time(self.cam, self._setup(sheet))

    # -- outputs
    def preview(self, sheet, rect, path):
        self._used("preview_camera")
        call("preview", fx_design.preview, self.app, rect, str(path))

    def export_f3d(self, path):
        fx_design.export_f3d(self.design, str(path))

    def save_to_team(self, name, project, folder):
        self._used("team_save")
        url, saved, note = fx_design.save_to_team(self.app, self.doc, name, project, folder)
        self.run_notes.append(note)
        return url, saved

    def finish(self, keep_open):
        if self.doc is not None and not keep_open:
            call("close document", self.doc.close, False)


def _stamp() -> str:
    t = time.time()
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"


def _as_adapter_error(name, method):
    """Any Fusion exception becomes an AdapterError naming the step, so one part or one sheet fails, not the job.
    Each call is also traced with the local time, to line up with Fusion's own log (a dialog it pops up, say)."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        what = ", ".join(repr(a)[:60] for a in args if isinstance(a, (str, int, float)))
        self.trace.append(f"{_stamp()} {name}({what})")
        try:
            return method(self, *args, **kwargs)
        except AdapterError as e:
            self.trace.append(f"{_stamp()}   -> {e}")
            raise
        except Exception as e:  # noqa: BLE001 - Fusion raises RuntimeError and friends
            self.trace.append(f"{_stamp()}   -> {type(e).__name__}: {e}")
            raise AdapterError(f"{name}: {type(e).__name__}: {e}") from None
        finally:
            self.trace.append(f"{_stamp()}   done {name}")
    return wrapper


for _name in ("begin", "import_step", "extract", "add_copy", "arrange", "box", "faces_up", "discard", "make_sheet",
              "make_extra_setup", "set_radial_stock", "op_names", "apply_template", "fill",
              "copy_op", "set_tabs", "set_tab_size", "set_tab_points", "loop_segments", "make_outer_ops", "delete_op",
              "top_edges", "set_tool", "generate", "post", "cuts_into_parts",
              "machining_time", "preview", "export_f3d", "save_to_team", "finish"):
    setattr(FusionAdapter, _name, _as_adapter_error(_name, getattr(FusionAdapter, _name)))
