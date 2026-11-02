"""process_job: one job.json -> nested sheets, posted programs, result.json (written last).

1. Check the job: core version, pause mode, pinned post, templates. A job that fails here raises
   JobFailed; the add-in moves it to failed/ with the message.
2. Parts: import each STEP, extract its shape, apply the plate rules (plate.py) and decide which tool it
   needs (tooling.py). Bad parts are reported, not nested.
3. Nest each stock thickness separately: one occurrence per ordered copy, one Arrange per sheet, the
   leftovers go to the next sheet. If a part's copies don't all fit, none are cut this run (layout.py).
   Arrange packs parts against the envelope's edges, so each envelope is the nest region shrunk by the
   part spacing: an outline's tool path (tool radius + lead-in) then stays out of the clamp strips.
   Offcuts of that thickness (partly used sheets, offcuts.py) are filled first, smallest room first, loaded the
   same way round as their last cut, then new sheets. On an offcut, the room beside earlier cuts is filled
   first (one Arrange per rectangle), then its free stretch: one sheet, several Arranges. It's tried with the
   parts in a few orders (NEST_ORDERS), each on its own copies and sheets, plus once with offcuts spun round
   where that gives more room. The best nest is kept: most copies placed, then fewest new sheets, then fewest
   offcuts spun round, then fewest sheets, then the shortest last sheet (the most room left for parts that join
   it later). The other tries' copies are hidden like any other leftover; nothing that an Arrange moved is ever
   arranged again or deleted.
4. Per sheet: one tool, each part's feature plan with that tool, the cut order of the outlines.
5. CAM per sheet: stock + setup, template (every op's tool GUID checked), selections, one outline op per
   part copy in cut order. Parts that ask for tabs (PartSpec.tabs) get tabs on their outline and their
   cutouts: a count per contour from its length, placed by tabs.place_tabs (straight edges clear of corners
   first) and given to Fusion as points; cutouts go in copies of the [inner] op, one per count.
   A sheet with parts that ask for facing (PartSpec.face) also gets a FACE setup of its own on the same stock
   and zero: the face template's one [face] op (its tool GUID checked) skims each of those copies' tops inside
   its outline, job.face.depth_in deep. It posts as the sheet's FACE program, run first; the sheet isn't
   offered without it.
   Bent parts that ask for their bend lines scribed (PartSpec.scribe, core 0.18.0; scribe.py finds each bend's
   centerline on the copy's top): "endmill" gets a [scribe] op in the sheet's own setup (a copy of the template's
   [outer] op, before the outlines); "chamfer" a SCRIBE setup with the chamfer mill, run after FACE and before
   the sheet. Scribing never holds a sheet up: a part whose bend lines can't be found or scribed gets a warning to
   mark them by hand instead.
6. Post, insert the pauses, and check the final bytes with the check the service repeats
   (sheetcheck.py). Failures are kept as *.REJECTED.tap. A FACE program is checked the same way
   (check_face_program: no deeper than the face depth) and its cutter must stay over the faced parts.
   Scribing (in the sheet's program or its SCRIBE program) must stay on the bend lines, at the scribing depth;
   the rest of the sheet's program still never has the tool center inside a part.
7. Preview, .f3d, result.json.

Pure Python: everything Fusion does goes through the Adapter (adapter.py).
"""

import hashlib
import json
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from autocam_core import CORE_VERSION
from autocam_core import errors as E
from autocam_core.errors import Issue
from autocam_core.holes import ToolProfile
from autocam_core.hotfolder import write_atomic
from autocam_core.layout import Placed, plan_layout
from autocam_core.offcuts import Placement, beside_as_loaded, placement_for, room_beside
from autocam_core.names import (face_program_name, fit_program_name, instance_id, outer_op_name, program_name,
                                scribe_op_name, scribe_program_name)
from autocam_core.ordering import order_outlines
from autocam_core.pauses import PauseError, insert
from autocam_core.plate import PlateAnalysis, analyze
from autocam_core.preview import (BADGE_MIN_PX, flatten, inside_polygon, label_spot, pixels_per_unit, preview_size,
                                  to_pixels)
from autocam_core.tabs import outline_length, place_tabs, tab_count, walls_length
from autocam_core.schema_job import Job, PartSpec, ToolSpec
from autocam_core.schema_result import (
    RESULT_SCHEMA, FusionTeamResult, GuardSummary, HoleCounts, PartResult, PauseEntryResult, PauseSummary,
    PostResult, ProgramResult, Result, SheetPart, SheetResult, ToolForce, WorkerInfo, overall_status, result_json,
)
from autocam_core.scribe import (Line, bend_centerlines, distance_to_line, in_sections, off_the_lines, scribe_sections,
                                 tip_radius_in)
from autocam_core.sheetcheck import (check_face_program, check_fit_program, check_scribe_program, check_sheet_program,
                                     pause_spec)
from autocam_core.tooling import FeaturePlan, ToolNeed, part_tool, plan_features, sheet_tool
from autocam_core.toolpoints import cutting_points

from .adapter import (BEARING, BORE, DRILL, FACE, INNER, OUTER, POCKET, POCKETFINISH, SCRIBE, TAGS, Adapter,
                      AdapterError, OpFill, Rect)

RESULT = "result.json"
LOG = "worker.log"
Z_TOL_IN = 0.001
EDGE_TOL_IN = 0.01      # body boxes can be a little loose; the envelope is already 0.25 in inside the nest region
MOVE_TOL_IN = 0.001     # a copy that moved more than this after nesting stops its sheet
SCRIBE_EPS_IN = 0.001   # a scribe op's radial stock is minus the tool's radius plus this: its center that close to the line


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


@dataclass(frozen=True)
class _Way:
    """One way of loading an offcut (as for its last cut, or turned end for end), in machine X/Y: its free
    stretch along the length (None if it's too short) and the room beside earlier cuts the machine can reach
    (index in the offcut's beside_in, rectangle)."""
    turned: bool
    stretch: Optional[Placement]
    beside: Tuple[Tuple[int, Rect], ...] = ()

    @property
    def length(self) -> float:
        return self.stretch.length if self.stretch else 0.0


@dataclass
class _Fit:
    """One fit series on a sheet: a part's fitted holes or outline, cut tight by the sheet's program, then a lap
    program per further stock value (core 0.16.0)."""
    series: int
    part_key: str
    kind: str                                 # "hole" | "outline"
    feature: str                              # e.g. "1.125 hole", "outline"
    tag: str                                  # the op that cuts it: BORE or BEARING for holes, OUTER
    stocks: Tuple[float, ...]
    holes: List[Tuple[str, Tuple[int, ...]]] = field(default_factory=list)   # (copy id, face ids)
    loops: List[Tuple[str, int, int]] = field(default_factory=list)          # holes cut as cutouts: (copy, face, loop)
    tabs: int = 0                                                            # ...the tabs that hold each slug (in the
                                                                             # sheet's program only: the slug comes
                                                                             # out by hand before the laps)
    instances: List[Tuple[str, str, int]] = field(default_factory=list)      # outline: (instance, copy id, face id)
    laps: List[str] = field(default_factory=list)
    results: List[ProgramResult] = field(default_factory=list)


@dataclass
class _Sheet:
    thickness_in: float
    origin: Tuple[float, float]               # the sheet's (0, 0) in the design, inches
    instances: List[Tuple[str, Placed]]       # (instance id like p03-2, the copy and where it landed)
    envelope: Optional[Rect] = None           # where Arrange could put parts, in the design (its last area)
    offcut_id: Optional[str] = None           # nested onto this offcut instead of a new sheet
    offcut: Optional[_Way] = None
    by_hand: bool = False                     # a part placed by hand: the "sheet" is its own box (origin = its
    size: Optional[Tuple[float, float]] = None   # front-left corner, the program's zero); run it `repeat` times
    repeat: int = 1
    areas: Tuple[Tuple[Optional[int], Rect], ...] = ()   # each Arrange's area with parts: (index of the room beside
                                                         # earlier cuts it filled, or None for the stretch, envelope)
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
    tab_points: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)   # op -> points, sheet X/Y
    result: Optional[SheetResult] = None
    face_name: str = ""                       # its FACE setup and program, if parts on it ask for facing
    face_tops: Tuple[Tuple[str, int], ...] = ()   # (copy id, top face id) of each faced copy
    face_result: Optional[ProgramResult] = None
    fits: List[_Fit] = field(default_factory=list)
    scribe_ops: Dict[str, Tuple[Line, ...]] = field(default_factory=dict)   # [scribe] ops in the sheet's setup
    scribed: Dict[str, int] = field(default_factory=dict)    # part key -> copies those ops scribe
    scribe_name: str = ""                     # its SCRIBE setup and program (the chamfer mill), if any
    scribe_lines: Tuple[Line, ...] = ()       # ...the bend lines it scribes (design X/Y)
    scribe_counts: Dict[str, int] = field(default_factory=dict)
    scribe_result: Optional[ProgramResult] = None
    suppressed: Set[str] = field(default_factory=set)        # ops left out (a scribe op that failed), never deleted

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


def _envelope(job: Job, origin: Tuple[float, float], offcut: Optional[Placement] = None) -> Rect:
    """Where Arrange may put parts on a sheet at origin: the nest region shrunk by the part spacing. On an
    offcut, only its free stretch along the length (Y)."""
    region, inset = job.fixture.nest_region_in, job.nest.part_spacing_in
    y0, y1 = (offcut.x0, offcut.x1) if offcut is not None else (region[1], region[3])
    return (origin[0] + region[0] + inset, origin[1] + y0 + inset,
            origin[0] + region[2] - inset, origin[1] + y1 - inset)


def _beside_envelope(job: Job, origin: Tuple[float, float], r: Rect) -> Rect:
    """Where Arrange may put parts in room beside earlier cuts (r, machine X/Y): shrunk by the part spacing, like
    the nest region."""
    inset = job.nest.part_spacing_in
    return (origin[0] + r[0] + inset, origin[1] + r[1] + inset, origin[0] + r[2] - inset, origin[1] + r[3] - inset)


def _offcut_places(job: Job, thickness: float) -> List[Tuple[str, Optional[_Way], Optional[_Way]]]:
    """This thickness's offcuts with room: (id, loaded the same way round as its last cut, spun round), either
    None when that way round leaves too little (no free stretch long enough and no room beside cuts).
    Smallest room first (best fit): parts go on the scraps they fit, and the big offcuts are kept for big
    parts."""
    region = job.fixture.nest_region_in
    out: List[Tuple[str, Optional[_Way], Optional[_Way]]] = []
    for o in job.offcuts:
        if abs(o.thickness_in - thickness) > 1e-6:
            continue
        ways: List[Optional[_Way]] = [None, None]
        for n, turned in enumerate((o.last_turned, not o.last_turned) if o.can_turn else (o.last_turned,)):
            stretch = placement_for(o.used_in, job.sheet.length_in, region[1], region[3], job.nest.offcut_gap_in,
                                    job.nest.offcut_min_in, turned)
            beside = tuple(beside_as_loaded(o.beside_in, job.sheet.width_in, job.sheet.length_in, turned, region,
                                            job.nest.offcut_beside_min_in))
            ways[n] = _Way(turned, stretch, beside) if stretch or beside else None
        if any(ways):
            out.append((o.id, ways[0], ways[1]))

    def room(place) -> float:
        way = place[1] or place[2]
        return way.length * (region[2] - region[0]) + sum((r[2] - r[0]) * (r[3] - r[1]) for _, r in way.beside)
    return sorted(out, key=room)                      # ties keep the job's order


def _offcut_slots(offcuts, spin: bool) -> List[Tuple[str, _Way]]:
    """Where each offcut takes parts: the same way round as before, unless that leaves too little; with spin,
    whichever way gives the longer free stretch."""
    out = []
    for offcut_id, same, spun in offcuts:
        if spin and spun is not None and (same is None or spun.length > same.length + 1e-6):
            out.append((offcut_id, spun))
        else:
            out.append((offcut_id, same or spun))
    return out


# ------------------------------------------------------------------ 3. nesting

NEST_ORDERS = ("as listed", "biggest first", "longest first")
SQUEEZE_TRIES = 4           # halvings of the last sheet's length when looking for the shortest strip
SQUEEZE_MIN_GAIN_IN = 2.0   # only worth trying if the last sheet could get at least this much shorter


@dataclass
class _Try:
    order: str
    copies: Dict[str, List[str]]                                  # part key -> this try's copies
    spin: bool = False                                            # offcuts spun round where that gives more room
    envelopes: List[Tuple[Tuple[float, float], Rect]] = field(default_factory=list)
    placed: List[Tuple[str, ...]] = field(default_factory=list)   # per envelope
    stock: List[Tuple[Optional[str], Optional[_Way]]] = field(default_factory=list)   # per envelope: offcut
    areas: List[Optional[int]] = field(default_factory=list)      # per envelope: room beside cuts it filled
    remaining: List[str] = field(default_factory=list)
    failed: Optional[str] = None
    score: Tuple[int, int, int, int, float] = (0, 0, 0, 0, 0.0)

    @property
    def name(self) -> str:
        return f"{self.order}, offcuts spun round" if self.spin else self.order


def _by_hand_sheet(adapter: Adapter, job: Job, part: _Part, x_start: float, slot: int,
                   log: Callable[[str], None]) -> Tuple[List["_Sheet"], int]:
    """A part placed by hand: its one copy laid flat by an Arrange of its own, in an area just long enough for its
    longest side along X and its shorter side along Y, so it lands that way round. Its "sheet" is its own box:
    the stock, and the program's zero at the box's front-left corner (bottom). Returns ([the sheet] or [], slot)."""
    cid = part.copies[0]
    long_side, short_side = _plate_size(adapter, part)
    pitch = job.sheet.width_in + job.nest.envelope_spacing_in
    ox = x_start + slot * pitch
    slot += 1
    envelope = (ox, 0.0, ox + long_side + 0.5, short_side + 0.05)
    try:
        got = adapter.arrange([cid], envelope, job.nest.part_spacing_in, {cid: part.analysis.up_face_id})
    except AdapterError as e:
        part.errors.append(Issue(E.ARRANGE_FAILED, f"laying it flat failed: {e}"))
        return [], slot
    if cid not in got.placed:
        why = next(iter(got.refused.values()), "Arrange didn't place it")
        part.errors.append(Issue(E.ARRANGE_FAILED, f"couldn't lay it flat with its long side along X: {why}"))
        return [], slot
    try:
        box = adapter.box(cid)
        upright = adapter.faces_up(cid, part.analysis.up_face_id)
    except AdapterError as e:
        part.errors.append(Issue(E.ARRANGE_FAILED, f"couldn't read where it is: {e}"))
        return [], slot
    tol = job.material.thickness_tol_in + Z_TOL_IN
    if abs(box.z0) > Z_TOL_IN or abs(box.z1 - part.analysis.stock_thickness_in) > tol or not upright:
        part.errors.append(Issue(E.ARRANGE_FAILED, "Arrange didn't lay it flat, top face up"))
        return [], slot
    size = (round(box.x1 - box.x0, 4), round(box.y1 - box.y0, 4))
    log(f"{part.key}: placed by hand, {size[0]:.2f} x {size[1]:.2f} in, run {part.spec.qty} time(s)")
    placed = Placed(cid, part.key, box.xy)
    sheet = _Sheet(part.analysis.stock_thickness_in, (box.x0, box.y0), [(instance_id(part.key, 1), placed)],
                   envelope=envelope, by_hand=True, size=size, repeat=part.spec.qty, areas=((None, envelope),))
    return [sheet], slot


def _plate_size(adapter: Adapter, part: _Part) -> Tuple[float, float]:
    """The plate's two biggest dimensions, from the imported copy's box (before anything is arranged)."""
    try:
        b = adapter.box(part.copies[0])
    except AdapterError:
        return 0.0, 0.0
    a, w, _ = sorted((b.x1 - b.x0, b.y1 - b.y0, b.z1 - b.z0), reverse=True)
    return a, w


def _reserve(adapter: Adapter, parts: Sequence[_Part], up: Dict[str, int],
             log: Callable[[str], None]) -> List[Dict[str, List[str]]]:
    """The squeeze's copies: SQUEEZE_TRIES sets, each with a copy of every copy of every part. They're made
    before anything is arranged, like the tries' copies: Fusion's Arrange refused every copy made after an
    Arrange had run, even ones put back where the part was imported (r012, r013: "upDirection (-1.0, 0.0, 0.0)
    is across the top face"). The ones the squeeze doesn't keep are hidden."""
    sets: List[Dict[str, List[str]]] = []
    for k in range(SQUEEZE_TRIES):
        made: Dict[str, List[str]] = {p.key: [] for p in parts}
        try:
            for p in parts:
                for cid in p.copies:
                    copy = f"{cid}~s{k}"
                    adapter.add_copy(p.copies[0], copy)
                    made[p.key].append(copy)
                    up[copy] = up[cid]
        except AdapterError as e:
            log(f"couldn't make copies to squeeze the last sheet: {e}")
            _discard(adapter, [c for cs in made.values() for c in cs], log)
            break
        sets.append(made)
    return sets


def _squeeze(adapter: Adapter, job: Job, best: "_Try", parts: Sequence[_Part], sizes: Dict[str, Tuple[float, float]],
             up: Dict[str, int], x_start: float, slot: int, pitch: float, thickness: float,
             sets: Sequence[Dict[str, List[str]]], log: Callable[[str], None]) -> int:
    """Fusion's Arrange packs against the left edge, so a few parts can run down the whole length of the last
    sheet (r009). Fit the last sheet's parts into full-width areas that get shorter, halving between the longest
    "shorter side" of its parts and what they take now, and keep the shortest that holds them all: a strip
    across the front, with the back of the sheet left free for parts that join it later or as an offcut.
    Each try is a whole new copy of the sheet at a new spot: its parts beside earlier cuts are arranged again in
    the same rooms, then the rest in the shorter area. Try k uses the copies in sets[k] (see _reserve); the
    caller hides the ones not kept. Returns the next free sheet slot."""
    if not best.envelopes or best.failed or best.areas[-1] is not None:
        return slot                                  # nothing nested, or no free stretch on the last sheet
    origin, env = best.envelopes[-1]
    idx = [i for i, (o, _) in enumerate(best.envelopes) if o == origin]    # the last sheet's areas, in order
    last = list(best.placed[-1])
    try:
        reach = max(adapter.box(c).y1 for c in last) - env[1]
    except AdapterError:
        return slot
    lo = max(min(sizes[c.rsplit(".", 1)[0]]) for c in last)       # each part needs its shorter side, at least
    if reach - lo < SQUEEZE_MIN_GAIN_IN:
        log(f"{thickness:g} in: not squeezed: the last sheet's parts take {reach:.1f} in, at least {lo:.1f} in")
        return slot
    hi, kept = reach, None                           # kept: (origin, envelope per area, copies per area, reach)
    for k in range(len(sets)):
        target = round((lo + hi) / 2, 3)
        if hi - target < 0.5:
            break
        o = (x_start + slot * pitch, 0.0)
        slot += 1
        dx, dy = o[0] - origin[0], o[1] - origin[1]
        moved = [(e[0] + dx, e[1] + dy, e[2] + dx, e[3] + dy) for e in (best.envelopes[i][1] for i in idx)]
        probe = (moved[-1][0], moved[-1][1], moved[-1][2], moved[-1][1] + target)
        areas = moved[:-1] + [probe]
        spare = {key: list(cs) for key, cs in sets[k].items()}
        copies = [[spare[c.rsplit(".", 1)[0]].pop(0) for c in best.placed[i]] for i in idx]
        done, refused = 0, None
        try:
            for area, cs in zip(areas, copies):
                got = adapter.arrange(cs, area, job.nest.part_spacing_in, {c: up[c] for c in cs})
                refused = refused or next(iter(got.refused.values()), None)
                if set(got.placed) != set(cs):
                    break
                done += 1
            fits = done == len(areas)
            r = max(adapter.box(c).y1 for c in copies[-1]) - probe[1] if fits else None
            log(f"{thickness:g} in squeeze try {k + 1}: {target:.1f} in long: "
                + (f"all {sum(map(len, copies))} placed, {r:.1f} in" if fits else
                   "the parts beside earlier cuts didn't all fit there again" if done < len(areas) - 1 else
                   "not all placed")
                + (f"; refused: {refused}" if refused else ""))
        except AdapterError as e:
            log(f"squeezing the last {thickness:g} in sheet stopped: {e}")
            break
        if fits:
            kept = (o, moved, copies, r)
            hi = r
        elif done < len(areas) - 1:
            break                                    # not about the shorter area: shorter still won't help
        else:
            lo = target
    if kept is None or kept[3] > reach - 0.5:
        log(f"{thickness:g} in: the last sheet stays {reach:.1f} in long (nothing shorter held all its parts)")
        return slot
    o, envs, copies, r = kept
    old = [c for i in idx for c in best.placed[i]]
    _discard(adapter, old, log)
    swap = dict(zip(old, [c for cs in copies for c in cs]))
    for p in parts:
        p.copies = [swap.get(c, c) for c in p.copies]
    for n, i in enumerate(idx):
        best.envelopes[i] = (o, envs[n])
        best.placed[i] = tuple(copies[n])
    log(f"{thickness:g} in: last sheet squeezed toward the front: {reach:.1f} -> {r:.1f} in along its length")
    return slot


def _part_order(parts: Sequence[_Part], order: str, sizes: Dict[str, Tuple[float, float]]) -> List[str]:
    keys = [p.key for p in parts]
    if order == "biggest first":
        return sorted(keys, key=lambda k: -(sizes[k][0] * sizes[k][1]))
    if order == "longest first":
        return sorted(keys, key=lambda k: -sizes[k][0])
    return keys


def _score(adapter: Adapter, t: _Try, last_turned: Dict[str, bool]) -> Tuple[int, int, int, int, float]:
    """Smaller is better: (-copies placed, new sheets, offcuts spun round, sheets, how far the last sheet's parts
    reach along its free stretch, Y; 0 if it only has parts beside earlier cuts)."""
    placed = sum(len(p) for p in t.placed)
    if not t.envelopes:
        return (0, 0, 0, 0, 0.0)
    stock = dict(zip((o for o, _ in t.envelopes), t.stock))          # sheet origin -> (offcut id, way)
    reach = 0.0
    if t.areas[-1] is None:
        env = t.envelopes[-1][1]
        try:
            reach = max(adapter.box(c).y1 for c in t.placed[-1]) - env[1]
        except AdapterError:
            reach = float("inf")
    fresh = sum(1 for offcut_id, _ in stock.values() if offcut_id is None)
    spins = sum(1 for offcut_id, way in stock.values() if offcut_id and way.turned != last_turned[offcut_id])
    return (-placed, fresh, spins, len(stock), round(reach, 3))

def _nest_group(adapter: Adapter, job: Job, thickness: float, parts: List[_Part], x_start: float, slot: int,
                log: Callable[[str], None], notes: List[str]) -> Tuple[List[_Sheet], int]:
    """Arrange one stock thickness onto as many sheets as it takes (up to the job's limit)."""
    pitch = job.sheet.width_in + job.nest.envelope_spacing_in       # sheets side by side along X
    by_key = {p.key: p for p in parts}

    def key_of(cid: str) -> str:
        return cid.rsplit(".", 1)[0]

    def reject(keys, msg: str) -> None:
        for key in sorted(set(keys)):
            if by_key[key].ok:
                by_key[key].errors.append(Issue(E.ARRANGE_FAILED, msg))

    up = {cid: p.analysis.up_face_id for p in parts for cid in p.copies}

    # The orders to try, each on its own copies, made now while every copy still sits where it was imported,
    # and the squeeze's copies too: Fusion refuses copies made after an Arrange (see _reserve).
    sizes = {p.key: _plate_size(adapter, p) for p in parts}
    offcuts = _offcut_places(job, thickness)
    last_turned = {o.id: o.last_turned for o in job.offcuts}
    tries = [_Try(NEST_ORDERS[0], {p.key: list(p.copies) for p in parts})]
    seen = [_part_order(parts, NEST_ORDERS[0], sizes)]
    plans = [(order, False) for order in NEST_ORDERS[1:]]
    if _offcut_slots(offcuts, True) != _offcut_slots(offcuts, False):
        plans.append((NEST_ORDERS[0], True))          # spinning an offcut would give more room: try that too
    for n, (order, spin) in enumerate(plans, 2):
        keys = _part_order(parts, order, sizes)
        if not spin and keys in seen:
            continue                                  # the same order as a try already planned
        seen.append(keys)
        made: Dict[str, List[str]] = {p.key: [] for p in parts}
        try:
            for p in parts:
                for cid in p.copies:
                    copy = f"{cid}~{n}"
                    adapter.add_copy(p.copies[0], copy)
                    made[p.key].append(copy)
                    up[copy] = up[cid]
        except AdapterError as e:
            log(f"couldn't make copies to try '{order}': {e}")
            _discard(adapter, [c for cs in made.values() for c in cs], log)
            continue
        tries.append(_Try(order, made, spin))
    reserve = _reserve(adapter, parts, up, log)

    for i, t in enumerate(tries):
        remaining = [c for k in _part_order(parts, t.order, sizes) for c in t.copies[k] if by_key[k].ok]
        for offcut_id, way in _offcut_slots(offcuts, t.spin) + [(None, None)] * job.nest.max_sheets_per_group:
            if not remaining:
                break
            origin = (x_start + slot * pitch, 0.0)
            slot += 1
            # An offcut's room beside earlier cuts first, one Arrange each, then its free stretch: one sheet.
            areas: List[Tuple[Optional[int], Rect]] = [(n, _beside_envelope(job, origin, r))
                                                       for n, r in (way.beside if way else ())]
            if way is None or way.stretch is not None:
                areas.append((None, _envelope(job, origin, way.stretch if way else None)))
            took, beside_took = 0, 0
            for which, envelope in areas:
                if not remaining:
                    break
                try:
                    got = adapter.arrange(remaining, envelope, job.nest.part_spacing_in,
                                          {c: up[c] for c in remaining})
                except AdapterError as e:
                    t.failed = f"Arrange failed: {e}"
                    if i == 0:
                        reject([key_of(c) for c in remaining], t.failed)
                    log(f"Arrange failed for a {thickness:g} in sheet ('{t.name}'): {e}")
                    break
                if i == 0:
                    for cid, why in got.refused.items():
                        reject([key_of(cid)], f"Arrange can't lay it flat: {why}")
                placed = set(got.placed)
                if placed:
                    t.envelopes.append((origin, envelope))
                    t.placed.append(tuple(c for c in remaining if c in placed))
                    t.stock.append((offcut_id, way))
                    t.areas.append(which)
                took += len(placed)
                beside_took += len(placed) if which is not None else 0
                remaining = [c for c in remaining if c not in placed and by_key[key_of(c)].ok]
            if t.failed:
                break
            what = f"offcut {offcut_id}" if offcut_id else "sheet"
            log(f"{thickness:g} in {what} at x={origin[0]:g} ('{t.name}'): placed {took}"
                + (f" ({beside_took} beside earlier cuts)" if beside_took else "") + f", {len(remaining)} left")
            if not took and offcut_id is None:
                break                                 # a new sheet that takes nothing: nothing more fits
        t.remaining = remaining
        t.score = _score(adapter, t, last_turned)
        if i == 0 and t.failed:
            break                                     # the first try's failure rejected the parts; nothing to compare

    usable = [t for t in tries if not t.failed] or tries[:1]
    best = min(usable, key=lambda t: t.score)          # ties keep the earlier try
    for t in tries:
        if t is not best:
            _discard(adapter, [c for cs in t.copies.values() for c in cs], log)
    if len(tries) > 1:
        def said(t: _Try) -> str:
            return "failed" if t.failed else f"{-t.score[0]} placed on {t.score[3]} sheet(s), last {t.score[4]:.1f} in"
        log(f"{thickness:g} in: kept '{best.name}' ({'; '.join(f'{t.name}: {said(t)}' for t in tries)})")
        if best is not tries[0] and best.score < tries[0].score:
            notes.append(f"{thickness:g} in: nesting the parts {best.name} beat the listed order "
                         f"({said(best)} instead of {said(tries[0])})")
    for p in parts:
        p.copies = list(best.copies[p.key])
    slot = _squeeze(adapter, job, best, parts, sizes, up, x_start, slot, pitch, thickness, reserve, log)
    kept = {c for p in parts for c in p.copies}
    _discard(adapter, [c for cs in reserve for k in cs.values() for c in k if c not in kept], log)
    up = {c: up[c] for p in parts for c in p.copies}
    envelopes = best.envelopes
    stock_of = {env: st for (_, env), st in zip(best.envelopes, best.stock)}
    area_of = {env: which for (_, env), which in zip(best.envelopes, best.areas)}
    sheet_ids = {o: n for n, o in enumerate(dict.fromkeys(o for o, _ in envelopes))}   # areas at one origin: a sheet
    remaining = best.remaining

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
                             edge_tol_in=EDGE_TOL_IN, sheet_of=[sheet_ids[o] for o, _ in envelopes])
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
    sheets = [_Sheet(thickness, origin_of[s.envelope_in], list(s.instances), envelope=s.envelope_in,
                     offcut_id=stock_of[s.envelope_in][0], offcut=stock_of[s.envelope_in][1],
                     areas=tuple((area_of[a], a) for a in s.areas))
              for s in layout.sheets]
    return sheets, slot


# ------------------------------------------------------------------ 4. tool and features per sheet

def _sheet_use(job: Job, sheet: _Sheet, parts: Dict[str, _Part]) -> Dict[str, Any]:
    """How full the sheet is (the parts' material area, the usable area, the empty strip at the back), which
    offcut it's on, the stretch along its length (Y) its free-stretch parts use (with the outlines' tool paths),
    which room beside earlier cuts it filled, and the room beside its own parts it leaves, in sheet coordinates."""
    if not sheet.areas or not sheet.instances:
        return {}
    ox, oy = sheet.origin
    boxes: Dict[Optional[int], List[Rect]] = {which: [] for which, _ in sheet.areas}
    area = 0.0
    for _, placed in sheet.instances:
        a = parts[placed.part_key].analysis
        area += sum(a.geometry.face(f).area_in2 for f in (a.top_face_ids or (a.up_face_id,)))
        cx, cy = placed.center
        which = next((w for w, e in sheet.areas if e[0] <= cx <= e[2] and e[1] <= cy <= e[3]), None)
        b = placed.bbox_in
        boxes.setdefault(which, []).append((b[0] - ox, b[1] - oy, b[2] - ox, b[3] - oy))
    margin = job.nest.part_spacing_in              # covers the outline's tool path around each part
    gap, least = job.nest.offcut_gap_in, job.nest.offcut_beside_min_in
    out: Dict[str, Any] = {
        "parts_area_in2": round(area, 2),
        "usable_area_in2": round(sum((e[2] - e[0]) * (e[3] - e[1]) for _, e in sheet.areas), 2),
        "offcut_id": sheet.offcut_id, "offcut_turned": bool(sheet.offcut and sheet.offcut.turned)}
    left: List[Rect] = []
    beside = dict(sheet.offcut.beside) if sheet.offcut else {}
    for which, bs in boxes.items():
        if which is not None and bs:               # what's left of room beside cuts, to the right of its new parts
            r = beside[which]
            room = room_beside(bs, (r[1], r[3]), r[2], margin, gap, least)
            if room:
                left.append(room)
    stretch = next((e for w, e in sheet.areas if w is None), None)
    bs = boxes.get(None, [])
    if stretch is not None and bs:
        used = (round(min(b[1] for b in bs) - margin, 3), round(max(b[3] for b in bs) + margin, 3))
        out["used_y_in"] = used
        out["free_length_in"] = round(max(0.0, stretch[3] - oy - max(b[3] for b in bs)), 2)
        room = room_beside(bs, used, job.fixture.nest_region_in[2], margin, gap, least)
        if room:
            left.append(room)
    out["beside_used"] = tuple(sorted(w for w, bs in boxes.items() if w is not None and bs))
    out["beside_left_in"] = tuple(left)
    return out


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
                if parts[k].spec.face and FACE not in adapter.capabilities:
                    missing.append(FACE)
                problems_fit = _fit_problems(parts[k], plan, job)
                problems = list(plan.errors) + problems_fit
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


def _fit_holes(part: _Part, plan: FeaturePlan, job: Job) -> Tuple[str, List[int], List[Any]]:
    """A hole fit's holes on the part: (BORE, BEARING or INNER, indexes into through_holes, and for INNER the
    loops: holes over the bore range, and small contoured ones, are cut as cutouts)."""
    d = part.spec.fit.diameter_in
    through = part.analysis.through_holes
    near = lambda i: abs(through[i].diameter_in - d) <= job.holes.bearing_tol_in + 1e-9
    bore, bearing = [i for i in plan.bore if near(i)], [i for i in plan.bearing if near(i)]
    if bearing or bore:
        return (BEARING, bearing, []) if bearing else (BORE, bore, [])
    loops = [l for l in plan.inner_loops if l.hole is not None and near(l.hole)]
    return INNER, [l.hole for l in loops], loops


def _fit_problems(part: _Part, plan: FeaturePlan, job: Job) -> List[Issue]:
    fit = part.spec.fit
    if fit is None or fit.kind != "hole":
        return []
    _, found, _ = _fit_holes(part, plan, job)
    if found:
        return []
    through = part.analysis.through_holes
    sizes = ", ".join(sorted({f"{h.diameter_in:.3f}" for h in through})) or "none"
    drilled = any(abs(through[i].diameter_in - fit.diameter_in) <= job.holes.bearing_tol_in for i in plan.drill)
    why = ("it's drilled, and a drilled hole can't be fitted" if drilled else
           f"no bored hole that size (its round holes: {sizes} in)")
    return [Issue(E.NEEDS_MANUAL_CAM, f"Fit: {fit.diameter_in:g} hole: {why}")]


def _sheet_xy(sheet: _Sheet, placed: Placed) -> Tuple[float, float]:
    cx, cy = placed.center
    return cx - sheet.origin[0], cy - sheet.origin[1]


# ------------------------------------------------------------------ 5. CAM

def outline_face(part: "_Part"):
    """What the part's outline is cut from: its top face, or for a top split into several faces in one plane (a
    sheet metal flat pattern is split along its bends) all of them, as a tuple: the adapter cuts their outline."""
    tops = tuple(part.analysis.top_face_ids)
    return tops if len(tops) > 1 else part.analysis.up_face_id


def _op_tag(name: str) -> Optional[str]:
    found = [t for t in TAGS if f"[{t}]" in name.lower()]
    return found[0] if len(found) == 1 else None


def _tab_op(adapter: Adapter, job: Job, sheet: _Sheet, op_name: str,
            contours: Sequence[Tuple[str, int, Optional[int]]], per_contour: int, tool_d: float,
            setup: Optional[str] = None) -> None:
    """Tabs on one contour op: `per_contour` on each of its contours (copy id, face id, inner loop index or None
    for the outer loop), placed by tabs.place_tabs and given to Fusion as points; if that fails, Fusion spreads
    the same number evenly (and the sheet card says so). setup: another setup of the sheet (a fit lap): the same
    tabs there, nothing recorded twice."""
    where = setup or sheet.name
    if job.tabs.width_in or job.tabs.height_in:
        try:
            adapter.set_tab_size(where, op_name, job.tabs.width_in, job.tabs.height_in)
        except AdapterError as e:
            sheet.warnings.append(Issue(E.OP_WARNING, f"{op_name}: the tab size stayed the template's ({e})"))
    if job.tabs.at_points:
        try:
            points = []
            on_lines = clear = 0
            for cid, fid, index in contours:
                placed = place_tabs(adapter.loop_segments(cid, fid, index), per_contour, tool_d,
                                    job.tabs.width_in or tool_d)
                points += [(p.x, p.y) for p in placed]
                on_lines += sum(p.on_line for p in placed)
                clear += sum(p.clear_of_corners for p in placed)
            adapter.set_tab_points(where, op_name, points)
            if setup is None:
                sheet.tab_points[op_name] = [(round(x - sheet.origin[0], 4), round(y - sheet.origin[1], 4))
                                             for x, y in points]
                sheet.notes.append(f"{op_name}: {len(points)} tabs at points, {on_lines} on straight edges, {clear} "
                                   "clear of corners")
            return
        except AdapterError as e:
            sheet.notes.append(f"{where} {op_name}: tabs at points didn't work ({e}); {per_contour} per contour, "
                               "spread evenly by Fusion instead")
    adapter.set_tabs(where, op_name, per_contour)


def _tabs_for(job: Job, part: _Part, length_in: float, tool_d: float) -> int:
    return tab_count(length_in, job.tabs.distance_in, tool_d, job.tabs.min_per_contour, job.tabs.max_per_contour,
                     job.tabs.width_in)


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
    size_x, size_y = sheet.size or (job.sheet.width_in, job.sheet.length_in)
    adapter.make_sheet(sheet.name, sheet.origin, size_x, size_y, sheet.thickness_in, copy_ids)
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
    tool_d = job.tooling.tools[sheet.tool_key].diameter_in
    tabbed: Dict[int, List[Tuple[str, int, int]]] = {}   # tab count -> tabbed parts' cutouts that get that many
    floors: List[Tuple[str, int]] = []
    fits: Dict[str, _Fit] = {}
    for _, placed in sheet.instances:
        part = parts[placed.part_key]
        plan = sheet.plans[part.key]
        through = part.analysis.through_holes
        fitted: Set[int] = set()
        fitted_loops: Set[Tuple[int, int]] = set()
        if part.spec.fit is not None:
            f = part.spec.fit
            if part.key not in fits:
                feature = f"{f.diameter_in:g} hole" if f.kind == "hole" else "outline"
                fits[part.key] = _Fit(len(fits) + 1, part.key, f.kind, feature, OUTER, tuple(f.stocks_in))
            if f.kind == "hole":
                tag, found, found_loops = _fit_holes(part, plan, job)
                fit = fits[part.key]
                fit.tag = tag
                if tag == INNER:                      # cut as a cutout: its loops, and the tabs that hold its slug
                    fit.loops += [(placed.body_id, l.face_id, l.index) for l in found_loops]
                    fitted_loops = {(l.face_id, l.index) for l in found_loops}
                    if part.spec.tabs and found_loops:
                        fit.tabs = _tabs_for(job, part, walls_length(part.analysis.geometry,
                                                                      found_loops[0].wall_face_ids), tool_d)
                else:
                    fit.holes += [(placed.body_id, through[i].face_ids) for i in found]
                fitted = set(found)
        for tag, indexes in ((DRILL, plan.drill), (BORE, plan.bore), (BEARING, plan.bearing)):
            holes[tag] += [(placed.body_id, through[i].face_ids) for i in indexes if i not in fitted]
        for loop in plan.inner_loops:
            if (loop.face_id, loop.index) in fitted_loops:
                continue                              # a fitted hole: its own op, below
            n = _tabs_for(job, part, walls_length(part.analysis.geometry, loop.wall_face_ids), tool_d) \
                if part.spec.tabs else 0
            (tabbed.setdefault(n, []) if n else loops).append((placed.body_id, loop.face_id, loop.index))
        floors += [(placed.body_id, f) for f in plan.pocket_floor_ids]
    for fit in fits.values():                         # a bearing-size fit without a [bearing] op is bored
        if fit.tag == BEARING and BEARING not in by_tag:
            fit.tag = BORE
        if fit.kind == "hole" and fit.tag not in by_tag:
            sheet.errors.append(Issue(E.TEMPLATE_PROBLEM, f"template has no [{fit.tag}] op for the fitted holes"))
    sheet.fits = sorted(fits.values(), key=lambda f: f.series)
    if holes[BEARING] and BEARING not in by_tag and BORE in by_tag:
        holes[BORE] += holes[BEARING]
        holes[BEARING] = []
        sheet.warnings.append(Issue(E.OP_WARNING, "the template has no [bearing] op; bearing holes are bored with [bore]"
                                    + (" and holes.bearing_stock_in isn't applied" if job.holes.bearing_stock_in else "")))

    if tabbed and INNER not in by_tag:
        loops += [loop for group in tabbed.values() for loop in group]     # reported as a missing [inner] op
        tabbed = {}
    stock = job.holes.bearing_stock_in or None             # press-fit tuning; 0 keeps the template's
    fills = {DRILL: OpFill(DRILL, holes=tuple(holes[DRILL])), BORE: OpFill(BORE, holes=tuple(holes[BORE])),
             BEARING: OpFill(BEARING, holes=tuple(holes[BEARING]), stock_in=stock), INNER: OpFill(INNER, loops=tuple(loops)),
             POCKET: OpFill(POCKET, floors=tuple(floors)), POCKETFINISH: OpFill(POCKETFINISH, floors=tuple(floors))}
    for tag, fill in fills.items():
        wanted = len(fill.holes) + len(fill.loops) + len(fill.floors)
        if wanted and tag not in by_tag and tag != POCKETFINISH:      # a template may leave out the finish pass
            sheet.errors.append(Issue(E.TEMPLATE_PROBLEM, f"template has no [{tag}] op but this sheet needs one "
                                                          f"({wanted} features)"))
    if sheet.errors:
        return
    for tag, names in by_tag.items():
        if tag == OUTER:
            continue
        for fit in sheet.fits:                       # fitted holes: their op again, from the template, alone, tight.
            if fit.kind == "hole" and fit.tag == tag:   # Never renamed: renaming a Bore op with no holes selected
                had = set(adapter.op_names(sheet.name))  # pops up "Failed to generate toolpath" in Fusion and holds
                added = [o.name for o in adapter.apply_template(sheet.name, tool.template_path)   # the job (r035)
                         if o.name not in had]
                keep = next(o for o in added if _op_tag(o) == tag)
                for o in added:
                    if o != keep:
                        adapter.delete_op(sheet.name, o)
                adapter.fill(sheet.name, keep, OpFill(tag, holes=tuple(fit.holes), loops=tuple(fit.loops)))
                adapter.set_radial_stock(sheet.name, keep, fit.stocks[0])
                if fit.loops and fit.tabs:            # the slug stays held, through every lap too
                    _tab_op(adapter, job, sheet, keep, fit.loops, fit.tabs, tool_d)
        if tag == INNER:
            # Tabbed parts' cutouts: a copy of the op per tab count, made before the op is filled, so each
            # copy starts empty; they land after it (and before the outlines).
            for n, group in sorted(tabbed.items()):
                name = f"{names[0]} - {n} tab{'s' if n != 1 else ''} each"
                adapter.copy_op(sheet.name, names[0], name)
                adapter.fill(sheet.name, name, OpFill(INNER, loops=tuple(group)))
                _tab_op(adapter, job, sheet, name, [(cid, fid, idx) for cid, fid, idx in group], n, tool_d)
        fill = fills[tag]
        if fill.holes or fill.loops or fill.floors:
            adapter.fill(sheet.name, names[0], fill)
        else:
            adapter.delete_op(sheet.name, names[0])

    _build_sheet_scribe(adapter, job, sheet, parts, by_tag[OUTER][0])
    points = [(inst, _sheet_xy(sheet, placed)) for inst, placed in sheet.instances]
    sheet.outer_order = tuple(order_outlines(points))
    by_instance = {inst: placed for inst, placed in sheet.instances}
    outlines = [(outer_op_name(inst), by_instance[inst].body_id, outline_face(parts[by_instance[inst].part_key]))
                for inst in sheet.outer_order]
    adapter.make_outer_ops(sheet.name, by_tag[OUTER][0], outlines)
    outline_fits = {f.part_key: f for f in sheet.fits if f.kind == "outline"}
    for inst in sheet.outer_order:                    # the template's tabs, on for the parts that ask for them
        part = parts[by_instance[inst].part_key]
        fit = outline_fits.get(part.key)
        if fit is not None:                           # a fitted outline: cut tight by the first stock
            adapter.set_radial_stock(sheet.name, outer_op_name(inst), fit.stocks[0])
            fit.instances.append((inst, by_instance[inst].body_id, outline_face(part)))
        if part.spec.tabs:
            n = _outline_tabs(job, part, tool_d)
            if n:
                _tab_op(adapter, job, sheet, outer_op_name(inst),
                        [(by_instance[inst].body_id, outline_face(part), None)], n, tool_d)
            else:
                sheet.warnings.append(Issue(E.OP_WARNING, f"{part.spec.name} is too small for tabs: it's cut free"))
    sheet.built = True
    _build_face(adapter, job, sheet, parts)
    _build_scribe(adapter, job, sheet, parts)
    _build_fits(adapter, job, sheet, parts)


def _outline_tabs(job: Job, part: _Part, tool_d: float) -> int:
    g = part.analysis.geometry
    inner = {w for loop in part.analysis.through_loops for w in loop.wall_face_ids}
    return _tabs_for(job, part, outline_length(g, inner), tool_d)


def _build_fits(adapter: Adapter, job: Job, sheet: _Sheet, parts: Dict[str, _Part]) -> None:
    """A lap per further stock value of each fit series: a setup of its own on the sheet's stock and zero, the
    sheet template's op for the feature alone (the fitted holes, or the fitted copies' outlines with the same tabs),
    leaving that stock."""
    tool = job.tooling.tools[sheet.tool_key]
    for fit in sheet.fits:
        part = parts[fit.part_key]
        copies = sorted({cid for cid, _ in fit.holes} | {cid for cid, _, _ in fit.loops}
                        | {cid for _, cid, _ in fit.instances})
        for lap, stock in enumerate(fit.stocks[1:], 1):
            name = fit_program_name(sheet.name, fit.series, lap)
            adapter.make_extra_setup(sheet.name, name, copies)
            ops = [o.name for o in adapter.apply_template(name, tool.template_path)]
            keep = next((o for o in ops if _op_tag(o) == fit.tag), None)
            if keep is None:
                sheet.errors.append(Issue(E.TEMPLATE_PROBLEM, f"template has no [{fit.tag}] op for {name}"))
                return
            for o in ops:
                if o != keep:
                    adapter.delete_op(name, o)
            if fit.kind == "hole":
                adapter.fill(name, keep, OpFill(fit.tag, holes=tuple(fit.holes), loops=tuple(fit.loops)))
                adapter.set_radial_stock(name, keep, stock)
                # A cutout's lap has no tabs: its slug was taken out by hand after the sheet's program (the team's
                # way), and the full lap also takes the broken tab stubs off the wall.
            else:
                adapter.make_outer_ops(name, keep, [(outer_op_name(inst), cid, fid) for inst, cid, fid in fit.instances])
                n = _outline_tabs(job, part, tool.diameter_in)
                for inst, cid, fid in fit.instances:
                    adapter.set_radial_stock(name, outer_op_name(inst), stock)
                    if n:                             # the same tabs, so the lap doesn't cut the part free
                        _tab_op(adapter, job, sheet, outer_op_name(inst), [(cid, fid, None)], n, tool.diameter_in,
                                setup=name)
            fit.laps.append(name)


def _build_face(adapter: Adapter, job: Job, sheet: _Sheet, parts: Dict[str, _Part]) -> None:
    """The sheet's FACE program, if parts on it ask for facing: a setup of its own on the sheet's stock and zero,
    the face template's one [face] op (with the face tool, checked by GUID), each faced copy's top skimmed inside
    its outline."""
    faced = [placed for _, placed in sheet.instances if parts[placed.part_key].spec.face]
    if not faced:
        return
    if job.face is None:
        sheet.errors.append(Issue(E.TEMPLATE_PROBLEM, "parts ask for facing but the job has no face settings"))
        return
    tool = job.face.tool
    name = face_program_name(sheet.name)
    adapter.make_extra_setup(sheet.name, name, [p.body_id for p in faced])
    ops = adapter.apply_template(name, tool.template_path)
    tagged = [o.name for o in ops if f"[{FACE}]" in o.name.lower()]
    if len(ops) != 1 or len(tagged) != 1:
        listed = ", ".join(repr(o.name) for o in ops) or "none"
        sheet.errors.append(Issue(E.TEMPLATE_PROBLEM, f"the face template needs exactly one op, tagged [face] "
                                                      f"(it has {listed})"))
        return
    if ops[0].tool_guid != tool.guid:
        sheet.errors.append(Issue(E.TOOL_GUID_MISMATCH, f"the face op's tool is {ops[0].tool_guid or 'unreadable'}, "
                                                        f"not the {tool.cutter_label} (GUID {tool.guid}); rebuild "
                                                        "the face template (fusion/tools/make_templates/make_face.py)"))
        return
    sheet.face_tops = tuple((p.body_id, outline_face(parts[p.part_key])) for p in faced)
    adapter.fill(name, tagged[0], OpFill(FACE, tops=sheet.face_tops, depth_in=job.face.depth_in))
    sheet.face_name = name


def _scribe_depth(job: Job, depth_in: float, faced: bool) -> float:
    """How far below the stock top a scribed line goes: its depth below the part's top, which a faced part has
    lower by the face depth."""
    return depth_in + (job.face.depth_in if faced and job.face is not None else 0.0)


def _scribe_groups(adapter: Adapter, job: Job, sheet: _Sheet, parts: Dict[str, _Part], way: str
                   ) -> Tuple[Dict[bool, List[Line]], Dict[str, int]]:
    """The bend lines of the copies on the sheet whose part asks to be scribed `way`, by faced or not, and how
    many copies of each part that is. A part whose lines can't be found on every copy isn't scribed: a sheet
    warning says to mark its bends by hand."""
    found: Dict[str, List[Tuple[Line, ...]]] = {}
    missed: Dict[str, str] = {}
    for _, placed in sheet.instances:
        part = parts[placed.part_key]
        if part.spec.scribe != way or part.key in missed:
            continue
        try:
            faces = adapter.top_edges(placed.body_id, tuple(part.analysis.top_face_ids))
        except AdapterError as e:
            missed[part.key] = f"its top couldn't be read: {e}"
            continue
        lines, why = bend_centerlines(faces, [(b.angle_deg, b.radius_in) for b in part.spec.bends],
                                      part.analysis.measured_thickness_in)
        if why:
            missed[part.key] = why
        else:
            found.setdefault(part.key, []).append(lines)
    for key, why in missed.items():
        found.pop(key, None)
        sheet.warnings.append(Issue(E.OP_WARNING, f"{parts[key].spec.name}: its bend lines aren't scribed ({why}). "
                                                  "Mark them by hand."))
    groups: Dict[bool, List[Line]] = {}
    for key, per_copy in found.items():
        groups.setdefault(bool(parts[key].spec.face), []).extend(ln for lines in per_copy for ln in lines)
    return groups, {key: len(per_copy) for key, per_copy in found.items()}


def _asks_scribe(job: Job, sheet: _Sheet, parts: Dict[str, _Part], way: str) -> bool:
    return job.scribe is not None and any(parts[p.part_key].spec.scribe == way for _, p in sheet.instances)


def _build_sheet_scribe(adapter: Adapter, job: Job, sheet: _Sheet, parts: Dict[str, _Part], outer_op: str) -> None:
    """Bend lines scribed with the sheet's own cutter: a copy of the template's [outer] op per faced/not group,
    made before the outline copies (so it runs before them), along the lines at the scribing depth."""
    if not _asks_scribe(job, sheet, parts, "endmill"):
        return
    if SCRIBE not in adapter.capabilities:
        sheet.warnings.append(Issue(E.OP_WARNING, "this worker can't scribe bend lines yet: mark them by hand"))
        return
    groups, counts = _scribe_groups(adapter, job, sheet, parts, "endmill")
    for faced, lines in sorted(groups.items()):
        name = scribe_op_name(faced)
        keys = [k for k in counts if bool(parts[k].spec.face) == faced]
        tool = job.tooling.tools[sheet.tool_key]
        try:
            adapter.copy_op(sheet.name, outer_op, name)
            adapter.fill(sheet.name, name, OpFill(SCRIBE, lines=tuple(lines),
                                                  depth_in=_scribe_depth(job, job.scribe.depth_in, faced),
                                                  stock_in=-(tool.diameter_in / 2 - SCRIBE_EPS_IN)))
        except AdapterError as e:
            if name in adapter.op_names(sheet.name):
                _suppress(adapter, sheet, sheet.name, name)
            sheet.warnings.append(Issue(E.OP_WARNING, f"bend lines not scribed ({e}): mark them by hand on "
                                                      f"{', '.join(parts[k].spec.name for k in keys)}"))
            continue
        sheet.scribe_ops[name] = tuple(lines)
        sheet.scribed.update({k: counts[k] for k in keys})


def _suppress(adapter: Adapter, sheet: _Sheet, setup: str, op: str) -> None:
    """Leave a scribe op out. Never deleted once its settings were changed: that crashed Fusion's next toolpath
    generation (r041-r043). If even that fails, the sheet can't be trusted."""
    try:
        adapter.suppress_op(setup, op)
        sheet.suppressed.add(op if setup == sheet.name else f"{setup}/{op}")
    except AdapterError as e:
        sheet.errors.append(Issue(E.OP_ERROR, f"{op} couldn't be left out: {e}"))


def _build_scribe(adapter: Adapter, job: Job, sheet: _Sheet, parts: Dict[str, _Part]) -> None:
    """The sheet's SCRIBE program: bend lines scribed with the chamfer mill. A setup of its own on the sheet's
    stock and zero, the sheet template's [outer] op copied per faced/not group with the chamfer mill set on it
    (checked by GUID), the rest of the template deleted."""
    if not _asks_scribe(job, sheet, parts, "chamfer"):
        return
    s = job.scribe
    if SCRIBE not in adapter.capabilities or s.chamfer is None:
        sheet.warnings.append(Issue(E.OP_WARNING, "bend lines can't be scribed with the chamfer mill here: mark "
                                                  "them by hand"))
        return
    groups, counts = _scribe_groups(adapter, job, sheet, parts, "chamfer")
    if not groups:
        return
    name = scribe_program_name(sheet.name)
    tool = job.tooling.tools[sheet.tool_key]
    setup = False
    try:
        adapter.make_extra_setup(sheet.name, name, [p.body_id for _, p in sheet.instances if p.part_key in counts])
        setup = True
        ops = adapter.apply_template(name, tool.template_path)
        outer = [o.name for o in ops if _op_tag(o.name) == OUTER]
        if len(outer) != 1:
            raise AdapterError(f"the sheet template has {len(outer)} [outer] ops")
        for o in ops:
            if o.name != outer[0]:
                adapter.delete_op(name, o.name)
        made = []
        for faced in sorted(groups):
            adapter.copy_op(name, outer[0], scribe_op_name(faced))
            made.append(faced)
        adapter.delete_op(name, outer[0])
        for faced in made:
            op = scribe_op_name(faced)
            adapter.set_tool(name, op, s.chamfer_tool_json)
            adapter.fill(name, op, OpFill(SCRIBE, lines=tuple(groups[faced]),
                                          depth_in=_scribe_depth(job, s.chamfer_depth_in, faced),
                                          stock_in=-max(tip_radius_in(s.chamfer_tool_json) - SCRIBE_EPS_IN, 0.0)))
    except AdapterError as e:
        sheet.warnings.append(Issue(E.OP_WARNING, f"no scribe program ({e}): mark the bend lines by hand on "
                                                  f"{', '.join(parts[k].spec.name for k in counts)}"))
        try:                                      # Fusion generates every setup: leave nothing half made in it
            for op in adapter.op_names(name) if setup else []:
                _suppress(adapter, sheet, name, op)
        except AdapterError as e2:
            sheet.errors.append(Issue(E.OP_ERROR, f"the scribe setup {name} couldn't be emptied: {e2}"))
        return
    sheet.scribe_name = name
    sheet.scribe_lines = tuple(ln for faced in made for ln in groups[faced])
    sheet.scribe_counts = counts


# ------------------------------------------------------------------ 6. post and check

def _labels(adapter: Adapter, sheet: _Sheet, parts: Dict[str, _Part], rect: Rect) -> Dict[str, Any]:
    """Where each part's label goes in the preview (the service draws them, labels.py): its cut-order number,
    name and copy ("2/3"), at the point on its material furthest from any edge (or the middle of its outline when
    the material is too thin for a badge), in pixels, with that clearance in pixels (how big a label fits)."""
    size = preview_size(rect)
    scale = pixels_per_unit(rect, size)
    by_instance = dict(sheet.instances)
    totals = sheet.counts()
    out = []
    for n, inst in enumerate(sheet.outer_order, 1):
        placed = by_instance[inst]
        part = parts[placed.part_key]
        outer = flatten(adapter.loop_segments(placed.body_id, outline_face(part), None))
        holes = [flatten(adapter.loop_segments(placed.body_id, f, i))
                 for f in (part.analysis.top_face_ids or (part.analysis.up_face_id,))
                 for i in range(len(part.analysis.geometry.face(f).inner_loops))]
        x, y, room = label_spot(outer, holes, min_room=BADGE_MIN_PX / scale)
        px, py = to_pixels(x, y, rect, size)
        copy = inst.rsplit("-", 1)[1]
        out.append({"n": n, "name": part.spec.name, "copy": f"{copy}/{totals[placed.part_key]}"
                    if totals[placed.part_key] > 1 else "", "x": round(px, 1), "y": round(py, 1),
                    "room": round(room * scale, 1)})
    return {"image": list(size), "sheet": sheet.name, "labels": out}


def _post_sheet(adapter: Adapter, job: Job, sheet: _Sheet, out_dir: Path) -> None:
    raw_dir = out_dir / "raw" / sheet.name
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True)
    if sheet.tab_points:                              # where its tabs were asked for (sheet X/Y), for checking
        write_atomic(out_dir / f"{sheet.name}.tabs.json",
                     (json.dumps(sheet.tab_points, indent=1) + "\n").encode("utf-8"))
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
    check = check_sheet_program(data, job, sheet.thickness_in, sheet.tool_key, sheet.outer_order, counts,
                                sheet.by_hand)
    if check.passed:
        # The tool center must never be inside a part: catches a cutout or outline cut on the wrong side. Scribing
        # is meant to be inside its parts: it must be on their bend lines instead.
        sections = scribe_sections(text)
        points = cutting_points(text, sheet.thickness_in)
        scribing = [p for p in points if in_sections(p[0], sections)]
        if sections and not sheet.scribe_ops:
            sheet.errors.append(Issue(E.TAP_REJECTED, "the program scribes, but no bend lines were asked for"))
        ox, oy = sheet.origin
        all_lines = [ln for lines in sheet.scribe_ops.values() for ln in lines]
        off = off_the_lines([(n, x + ox, y + oy, z) for n, x, y, z in scribing], all_lines)
        if scribing and all_lines:
            far = max(min(distance_to_line(x + ox, y + oy, ln) for ln in all_lines) for _, x, y, _ in scribing)
            sheet.notes.append(f"scribing: {len(scribing)} points, all within {far:.4f} in of the bend lines")
        if off:
            where = "; ".join(f"line {n} (X{x - ox:.4f} Y{y - oy:.4f})" for n, x, y in off[:5])
            sheet.errors.append(Issue(E.TAP_REJECTED, f"the scribing leaves the bend lines: {where}"))
        try:
            hits = adapter.cuts_into_parts(sheet.name, [p for p in points if not in_sections(p[0], sections)])
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


def _face_counts(sheet: _Sheet, parts: Dict[str, _Part]) -> Dict[str, int]:
    return dict(Counter(p.part_key for _, p in sheet.instances if parts[p.part_key].spec.face))


def _post_face(adapter: Adapter, job: Job, sheet: _Sheet, parts: Dict[str, _Part], out_dir: Path) -> None:
    """Post and check the sheet's FACE program: the check the service repeats (no deeper than the face depth, no
    stops, no outlines), and every point where the cutter is below the stock top must be over a faced copy (inside
    its outline): the skim never reaches a part that didn't ask for it, or the clamps. A failure stops the sheet."""
    name = sheet.face_name
    raw_dir = out_dir / "raw" / name
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True)
    try:
        posted = adapter.post(name, name, raw_dir, job.post.path, job.post.properties)
    except AdapterError as e:
        sheet.errors.append(Issue(E.POST_FAILED, f"posting the face program failed: {e}"))
        return
    data = posted.read_bytes()
    text = data.decode("ascii", errors="surrogateescape")
    counts = _face_counts(sheet, parts)
    check = check_face_program(data, job, sheet.thickness_in, counts, sheet.by_hand)
    problems = check.problems()
    if check.passed:
        try:
            outlines = [flatten(adapter.loop_segments(cid, fid, None)) for cid, fid in sheet.face_tops]
            ox, oy = sheet.origin
            off = [(n, x, y) for n, x, y, _ in cutting_points(text, sheet.thickness_in)
                   if not any(inside_polygon(x + ox, y + oy, poly) for poly in outlines)]
        except AdapterError as e:
            off = []
            problems.append(f"couldn't check the face program against the parts' outlines: {e}")
        if off:
            where = "; ".join(f"line {n} (X{x:.4f} Y{y:.4f})" for n, x, y in off[:5])
            problems.append(f"the face program's cutter leaves the faced parts: {where}")
    ok = check.passed and not problems
    tap, rejected = (f"{name}.tap", None) if ok else (None, f"{name}.REJECTED.tap")
    write_atomic(out_dir / (tap or rejected), data)
    try:
        machining = adapter.machining_time(name)
    except AdapterError as e:
        machining = None
        sheet.notes.append(f"face program: machining time unavailable: {e}")
    g = check.guard
    tool = job.face.tool
    sheet.face_result = ProgramResult(
        kind="face", name=name, runs="before", tool_guid=tool.guid, cutter_label=tool.cutter_label,
        tool_number=tool.number, tap=tap, tap_rejected=rejected, tap_bytes=len(data),
        tap_sha256=g.sha256 if tap else None,
        guard=GuardSummary(g.passed, g.sha256, g.floor_in, g.min_z_in, g.units, g.offenders, g.clamp_violations,
                           g.problems),
        machining_time_s=machining, parts=tuple(SheetPart(k, n) for k, n in sorted(counts.items())),
        depth_in=job.face.depth_in)
    if not ok:
        sheet.errors += [Issue(E.TAP_REJECTED, f"face program: {msg}") for msg in problems]


def _drop_scribe(sheet: _Sheet, parts: Dict[str, _Part], why: str) -> None:
    names = ", ".join(parts[k].spec.name for k in sheet.scribe_counts if k in parts)
    sheet.warnings.append(Issue(E.OP_WARNING, f"no scribe program ({why}): mark the bend lines by hand on {names}"))
    sheet.scribe_counts = {}


def _post_scribe(adapter: Adapter, job: Job, sheet: _Sheet, parts: Dict[str, _Part], out_dir: Path) -> None:
    """Post and check the sheet's SCRIBE program: the check the service repeats (the scribing depth below the
    parts' tops, no deeper, no stops, no outlines), and every point where the cutter is below the stock top must be
    on one of the bend lines. A failure only drops the program (its parts are marked by hand); a failed program is
    kept as REJECTED for a look, never offered."""
    name = sheet.scribe_name
    raw_dir = out_dir / "raw" / name
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True)
    try:
        posted = adapter.post(name, name, raw_dir, job.post.path, job.post.properties)
    except AdapterError as e:
        _drop_scribe(sheet, parts, f"posting it failed: {e}")
        return
    data = posted.read_bytes()
    text = data.decode("ascii", errors="surrogateescape")
    check = check_scribe_program(data, job, sheet.thickness_in, sheet.scribe_counts, sheet.by_hand)
    problems = check.problems()
    if check.passed:
        ox, oy = sheet.origin
        pts = [(n, x + ox, y + oy, z) for n, x, y, z in cutting_points(text, sheet.thickness_in)]
        off = off_the_lines(pts, sheet.scribe_lines)
        if pts and sheet.scribe_lines:
            far = max(min(distance_to_line(x, y, ln) for ln in sheet.scribe_lines) for _, x, y, _ in pts)
            sheet.notes.append(f"scribe program: {len(pts)} points, all within {far:.4f} in of the bend lines")
        if off:
            problems.append("its cutter leaves the bend lines: "
                            + "; ".join(f"line {n} (X{x - ox:.4f} Y{y - oy:.4f})" for n, x, y in off[:5]))
    if not check.passed or problems:
        write_atomic(out_dir / f"{name}.REJECTED.tap", data)
        _drop_scribe(sheet, parts, "; ".join(problems))
        return
    write_atomic(out_dir / f"{name}.tap", data)
    try:
        machining = adapter.machining_time(name)
    except AdapterError as e:
        machining = None
        sheet.notes.append(f"scribe program: machining time unavailable: {e}")
    g = check.guard
    tool = job.scribe.chamfer
    sheet.scribe_result = ProgramResult(
        kind="scribe", name=name, runs="before", tool_guid=tool.guid, cutter_label=tool.cutter_label,
        tool_number=tool.number, tap=f"{name}.tap", tap_rejected=None, tap_bytes=len(data), tap_sha256=g.sha256,
        guard=GuardSummary(g.passed, g.sha256, g.floor_in, g.min_z_in, g.units, g.offenders, g.clamp_violations,
                           g.problems),
        machining_time_s=machining, parts=tuple(SheetPart(k, n) for k, n in sorted(sheet.scribe_counts.items())),
        depth_in=job.scribe.chamfer_depth_in)


def _post_fits(adapter: Adapter, job: Job, sheet: _Sheet, out_dir: Path) -> None:
    """Post and check every fit lap: the check the service repeats (check_fit_program), and the tool center never
    inside a part, as for the sheet's program. One that fails stops the sheet."""
    tool = job.tooling.tools[sheet.tool_key]
    for fit in sheet.fits:
        outer = tuple(inst for inst, _, _ in fit.instances)
        counts = {fit.part_key: len({cid for cid, _ in fit.holes} | {cid for cid, _, _ in fit.loops}
                                    | {cid for _, cid, _ in fit.instances})}
        for lap, (name, stock) in enumerate(zip(fit.laps, fit.stocks[1:]), 1):
            raw_dir = out_dir / "raw" / name
            if raw_dir.exists():
                shutil.rmtree(raw_dir)
            raw_dir.mkdir(parents=True)
            try:
                posted = adapter.post(name, name, raw_dir, job.post.path, job.post.properties)
            except AdapterError as e:
                sheet.errors.append(Issue(E.POST_FAILED, f"posting {name} failed: {e}"))
                return
            data = posted.read_bytes()
            text = data.decode("ascii", errors="surrogateescape")
            check = check_fit_program(data, job, sheet.thickness_in, sheet.tool_key, outer, counts, sheet.by_hand)
            problems = check.problems()
            if check.passed:
                try:
                    hits = adapter.cuts_into_parts(name, cutting_points(text, sheet.thickness_in))
                except AdapterError as e:
                    hits = []
                    problems.append(f"couldn't check the toolpaths against the parts: {e}")
                if hits:
                    where = "; ".join(f"line {n} (X{x:.4f} Y{y:.4f})" for n, x, y in hits[:5])
                    problems.append(f"the tool cuts into a part: {where}")
            ok = check.passed and not problems
            tap, rejected = (f"{name}.tap", None) if ok else (None, f"{name}.REJECTED.tap")
            write_atomic(out_dir / (tap or rejected), data)
            try:
                machining = adapter.machining_time(name)
            except AdapterError:
                machining = None
            g = check.guard
            fit.results.append(ProgramResult(
                kind="fit", name=name, runs="after", tool_guid=tool.guid, cutter_label=tool.cutter_label,
                tool_number=tool.number, tap=tap, tap_rejected=rejected, tap_bytes=len(data),
                tap_sha256=g.sha256 if tap else None,
                guard=GuardSummary(g.passed, g.sha256, g.floor_in, g.min_z_in, g.units, g.offenders,
                                   g.clamp_violations, g.problems),
                machining_time_s=machining, parts=tuple(SheetPart(k, n) for k, n in counts.items()),
                stock_in=stock, series=fit.series, lap=lap, feature=fit.feature, cutout=bool(fit.loops)))
            if not ok:
                sheet.errors += [Issue(E.TAP_REJECTED, f"{name}: {msg}") for msg in problems]
                return


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
    live = getattr(adapter, "trace", None)
    if hasattr(live, "path"):                         # written as it goes: a crash leaves how far it got
        live.path = out_dir / "fusion_calls.log"
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
                trace = getattr(adapter, "trace", None)
                if trace:                             # every Fusion call, timed (fx_adapter)
                    (out_dir / "fusion_calls.log").write_text("\n".join(trace) + "\n", encoding="utf-8")
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
            for n in range(2, 1 + (1 if part.spec.by_hand else part.spec.qty)):   # placed by hand: one copy, run
                cid = f"{part.key}.{n}"                                          # qty times
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
            if not p.spec.by_hand:
                groups.setdefault(p.analysis.stock_thickness_in, []).append(p)
        for thickness in sorted(groups):
            made, slot = _nest_group(adapter, job, thickness, groups[thickness], x_start, slot, log, notes)
            sheets += made
        for p in [p for p in good if p.spec.by_hand]:
            made, slot = _by_hand_sheet(adapter, job, p, x_start, slot, log)
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
            states = adapter.generate([s.name for s in built] + [s.face_name for s in built if s.face_name]
                                      + [s.scribe_name for s in built if s.scribe_counts]
                                      + [lap for s in built for f in s.fits for lap in f.laps])
        except AdapterError as e:
            for s in built:
                s.errors.append(Issue(E.OP_ERROR, f"toolpath generation failed: {e}"))
    for sheet in built:
        for st in states.get(sheet.name, []):         # scribing that won't generate: taken out, marked by hand
            if st.name in sheet.scribe_ops and (st.error or not st.has_toolpath):
                _suppress(adapter, sheet, sheet.name, st.name)
                if st.name not in sheet.suppressed:
                    continue
                keys = [k for k in sheet.scribed if bool(parts[k].spec.face) == st.name.endswith(" faced")]
                sheet.warnings.append(Issue(E.OP_WARNING, f"bend lines not scribed ({st.error or 'no toolpath'}): "
                                                          f"mark them by hand on "
                                                          f"{', '.join(parts[k].spec.name for k in keys)}"))
                sheet.scribe_ops.pop(st.name)
                for k in keys:
                    sheet.scribed.pop(k, None)
        states[sheet.name] = [st for st in states.get(sheet.name, []) if st.name not in sheet.suppressed]
        if sheet.scribe_counts:
            bad = [st for st in states.get(sheet.scribe_name, []) if st.error or not st.has_toolpath]
            if sheet.scribe_name not in states or bad:
                _drop_scribe(sheet, parts, "; ".join(f"{st.name}: {st.error or 'no toolpath'}" for st in bad)
                             or "Fusion reported no toolpaths for it")
        for setup in [sheet.name] + ([sheet.face_name] if sheet.face_name else []) + \
                [lap for f in sheet.fits for lap in f.laps]:
            if setup not in states and not sheet.errors:
                sheet.errors.append(Issue(E.OP_ERROR, f"Fusion reported no toolpaths for {setup}"))
            for st in states.get(setup, []):
                if st.error:
                    sheet.errors.append(Issue(E.OP_ERROR, f"{st.name}: {st.error}"))
                elif not st.has_toolpath:
                    sheet.errors.append(Issue(E.OP_ERROR, f"{st.name}: no toolpath"))
                elif st.warning:
                    sheet.warnings.append(Issue(E.OP_WARNING, f"{st.name}: {st.warning}"))
        if not sheet.errors and sheet.face_name:
            _post_face(adapter, job, sheet, parts, out_dir)       # first: the sheet isn't offered without it
        if not sheet.errors and sheet.scribe_counts:
            _post_scribe(adapter, job, sheet, parts, out_dir)     # never holds the sheet up
        if not sheet.errors and sheet.fits:
            _post_fits(adapter, job, sheet, out_dir)              # before it too, for the same reason
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
            if sheet.by_hand:                         # the part's box and a little round it
                w, h = sheet.size
                ox, oy = sheet.origin
                rect = (ox - 0.5, oy - 0.5, ox + w + 0.5, oy + h + 0.5)
            else:
                rect = (sheet.origin[0], sheet.origin[1], sheet.origin[0] + job.sheet.width_in,
                        sheet.origin[1] + job.sheet.length_in)
            try:
                adapter.preview(sheet.name, rect, out_dir / png)
                preview = png
            except AdapterError as e:
                sheet.notes.append(f"no preview: {e}")
            if preview:
                try:
                    labels = _labels(adapter, sheet, parts, rect)
                    write_atomic(out_dir / f"{sheet.name}.labels.json",
                                 (json.dumps(labels, indent=1) + "\n").encode("utf-8"))
                except (AdapterError, KeyError, IndexError, ValueError) as e:
                    sheet.notes.append(f"no labels on the preview: {e}")
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
            errors=_dedupe(sheet.errors), warnings=_dedupe(sheet.warnings), notes=tuple(sheet.notes),
            by_hand=sheet.by_hand, repeat=sheet.repeat,
            programs=((sheet.face_result,) if sheet.face_result else ())
            + ((sheet.scribe_result,) if sheet.scribe_result else ())
            + tuple(r for f in sheet.fits for r in f.results),
            scribed=tuple(SheetPart(k, n) for k, n in sorted(sheet.scribed.items())) if sheet.result else (),
            **({} if sheet.by_hand else _sheet_use(job, sheet, parts))))

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
        placed = sum(s.counts().get(part.key, 0) * s.repeat for s in sheets) if on else 0
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
