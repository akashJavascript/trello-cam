"""job.json: everything the Fusion worker needs for one batch, self-contained (inches).

The service writes one job per material (+ color) per run. Fusion measures each part's
thickness and nests each stock thickness separately (docs/decisions.md), so `material`
lists every stock thickness for that material.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .holes import HoleRules
from .names import is_safe_name
from .schema import dumps, from_dict

JOB_SCHEMA = "autocam.job/1"
FAMILIES = ("aluminum", "polycarbonate")
PAUSE_MODES = ("tap_text", "manual_nc")
SOURCES = ("onshape", "trello_attachment", "local")

Rect = Tuple[float, float, float, float]
MAX_FACE_DEPTH_IN = 0.02          # facing skims a top clean; anything deeper is a pocket


@dataclass(frozen=True)
class MaterialSpec:
    key: str
    name: str
    family: str
    color: str
    thicknesses_in: Tuple[float, ...]
    thickness_tol_in: float
    use_mist: bool
    program_prefix: str


@dataclass(frozen=True)
class SheetSpec:
    length_in: float
    width_in: float
    reach_in: float            # along the sheet's length (machine Y); the rest overhangs the back of the bed


@dataclass(frozen=True)
class FixtureSpec:
    nest_region_in: Rect
    clamp_zones_in: Tuple[Rect, ...]
    clamp_height_in: float
    min_clear_above_stock_in: float


@dataclass(frozen=True)
class NestSpec:
    part_spacing_in: float
    max_sheets_per_group: int
    rotation: str
    part_in_part: bool
    envelope_spacing_in: float
    short_qty: str
    offcut_gap_in: float = 0.5        # how far a new nest stays from the stretches of an offcut already cut
    offcut_min_in: float = 6.0        # a free stretch shorter than this isn't worth loading the offcut for
    offcut_beside_min_in: float = 3.0   # room beside earlier cuts is kept and used when both its sides are this long


@dataclass(frozen=True)
class ToolSpec:
    key: str
    guid: str
    number: int
    diameter_in: float
    flute_in: float
    min_inside_radius_in: float
    drill_sizes_in: Tuple[float, ...]
    cutter_label: str
    template_key: str
    template_path: str


@dataclass(frozen=True)
class ToolingSpec:
    default: str
    small_features: Optional[str]   # None: no small-feature tool + template for this family yet
    tools: Dict[str, ToolSpec]


@dataclass(frozen=True)
class PlateSpec:
    strict_inside_radius: bool


@dataclass(frozen=True)
class PauseSettings:
    enabled: bool
    after_last_part: bool
    mode: str
    park: str
    spindle_rpm: int
    dwell_s: float


@dataclass(frozen=True)
class PostSpec:
    description: str
    path: str
    sha256: str
    properties: Dict[str, Any]      # every post property, including useMist for this material
    units: str


@dataclass(frozen=True)
class GuardSettings:
    z_floor_in: float
    allowed_g: Tuple[int, ...]
    allowed_m: Tuple[int, ...]
    clamp_margin_in: float


@dataclass(frozen=True)
class FusionTeamSpec:
    project: str
    folder: str


@dataclass(frozen=True)
class OnshapeRef:
    did: str
    vid: str                           # version id, or the workspace id for workspace links
    eid: str
    part_id: str
    url: str                           # the link on the card
    microversion: Optional[str] = None  # workspace links: the exact state that was exported


FIT_KINDS = ("hole", "outline")
MAX_FIT_STOCK_IN = 0.02


@dataclass(frozen=True)
class FitSpec:
    """A feature cut tight and then fitted at the machine (core 0.16.0): the part's holes of `diameter_in` (bored
    ones) or its outline. The sheet's program leaves stocks_in[0] of radial stock on it (+: a hole smaller, the
    outline bigger); each lap program after it leaves the next value, a little less each time."""
    kind: str                                          # "hole" | "outline"
    stocks_in: Tuple[float, ...]
    diameter_in: float = 0.0                           # hole fits


@dataclass(frozen=True)
class BendSpec:
    """One bend of a sheet metal part, from its bend table: cut flat, bent after (core 0.17.0)."""
    name: str
    angle_deg: float
    direction: str                                     # "up" or "down", as Onshape says it
    radius_in: float = 0.0
    order: int = 0                                     # Onshape's bend sequence (0: not given)


@dataclass(frozen=True)
class PartSpec:
    part_key: str
    card_id: str
    card_url: str
    name: str
    qty: int
    step: str
    step_sha256: str
    source: str
    onshape: Optional[OnshapeRef]
    force_small_tool: bool
    tabs: bool = False                                 # hold it in with tabs (core 0.7.0)
    by_hand: bool = False                              # not nested: one copy alone, placed by hand (core 0.12.0)
    face: bool = False                                 # its top skimmed clean first, in the sheet's FACE program
                                                       # (core 0.14.0)
    fit: Optional[FitSpec] = None                      # a hole size or the outline fitted at the machine (0.16.0)
    bends: Tuple[BendSpec, ...] = ()                   # a sheet metal part: its STEP is the flat pattern (0.17.0)


@dataclass(frozen=True)
class TabSpec:
    """Tabs on the outlines and cutouts of parts that ask for them: the template's tabs (shape, width, height)
    turned on, a count per contour from its length (tabs.py; core 0.7.0, counts 0.8.0)."""
    distance_in: float = 2.5
    min_per_contour: int = 2
    max_per_contour: int = 6
    at_points: bool = True                             # placed by tabs.place_tabs, else spread evenly by Fusion
    width_in: float = 0.0                              # set on each tabbed op; 0: the template's (core 0.11.0)
    height_in: float = 0.0                             # the shape is always the template's


@dataclass(frozen=True)
class FaceSpec:
    """Parts with face=True get their tops skimmed `depth_in` deep before the sheet is cut: a program of its own
    (the sheet's FACE program, run first) with the face tool, over those parts only (core 0.14.0)."""
    depth_in: float
    tool: ToolSpec                                     # its template has one [face] op, with this tool


@dataclass(frozen=True)
class OffcutSpec:
    """A partly used sheet this job may nest onto before starting new sheets (offcuts.py)."""
    id: str                                            # the offcut card's id
    thickness_in: float
    used_in: Tuple[Tuple[float, float], ...]           # used stretches along it, in its own coordinates
    last_turned: bool = False                          # which way round it was loaded for its last cut
    beside_in: Tuple[Rect, ...] = ()                   # room beside earlier cuts, in its own coordinates (core 0.5.0)
    can_turn: bool = True                              # False: a piece shorter than a sheet, always loaded at the
                                                       # front the same way round (core 0.6.0)


@dataclass(frozen=True)
class Job:
    schema: str
    core_version: str
    job_id: str
    run_id: str
    created_utc: str
    material: MaterialSpec
    sheet: SheetSpec
    fixture: FixtureSpec
    nest: NestSpec
    tooling: ToolingSpec
    holes: HoleRules
    plate: PlateSpec
    pauses: PauseSettings
    post: PostSpec
    guard: GuardSettings
    fusion_params: Dict[str, Any]
    fusion_team: FusionTeamSpec
    parts: Tuple[PartSpec, ...]
    offcuts: Tuple[OffcutSpec, ...] = ()
    tabs: TabSpec = TabSpec()
    face: Optional[FaceSpec] = None

    def validate(self) -> List[str]:
        e: List[str] = []
        if self.schema != JOB_SCHEMA:
            e.append(f"schema: expected {JOB_SCHEMA}, got {self.schema!r}")
        for name in ("job_id", "run_id"):
            if not is_safe_name(getattr(self, name)):
                e.append(f"{name}: use letters, digits, '-' and '_' only")
        if not self.core_version:
            e.append("core_version: missing")
        m = self.material
        if m.family not in FAMILIES:
            e.append(f"material.family: {m.family!r} is not one of {', '.join(FAMILIES)}")
        if not is_safe_name(m.program_prefix):
            e.append("material.program_prefix: use letters, digits, '-' and '_' only")
        if not m.thicknesses_in:
            e.append("material.thicknesses_in: empty")
        t = self.tooling
        if t.default not in t.tools:
            e.append(f"tooling.default: no tool {t.default!r}")
        if t.small_features is not None and t.small_features not in t.tools:
            e.append(f"tooling.small_features: no tool {t.small_features!r}")
        for key, tool in t.tools.items():
            if tool.key != key:
                e.append(f"tooling.tools.{key}: key says {tool.key!r}")
        if self.guard.z_floor_in < 0:
            e.append("guard.z_floor_in: must be >= 0")
        if self.pauses.mode not in PAUSE_MODES:
            e.append(f"pauses.mode: {self.pauses.mode!r} is not one of {', '.join(PAUSE_MODES)}")
        if self.post.units != "in":
            e.append("post.units: only inch programs are supported")
        keys = [p.part_key for p in self.parts]
        if len(set(keys)) != len(keys):
            e.append("parts: duplicate part_key")
        ids = [o.id for o in self.offcuts]
        if len(set(ids)) != len(ids):
            e.append("offcuts: duplicate id")
        for o in self.offcuts:
            if any(not 0 <= a < b <= self.sheet.length_in for a, b in o.used_in):
                e.append(f"offcuts.{o.id}: used stretches must be inside the sheet")
            if any(not (0 <= x0 < x1 <= self.sheet.width_in and 0 <= y0 < y1 <= self.sheet.length_in)
                   for x0, y0, x1, y1 in o.beside_in):
                e.append(f"offcuts.{o.id}: room beside cuts must be inside the sheet")
        if self.tabs.distance_in <= 0:
            e.append("tabs.distance_in: must be > 0")
        if self.tabs.width_in < 0 or self.tabs.height_in < 0:
            e.append("tabs: width_in and height_in can't be negative")
        if self.tabs.height_in and self.tabs.height_in >= min(self.material.thicknesses_in):
            e.append("tabs.height_in: must be less than the stock thickness")
        if not 1 <= self.tabs.min_per_contour <= self.tabs.max_per_contour:
            e.append("tabs: need 1 <= min_per_contour <= max_per_contour")
        if self.face is not None:
            f = self.face
            if not 0 < f.depth_in <= MAX_FACE_DEPTH_IN:
                e.append(f"face.depth_in: must be more than 0 and at most {MAX_FACE_DEPTH_IN} in (a skim, not a cut)")
            if f.depth_in >= min(self.material.thicknesses_in, default=0.0):
                e.append("face.depth_in: must be less than the stock thickness")
            if f.tool.key in t.tools:
                e.append(f"face.tool: {f.tool.key} is one of the sheet tools")
        if any(p.face for p in self.parts) and self.face is None:
            e.append("parts ask for facing but the job has no face settings")
        if not self.parts:
            e.append("parts: empty")
        for p in self.parts:
            where = f"parts.{p.part_key}"
            if not is_safe_name(p.part_key):
                e.append(f"{where}: part_key must be letters, digits, '-' or '_'")
            if p.qty < 1:
                e.append(f"{where}.qty: must be >= 1")
            if p.source not in SOURCES:
                e.append(f"{where}.source: {p.source!r} is not one of {', '.join(SOURCES)}")
            if (p.source == "onshape") != (p.onshape is not None):
                e.append(f"{where}.onshape: required exactly when source is onshape")
            f = p.fit
            if f is not None:
                if f.kind not in FIT_KINDS:
                    e.append(f"{where}.fit.kind: {f.kind!r} is not one of {', '.join(FIT_KINDS)}")
                if f.kind == "hole" and f.diameter_in <= 0:
                    e.append(f"{where}.fit.diameter_in: a hole fit needs the hole's diameter")
                if len(f.stocks_in) < 2 or any(abs(s) > MAX_FIT_STOCK_IN for s in f.stocks_in) \
                        or any(b >= a for a, b in zip(f.stocks_in, f.stocks_in[1:])):
                    e.append(f"{where}.fit.stocks_in: at least two values, each at most {MAX_FIT_STOCK_IN} in, "
                             "each less than the one before")
                if not p.tabs:
                    e.append(f"{where}.fit: a fitted part is always held by tabs (its laps run after it's cut out)")
                if p.by_hand:
                    e.append(f"{where}.fit: parts placed by hand aren't fitted yet")
        return e

    def part(self, part_key: str) -> PartSpec:
        for p in self.parts:
            if p.part_key == part_key:
                return p
        raise KeyError(part_key)


def load_job(data: Dict[str, Any]) -> Job:
    return from_dict(Job, data)


def read_job(path: Path) -> Job:
    return load_job(json.loads(Path(path).read_text(encoding="utf-8")))


def job_json(job: Job) -> str:
    return dumps(job)
