"""process_job: one job.json -> nested sheets, posted programs, result.json (written last).

1. Check the job: core version, pause mode, pinned post, templates. A job that fails here raises
   JobFailed; the add-in moves it to failed/ with the message.
2. Parts: import each STEP, extract its shape, apply the plate rules (plate.py) and decide which tool it
   needs (tooling.py). Bad parts are reported, not nested.
3. Nest each stock thickness separately: one occurrence per ordered copy, one Arrange per sheet, the
   leftovers go to the next sheet. If a part's copies don't all fit, none are cut this run (layout.py).
   Arrange packs parts against the envelope's edges, so each envelope is the nest region shrunk by the
   part spacing: an outline's tool path (tool radius + lead-in) then stays out of the clamp strips.
4. Per sheet: one tool, each part's feature plan with that tool, the cut order of the outlines.
5. CAM per sheet: stock + setup, template (every op's tool GUID checked), selections, one outline op per
   part copy in cut order.
6. Post, insert the pauses, and check the final bytes with the check the service repeats
   (sheetcheck.py). Failures are kept as *.REJECTED.tap.
7. Preview, .f3d, result.json.

Pure Python: everything Fusion does goes through the Adapter (adapter.py).
"""

import hashlib
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from autocam_core import CORE_VERSION
from autocam_core import errors as E
from autocam_core.errors import Issue
from autocam_core.holes import ToolProfile
from autocam_core.hotfolder import write_atomic
from autocam_core.layout import Placed, plan_layout
from autocam_core.names import outer_op_name, program_name
from autocam_core.ordering import order_outlines
from autocam_core.pauses import PauseError, insert
from autocam_core.plate import PlateAnalysis, analyze
from autocam_core.schema_job import Job, PartSpec, ToolSpec
from autocam_core.schema_result import (
    RESULT_SCHEMA, FusionTeamResult, GuardSummary, HoleCounts, PartResult, PauseEntryResult, PauseSummary,
    PostResult, Result, SheetPart, SheetResult, ToolForce, WorkerInfo, overall_status, result_json,
)
from autocam_core.sheetcheck import check_sheet_program, pause_spec
from autocam_core.tooling import FeaturePlan, ToolNeed, part_tool, plan_features, sheet_tool
from autocam_core.toolpoints import cutting_points

from .adapter import BEARING, BORE, DRILL, INNER, OUTER, POCKET, TAGS, Adapter, AdapterError, OpFill, Rect

RESULT = "result.json"
LOG = "worker.log"
Z_TOL_IN = 0.001
EDGE_TOL_IN = 0.01      # body boxes can be a little loose; the envelope is already 0.25 in inside the nest region
MOVE_TOL_IN = 0.001     # a copy that moved more than this after nesting stops its sheet


class JobFailed(Exception):
    """The job as a whole can't run. Nothing was made."""


# One job at a time per Fusion process. Fusion runs event handlers while a job waits (adsk.doEvents), so the
# add-in could otherwise start a queued job inside a manual autocam_run job. Kept on `sys` because autocam_run
# reloads the autocam_* modules, which would give each copy its own flag.
_RUNNING = "_autocam_job_running"


def job_running() -> Optional[str]:
    return getattr(sys, _RUNNING, None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def profile(tool: ToolSpec) -> ToolProfile:
    return ToolProfile(tool.key, tool.diameter_in, tool.min_inside_radius_in, tool.drill_sizes_in, tool.cutter_label)


def _dedupe(issues: Sequence[Issue]) -> Tuple[Issue, ...]:
    return tuple(dict.fromkeys(issues))


@dataclass
class _Part:
    spec: PartSpec
    copies: List[str] = field(default_factory=list)
    analysis: Optional[PlateAnalysis] = None
    need: Optional[ToolNeed] = None
    plan: Optional[FeaturePlan] = None        # with the tool of the first sheet it's on
    errors: List[Issue] = field(default_factory=list)
    warnings: List[Issue] = field(default_factory=list)
    deferred: bool = False

    @property
    def key(self) -> str:
        return self.spec.part_key

    @property
    def ok(self) -> bool:
        return not self.errors and self.analysis is not None


@dataclass
class _Sheet:
    thickness_in: float
    origin: Tuple[float, float]               # the sheet's (0, 0) in the design, inches
    instances: List[Tuple[str, Placed]]       # (instance id like p03-2, the copy and where it landed)
    index: int = 0
    name: str = ""
    tool_key: str = ""
    forced: Tuple[Tuple[str, str], ...] = ()
    plans: Dict[str, FeaturePlan] = field(default_factory=dict)
    outer_order: Tuple[str, ...] = ()
    built: bool = False
    errors: List[Issue] = field(default_factory=list)
    warnings: List[Issue] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    result: Optional[SheetResult] = None

    def counts(self) -> Dict[str, int]:
        return dict(Counter(p.part_key for _, p in self.instances))


# ------------------------------------------------------------------ 1. the job

def check_job(job: Job) -> None:
    problems = job.validate()
    if problems:
        raise JobFailed(f"{E.JOB_INVALID}: " + "; ".join(problems))
    if job.core_version != CORE_VERSION:
        raise JobFailed(f"{E.CORE_VERSION_MISMATCH}: the job was built by core {job.core_version} but this worker "
                        f"runs core {CORE_VERSION}; pull the repo on both sides and restart the add-in")
    if job.pauses.enabled and job.pauses.mode != "tap_text":
        raise JobFailed(f"{E.JOB_INVALID}: pause mode {job.pauses.mode!r} isn't supported; a Manual NC Stop posts "
                        "a bare M0 with the spindle running (docs/fusion-api-status.md)")
    post = Path(job.post.path)
    if not post.is_file():
        raise JobFailed(f"{E.POST_FAILED}: the pinned post {post} is missing")
    if _sha256(post) != job.post.sha256:
        raise JobFailed(f"{E.POST_FAILED}: {post.name} doesn't match the pinned sha256; the post must never be edited")
    for tool in job.tooling.tools.values():
        if not Path(tool.template_path).is_file():
            raise JobFailed(f"{E.TEMPLATE_PROBLEM}: template {tool.template_path} for {tool.cutter_label} is missing")


# ------------------------------------------------------------------ 2. parts

def _load_part(adapter: Adapter, job: Job, part: _Part) -> None:
    spec = part.spec
    path = Path(spec.step)
    if not path.is_file():
        part.errors.append(Issue(E.STEP_MISSING, f"STEP file not found: {spec.step}"))
        return
    if spec.step_sha256 and _sha256(path) != spec.step_sha256:
        part.errors.append(Issue(E.STEP_IMPORT, "the STEP file changed after the job was made"))
        return
    first = f"{spec.part_key}.1"
    try:
        adapter.import_step(first, str(path))
    except AdapterError as e:
        part.errors.append(Issue(E.STEP_IMPORT, f"STEP import failed: {e}"))
        return
    part.copies.append(first)
    try:
        geometry = adapter.extract(first)
    except AdapterError as e:
        part.errors.append(Issue(E.STEP_IMPORT, f"couldn't read the part's shape: {e}"))
        return
    analysis = analyze(geometry, job.material.thicknesses_in, job.material.thickness_tol_in)
    part.analysis = analysis
    part.errors += analysis.errors
    part.warnings += analysis.warnings
    if part.errors:
        return
    if analysis.up_face_id is None:
        part.errors.append(Issue(E.NOT_A_PLATE, "no top face to lay face up"))
        return
    if len(analysis.top_face_ids) > 1:
        part.errors.append(Issue(E.NEEDS_MANUAL_CAM, f"needs manual CAM: the top is split into "
                                                     f"{len(analysis.top_face_ids)} faces, so there's no single "
                                                     "outline to cut"))
        return
    tools = job.tooling.tools
    small = tools.get(job.tooling.small_features) if job.tooling.small_features else None
    need = part_tool(analysis, family=job.material.family, default=profile(tools[job.tooling.default]),
                     small=profile(small) if small else None, rules=job.holes, force_small=spec.force_small_tool,
                     strict_inside_radius=job.plate.strict_inside_radius)
    part.need = need
    part.errors += need.errors
    part.warnings += need.warnings


def _discard(adapter: Adapter, cids: Sequence[str], log: Callable[[str], None]) -> None:
    for cid in cids:
        try:
            adapter.discard(cid)
        except AdapterError as e:
            log(f"couldn't take {cid} out: {e}")


def _drop(adapter: Adapter, part: _Part, log: Callable[[str], None]) -> None:
    _discard(adapter, part.copies, log)
    part.copies = []


def _envelope(job: Job, origin: Tuple[float, float]) -> Rect:
    region, inset = job.fixture.nest_region_in, job.nest.part_spacing_in
    return (origin[0] + region[0] + inset, origin[1] + region[1] + inset,
            origin[0] + region[2] - inset, origin[1] + region[3] - inset)


# ------------------------------------------------------------------ 3. nesting

def _nest_group(adapter: Adapter, job: Job, thickness: float, parts: List[_Part], x_start: float, slot: int,
                log: Callable[[str], None], notes: List[str]) -> Tuple[List[_Sheet], int]:
    """Arrange one stock thickness onto as many sheets as it takes (up to the job's limit)."""
    pitch = job.sheet.length_in + job.nest.envelope_spacing_in
    by_key = {p.key: p for p in parts}

    def key_of(cid: str) -> str:
        return cid.rsplit(".", 1)[0]

    def reject(keys, msg: str) -> None:
        for key in sorted(set(keys)):
            if by_key[key].ok:
                by_key[key].errors.append(Issue(E.ARRANGE_FAILED, msg))

    up = {cid: p.analysis.up_face_id for p in parts for cid in p.copies}
    remaining = [cid for p in parts for cid in p.copies]
    envelopes: List[Tuple[Tuple[float, float], Rect]] = []
    for _ in range(job.nest.max_sheets_per_group):
        if not remaining:
            break
        origin = (x_start + slot * pitch, 0.0)
        envelope = _envelope(job, origin)
        try:
            got = adapter.arrange(remaining, envelope, job.nest.part_spacing_in, {c: up[c] for c in remaining})
        except AdapterError as e:
            reject([key_of(c) for c in remaining], f"Arrange failed: {e}")
            log(f"Arrange failed for a {thickness:g} in sheet: {e}")
            break
        for cid, why in got.refused.items():
            reject([key_of(cid)], f"Arrange can't lay it flat: {why}")
        placed = set(got.placed)
        slot += 1
        if placed:
            envelopes.append((origin, envelope))
        remaining = [c for c in remaining if c not in placed and by_key[key_of(c)].ok]
        log(f"{thickness:g} in sheet at x={origin[0]:g}: placed {len(placed)}, {len(remaining)} left")
        if not placed:
            break

    # Copies still left over: if a part doesn't fit even alone on an empty sheet, say so instead of
    # deferring it run after run.
    for key in dict.fromkeys(key_of(c) for c in remaining):
        if not by_key[key].ok:
            continue
        cid = next(c for c in remaining if key_of(c) == key)
        envelope = _envelope(job, (x_start + slot * pitch, 0.0))
        slot += 1
        try:
            alone = adapter.arrange([cid], envelope, job.nest.part_spacing_in, {cid: up[cid]}).placed
        except AdapterError:
            alone = ()
        if not alone:
            w, h = envelope[2] - envelope[0], envelope[3] - envelope[1]
            reject([key], f"doesn't fit the nest area ({w:.1f} x {h:.1f} in) even alone on a sheet")
    tol = job.material.thickness_tol_in + Z_TOL_IN
    left = set(remaining)
    for cid in [c for c in up if c not in left]:
        part = by_key[cid.rsplit(".", 1)[0]]
        if not part.ok:
            continue
        try:
            box = adapter.box(cid)
            upright = adapter.faces_up(cid, part.analysis.up_face_id)
        except AdapterError as e:
            part.errors.append(Issue(E.ARRANGE_FAILED, f"couldn't check where Arrange put it: {e}"))
            continue
        if abs(box.z0) > Z_TOL_IN or abs(box.z1 - thickness) > tol:
            part.errors.append(Issue(E.ARRANGE_FAILED, f"Arrange left it at Z {box.z0:.4f} to {box.z1:.4f} in, "
                                                       f"not on the sheet"))
        elif not upright:
            part.errors.append(Issue(E.ARRANGE_FAILED, "Arrange turned it upside down (the pocket side must face up)"))

    while True:
        bodies = []
        for p in [p for p in parts if p.ok]:
            for cid in p.copies:
                try:
                    bodies.append(Placed(cid, p.key, adapter.box(cid).xy))
                except AdapterError as e:
                    p.errors.append(Issue(E.ARRANGE_FAILED, f"couldn't read where it is: {e}"))
        good = [p for p in parts if p.ok]
        bodies = [b for b in bodies if by_key[b.part_key].ok]
        layout = plan_layout(bodies, [env for _, env in envelopes], {p.key: p.spec.qty for p in good},
                             edge_tol_in=EDGE_TOL_IN)
        bad = {msg.split(":", 1)[0] for msg in layout.problems}
        if not bad:
            break
        for msg in layout.problems:
            key = msg.split(":", 1)[0]
            if key in by_key:
                by_key[key].errors.append(Issue(E.ARRANGE_FAILED, msg.split(":", 1)[1].strip()))
        if not bad & set(by_key):
            raise JobFailed(f"{E.NOTHING_TO_NEST}: layout problems: {'; '.join(layout.problems)}")

    for p in parts:
        if not p.ok:
            _drop(adapter, p, log)
        elif p.key in layout.deferred:
            p.deferred = True
    _discard(adapter, layout.removed_bodies + layout.unplaced_bodies, log)
    for p in parts:
        if p.deferred:
            p.copies = []

    origin_of = {env: origin for origin, env in envelopes}
    sheets = [_Sheet(thickness, origin_of[s.envelope_in], list(s.instances)) for s in layout.sheets]
    return sheets, slot


# ------------------------------------------------------------------ 4. tool and features per sheet

def _missing_ops(plan: FeaturePlan, capabilities: frozenset) -> List[str]:
    used = ((DRILL, plan.drill), (BORE, plan.bore), (BEARING, plan.bearing), (POCKET, plan.pocket_floor_ids),
            (INNER, plan.inner_loops))
    return [tag for tag, items in used if items and tag not in capabilities]


def _plan_sheets(adapter: Adapter, job: Job, sheets: List[_Sheet], parts: Dict[str, _Part],
                 log: Callable[[str], None]) -> List[_Sheet]:
    """Pick each sheet's tool and plan its parts' features. A part that can't be planned is rejected
    (every copy, on every sheet) and the sheets are planned again without it."""
    while True:
        rejected = set()
        for sheet in sheets:
            keys = sorted(sheet.counts())
            sheet.tool_key, sheet.forced = sheet_tool({k: parts[k].need for k in keys}, job.tooling.default,
                                                      job.tooling.small_features)
            tool = job.tooling.tools[sheet.tool_key]
            sheet.plans = {}
            for k in keys:
                plan = plan_features(parts[k].analysis, profile(tool), job.holes)
                missing = _missing_ops(plan, adapter.capabilities)
                problems = list(plan.errors)
                if missing:
                    problems.append(Issue(E.NEEDS_MANUAL_CAM,
                                          f"needs manual CAM: {', '.join(missing)} ops aren't automated yet"))
                if problems:
                    parts[k].errors += problems
                    rejected.add(k)
                sheet.plans[k] = plan
        if not rejected:
            break
        for k in rejected:
            _drop(adapter, parts[k], log)
        for sheet in sheets:
            sheet.instances = [(i, p) for i, p in sheet.instances if p.part_key not in rejected]
        sheets = [s for s in sheets if s.instances]
    for sheet in sheets:
        for k, plan in sheet.plans.items():
            part = parts[k]
            part.warnings += plan.warnings
            if part.plan is None:
                part.plan = plan
    return sheets


def _sheet_xy(sheet: _Sheet, placed: Placed) -> Tuple[float, float]:
    cx, cy = placed.center
    return cx - sheet.origin[0], cy - sheet.origin[1]


# ------------------------------------------------------------------ 5. CAM

def _op_tag(name: str) -> Optional[str]:
    found = [t for t in TAGS if f"[{t}]" in name.lower()]
    return found[0] if len(found) == 1 else None


def _build_sheet(adapter: Adapter, job: Job, sheet: _Sheet, parts: Dict[str, _Part]) -> None:
    tool = job.tooling.tools[sheet.tool_key]
    copy_ids = [p.body_id for _, p in sheet.instances]
    moved = []
    for inst, placed in sheet.instances:
        now = adapter.box(placed.body_id).xy
        if max(abs(a - b) for a, b in zip(now, placed.bbox_in)) > MOVE_TOL_IN:
            moved.append(f"{inst} ({now[0]:.3f}, {now[1]:.3f} instead of {placed.bbox_in[0]:.3f}, "
                         f"{placed.bbox_in[1]:.3f})")
    if moved:
        sheet.errors.append(Issue(E.ARRANGE_FAILED, f"parts moved after nesting: {'; '.join(moved)}"))
        return
    adapter.make_sheet(sheet.name, sheet.origin, job.sheet.length_in, job.sheet.width_in, sheet.thickness_in, copy_ids)
    ops = adapter.apply_template(sheet.name, tool.template_path)

    wrong = [o.name for o in ops if o.tool_guid != tool.guid]
    if wrong:
        sheet.errors.append(Issue(E.TOOL_GUID_MISMATCH,
                                  f"template ops {', '.join(wrong)} don't use {tool.cutter_label} (GUID {tool.guid}); "
                                  "rebuild the template from the pinned tool library"))
        return
    by_tag: Dict[str, List[str]] = {}
    for o in ops:
        tag = _op_tag(o.name)
        if tag is None:
            sheet.errors.append(Issue(E.TEMPLATE_PROBLEM, f"template op {o.name!r} needs exactly one tag "
                                                          f"({', '.join('[' + t + ']' for t in TAGS)})"))
        else:
            by_tag.setdefault(tag, []).append(o.name)
    for tag, names in by_tag.items():
        if len(names) > 1:
            sheet.errors.append(Issue(E.TEMPLATE_PROBLEM, f"template has {len(names)} [{tag}] ops: {', '.join(names)}"))
    if OUTER not in by_tag:
        sheet.errors.append(Issue(E.TEMPLATE_PROBLEM, "template has no [outer] op"))
    if sheet.errors:
        return

    holes: Dict[str, List[Tuple[str, Tuple[int, ...]]]] = {DRILL: [], BORE: [], BEARING: []}
    loops: List[Tuple[str, int, int]] = []
    floors: List[Tuple[str, int]] = []
    for _, placed in sheet.instances:
        part = parts[placed.part_key]
        plan = sheet.plans[part.key]
        through = part.analysis.through_holes
        for tag, indexes in ((DRILL, plan.drill), (BORE, plan.bore), (BEARING, plan.bearing)):
            holes[tag] += [(placed.body_id, through[i].face_ids) for i in indexes]
        loops += [(placed.body_id, loop.face_id, loop.index) for loop in plan.inner_loops]
        floors += [(placed.body_id, f) for f in plan.pocket_floor_ids]
    if holes[BEARING] and BEARING not in by_tag and BORE in by_tag:
        holes[BORE] += holes[BEARING]
        holes[BEARING] = []
        sheet.warnings.append(Issue(E.OP_WARNING, "the template has no [bearing] op; bearing holes are bored with [bore]"))

    fills = {DRILL: OpFill(DRILL, holes=tuple(holes[DRILL])), BORE: OpFill(BORE, holes=tuple(holes[BORE])),
             BEARING: OpFill(BEARING, holes=tuple(holes[BEARING])), INNER: OpFill(INNER, loops=tuple(loops)),
             POCKET: OpFill(POCKET, floors=tuple(floors))}
    for tag, fill in fills.items():
        wanted = len(fill.holes) + len(fill.loops) + len(fill.floors)
        if wanted and tag not in by_tag:
            sheet.errors.append(Issue(E.TEMPLATE_PROBLEM, f"template has no [{tag}] op but this sheet needs one "
                                                          f"({wanted} features)"))
    if sheet.errors:
        return
    for tag, names in by_tag.items():
        if tag == OUTER:
            continue
        fill = fills[tag]
        if fill.holes or fill.loops or fill.floors:
            adapter.fill(sheet.name, names[0], fill)
        else:
            adapter.delete_op(sheet.name, names[0])

    points = [(inst, _sheet_xy(sheet, placed)) for inst, placed in sheet.instances]
    sheet.outer_order = tuple(order_outlines(points))
    by_instance = {inst: placed for inst, placed in sheet.instances}
    outlines = [(outer_op_name(inst), by_instance[inst].body_id, parts[by_instance[inst].part_key].analysis.up_face_id)
                for inst in sheet.outer_order]
    adapter.make_outer_ops(sheet.name, by_tag[OUTER][0], outlines)
    sheet.built = True


# ------------------------------------------------------------------ 6. post and check

def _post_sheet(adapter: Adapter, job: Job, sheet: _Sheet, out_dir: Path) -> None:
    raw_dir = out_dir / "raw" / sheet.name
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True)
    try:
        posted = adapter.post(sheet.name, sheet.name, raw_dir, job.post.path, job.post.properties)
    except AdapterError as e:
        sheet.errors.append(Issue(E.POST_FAILED, f"posting failed: {e}"))
        return
    text = posted.read_bytes().decode("ascii", errors="surrogateescape")
    if job.pauses.enabled:
        try:
            text = insert(text, sheet.outer_order, pause_spec(job), after_last_part=job.pauses.after_last_part)
        except PauseError as e:
            sheet.errors.append(Issue(E.PAUSES_WRONG, f"couldn't insert the pauses: {e}"))
            return
    data = text.encode("ascii", errors="surrogateescape")
    counts = sheet.counts()
    check = check_sheet_program(data, job, sheet.thickness_in, sheet.tool_key, sheet.outer_order, counts)
    if check.passed:
        # The tool center must never be inside a part: catches a cutout or outline cut on the wrong side.
        try:
            hits = adapter.cuts_into_parts(sheet.name, cutting_points(text, sheet.thickness_in))
        except AdapterError as e:
            hits = []
            sheet.errors.append(Issue(E.TAP_REJECTED, f"couldn't check the toolpaths against the parts: {e}"))
        if hits:
            where = "; ".join(f"line {n} (X{x:.4f} Y{y:.4f})" for n, x, y in hits[:5])
            sheet.errors.append(Issue(E.TAP_REJECTED, f"the tool cuts into a part (a cutout or outline cut on the "
                                                      f"wrong side?): {where}"))
    g = check.guard
    guard = GuardSummary(g.passed, g.sha256, g.floor_in, g.min_z_in, g.units, g.offenders, g.clamp_violations,
                         g.problems)
    pauses = None
    if check.pauses is not None:
        pauses = PauseSummary("tap_text", check.pauses.expected, check.pauses.found,
                              tuple(PauseEntryResult(e.after, e.line) for e in check.pauses.entries),
                              check.pauses.problems)
    if check.passed and not sheet.errors:
        tap, rejected = f"{sheet.name}.tap", None
    else:
        tap, rejected = None, f"{sheet.name}.REJECTED.tap"
        sheet.errors += [Issue(E.TAP_REJECTED, msg) for msg in check.problems()]
    write_atomic(out_dir / (tap or rejected), data)
    sheet.result = SheetResult(
        index=sheet.index, name=sheet.name, stock_type="", thickness_in=sheet.thickness_in, tool=sheet.tool_key,
        tool_guid="", cutter_label="", template="", tap=tap, tap_rejected=rejected, tap_bytes=len(data),
        tap_sha256=g.sha256 if tap else None, guard=guard, pauses=pauses, machining_time_s=None, preview_png=None,
        parts=(), outer_order=sheet.outer_order, tool_forced_by=(), errors=(), warnings=(), notes=())


# ------------------------------------------------------------------ the job

def process_job(job: Job, adapter: Adapter, out_dir: Path, *, attempt: int = 1, keep_open: bool = False,
                now: Callable[[], str] = _now) -> Result:
    """Run the job and write result.json into out_dir (last). Raises JobFailed if the job can't run at all;
    any other exception is a worker bug and also means the job failed."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_lines: List[str] = []

    def log(msg: str) -> None:
        log_lines.append(f"{now()} {msg}")

    started = now()
    check_job(job)
    if job_running():
        raise JobFailed(f"another job ({job_running()}) is already running in this Fusion")
    setattr(sys, _RUNNING, job.job_id)
    try:
        log(f"job {job.job_id}: {len(job.parts)} parts, material {job.material.key}")
        adapter.begin(job.job_id)
        try:
            result = _run(job, adapter, out_dir, log, attempt, started, now)
        finally:
            try:
                adapter.finish(keep_open)
            finally:
                (out_dir / LOG).write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    finally:
        setattr(sys, _RUNNING, None)
    problems = result.validate()
    if problems:
        raise JobFailed(f"worker bug: result.json would be invalid: {'; '.join(problems)}")
    write_atomic(out_dir / RESULT, result_json(result).encode("utf-8"))
    return result


def _run(job: Job, adapter: Adapter, out_dir: Path, log: Callable[[str], None], attempt: int, started: str,
         now: Callable[[], str]) -> Result:
    parts = {spec.part_key: _Part(spec) for spec in job.parts}
    notes: List[str] = []
    for part in parts.values():
        _load_part(adapter, job, part)
        if not part.ok:
            _drop(adapter, part, log)
            log(f"{part.key}: rejected: {'; '.join(i.msg for i in part.errors)}")

    for part in parts.values():
        if not part.ok:
            continue
        try:
            for n in range(2, part.spec.qty + 1):
                cid = f"{part.key}.{n}"
                adapter.add_copy(part.copies[0], cid)
                part.copies.append(cid)
        except AdapterError as e:
            part.errors.append(Issue(E.STEP_IMPORT, f"couldn't make copy {len(part.copies) + 1}: {e}"))
            _drop(adapter, part, log)

    # Sheets go to the right of everything imported, so a copy that didn't fit never sits on a sheet.
    right = 0.0
    for part in [p for p in parts.values() if p.ok]:
        try:
            right = max([right] + [adapter.box(cid).x1 for cid in part.copies])
        except AdapterError as e:
            part.errors.append(Issue(E.STEP_IMPORT, f"couldn't measure the imported part: {e}"))
            _drop(adapter, part, log)
    good = [p for p in parts.values() if p.ok]
    sheets: List[_Sheet] = []
    if good:
        x_start = right + job.nest.envelope_spacing_in
        slot = 0
        groups: Dict[float, List[_Part]] = {}
        for p in good:
            groups.setdefault(p.analysis.stock_thickness_in, []).append(p)
        for thickness in sorted(groups):
            made, slot = _nest_group(adapter, job, thickness, groups[thickness], x_start, slot, log, notes)
            sheets += made
    sheets = _plan_sheets(adapter, job, sheets, parts, log)

    tools = job.tooling.tools
    for index, sheet in enumerate(sheets, 1):
        sheet.index = index
        sheet.name = program_name(job.material.program_prefix, sheet.thickness_in, job.run_id, index)
        try:
            _build_sheet(adapter, job, sheet, parts)
        except AdapterError as e:
            sheet.errors.append(Issue(E.OP_ERROR, f"setting up the CAM failed: {e}"))
        log(f"{sheet.name}: {len(sheet.instances)} parts, tool {sheet.tool_key}, built={sheet.built}")

    built = [s for s in sheets if s.built and not s.errors]
    states = {}
    if built:
        try:
            states = adapter.generate([s.name for s in built])
        except AdapterError as e:
            for s in built:
                s.errors.append(Issue(E.OP_ERROR, f"toolpath generation failed: {e}"))
    for sheet in built:
        if sheet.name not in states and not sheet.errors:
            sheet.errors.append(Issue(E.OP_ERROR, "Fusion reported no toolpaths for this sheet"))
        for st in states.get(sheet.name, []):
            if st.error:
                sheet.errors.append(Issue(E.OP_ERROR, f"{st.name}: {st.error}"))
            elif not st.has_toolpath:
                sheet.errors.append(Issue(E.OP_ERROR, f"{st.name}: no toolpath"))
            elif st.warning:
                sheet.warnings.append(Issue(E.OP_WARNING, f"{st.name}: {st.warning}"))
        if not sheet.errors:
            _post_sheet(adapter, job, sheet, out_dir)

    results: List[SheetResult] = []
    for sheet in sheets:
        tool = tools[sheet.tool_key]
        machining = preview = None
        if sheet.built:
            try:
                machining = adapter.machining_time(sheet.name)
            except AdapterError as e:
                sheet.notes.append(f"machining time unavailable: {e}")
            png = f"{sheet.name}.png"
            rect = (sheet.origin[0], sheet.origin[1], sheet.origin[0] + job.sheet.length_in,
                    sheet.origin[1] + job.sheet.width_in)
            try:
                adapter.preview(sheet.name, rect, out_dir / png)
                preview = png
            except AdapterError as e:
                sheet.notes.append(f"no preview: {e}")
        base = sheet.result or SheetResult(
            index=sheet.index, name=sheet.name, stock_type="", thickness_in=sheet.thickness_in, tool=sheet.tool_key,
            tool_guid="", cutter_label="", template="", tap=None, tap_rejected=None, tap_bytes=None, tap_sha256=None,
            guard=None, pauses=None, machining_time_s=None, preview_png=None, parts=(), outer_order=sheet.outer_order,
            tool_forced_by=(), errors=(), warnings=(), notes=())
        results.append(SheetResult(
            index=sheet.index, name=sheet.name, stock_type=f"{job.material.key}-{sheet.thickness_in:g}",
            thickness_in=sheet.thickness_in, tool=sheet.tool_key, tool_guid=tool.guid,
            cutter_label=tool.cutter_label, template=tool.template_key, tap=base.tap, tap_rejected=base.tap_rejected,
            tap_bytes=base.tap_bytes, tap_sha256=base.tap_sha256, guard=base.guard, pauses=base.pauses,
            machining_time_s=machining, preview_png=preview,
            parts=tuple(SheetPart(k, n) for k, n in sorted(sheet.counts().items())),
            outer_order=sheet.outer_order, tool_forced_by=tuple(ToolForce(k, r) for k, r in sheet.forced),
            errors=_dedupe(sheet.errors), warnings=_dedupe(sheet.warnings), notes=tuple(sheet.notes)))

    f3d_name: Optional[str] = None
    f3d_bytes: Optional[int] = None
    if sheets:
        f3d = out_dir / f"{job.job_id}.f3d"
        try:
            adapter.export_f3d(f3d)
            f3d_name, f3d_bytes = f3d.name, f3d.stat().st_size
        except (AdapterError, OSError) as e:
            notes.append(f"no .f3d: {e}")
    team: Optional[FusionTeamResult] = None
    if sheets and job.fusion_team.project:
        try:
            url, saved = adapter.save_to_team(job.job_id, job.fusion_team.project, job.fusion_team.folder)
            team = FusionTeamResult(url, saved)
            log(f"saved to Fusion Team: {saved} {url or '(no link yet)'}")
        except AdapterError as e:
            notes.append(f"not saved to Fusion Team ({e}); the .f3d is attached instead")
    elif sheets:
        notes.append("no Fusion Team folder in config ([fusion_team]); only the local .f3d")

    part_rows: List[PartResult] = []
    for part in parts.values():
        on = sorted(s.index for s in sheets if part.key in s.counts()) if part.ok and not part.deferred else []
        placed = sum(s.counts().get(part.key, 0) for s in sheets) if on else 0
        holes = None
        if part.plan is not None:
            c = part.plan.counts()
            holes = HoleCounts(c["drill"], c["bore"], c["bearing"], c["inner"], c["contour_warn"], c["pockets"])
        a = part.analysis
        part_rows.append(PartResult(
            part_key=part.key, card_id=part.spec.card_id, qty=part.spec.qty, placed=placed,
            deferred=part.deferred and not part.errors, sheets=tuple(on),
            measured_thickness_in=a.measured_thickness_in if a else None,
            stock_thickness_in=a.stock_thickness_in if a else None,
            tool_need=part.need.tool if part.need else None, holes=holes,
            errors=_dedupe(part.errors), warnings=_dedupe(part.warnings)))

    notes += list(adapter.notes())
    job_errors: Tuple[Issue, ...] = ()
    if not sheets:
        job_errors = (Issue(E.NOTHING_TO_NEST, "no part could be nested"),)
    sheet_rows, part_tuple = tuple(results), tuple(part_rows)
    fusion_version, python_version = adapter.versions()
    return Result(
        schema=RESULT_SCHEMA, core_version=CORE_VERSION, job_id=job.job_id, run_id=job.run_id,
        status=overall_status(sheet_rows, part_tuple, job_errors),
        worker=WorkerInfo(fusion_version, python_version, attempt, started, now(), adapter.untested()),
        post=PostResult("path", True, dict(job.post.properties)), fusion_team=team,
        f3d=f3d_name, f3d_bytes=f3d_bytes, sheets=sheet_rows, parts=part_tuple, errors=job_errors,
        notes=tuple(notes))
