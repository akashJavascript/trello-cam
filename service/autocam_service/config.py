"""Load and validate config/autocam.toml.

Strict on purpose: unknown keys, wrong types, and unsafe values are errors, and all of
them are reported at once. Lengths are inches. The Fusion add-in never reads this file;
the service copies what a job needs into job.json.
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple

if sys.version_info >= (3, 11):
    import tomllib
else:  # WSL dev boxes still ship Python 3.10
    import tomli as tomllib

from autocam_core.schema_job import MAX_FACE_DEPTH_IN, MAX_FIT_STOCK_IN, MAX_SCRIBE_DEPTH_IN
from autocam_core.tapguard import SAFE_G, SAFE_M
from autocam_core.toollib import ToolLibrary, ToolLibraryError

MAX_BEARING_STOCK_IN = 0.01
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "autocam.toml"

SCHEMA = 1
FAMILIES = ("aluminum", "polycarbonate")
CLAMP_EDGES = ("left", "right", "front")      # the long edges, and the end by the operator
OP_TAGS = ("pocket", "pocketfinish", "drill", "bore", "bearing", "inner", "outer")
TRELLO_LISTS = ("inbox", "ready_for_cam", "needs_fixing", "nested", "sheet_review",
                "ready_to_cut", "cut", "offcuts", "control")
TRELLO_TARGETS = ("part_nested", "part_rejected", "part_deferred", "part_cut", "sheet_created",
                  "checklist_return")
TRELLO_CARDS = ("system",)
NEVER_AUTOMATED = "ready_to_cut"
TRELLO_FREE_ATTACHMENT_MB = 10
ENTERPRISE_CALLS_PER_YEAR = 10000   # Onshape: shared by the whole enterprise (runs and the panel are a share)
# The guard only understands autocam_core.tapguard.SAFE_G / SAFE_M; allowlists must stay inside them.
REQUIRED_G = frozenset({0, 1, 4, 20, 53, 90})    # G4: the stops (a bare G4) and the spin-up dwell
REQUIRED_M = frozenset({3, 5})               # no M0: WinCNC has none (pauses.py)
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
    reach_y_in: float
    z_floor_in: float
    units: str
    spindle_rpm: int
    spin_up_dwell_s: float
    park: str
    z_touchoff: str
    air_test_gap_in: float
    air_test_feed_ipm: float
    air_test_auto: bool = True        # "Add an air test program" ticked from the start on risky sheets (2026-11-04)


@dataclass(frozen=True)
class Tabs:
    default: bool = False        # part cards' "Hold it in with tabs" box starts ticked
    distance_in: float = 2.5     # about this far apart along a contour
    min_per_contour: int = 2
    max_per_contour: int = 6
    at_points: bool = True
    width_in: float = 0.0
    height_in: float = 0.0
    wide_max_tier: int = 4       # tabs wider than the cutter only on spots this good or better (tabs.place_tabs)
    free_max_in: float = 0.0     # beside wide tabs, curvy cutouts this small are cut free (0: never)


@dataclass(frozen=True)
class Status:
    update_every_s: int
    fusion_stale_after_s: int
    season_start: str = "09-01"


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
    offcut_gap_in: float
    offcut_min_in: float
    offcut_beside_min_in: float
    priority_by_size: bool = True
    straight_max: int = 12


@dataclass(frozen=True)
class Clamps:
    edges: Tuple[str, ...]
    reach_in: float
    clearance_in: float
    height_in: float
    min_clear_above_stock_in: float
    in_fusion: bool = True             # the strips as fixtures in each sheet's Fusion setup, so its simulation shows them


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
    sheet_length_in: float = 0.0   # its new sheets' size when it isn't [sheet]'s (SWYFT 24 x 24, MAXComposite 47 x 23);
    sheet_width_in: float = 0.0    # 0: [sheet]'s


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
    split: bool = True        # small holes and tight cutouts in a program of their own with the small tool, the rest
                              # on the default (core 0.19.0); false: a part like that puts its whole sheet on it


@dataclass(frozen=True)
class Holes:
    drill_tol_in: float
    bore_min_in: float
    bore_min_tol_in: float
    bore_max_in: float
    bearing_sizes_in: Tuple[float, ...]
    bearing_tol_in: float
    drill_sizes_in: Mapping[str, Tuple[float, ...]]
    bearing_stock_in: float = 0.0


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
    rush: str = "Rush"
    face: str = "Face"
    cad_changed: str = "CAD changed"    # a part changed in Onshape after it was CAM'd (panel/board.py)
    spare: str = "Spare"                # spares for the spares box: nested after the robot parts (2026-11-04)
    check: str = "Check"                # a hand-made program to check only, nothing placed (program_checks.py)


@dataclass(frozen=True)
class Fit:
    """A "Fit:" line on a part card (fits.py): that hole size or the outline is cut a little tight, and the sheet
    gets lap programs that each take a little more off, run one by one at the machine until it fits."""
    enabled: bool = True
    start_in: float = 0.003      # radial stock the sheet's program leaves (+: a hole smaller, an outline bigger)
    step_in: float = 0.001       # each lap leaves this much less
    laps: int = 4
    outlines_last: bool = True   # a hole fit's part stays solid in the sheet while its hole is lapped: its outline
                                 # is cut by the sheet's OUTLINES program once the hole fits (2026-11-04)


@dataclass(frozen=True)
class HandCuts:
    """Parts cut out of offcuts outside AutoCAM: besides a typed "Cut by hand:" rectangle, the program that cut it
    can be attached with a "Zeroed at:" line (handprograms.py)."""
    programs: bool = True        # read attached programs at all
    cutter_in: float = 0.25      # the cutter's diameter when the card has no Cutter: line
    place_programs: bool = True  # a part card with its own .tap: AutoCAM picks the spot (own_programs.py)
    place_spare_across_in: float = 0.05  # its footprint's spare inside the nest region across (off the clamp strip)


@dataclass(frozen=True)
class Costs:
    """The panel's cost and sheet use estimate: what a full sheet costs, per material and thickness."""
    sheet_prices: Mapping[str, Mapping[float, float]] = field(default_factory=dict)   # material -> thickness -> $
    nest_efficiency: float = 0.8     # the share of a sheet that ends up as parts (the rest is gaps and edges)


@dataclass(frozen=True)
class Panel:
    """The Onshape panel ("Send to AutoCAM", panel/): a page in Onshape's right panel that makes part cards. This
    service serves it on 127.0.0.1:port; a Cloudflare tunnel (tunnel_dir) makes it public_url."""
    enabled: bool = False
    port: int = 8790
    public_url: str = ""
    tunnel_dir: str = ""                 # cloudflared.exe and config.yml ("": no tunnel started by the service)
    onshape_calls_per_day: int = 40      # the panel's own calls, counted apart from runs' (ledger run_id "panel")
    onshape_calls_per_year: int = 1500
    parts_cache_s: int = 600             # a Part Studio's parts list is read again after this long (or on Refresh)
    assembly_cache_s: int = 1800         # the document's assemblies (for quantities)
    max_assemblies: int = 4              # quantities are counted in at most this many assemblies of the document
    assembly_check_minutes: int = 60     # an assembly AutoCAM watches (panel/assemblies.py) is read at most this often
    board_cache_s: int = 30              # the Trello lists read for each part's status
    sends_per_minute: int = 20
    change_check_hours: float = 12.0     # sheets in Sheet review are checked against Onshape this often (one call
                                         # per document state); Ready to cut ones once each, when they get there
    webhooks: bool = True                # Onshape's company webhook tells AutoCAM what changed (panel/hooks.py)
    webhook_fallback_hours: float = 72.0  # with the webhook working, the periodic check above is only this often
    pictures: bool = True                # a picture of each part on its card (panel/pictures.py)
    auto_refresh_s: int = 15             # an open panel re-reads its statuses this often (Trello only; 0: never)
    check_every_s: int = 30              # the before-cut check's loop (Trello reads; Onshape only when it's due)
    change_quiet_s: int = 120            # a webhook-reported change is checked once nobody has touched it this long
    company_id: str = ""                 # the Onshape enterprise's id, for its API count (panel/usage.py)
    team_usage_hours: float = 24.0       # read the whole team's API count this often for the System card (0: never)
    shop_signin: str = "slack"           # the phone page (/shop): "slack" = Sign in with Slack once AUTOCAM_SLACK_*
                                         # is in .env (panel/signin.py); "" = only the key link
    shop_session_days: int = 60          # a signed-in phone stays signed in this long after it was last used
    shop_old_link_days: int = 7          # the key link keeps working this long after the first Slack sign-in
    machine: bool = True                 # the operator's screen on the ShopSabre's PC (/machine, machine.py)


@dataclass(frozen=True)
class ShaftTab:
    """A library Part Studio that's a shaft: its stock (a name in shafts.STOCKS, or "round": round bar picked by the
    part's OD), and its default configuration's length (and OD) for a row in the default configuration."""
    stock: str
    length_in: float
    od_in: float = 0.0


@dataclass(frozen=True)
class Shafts:
    """The shop's shaft list (shafts.py, docs/shafts.md; 2026-11-02): library shafts sent from the Onshape panel,
    made from the /shop page."""
    enabled: bool = False
    library_document: str = ""                 # the team's common parts library (its shaft tabs are below)
    tabs: Mapping[str, "ShaftTab"] = field(default_factory=dict)   # the library tab's element id -> what it is
    tap: str = "10-32"                         # every end, unless the sender picks another
    groove_depth_in: float = 0.020             # per side, every snap-ring groove
    keep_done_days: int = 14                   # done shafts drop off the list after this long


@dataclass(frozen=True)
class Parts:
    """The shop's bought-parts list (bought.py, docs/bought-parts.md; 2026-11-02): an assembly's bought parts sent from
    the Onshape panel, checked off on the /shop page's Parts tab (In the bin, or Order)."""
    enabled: bool = False
    order_sheet_url: str = ""                  # the team's Google Sheet: Order opens it (AutoCAM never writes to it).
                                               # Usually AUTOCAM_ORDER_SHEET_URL in .env instead: the repo is public
    keep_days: int = 14                        # parts in the bin drop off the list after this long


@dataclass(frozen=True)
class Scribe:
    """Bend lines scribed on bent parts that ask for it (core 0.18.0): with the sheet's own cutter in its program,
    or with the chamfer mill (identified by GUID, checked against the library) in a SCRIBE program of its own."""
    depth_in: float
    chamfer_depth_in: float
    chamfer_guid: str
    chamfer_number: int
    chamfer_diameter_in: float
    chamfer_flute_in: float
    chamfer_label: str


@dataclass(frozen=True)
class Face:
    """The "Face" label: that part's top skimmed clean, in a FACE program run before the sheet's own (core
    0.14.0). Its tool is identified by GUID only, like every tool, and must be the face template's."""
    depth_in: float
    guid: str
    number: int
    diameter_in: float
    flute_in: float
    cutter_label: str
    template: Path


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
    start_delay_s: int
    nest_checklist: str
    nest_item: str
    options_checklist: str
    offcut_checklist: str
    offcut_item: str
    no_stop_item: str
    air_test_item: str
    checklist_name: str
    checklist: Tuple[str, ...]
    machine_checklist_name: str
    machine_checklist: Tuple[str, ...]
    lists: Mapping[str, str]
    targets: Mapping[str, str]
    cards: Mapping[str, str]
    tabs_checklist: str = "Tabs"
    tabs_item: str = "Hold it in with tabs"
    stock_checklist: str = "Stock"
    not_found_item: str = "The offcut isn't on the rack"
    cut_away_item: str = "Something's already cut where this program goes"
    renest_item: str = "Re-nest it on other stock (a new run and a new review)"
    new_sheet_item: str = "Cut it on a new sheet (the same program)"


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
    status: Status
    paths: Paths
    placeholders: Tuple[str, ...]  # dotted keys still set to ""
    warnings: Tuple[str, ...]
    tabs: Tabs = Tabs()
    face: Optional[Face] = None
    scribe: Optional[Scribe] = None
    hand_cuts: HandCuts = HandCuts()
    fit: Fit = Fit()
    panel: Panel = Panel()
    costs: Costs = Costs()
    shafts: Shafts = Shafts()
    parts: Parts = Parts()

    def stock_types(self) -> List[Tuple[Material, float]]:
        return [(m, t) for m in self.materials.values() for t in m.thicknesses_in]

    def sheet_for(self, material: Optional[str]) -> Sheet:
        """A new sheet of this material: its own size if it has one, else [sheet]'s (2026-11-02)."""
        m = self.materials.get(material or "")
        if m is None or not m.sheet_length_in:
            return self.sheet
        return Sheet(length_in=m.sheet_length_in, width_in=m.sheet_width_in)


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

    def boolean(self, key: str, default: Any = _MISSING) -> bool:
        v = self._get(key, "true or false", default)
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
        reach_y_in=t.number("reach_y_in", positive=True),
        z_floor_in=t.number("z_floor_in"),
        units=t.string("units", choices=("in",)),
        spindle_rpm=t.integer("spindle_rpm", minimum=1),
        spin_up_dwell_s=t.number("spin_up_dwell_s", minimum=0),
        park=t.string("park", required=True),
        z_touchoff=t.string("z_touchoff", choices=("spoilboard",)),
        air_test_gap_in=t.number("air_test_gap_in", positive=True),
        air_test_feed_ipm=t.number("air_test_feed_ipm", positive=True),
        air_test_auto=t.boolean("air_test_auto", default=True),
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
        offcut_gap_in=t.number("offcut_gap_in", minimum=0),
        offcut_min_in=t.number("offcut_min_in", positive=True),
        offcut_beside_min_in=t.number("offcut_beside_min_in", positive=True),
        priority_by_size=t.boolean("priority_by_size"),
        straight_max=t.integer("straight_max", minimum=0, default=12),
    )
    t.finish()

    t = top.table("clamps")
    clamps = Clamps(
        edges=t.strings("edges", choices=CLAMP_EDGES),
        reach_in=t.number("reach_in", minimum=0),
        clearance_in=t.number("clearance_in", minimum=0),
        height_in=t.number("height_in", minimum=0),
        min_clear_above_stock_in=t.number("min_clear_above_stock_in", positive=True),
        in_fusion=t.boolean("in_fusion", default=True),
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
            sheet_length_in=t.number("sheet_length_in", minimum=0, default=0.0),
            sheet_width_in=t.number("sheet_width_in", minimum=0, default=0.0),
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
                                        small_features=t.string("small_features", required=True),
                                        split=t.boolean("split", default=True))
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
        bearing_stock_in=t.number("bearing_stock_in", default=0.0),
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
    labels = Labels(smoked=t.string("smoked", required=True), tool_eighth=t.string("tool_eighth", required=True),
                    rush=t.string("rush", default="Rush"), face=t.string("face", default="Face"),
                    cad_changed=t.string("cad_changed", default="CAD changed"), spare=t.string("spare", default="Spare"),
                    check=t.string("check", default="Check"))
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
        start_delay_s=t.integer("start_delay_s", minimum=0),
        nest_checklist=t.string("nest_checklist", required=True),
        nest_item=t.string("nest_item", required=True),
        options_checklist=t.string("options_checklist", required=True),
        offcut_checklist=t.string("offcut_checklist", required=True),
        offcut_item=t.string("offcut_item", required=True),
        no_stop_item=t.string("no_stop_item", required=True),
        air_test_item=t.string("air_test_item", required=True),
        checklist_name=t.string("checklist_name", required=True),
        checklist=t.strings("checklist"),
        machine_checklist_name=t.string("machine_checklist_name", required=True),
        machine_checklist=t.strings("machine_checklist"),
        lists=MappingProxyType(t.string_map("lists", keys=TRELLO_LISTS, value_pattern=TRELLO_ID_RE)),
        targets=MappingProxyType(t.string_map("targets", keys=TRELLO_TARGETS)),
        cards=MappingProxyType(t.string_map("cards", keys=TRELLO_CARDS, value_pattern=TRELLO_ID_RE)),
        tabs_checklist=t.string("tabs_checklist", default="Tabs"),
        tabs_item=t.string("tabs_item", default="Hold it in with tabs"),
        stock_checklist=t.string("stock_checklist", default="Stock"),
        not_found_item=t.string("not_found_item", default="The offcut isn't on the rack"),
        cut_away_item=t.string("cut_away_item", default="Something's already cut where this program goes"),
        renest_item=t.string("renest_item", default="Re-nest it on other stock (a new run and a new review)"),
        new_sheet_item=t.string("new_sheet_item", default="Cut it on a new sheet (the same program)"),
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

    t = top.table("status")
    status = Status(update_every_s=t.integer("update_every_s", minimum=60),
                    fusion_stale_after_s=t.integer("fusion_stale_after_s", minimum=30),
                    season_start=t.string("season_start", pattern=MONTH_DAY_RE, default="09-01"))
    t.finish()

    t = top.table("tabs") if "tabs" in top.data else None
    tabs = Tabs(default=t.boolean("default"), distance_in=t.number("distance_in", positive=True, default=2.5),
                min_per_contour=t.integer("min_per_contour", minimum=1, default=2),
                max_per_contour=t.integer("max_per_contour", minimum=1, default=6),
                at_points=t.boolean("at_points"),
                width_in=t.number("width_in", minimum=0, default=0.0),
                height_in=t.number("height_in", minimum=0, default=0.0),
                wide_max_tier=t.integer("wide_max_tier", minimum=0, default=4),
                free_max_in=t.number("free_max_in", minimum=0, default=0.0)) if t else Tabs()
    if t:
        t.finish()

    face = None
    if "face" in top.data:
        t = top.table("face")
        face = Face(depth_in=t.number("depth_in", positive=True), guid=t.string("guid", pattern=GUID_RE, required=True),
                    number=t.integer("number", minimum=0), diameter_in=t.number("diameter_in", positive=True),
                    flute_in=t.number("flute_in", positive=True), cutter_label=t.string("cutter_label", required=True),
                    template=root / t.string("template", required=True))
        t.finish()

    scribe = None
    if "scribe" in top.data:
        t = top.table("scribe")
        scribe = Scribe(depth_in=t.number("depth_in", positive=True),
                        chamfer_depth_in=t.number("chamfer_depth_in", positive=True),
                        chamfer_guid=t.string("chamfer_guid", pattern=GUID_RE, required=True),
                        chamfer_number=t.integer("chamfer_number", minimum=0),
                        chamfer_diameter_in=t.number("chamfer_diameter_in", positive=True),
                        chamfer_flute_in=t.number("chamfer_flute_in", positive=True),
                        chamfer_label=t.string("chamfer_label", required=True))
        t.finish()

    fit = Fit()
    if "fit" in top.data:
        t = top.table("fit")
        fit = Fit(enabled=t.boolean("enabled"), start_in=t.number("start_in"), step_in=t.number("step_in", positive=True),
                  laps=t.integer("laps", minimum=1), outlines_last=t.boolean("outlines_last", default=True))
        t.finish()

    hand_cuts = HandCuts()
    if "hand_cuts" in top.data:
        t = top.table("hand_cuts")
        hand_cuts = HandCuts(programs=t.boolean("programs"), cutter_in=t.number("cutter_in", positive=True),
                             place_programs=t.boolean("place_programs"),
                             place_spare_across_in=t.number("place_spare_across_in", minimum=0.0, default=0.05))
        t.finish()

    panel = Panel()
    if "panel" in top.data:
        t = top.table("panel")
        panel = Panel(enabled=t.boolean("enabled"), port=t.integer("port", minimum=1024),
                      public_url=t.string("public_url", default=""), tunnel_dir=t.string("tunnel_dir", default=""),
                      onshape_calls_per_day=t.integer("onshape_calls_per_day", minimum=0),
                      onshape_calls_per_year=t.integer("onshape_calls_per_year", minimum=0),
                      parts_cache_s=t.integer("parts_cache_s", minimum=0),
                      assembly_cache_s=t.integer("assembly_cache_s", minimum=0),
                      max_assemblies=t.integer("max_assemblies", minimum=0),
                      assembly_check_minutes=t.integer("assembly_check_minutes", minimum=5, default=60),
                      board_cache_s=t.integer("board_cache_s", minimum=0),
                      sends_per_minute=t.integer("sends_per_minute", minimum=1),
                      change_check_hours=t.number("change_check_hours", positive=True, default=12.0),
                      webhooks=t.boolean("webhooks", default=True),
                      webhook_fallback_hours=t.number("webhook_fallback_hours", positive=True, default=72.0),
                      pictures=t.boolean("pictures", default=True),
                      auto_refresh_s=t.integer("auto_refresh_s", minimum=0, default=15),
                      check_every_s=t.integer("check_every_s", minimum=10, default=30),
                      change_quiet_s=t.integer("change_quiet_s", minimum=30, default=120),
                      company_id=t.string("company_id", default=""),
                      team_usage_hours=t.number("team_usage_hours", minimum=0, default=24.0),
                      shop_signin=t.string("shop_signin", choices=("slack", ""), default="slack"),
                      shop_session_days=t.integer("shop_session_days", minimum=1, default=60),
                      shop_old_link_days=t.integer("shop_old_link_days", minimum=0, default=7),
                      machine=t.boolean("machine", default=True))
        t.finish()

    costs = Costs()
    if "costs" in top.data:
        t = top.table("costs")
        prices: Dict[str, Dict[float, float]] = {}
        sp = t.table("sheet_prices")
        for key in list(sp.data):
            row = sp.table(key)
            prices[key] = {}
            for thick in list(row.data):
                try:
                    prices[key][float(thick)] = row.number(thick, positive=True)
                except ValueError:
                    errors.append(f"costs.sheet_prices.{key}.{thick}: the key must be a thickness in inches")
            row.finish()
        sp.finish()
        costs = Costs(sheet_prices=MappingProxyType(prices),
                      nest_efficiency=t.number("nest_efficiency", positive=True, default=0.8))
        t.finish()

    shafts = Shafts()
    if "shafts" in top.data:
        from .shafts import ROUND, STOCKS, TAP_RE
        t = top.table("shafts")
        shaft_tabs: Dict[str, ShaftTab] = {}
        tt = t.table("tabs")
        for eid, row in tt.subtables():
            if not re.match(r"^[0-9a-f]{24}$", eid):
                errors.append(f"shafts.tabs.{eid}: the key must be the library tab's element id")
            stock = row.string("stock", choices=tuple(STOCKS) + (ROUND,))
            shaft_tabs[eid] = ShaftTab(stock=stock, length_in=row.number("length_in", positive=True),
                                 od_in=row.number("od_in", positive=True) if stock == ROUND else 0.0)
            row.finish()
        tt.finish()
        shafts = Shafts(enabled=t.boolean("enabled"),
                        library_document=t.string("library_document", pattern=re.compile(r"^[0-9a-f]{24}$"), required=True),
                        tabs=MappingProxyType(shaft_tabs), tap=t.string("tap", pattern=TAP_RE, default="10-32"),
                        groove_depth_in=t.number("groove_depth_in", positive=True, default=0.020),
                        keep_done_days=t.integer("keep_done_days", minimum=1, default=14))
        t.finish()

    parts = Parts()
    if "parts" in top.data:
        from .bought import SHEET_RE, sheet_link
        t = top.table("parts")
        parts = Parts(enabled=t.boolean("enabled"),
                      order_sheet_url=sheet_link(t.string("order_sheet_url", pattern=SHEET_RE, default="")),
                      keep_days=t.integer("keep_days", minimum=1, default=14))
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
        trello=trello, fusion=fusion, tapguard=tapguard, fusion_team=fusion_team, status=status, paths=paths,
        placeholders=tuple(_placeholders(data)), warnings=(), tabs=tabs, face=face, scribe=scribe, hand_cuts=hand_cuts,
        fit=fit, panel=panel, costs=costs, shafts=shafts, parts=parts,
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
        if bool(m.sheet_length_in) != bool(m.sheet_width_in):
            e(f"materials.{m.key}: give both sheet_length_in and sheet_width_in, or neither")
        elif m.sheet_width_in > m.sheet_length_in:
            e(f"materials.{m.key}: sheet_width_in is the short side (clamps go on the long edges)")
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
    widest = max([t.diameter_in for t in cfg.tools.values()] + ([cfg.face.diameter_in] if cfg.face else [])
                 + ([cfg.scribe.chamfer_diameter_in] if cfg.scribe else []), default=0.0)
    if cfg.nest.part_spacing_in <= widest:
        e("nest.part_spacing_in: must exceed the largest tool diameter (the face tool's too)")

    # Facing (the Face label): a skim with its own tool, checked against the library like any tool.
    f = cfg.face
    if f is not None:
        if f.depth_in > MAX_FACE_DEPTH_IN:
            e(f"face.depth_in: at most {MAX_FACE_DEPTH_IN} in (it skims a top clean; anything deeper is a pocket)")
        thinnest = min((t for m in cfg.materials.values() for t in m.thicknesses_in), default=0.0)
        if f.depth_in >= thinnest:
            e("face.depth_in: must be less than the thinnest stock")
        if f.guid in guids:
            e("face.guid: the face tool can't also be a sheet tool")
        if library is not None:
            if f.guid not in library:
                e(f"face.guid: {f.guid} is not in {cfg.fusion.tool_library.name}")
            else:
                lib = library.by_guid(f.guid)
                if lib.number != f.number:
                    e(f"face.number: library says T{lib.number} for this GUID, config says T{f.number}")
                if abs(lib.diameter_in - f.diameter_in) > 0.0005:
                    e(f"face.diameter_in: library says {lib.diameter_in:.5f} in, config says {f.diameter_in}")
                if abs(lib.flute_length_in - f.flute_in) > 0.002:
                    e(f"face.flute_in: library says {lib.flute_length_in:.4f} in, config says {f.flute_in}")
        if not f.template.is_file():
            warnings.append(f"face.template: {_rel(f.template, cfg.root)} not exported yet; the Face label is "
                            "refused until it is")

    # Scribed bend lines: a mark, not a cut; the chamfer mill checked against the library like any tool.
    sc = cfg.scribe
    if sc is not None:
        thinnest = min((t for m in cfg.materials.values() for t in m.thicknesses_in), default=0.0)
        for name in ("depth_in", "chamfer_depth_in"):
            if getattr(sc, name) > MAX_SCRIBE_DEPTH_IN:
                e(f"scribe.{name}: at most {MAX_SCRIBE_DEPTH_IN} in (a scribed line is a mark, not a cut)")
            elif getattr(sc, name) >= thinnest / 2:
                e(f"scribe.{name}: must be less than half the thinnest stock")
        if sc.chamfer_guid in guids:
            e("scribe.chamfer_guid: the chamfer mill can't also be a sheet tool")
        if library is not None:
            if sc.chamfer_guid not in library:
                e(f"scribe.chamfer_guid: {sc.chamfer_guid} is not in {cfg.fusion.tool_library.name}")
            else:
                lib = library.by_guid(sc.chamfer_guid)
                if lib.type != "chamfer mill":
                    e(f"scribe.chamfer_guid: the library says that's a {lib.type}, not a chamfer mill")
                if lib.number != sc.chamfer_number:
                    e(f"scribe.chamfer_number: library says T{lib.number} for this GUID, config says T{sc.chamfer_number}")
                if abs(lib.diameter_in - sc.chamfer_diameter_in) > 0.0005:
                    e(f"scribe.chamfer_diameter_in: library says {lib.diameter_in:.5f} in, config says "
                      f"{sc.chamfer_diameter_in}")
                if abs(lib.flute_length_in - sc.chamfer_flute_in) > 0.002:
                    e(f"scribe.chamfer_flute_in: library says {lib.flute_length_in:.4f} in, config says "
                      f"{sc.chamfer_flute_in}")

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
    f = cfg.fit
    if abs(f.start_in) > MAX_FIT_STOCK_IN or abs(f.start_in - f.laps * f.step_in) > MAX_FIT_STOCK_IN:
        e(f"fit: start_in and its last lap (start_in - laps x step_in) must stay within {MAX_FIT_STOCK_IN} in")
    if f.laps > 8:
        e("fit.laps: at most 8 (each is a program on the card)")
    if abs(cfg.holes.bearing_stock_in) > MAX_BEARING_STOCK_IN:
        e(f"holes.bearing_stock_in: at most {MAX_BEARING_STOCK_IN} in either way (a fit, not a size change)")

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

    names = [_norm_label(n) for n in (cfg.labels.smoked, cfg.labels.tool_eighth, cfg.labels.rush, cfg.labels.face,
                                      cfg.labels.spare, cfg.labels.check)]
    if len(set(names)) != len(names):
        e("labels: smoked, tool_eighth, rush, face, spare and check must all differ")

    for name, key in cfg.onshape.material_map.items():
        if key not in cfg.materials:
            e(f"onshape.material_map.{name}: no material {key!r}")
    if cfg.onshape.per_run_max_calls > cfg.onshape.yearly_cap_calls:
        e("onshape.per_run_max_calls: larger than yearly_cap_calls")
    for key, row in cfg.costs.sheet_prices.items():
        if key not in cfg.materials:
            e(f"costs.sheet_prices.{key}: no material {key!r}")
            continue
        for thick in row:
            if not any(abs(thick - s) < 1e-6 for s in cfg.materials[key].thicknesses_in):
                e(f"costs.sheet_prices.{key}: {thick} in isn't one of its stock thicknesses")
    if not 0 < cfg.costs.nest_efficiency <= 1:
        e("costs.nest_efficiency: must be more than 0 and at most 1")
    p = cfg.panel
    if p.onshape_calls_per_day > p.onshape_calls_per_year:
        e("panel.onshape_calls_per_day: larger than onshape_calls_per_year")
    if p.enabled and p.public_url and not p.public_url.startswith("https://"):
        e("panel.public_url: must start with https:// (Onshape only loads panels over HTTPS)")
    if cfg.onshape.yearly_cap_calls + p.onshape_calls_per_year > ENTERPRISE_CALLS_PER_YEAR:
        e(f"onshape.yearly_cap_calls + panel.onshape_calls_per_year: more than the enterprise's "
          f"{ENTERPRISE_CALLS_PER_YEAR} calls a year")

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


def _norm_label(name: str) -> str:
    return " ".join(name.lower().split())


def _rel(p: Path, root: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return str(p)
