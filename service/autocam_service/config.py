"""Load and validate config/autocam.toml.

Strict on purpose: unknown keys, wrong types, and unsafe values are errors, and all of
them are reported at once. Lengths are inches. The Fusion add-in never reads this file;
the service copies what a job needs into job.json.
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple

if sys.version_info >= (3, 11):
    import tomllib
else:  # WSL dev boxes still ship Python 3.10
    import tomli as tomllib

from autocam_core.tapguard import SAFE_G, SAFE_M
from autocam_core.toollib import ToolLibrary, ToolLibraryError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "autocam.toml"

SCHEMA = 1
FAMILIES = ("aluminum", "polycarbonate")
CLAMP_EDGES = ("front", "back", "zero_end")
OP_TAGS = ("drill", "bore", "bearing", "pocket", "inner", "outer")
TRELLO_LISTS = ("inbox", "ready_for_cam", "needs_fixing", "nested", "sheet_review",
                "ready_to_cut", "cut", "control", "run_nest")
TRELLO_TARGETS = ("part_nested", "part_rejected", "part_deferred", "part_cut", "sheet_created",
                  "checklist_return", "control_return")
TRELLO_CARDS = ("run_nest_control", "system")
NEVER_AUTOMATED = "ready_to_cut"
TRELLO_FREE_ATTACHMENT_MB = 10
# The guard only understands autocam_core.tapguard.SAFE_G / SAFE_M; allowlists must stay inside them.
REQUIRED_G = frozenset({0, 1, 20, 53, 90})
REQUIRED_M = frozenset({0, 3, 5})
MIST_M = frozenset({11, 12})
# Post properties whose value the automation depends on (see docs/PLAN.md, "Post behavior").
FIXED_POST_PROPERTIES = {"safePositionMethod": "G53", "useToolCall": False, "useCoolant": False}

GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TRELLO_ID_RE = re.compile(r"^[0-9a-f]{24}$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
BASE_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+$")
MONTH_DAY_RE = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")
_POST_PROP_RE = re.compile(r"^  (\w+)\s*:\s*\{", re.MULTILINE)
_POST_EXTRA_PROP_RE = re.compile(r"^properties\.(\w+)\s*=", re.MULTILINE)


class ConfigError(Exception):
    def __init__(self, errors: List[str]):
        self.errors = list(errors)
        super().__init__("invalid config:\n" + "\n".join(f"  - {e}" for e in self.errors))


# ----------------------------------------------------------------- model

@dataclass(frozen=True)
class Machine:
    reach_x_in: float
    z_floor_in: float
    units: str
    spindle_rpm: int
    spin_up_dwell_s: float
    park: str
    z_touchoff: str


@dataclass(frozen=True)
class Sheet:
    length_in: float
    width_in: float


@dataclass(frozen=True)
class Nest:
    edge_margin_in: float
    reach_margin_in: float
    part_spacing_in: float
    max_sheets_per_group: int
    rotation: str
    part_in_part: bool
    envelope_spacing_in: float
    short_qty: str


@dataclass(frozen=True)
class Clamps:
    edges: Tuple[str, ...]
    reach_in: float
    clearance_in: float
    height_in: float
    min_clear_above_stock_in: float


@dataclass(frozen=True)
class Pauses:
    enabled: bool
    after_last_part: bool
    mode: str
    resume_key: str


@dataclass(frozen=True)
class Plate:
    thickness_tol_in: float
    strict_inside_radius: bool


@dataclass(frozen=True)
class Material:
    key: str
    name: str
    family: str
    color: str
    thicknesses_in: Tuple[float, ...]
    use_mist: bool
    program_prefix: str


@dataclass(frozen=True)
class Tool:
    key: str
    guid: str              # "" = not in the library yet
    number: int
    diameter_in: float
    flute_in: float
    min_inside_radius_in: float
    family: str
    cutter_label: str

    @property
    def available(self) -> bool:
        return bool(self.guid)


@dataclass(frozen=True)
class FamilyTooling:
    default: str
    small_features: str


@dataclass(frozen=True)
class Holes:
    drill_tol_in: float
    bore_min_in: float
    bore_min_tol_in: float
    bore_max_in: float
    bearing_sizes_in: Tuple[float, ...]
    bearing_tol_in: float
    drill_sizes_in: Mapping[str, Tuple[float, ...]]


@dataclass(frozen=True)
class Template:
    key: str
    family: str
    tool: str
    file: Path


@dataclass(frozen=True)
class Labels:
    smoked: str
    tool_eighth: str


@dataclass(frozen=True)
class Onshape:
    base_url: str
    calls_per_part_estimate: int
    per_run_max_calls: int
    monthly_soft_calls: int
    yearly_cap_calls: int
    budget_year_start: str
    poll_first_s: float
    poll_factor: float
    poll_max_s: float
    poll_max_count: int
    retry_after_max_wait_s: float
    material_map: Mapping[str, str]


@dataclass(frozen=True)
class Trello:
    board_id: str
    poll_interval_s: int
    attachment_limit_mb: float
    job_timeout_s: int
    checklist_name: str
    checklist: Tuple[str, ...]
    machine_checklist_name: str
    machine_checklist: Tuple[str, ...]
    lists: Mapping[str, str]
    targets: Mapping[str, str]
    cards: Mapping[str, str]


@dataclass(frozen=True)
class Fusion:
    post_description: str
    post_file: Path
    post_sha256: str
    tool_library: Path
    job_timeout_s: int
    max_attempts: int
    post_properties: Mapping[str, Any]
    params: Mapping[str, Any]      # undocumented Fusion parameter names; passed through to jobs


@dataclass(frozen=True)
class TapGuard:
    allowed_g: Tuple[int, ...]
    allowed_m: Tuple[int, ...]
    clamp_margin_in: float


@dataclass(frozen=True)
class FusionTeam:
    project: str
    folder: str


@dataclass(frozen=True)
class Paths:
    queue: Path
    cache: Path
    state: Path
    logs: Path


@dataclass(frozen=True)
class Config:
    path: Optional[Path]
    root: Path
    assumed: Mapping[str, str]
    machine: Machine
    sheet: Sheet
    nest: Nest
    clamps: Clamps
    pauses: Pauses
    plate: Plate
    materials: Mapping[str, Material]
    tools: Mapping[str, Tool]
    tooling: Mapping[str, FamilyTooling]
    holes: Holes
    op_tags: Tuple[str, ...]
    templates: Mapping[str, Template]
    labels: Labels
    onshape: Onshape
    trello: Trello
    fusion: Fusion
    tapguard: TapGuard
    fusion_team: FusionTeam
    paths: Paths
    placeholders: Tuple[str, ...]  # dotted keys still set to ""
    warnings: Tuple[str, ...]

    def stock_types(self) -> List[Tuple[Material, float]]:
        return [(m, t) for m in self.materials.values() for t in m.thicknesses_in]


# ---------------------------------------------------------------- reader

_MISSING = object()


class _Table:
    """One TOML table. Records type errors and unknown keys instead of stopping at the first."""

    def __init__(self, data: Any, path: str, errors: List[str]):
        self.path = path
        self.errors = errors
        if not isinstance(data, dict):
            errors.append(f"{path}: expected a table")
            data = {}
        self.data = data
        self._seen = set()

    def where(self, key: str) -> str:
        return f"{self.path}.{key}" if self.path else key

    def _get(self, key: str, expected: str, default: Any) -> Any:
        self._seen.add(key)
        if key in self.data:
            return self.data[key]
        if default is _MISSING:
            self.errors.append(f"{self.where(key)}: missing ({expected})")
            return None
        return default

    def _bad(self, key: str, expected: str, value: Any) -> None:
        self.errors.append(f"{self.where(key)}: expected {expected}, got {value!r}")

    def number(self, key: str, *, minimum: Optional[float] = None, positive: bool = False,
               default: Any = _MISSING) -> float:
        v = self._get(key, "a number", default)
        if v is None:
            return 0.0
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            self._bad(key, "a number", v)
            return 0.0
        v = float(v)
        if positive and v <= 0:
            self.errors.append(f"{self.where(key)}: must be > 0")
        if minimum is not None and v < minimum:
            self.errors.append(f"{self.where(key)}: must be >= {minimum}")
        return v

    def integer(self, key: str, *, minimum: Optional[int] = None, default: Any = _MISSING) -> int:
        v = self._get(key, "an integer", default)
        if v is None:
            return 0
        if isinstance(v, bool) or not isinstance(v, int):
            self._bad(key, "an integer", v)
            return 0
        if minimum is not None and v < minimum:
            self.errors.append(f"{self.where(key)}: must be >= {minimum}")
        return v

    def boolean(self, key: str) -> bool:
        v = self._get(key, "true or false", _MISSING)
        if v is None:
            return False
        if not isinstance(v, bool):
            self._bad(key, "true or false", v)
            return False
        return v

    def string(self, key: str, *, choices: Optional[Tuple[str, ...]] = None,
               pattern: Optional["re.Pattern[str]"] = None, required: bool = False,
               default: Any = _MISSING) -> str:
        v = self._get(key, "a string", default)
        if v is None:
            return ""
        if not isinstance(v, str):
            self._bad(key, "a string", v)
            return ""
        if choices is not None and v not in choices:
            self.errors.append(f"{self.where(key)}: {v!r} is not one of {', '.join(choices)}")
        if required and not v:
            self.errors.append(f"{self.where(key)}: must not be empty")
        if v and pattern is not None and not pattern.match(v):
            self.errors.append(f"{self.where(key)}: {v!r} has the wrong format")
        return v

    def numbers(self, key: str, *, positive: bool = True) -> Tuple[float, ...]:
        v = self._get(key, "a list of numbers", _MISSING)
        if v is None:
            return ()
        if not isinstance(v, list) or not v or any(
                isinstance(x, bool) or not isinstance(x, (int, float)) for x in v):
            self._bad(key, "a non-empty list of numbers", v)
            return ()
        out = tuple(float(x) for x in v)
        if positive and any(x <= 0 for x in out):
            self.errors.append(f"{self.where(key)}: every value must be > 0")
        if len(set(out)) != len(out):
            self.errors.append(f"{self.where(key)}: has duplicates")
        return out

    def integers(self, key: str) -> Tuple[int, ...]:
        v = self._get(key, "a list of integers", _MISSING)
        if v is None:
            return ()
        if not isinstance(v, list) or any(isinstance(x, bool) or not isinstance(x, int) for x in v):
            self._bad(key, "a list of integers", v)
            return ()
        return tuple(v)

    def strings(self, key: str, *, choices: Optional[Tuple[str, ...]] = None) -> Tuple[str, ...]:
        v = self._get(key, "a list of strings", _MISSING)
        if v is None:
            return ()
        if not isinstance(v, list) or not v or any(not isinstance(x, str) or not x for x in v):
            self._bad(key, "a non-empty list of non-empty strings", v)
            return ()
        if choices is not None:
            for x in v:
                if x not in choices:
                    self.errors.append(f"{self.where(key)}: {x!r} is not one of {', '.join(choices)}")
        if len(set(v)) != len(v):
            self.errors.append(f"{self.where(key)}: has duplicates")
        return tuple(v)

    def table(self, key: str) -> "_Table":
        v = self._get(key, "a table", _MISSING)
        return _Table(v if v is not None else {}, self.where(key), self.errors)

    def subtables(self) -> List[Tuple[str, "_Table"]]:
        """Every key of this table, each read as a sub-table (for [materials.*], [tools.*], ...)."""
        out = []
        for key in self.data:
            self._seen.add(key)
            out.append((key, _Table(self.data[key], self.where(key), self.errors)))
        return out

    def string_map(self, key: str, *, keys: Optional[Tuple[str, ...]] = None,
                   value_pattern: Optional["re.Pattern[str]"] = None) -> Dict[str, str]:
        t = self.table(key)
        out: Dict[str, str] = {}
        names = keys if keys is not None else tuple(t.data)
        for name in names:
            out[name] = t.string(name, pattern=value_pattern)
        t.finish()
        return out

    def finish(self) -> None:
        for key in self.data:
            if key not in self._seen:
                self.errors.append(f"{self.where(key)}: unknown key")


# ---------------------------------------------------------------- parsing

def load_config(path: Optional[Path] = None) -> Config:
    path = Path(path) if path is not None else DEFAULT_CONFIG
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigError([f"cannot read {path}: {e}"]) from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError([f"{path}: not valid TOML: {e}"]) from e
    return parse_config(data, root=path.resolve().parent.parent, path=path)


def parse_config(data: Dict[str, Any], root: Path, path: Optional[Path] = None) -> Config:
    errors: List[str] = []
    top = _Table(data, "", errors)

    schema = top.integer("schema")
    if schema != SCHEMA:
        errors.append(f"schema: this code reads schema {SCHEMA}, the file says {schema}")
    assumed = top.string_map("assumed")

    t = top.table("machine")
    machine = Machine(
        reach_x_in=t.number("reach_x_in", positive=True),
        z_floor_in=t.number("z_floor_in"),
        units=t.string("units", choices=("in",)),
        spindle_rpm=t.integer("spindle_rpm", minimum=1),
        spin_up_dwell_s=t.number("spin_up_dwell_s", minimum=0),
        park=t.string("park", required=True),
        z_touchoff=t.string("z_touchoff", choices=("spoilboard",)),
    )
    t.finish()

    t = top.table("sheet")
    sheet = Sheet(length_in=t.number("length_in", positive=True), width_in=t.number("width_in", positive=True))
    t.finish()

    t = top.table("nest")
    nest = Nest(
        edge_margin_in=t.number("edge_margin_in", minimum=0),
        reach_margin_in=t.number("reach_margin_in", minimum=0),
        part_spacing_in=t.number("part_spacing_in", positive=True),
        max_sheets_per_group=t.integer("max_sheets_per_group", minimum=1),
        rotation=t.string("rotation", choices=("all", "none")),
        part_in_part=t.boolean("part_in_part"),
        envelope_spacing_in=t.number("envelope_spacing_in", positive=True),
        short_qty=t.string("short_qty", choices=("defer_card",)),
    )
    t.finish()

    t = top.table("clamps")
    clamps = Clamps(
        edges=t.strings("edges", choices=CLAMP_EDGES),
        reach_in=t.number("reach_in", minimum=0),
        clearance_in=t.number("clearance_in", minimum=0),
        height_in=t.number("height_in", minimum=0),
        min_clear_above_stock_in=t.number("min_clear_above_stock_in", positive=True),
    )
    t.finish()

    t = top.table("pauses")
    pauses = Pauses(
        enabled=t.boolean("enabled"),
        after_last_part=t.boolean("after_last_part"),
        mode=t.string("mode", choices=("tap_text", "manual_nc")),
        resume_key=t.string("resume_key"),
    )
    t.finish()

    t = top.table("plate")
    plate = Plate(thickness_tol_in=t.number("thickness_tol_in", positive=True),
                  strict_inside_radius=t.boolean("strict_inside_radius"))
    t.finish()

    materials: Dict[str, Material] = {}
    for key, t in top.table("materials").subtables():
        family = t.string("family", choices=FAMILIES)
        materials[key] = Material(
            key=key,
            name=t.string("name", required=True),
            family=family,
            color=t.string("color", default=""),
            thicknesses_in=t.numbers("thicknesses_in"),
            use_mist=t.boolean("use_mist"),
            program_prefix=t.string("program_prefix", pattern=SAFE_NAME_RE, required=True),
        )
        t.finish()

    tools: Dict[str, Tool] = {}
    for key, t in top.table("tools").subtables():
        guid = t.string("guid", pattern=GUID_RE)
        library_field = _MISSING if guid else 0  # a tool not in the library yet may omit these
        tools[key] = Tool(
            key=key,
            guid=guid,
            number=t.integer("number", minimum=0, default=library_field),
            diameter_in=t.number("diameter_in", positive=True),
            flute_in=t.number("flute_in", minimum=0, default=library_field),
            min_inside_radius_in=t.number("min_inside_radius_in", positive=True),
            family=t.string("family", choices=FAMILIES),
            cutter_label=t.string("cutter_label", required=True),
        )
        t.finish()

    tooling: Dict[str, FamilyTooling] = {}
    for family, t in top.table("tooling").subtables():
        tooling[family] = FamilyTooling(default=t.string("default", required=True),
                                        small_features=t.string("small_features", required=True))
        t.finish()

    t = top.table("holes")
    drill_t = t.table("drill_sizes_in")
    drill_sizes = {key: drill_t.numbers(key) for key in drill_t.data}
    holes = Holes(
        drill_tol_in=t.number("drill_tol_in", positive=True),
        bore_min_in=t.number("bore_min_in", positive=True),
        bore_min_tol_in=t.number("bore_min_tol_in", minimum=0),
        bore_max_in=t.number("bore_max_in", positive=True),
        bearing_sizes_in=t.numbers("bearing_sizes_in"),
        bearing_tol_in=t.number("bearing_tol_in", positive=True),
        drill_sizes_in=MappingProxyType(drill_sizes),
    )
    t.finish()

    t = top.table("cam")
    op_tags = t.strings("op_tags", choices=OP_TAGS)
    t.finish()

    templates: Dict[str, Template] = {}
    for key, t in top.table("templates").subtables():
        templates[key] = Template(
            key=key,
            family=t.string("family", choices=FAMILIES),
            tool=t.string("tool", required=True),
            file=root / t.string("file", required=True),
        )
        t.finish()

    t = top.table("labels")
    labels = Labels(smoked=t.string("smoked", required=True), tool_eighth=t.string("tool_eighth", required=True))
    t.finish()

    t = top.table("onshape")
    onshape = Onshape(
        base_url=t.string("base_url", pattern=BASE_URL_RE, required=True),
        calls_per_part_estimate=t.integer("calls_per_part_estimate", minimum=1),
        per_run_max_calls=t.integer("per_run_max_calls", minimum=1),
        monthly_soft_calls=t.integer("monthly_soft_calls", minimum=1),
        yearly_cap_calls=t.integer("yearly_cap_calls", minimum=1),
        budget_year_start=t.string("budget_year_start", pattern=MONTH_DAY_RE, required=True),
        poll_first_s=t.number("poll_first_s", positive=True),
        poll_factor=t.number("poll_factor", minimum=1),
        poll_max_s=t.number("poll_max_s", positive=True),
        poll_max_count=t.integer("poll_max_count", minimum=1),
        retry_after_max_wait_s=t.number("retry_after_max_wait_s", minimum=0),
        material_map=MappingProxyType(t.string_map("material_map")),
    )
    t.finish()

    t = top.table("trello")
    trello = Trello(
        board_id=t.string("board_id", pattern=TRELLO_ID_RE),
        poll_interval_s=t.integer("poll_interval_s", minimum=10),
        attachment_limit_mb=t.number("attachment_limit_mb", positive=True),
        job_timeout_s=t.integer("job_timeout_s", minimum=60),
        checklist_name=t.string("checklist_name", required=True),
        checklist=t.strings("checklist"),
        machine_checklist_name=t.string("machine_checklist_name", required=True),
        machine_checklist=t.strings("machine_checklist"),
        lists=MappingProxyType(t.string_map("lists", keys=TRELLO_LISTS, value_pattern=TRELLO_ID_RE)),
        targets=MappingProxyType(t.string_map("targets", keys=TRELLO_TARGETS)),
        cards=MappingProxyType(t.string_map("cards", keys=TRELLO_CARDS, value_pattern=TRELLO_ID_RE)),
    )
    t.finish()

    t = top.table("fusion")
    pp = t.table("post_properties")
    post_properties = dict(pp.data)
    pp._seen.update(pp.data)
    for prop, value in post_properties.items():
        if not isinstance(value, (bool, int, str)):
            errors.append(f"{pp.where(prop)}: expected true/false, a number, or a string")
    fusion = Fusion(
        post_description=t.string("post_description", required=True),
        post_file=root / t.string("post_file", required=True),
        post_sha256=t.string("post_sha256", pattern=re.compile(r"^[0-9a-f]{64}$"), required=True),
        tool_library=root / t.string("tool_library", required=True),
        job_timeout_s=t.integer("job_timeout_s", minimum=60),
        max_attempts=t.integer("max_attempts", minimum=1),
        post_properties=MappingProxyType(post_properties),
        params=MappingProxyType(_read_params(t.table("params"), errors)),
    )
    t.finish()

    t = top.table("tapguard")
    tapguard = TapGuard(allowed_g=t.integers("allowed_g"), allowed_m=t.integers("allowed_m"),
                        clamp_margin_in=t.number("clamp_margin_in", minimum=0))
    t.finish()

    t = top.table("fusion_team")
    fusion_team = FusionTeam(project=t.string("project"), folder=t.string("folder"))
    t.finish()

    t = top.table("paths")
    paths = Paths(**{k: root / t.string(k, required=True) for k in ("queue", "cache", "state", "logs")})
    t.finish()

    top.finish()
    if errors:
        raise ConfigError(errors)

    cfg = Config(
        path=path, root=root, assumed=MappingProxyType(assumed), machine=machine, sheet=sheet,
        nest=nest, clamps=clamps, pauses=pauses, plate=plate, materials=MappingProxyType(materials),
        tools=MappingProxyType(tools), tooling=MappingProxyType(tooling), holes=holes,
        op_tags=op_tags, templates=MappingProxyType(templates), labels=labels, onshape=onshape,
        trello=trello, fusion=fusion, tapguard=tapguard, fusion_team=fusion_team, paths=paths,
        placeholders=tuple(_placeholders(data)), warnings=(),
    )
    warnings: List[str] = []
    _cross_check(cfg, data, errors, warnings)
    if errors:
        raise ConfigError(errors)
    object.__setattr__(cfg, "warnings", tuple(warnings))
    return cfg


def _read_params(t: _Table, errors: List[str]) -> Dict[str, Any]:
    """Fusion parameter names: nested tables of strings / lists of strings, passed through."""
    out: Dict[str, Any] = {}
    for key, value in t.data.items():
        t._seen.add(key)
        where = t.where(key)
        if isinstance(value, dict):
            out[key] = _read_params(_Table(value, where, errors), errors)
        elif isinstance(value, str) or (isinstance(value, list) and all(isinstance(x, str) for x in value)):
            out[key] = value
        else:
            errors.append(f"{where}: expected a parameter name or a list of names, got {value!r}")
    return out


def _placeholders(data: Any, prefix: str = "") -> List[str]:
    out: List[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            out += _placeholders(value, f"{prefix}.{key}" if prefix else key)
    elif data == "":
        out.append(prefix)
    return out


def _resolves(data: Any, dotted: str) -> bool:
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def post_property_names(cps_text: str) -> set:
    """Property names a .cps post defines (its `properties = {...}` block plus `properties.x = ...`)."""
    names = set()
    start = cps_text.find("properties = {")
    if start != -1:
        end = cps_text.find("\n};", start)
        names |= set(_POST_PROP_RE.findall(cps_text[start:end if end != -1 else None]))
    names |= set(_POST_EXTRA_PROP_RE.findall(cps_text))
    return names


# ----------------------------------------------------------- cross-checks

def _cross_check(cfg: Config, data: Dict[str, Any], errors: List[str], warnings: List[str]) -> None:
    e = errors.append

    for key in cfg.assumed:
        if not _resolves(data, key):
            e(f"assumed.{key}: no such key in the config")

    # Nothing may go below Z0 (the stock bottom), decision 22.
    if cfg.machine.z_floor_in < 0:
        e("machine.z_floor_in: must be >= 0; nothing may go below Z0 (the stock bottom)")
    if cfg.clamps.min_clear_above_stock_in <= cfg.clamps.height_in:
        e("clamps.min_clear_above_stock_in: must be above clamps.height_in, or tools hit the clamps")

    # Materials: one stock type per (material, color, thickness).
    seen_materials = set()
    for m in cfg.materials.values():
        if m.family == "polycarbonate" and not m.color:
            e(f"materials.{m.key}.color: polycarbonate needs a color (clear and smoked never share a sheet)")
        ident = (m.name, m.color)
        if ident in seen_materials:
            e(f"materials.{m.key}: duplicate of another material ({m.name} {m.color})".rstrip())
        seen_materials.add(ident)
        if m.family not in cfg.tooling:
            e(f"tooling.{m.family}: missing (used by materials.{m.key})")
    thicknesses = sorted({t for m in cfg.materials.values() for t in m.thicknesses_in})
    gaps = [b - a for a, b in zip(thicknesses, thicknesses[1:])]
    if gaps and cfg.plate.thickness_tol_in >= min(gaps) / 2:
        e("plate.thickness_tol_in: must be under half the smallest gap between stock thicknesses")

    # Tools: checked against the pinned library by GUID; never selected by number or diameter.
    library: Optional[ToolLibrary] = None
    try:
        library = ToolLibrary.load(cfg.fusion.tool_library)
    except ToolLibraryError as err:
        e(f"fusion.tool_library: {err}")
    guids = [t.guid for t in cfg.tools.values() if t.available]
    if len(set(guids)) != len(guids):
        e("tools: two tools share a GUID")
    for tool in cfg.tools.values():
        where = f"tools.{tool.key}"
        if tool.min_inside_radius_in < tool.diameter_in / 2 - 0.001:
            e(f"{where}.min_inside_radius_in: smaller than the tool radius")
        if not tool.available:
            warnings.append(f"{where}: no GUID yet; parts that need it go to Needs fixing")
            continue
        family_max = max((t for m in cfg.materials.values() if m.family == tool.family
                          for t in m.thicknesses_in), default=0.0)
        if tool.flute_in <= family_max:
            e(f"{where}.flute_in: {tool.flute_in} in doesn't cover {family_max} in {tool.family} plate")
        if library is None:
            continue
        if tool.guid not in library:
            e(f"{where}.guid: {tool.guid} is not in {cfg.fusion.tool_library.name}")
            continue
        lib = library.by_guid(tool.guid)
        if lib.number != tool.number:
            e(f"{where}.number: library says T{lib.number} for this GUID, config says T{tool.number}")
        if abs(lib.diameter_in - tool.diameter_in) > 0.0005:
            e(f"{where}.diameter_in: library says {lib.diameter_in:.5f} in, config says {tool.diameter_in}")
        if abs(lib.flute_length_in - tool.flute_in) > 0.002:
            e(f"{where}.flute_in: library says {lib.flute_length_in:.4f} in, config says {tool.flute_in}")
    widest = max((t.diameter_in for t in cfg.tools.values()), default=0.0)
    if cfg.nest.part_spacing_in <= widest:
        e("nest.part_spacing_in: must exceed the largest tool diameter")

    # Per-family tool choice (decision 13).
    templates_by_tool = {t.tool: t for t in cfg.templates.values()}
    for family, ft in cfg.tooling.items():
        where = f"tooling.{family}"
        if family not in FAMILIES:
            e(f"{where}: unknown material family")
            continue
        for role in ("default", "small_features"):
            tool = cfg.tools.get(getattr(ft, role))
            if tool is None:
                e(f"{where}.{role}: no tool {getattr(ft, role)!r}")
            elif tool.family != family:
                e(f"{where}.{role}: {tool.key} is a {tool.family} tool")
        default = cfg.tools.get(ft.default)
        if default is not None:
            if not default.available:
                e(f"{where}.default: {default.key} has no GUID")
            elif default.key not in templates_by_tool:
                e(f"{where}.default: no template uses {default.key}")
        small = cfg.tools.get(ft.small_features)
        if small is not None and small.available and small.key not in templates_by_tool:
            warnings.append(f"{where}.small_features: no template for {small.key} yet; "
                            "parts that need it go to Needs fixing")

    # Hole sizes (decision 7): drill sizes stay an exact list below the bore range.
    for key in cfg.holes.drill_sizes_in:
        if key not in cfg.tools:
            e(f"holes.drill_sizes_in.{key}: no such tool")
    for key in cfg.tools:
        if key not in cfg.holes.drill_sizes_in:
            e(f"holes.drill_sizes_in.{key}: missing")
    bore_floor = cfg.holes.bore_min_in - cfg.holes.bore_min_tol_in
    for key, sizes in cfg.holes.drill_sizes_in.items():
        if any(s + cfg.holes.drill_tol_in >= bore_floor for s in sizes):
            e(f"holes.drill_sizes_in.{key}: drill sizes must stay below the bore range")
    if cfg.holes.bore_min_in >= cfg.holes.bore_max_in:
        e("holes.bore_min_in: must be below bore_max_in")
    for s in cfg.holes.bearing_sizes_in:
        if not cfg.holes.bore_min_in <= s <= cfg.holes.bore_max_in:
            e(f"holes.bearing_sizes_in: {s} is outside the bore range")

    if set(cfg.op_tags) != set(OP_TAGS):
        e(f"cam.op_tags: must list exactly {', '.join(OP_TAGS)}")

    # Templates: one per (family, tool); poly never uses the aluminum 1/8 in (T12).
    seen_templates = set()
    for tpl in cfg.templates.values():
        where = f"templates.{tpl.key}"
        tool = cfg.tools.get(tpl.tool)
        if tool is None:
            e(f"{where}.tool: no tool {tpl.tool!r}")
            continue
        if tool.family != tpl.family:
            if tpl.family == "polycarbonate":
                e(f"{where}.tool: polycarbonate never uses {tool.key} (an aluminum tool, T{tool.number})")
            else:
                e(f"{where}.tool: {tool.key} is a {tool.family} tool")
        if not tool.available:
            e(f"{where}.tool: {tool.key} has no GUID yet")
        if (tpl.family, tpl.tool) in seen_templates:
            e(f"{where}: another template already covers {tpl.family} + {tpl.tool}")
        seen_templates.add((tpl.family, tpl.tool))
        if not tpl.file.is_file():
            warnings.append(f"{where}.file: {_rel(tpl.file, cfg.root)} not exported yet")

    if cfg.labels.smoked == cfg.labels.tool_eighth:
        e("labels: smoked and tool_eighth must differ")

    for name, key in cfg.onshape.material_map.items():
        if key not in cfg.materials:
            e(f"onshape.material_map.{name}: no material {key!r}")
    if cfg.onshape.per_run_max_calls > cfg.onshape.yearly_cap_calls:
        e("onshape.per_run_max_calls: larger than yearly_cap_calls")

    # Trello: automation never moves anything to Ready to cut (decision 8).
    if cfg.trello.attachment_limit_mb > TRELLO_FREE_ATTACHMENT_MB:
        e(f"trello.attachment_limit_mb: Trello Free caps attachments at {TRELLO_FREE_ATTACHMENT_MB} MB")
    ids = [v for v in cfg.trello.lists.values() if v]
    if len(set(ids)) != len(ids):
        e("trello.lists: two lists share an ID")
    ready_id = cfg.trello.lists.get(NEVER_AUTOMATED, "")
    for target, list_key in cfg.trello.targets.items():
        where = f"trello.targets.{target}"
        if list_key == NEVER_AUTOMATED:
            e(f"{where}: automation never moves cards to {NEVER_AUTOMATED}; a human does")
        elif list_key not in TRELLO_LISTS:
            e(f"{where}: {list_key!r} is not a list in trello.lists")
        elif ready_id and cfg.trello.lists[list_key] == ready_id:
            e(f"{where}: {list_key} has the same ID as {NEVER_AUTOMATED}")

    # Post: the shop's machine-proven post is never edited (sha256-pinned).
    try:
        post_bytes = cfg.fusion.post_file.read_bytes()
    except OSError as err:
        e(f"fusion.post_file: {err}")
        post_bytes = None
    if post_bytes is not None:
        if hashlib.sha256(post_bytes).hexdigest() != cfg.fusion.post_sha256:
            e("fusion.post_file: sha256 doesn't match fusion.post_sha256; the post must never be edited")
        names = post_property_names(post_bytes.decode("utf-8", errors="replace"))
        for prop in cfg.fusion.post_properties:
            if prop == "useMist":
                e("fusion.post_properties.useMist: set per material (materials.*.use_mist), not here")
            elif prop not in names:
                e(f"fusion.post_properties.{prop}: the post has no such property")
        for prop in sorted(names - {"useMist"} - set(cfg.fusion.post_properties)):
            e(f"fusion.post_properties.{prop}: missing; every post property is set explicitly")
    for prop, value in FIXED_POST_PROPERTIES.items():
        if cfg.fusion.post_properties.get(prop, _MISSING) != value:
            e(f"fusion.post_properties.{prop}: must be {value!r}")

    # Z-floor guard allowlists.
    unknown_g = sorted(set(cfg.tapguard.allowed_g) - SAFE_G)
    if unknown_g:
        e(f"tapguard.allowed_g: G{', G'.join(map(str, unknown_g))} can never be allowed "
          f"(the guard only understands G{', G'.join(map(str, sorted(SAFE_G)))})")
    unknown_m = sorted(set(cfg.tapguard.allowed_m) - SAFE_M)
    if unknown_m:
        e(f"tapguard.allowed_m: M{', M'.join(map(str, unknown_m))} can never be allowed")
    missing_g = sorted(REQUIRED_G - set(cfg.tapguard.allowed_g))
    if missing_g:
        e(f"tapguard.allowed_g: must include G{', G'.join(map(str, missing_g))}")
    needed_m = set(REQUIRED_M)
    if any(m.use_mist for m in cfg.materials.values()):
        needed_m |= MIST_M
    missing_m = sorted(needed_m - set(cfg.tapguard.allowed_m))
    if missing_m:
        e(f"tapguard.allowed_m: must include M{', M'.join(map(str, missing_m))}")


def _rel(p: Path, root: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return str(p)
