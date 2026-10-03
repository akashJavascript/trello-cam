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
    reach_x_in: float


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


@dataclass(frozen=True)
class OffcutSpec:
    """A partly used sheet this job may nest onto before starting new sheets (offcuts.py)."""
    id: str                                            # the offcut card's id
    thickness_in: float
    used_in: Tuple[Tuple[float, float], ...]           # used stretches along it, in its own coordinates
    last_turned: bool = False                          # which way round it was loaded for its last cut


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
