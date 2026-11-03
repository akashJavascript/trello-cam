"""The Onshape panel's Onshape calls, with its own limits and caches ([panel] in config).

Every call goes through OnshapeClient, so the ledger records it like any other, under run_id "panel". Before
any call: the 402 latch, the panel's calls today and its calls this budget year. They're counted apart from
runs', so the panel can never use up the calls a run needs.

Reads:
- a Part Studio's parts: GET /parts/d/{did}/{wvm}/{wvmid}/e/{eid}. One call, kept parts_cache_s.
- quantities: the document's assemblies, GET /documents/d/{did}/{wvm}/{wvmid}/elements?elementType=ASSEMBLY, then
  each one's definition, GET /assemblies/d/{did}/{wvm}/{wvmid}/e/{aid}?excludeSuppressed=true (getAssemblyDefinition).
  That's 1 + up to max_assemblies calls, kept assembly_cache_s for every part of the document. A part's quantity
  in an assembly is how many of its occurrences (subassemblies included) are that part.
- a part's shape, for the design checks: GET /parts/d/{did}/{wvm}/{wvmid}/e/{eid}/partid/{pid}/bodydetails. One
  call per part per state of the Part Studio (keyed by the parts list's microversion), kept on disk as AutoCAM's part
  model (geometry.py), not the raw response (a plate's can be 2 MB).
Write:
- a part's material: POST /metadata/d/{did}/w/{wid}/e/{eid}/p/{pid}, setting the Material property. One call.
  The value is a material object copied from a part that already has that material (in this Part Studio, or
  in any parts list in AutoCAM's cache). Not yet confirmed on a live write, so the caller keeps a fallback: if
  it fails, the card gets a Material: line instead and the run reads that.

Only assemblies in the same document are counted (a robot assembly in another document isn't).
"""

import base64
import json
import logging
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, unquote

from autocam_core.geometry import PartGeometry
from autocam_core.hotfolder import write_atomic
from autocam_core.names import safe_token

from ..onshape.client import API, BudgetExceeded, OnshapeClient, OnshapeError
from ..onshape.ledger import Ledger
from ..onshape.urls import configuration_tag, normal_configuration
from .geometry import read_body, solid_body

log = logging.getLogger("autocam.panel")

MATERIAL_PROPERTY = "57f3fb8efa3416c06701d615"   # Onshape's Material property (a real BOM's headers, 2026-11-01;
                                                 # ...d60f is Part number)
REFRESH_MIN_S = 10.0                             # a Refresh this soon after a read uses that read
OWNERS_FILE = "document_owners.json"             # cache/: each document's owner, read once (they don't change)
OWNER_LOOKUPS_PER_READ = 12                      # new documents looked up per assembly read (the rest next time)
TOP_NAMES = re.compile(r"robot|main|full|top|master|final", re.IGNORECASE)


class PanelOnshapeError(Exception):
    """Something to tell the student (the message is theirs)."""


class OutOfCalls(PanelOnshapeError):
    pass


class NoAccess(PanelOnshapeError):
    """Onshape says the team's account can't open this document (403/404)."""


@dataclass
class _Entry:
    at: float
    value: Any


@dataclass
class _Assemblies:
    """The document's assemblies, as occurrence counts per part: [(name, {(did, eid, pid): count})]."""
    counted: List[Tuple[str, Dict[Tuple[str, str, str], int]]] = field(default_factory=list)
    total: int = 0               # assemblies in the document
    note: str = ""               # why some or all weren't counted


def _q(cfg: str, **more: Any) -> Dict[str, Any]:
    """An API query: `more`, plus the configuration when there is one."""
    return {**more, **({"configuration": cfg} if cfg else {})}


_TO_IN = {"meter": 1 / 0.0254, "m": 1 / 0.0254, "millimeter": 1 / 25.4, "mm": 1 / 25.4, "centimeter": 1 / 2.54,
          "cm": 1 / 2.54, "inch": 1.0, "in": 1.0, "foot": 12.0, "ft": 12.0}
_QUANTITY_RE = re.compile(r"^\s*(-?[0-9.]+(?:e-?[0-9]+)?)\s*\+?\s*([A-Za-z]+)\s*$")


def _quantity(value: str) -> str:
    """A configuration's length in inches ("0.0047625+meter" is "0.1875 in"; Onshape stores lengths in meters);
    anything else as written, with Onshape's "+" as a space."""
    m = _QUANTITY_RE.match(value)
    if m and m[2].lower() in _TO_IN:
        try:
            inches = float(m[1]) * _TO_IN[m[2].lower()]
        except ValueError:
            return value.replace("+", " ")
        return f"{inches:.4f}".rstrip("0").rstrip(".") + " in"
    return value.replace("+", " ")


def configuration_label(cfg: str, definition: Dict[str, Any]) -> str:
    """"Length: 19 in; Size: Wide" from a configuration string and the Part Studio's configuration definition
    (each parameter's id and name, each list option's id and name; the old panel's labels). Anything the
    definition doesn't name is shown as it is, with Onshape's "+" as a space."""
    if not cfg:
        return ""
    params = []
    for item in (definition or {}).get("configurationParameters") or []:
        if isinstance(item, dict):
            params.append(item.get("message") if isinstance(item.get("message"), dict) else item)
    shown = []
    for entry in cfg.split(";"):
        key, eq, value = entry.partition("=")
        if not eq:
            shown.append(unquote(entry).replace("+", " "))
            continue
        key, value = unquote(key), unquote(value)
        param = next((x for x in params if x.get("parameterId") == key), {})
        options = [o.get("message") if isinstance(o.get("message"), dict) else o
                   for o in param.get("options") or [] if isinstance(o, dict)]
        option = next((o for o in options if o.get("option") == value), {})
        shown.append(f"{param.get('parameterName') or key}: {option.get('optionName') or _quantity(value)}")
    text = "; ".join(shown)
    return text if len(text) <= 80 else text[:77] + "..."


def occurrence_counts(definition: Dict[str, Any]) -> Dict[Tuple[str, ...], int]:
    """(documentId, elementId, partId) -> how many occurrences of that part the assembly has, at any depth, and
    (documentId, elementId, partId, configuration) -> how many in that configuration ("" the default).

    An occurrence's path is instance ids from the top; its last id is the instance itself, defined in the root
    assembly or in one of the subassemblies."""
    root = definition.get("rootAssembly") or {}
    instances: Dict[str, Dict[str, Any]] = {}
    for i in root.get("instances") or []:
        instances[i.get("id")] = i
    for sub in definition.get("subAssemblies") or []:
        for i in sub.get("instances") or []:
            instances.setdefault(i.get("id"), i)
    counts: Counter = Counter()
    for occ in root.get("occurrences") or []:
        path = occ.get("path") or []
        inst = instances.get(path[-1]) if path else None
        if not inst or inst.get("type") != "Part" or inst.get("suppressed"):
            continue
        ids = (inst.get("documentId"), inst.get("elementId"), inst.get("partId"))
        counts[ids] += 1
        counts[ids + (normal_configuration(inst.get("configuration")),)] += 1
    return dict(counts)


def pick_assemblies(elements: List[Dict[str, Any]], most: int) -> List[Dict[str, Any]]:
    """The assemblies to count in, at most `most`: ones named like a whole robot first, then document order."""
    assemblies = [e for e in elements if (e.get("elementType") or "").upper() == "ASSEMBLY" and e.get("id")]
    return sorted(assemblies, key=lambda e: 0 if TOP_NAMES.search(e.get("name") or "") else 1)[:most]


class PanelOnshape:
    def __init__(self, *, ledger: Ledger, client_for: Optional[Callable[[int], OnshapeClient]], cache_root: Path,
                 calls_per_day: int, calls_per_year: int, year_start: str, parts_cache_s: float,
                 assembly_cache_s: float, max_assemblies: int, clock: Callable[[], float] = time.monotonic):
        self.ledger = ledger
        self.client_for = client_for          # max_calls -> a client (None: no Onshape keys)
        self.cache_root = Path(cache_root)
        self.calls_per_day = calls_per_day
        self.calls_per_year = calls_per_year
        self.year_start = year_start
        self.parts_cache_s = parts_cache_s
        self.assembly_cache_s = assembly_cache_s
        self.max_assemblies = max_assemblies
        self.clock = clock
        self._lock = threading.RLock()        # one Onshape request at a time: two students never pay twice
        self._parts: Dict[Tuple[str, ...], _Entry] = {}
        self._configs: Dict[Tuple[str, str, str, str], _Entry] = {}
        self._assemblies: Dict[Tuple[str, str, str], _Entry] = {}
        self._shapes: Dict[Tuple[str, ...], Tuple[PartGeometry, Dict[str, Any]]] = {}
        self._elements: Dict[Tuple[str, str, str], _Entry] = {}
        self._states: Dict[Tuple[str, str], _Entry] = {}
        self._boms: Dict[Tuple[str, str, str, str], _Entry] = {}

    # ---- limits
    def calls_left(self) -> int:
        if self.ledger.latched() is not None:
            return 0
        today = self.calls_per_day - self.ledger.day_count(panel=True)
        year = self.calls_per_year - self.ledger.year_count(self.year_start, panel=True)
        return max(0, min(today, year))

    def _client(self, needed: int = 1) -> OnshapeClient:
        if self.client_for is None:
            raise OutOfCalls("AutoCAM has no Onshape keys, so the panel can't read Onshape. Make the card on "
                             "Trello instead.")
        if self.ledger.latched() is not None:
            raise OutOfCalls("Onshape said the team is out of API calls, so AutoCAM has stopped calling it. Ask a "
                             "mentor.")
        left = self.calls_left()
        if left < needed:
            raise OutOfCalls("The panel has used its Onshape calls for today. Make the card on Trello instead, "
                             "or try again tomorrow.")
        return self.client_for(left)

    # ---- parts
    def parts(self, did: str, wvm: str, wvmid: str, eid: str, fresh: bool = False,
              cfg: str = "") -> List[Dict[str, Any]]:
        """cfg: the Part Studio's configuration ("" the default), as everywhere below."""
        key = (did, wvm, wvmid, eid, cfg)
        with self._lock:
            hit = self._parts.get(key)
            age = None if hit is None else self.clock() - hit.at
            if hit is not None and (age < REFRESH_MIN_S or (not fresh and age < self.parts_cache_s)):
                return hit.value
            client = self._client()
            try:
                data = client.get_json(f"{API}/parts/d/{did}/{wvm}/{wvmid}/e/{eid}",
                                       query=_q(cfg, withThumbnails="false", includeFlatParts="true"),
                                       purpose="panel_parts")
            except BudgetExceeded as e:
                raise OutOfCalls(str(e)) from e
            except OnshapeError as e:
                if e.status in (403, 404):
                    raise NoAccess(str(e)) from e
                raise PanelOnshapeError(f"Onshape couldn't list this tab's parts ({e.status or 'no answer'}). Is "
                                        "it a Part Studio?") from e
            if not isinstance(data, list):
                raise PanelOnshapeError("Onshape didn't return a parts list. Is this tab a Part Studio?")
            self._parts[key] = _Entry(self.clock(), data)
            return data

    def cached_parts(self, did: str, wvm: str, wvmid: str, eid: str,
                     cfg: str = "") -> Optional[List[Dict[str, Any]]]:
        """The last parts list read, however old (None: never read). Never calls Onshape."""
        hit = self._parts.get((did, wvm, wvmid, eid, cfg))
        return None if hit is None else hit.value

    # ---- a part's shape, for the design checks
    def _shape_path(self, did: str, wvm: str, wvmid: str, eid: str, pid: str, microversion: str,
                    cfg: str = "") -> Path:
        return self.cache_root / "onshape" / (f"geometry_{did}_{wvm}{wvmid}_{eid}_{safe_token(pid)}_"
                                              f"{safe_token(microversion)}{configuration_tag(cfg)}.json")

    def cached_geometry(self, did: str, wvm: str, wvmid: str, eid: str, pid: str,
                        microversion: str, cfg: str = "") -> Optional[Tuple[PartGeometry, Dict[str, Any]]]:
        """Only what's already here (no call): memory, then disk."""
        key = (did, wvm, wvmid, eid, pid, microversion, cfg)
        with self._lock:
            if key in self._shapes:
                return self._shapes[key]
            path = self._shape_path(did, wvm, wvmid, eid, pid, microversion, cfg)
            if not microversion or not path.is_file():
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                found = (PartGeometry.from_dict(data["geometry"]), dict(data.get("extra") or {}))
            except (OSError, ValueError, KeyError, TypeError):
                return None
            self._shapes[key] = found
            return found

    def geometry(self, did: str, wvm: str, wvmid: str, eid: str, pid: str, microversion: str, name: str,
                 fresh: bool = False, cfg: str = "") -> Tuple[PartGeometry, Dict[str, Any]]:
        """The part as AutoCAM's model (and geometry.read_body's extras), from memory, disk (the same Part Studio
        state), or one bodydetails call. A version or microversion ("v"/"m") never changes: pass its id as
        `microversion`."""
        key = (did, wvm, wvmid, eid, pid, microversion, cfg)
        path = self._shape_path(did, wvm, wvmid, eid, pid, microversion, cfg)
        with self._lock:
            if not fresh:
                found = self.cached_geometry(did, wvm, wvmid, eid, pid, microversion, cfg)
                if found is not None:
                    return found
            client = self._client()
            try:
                data = client.get_json(f"{API}/parts/d/{did}/{wvm}/{wvmid}/e/{eid}/partid/{quote(pid, safe='')}"
                                       "/bodydetails", query=_q(cfg) or None, purpose="panel_bodydetails")
            except BudgetExceeded as e:
                raise OutOfCalls(str(e)) from e
            except OnshapeError as e:
                if e.status in (403, 404):
                    raise NoAccess(str(e)) from e
                raise PanelOnshapeError(f"Onshape couldn't describe the part's shape ({e.status or 'no answer'}).") \
                    from e
            body = solid_body(data, pid)
            if body is None:
                raise PanelOnshapeError("Onshape didn't return the part's shape.")
            found = read_body(body, name)
            self._shapes[key] = found
            if microversion:
                path.parent.mkdir(parents=True, exist_ok=True)
                write_atomic(path, json.dumps({"geometry": found[0].to_dict(), "extra": found[1]}).encode("utf-8"))
            return found

    # ---- a workspace's current state (one call says whether anything in the document changed)
    def current_microversion(self, did: str, wid: str) -> str:
        key = (did, wid)
        with self._lock:
            hit = self._states.get(key)
            if hit is not None and self.clock() - hit.at < 60:
                return hit.value
            client = self._client()
            try:
                data = client.get_json(f"{API}/documents/d/{did}/w/{wid}/currentmicroversion",
                                       purpose="panel_microversion")
            except BudgetExceeded as e:
                raise OutOfCalls(str(e)) from e
            except OnshapeError as e:
                if e.status in (403, 404):
                    raise NoAccess(str(e)) from e
                raise PanelOnshapeError(f"Onshape didn't say the workspace's state ({e.status or 'no answer'}).") from e
            mid = str((data or {}).get("microversion") or "")
            self._states[key] = _Entry(self.clock(), mid)
            return mid

    # ---- the document's tabs, and an assembly's bill of materials
    def elements(self, did: str, wvm: str, wvmid: str, fresh: bool = False) -> List[Dict[str, Any]]:
        """Every tab of the document ({"id", "name", "elementType"}): one call, kept assembly_cache_s."""
        key = (did, wvm, wvmid)
        with self._lock:
            hit = self._elements.get(key)
            age = None if hit is None else self.clock() - hit.at
            if hit is not None and (age < REFRESH_MIN_S or (not fresh and age < self.assembly_cache_s)):
                return hit.value
            client = self._client()
            try:
                data = client.get_json(f"{API}/documents/d/{did}/{wvm}/{wvmid}/elements", purpose="panel_elements")
            except BudgetExceeded as e:
                raise OutOfCalls(str(e)) from e
            except OnshapeError as e:
                if e.status in (403, 404):
                    raise NoAccess(str(e)) from e
                raise PanelOnshapeError(f"Onshape couldn't list the document's tabs ({e.status or 'no answer'}).") from e
            found = [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
            self._elements[key] = _Entry(self.clock(), found)
            return found

    # ---- who owns a document: bought (COTS) parts come from vendors' public libraries (2026-11-02: the Shooter's
    # wheels, sushi rollers and nut strip are in documents owned by the MKCad account, set to Polycarbonate or 6061)
    def document_owners(self, dids: Iterable[str], cached_only: bool = False,
                        most: int = OWNER_LOOKUPS_PER_READ) -> Dict[str, str]:
        """did -> its owner's id ("" not known yet). GET /documents/{did}, one call per document, kept for good in
        cache/document_owners.json. At most `most` new ones per read; cached_only never calls Onshape. A document
        AutoCAM's account can't open is "noaccess" (until the service restarts)."""
        path = self.cache_root / OWNERS_FILE
        with self._lock:
            if not hasattr(self, "_owners"):
                try:
                    self._owners: Dict[str, str] = dict(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    self._owners = {}
            out: Dict[str, str] = {}
            wanted = [d for d in dict.fromkeys(dids) if d]
            new = [d for d in wanted if d not in self._owners][:0 if cached_only else most]
            client, saved = None, False
            for did in new:
                try:
                    client = client or self._client()
                    info = client.get_json(f"{API}/documents/{did}", purpose="panel_document_owner")
                except (OutOfCalls, BudgetExceeded):
                    break
                except OnshapeError as e:
                    if e.status in (403, 404):
                        self._owners[did] = "noaccess"
                        continue
                    log.warning("panel: who owns document %s: %s", did, e)
                    break
                owner = str(((info or {}).get("owner") or {}).get("id") or "")
                if owner:
                    self._owners[did] = owner
                    saved = True
            if saved:
                path.parent.mkdir(parents=True, exist_ok=True)
                kept = {k: v for k, v in self._owners.items() if v != "noaccess"}
                write_atomic(path, (json.dumps(kept, indent=1, sort_keys=True) + "\n").encode("utf-8"))
            for did in wanted:
                out[did] = self._owners.get(did, "")
            return out

    # ---- a part clicked in an assembly (its occurrence path's last id is the part's instance; seen 2026-11-02)
    def assembly_instances(self, did: str, wvm: str, wvmid: str, aid: str, fresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """instance id -> {"documentId", "elementId", "partId", "configuration", ...} for every part instance in the
        assembly and its subassemblies: GET /assemblies/... (one call), kept assembly_cache_s. fresh: read again
        (a part just added), unless it was read in the last minute."""
        key = (did, wvm, wvmid, aid)
        with self._lock:
            hit = getattr(self, "_instances", {}).get(key)
            age = None if hit is None else self.clock() - hit.at
            if hit is not None and (age < 60 or (not fresh and age < self.assembly_cache_s)):
                return hit.value[0]
            client = self._client()
            try:
                d = client.get_json(f"{API}/assemblies/d/{did}/{wvm}/{wvmid}/e/{aid}",
                                    query={"excludeSuppressed": "true"}, purpose="panel_assembly_instances")
            except BudgetExceeded as e:
                raise OutOfCalls(str(e)) from e
            except OnshapeError as e:
                if e.status in (403, 404):
                    raise NoAccess(str(e)) from e
                raise PanelOnshapeError(f"Onshape couldn't read the assembly ({e.status or 'no answer'}).") from e
            found: Dict[str, Dict[str, Any]] = {}
            d = d if isinstance(d, dict) else {}
            for group in [d.get("rootAssembly") or {}] + list(d.get("subAssemblies") or []):
                for i in group.get("instances") or []:
                    if isinstance(i, dict) and i.get("type") == "Part" and i.get("id"):
                        found.setdefault(i["id"], i)
            # A copy made by a Replicate or Mirror (parametricInstances: its feature id, then the copies' instance
            # ids) is named in Onshape's selections by the feature and the copy, ["MAeTC/ZZkT0fup+er", "InRI/..."],
            # where the definition's occurrence path has the copy alone (seen in the Shooter, 2026-11-02)
            made_by: Dict[str, str] = {}
            for group in [d.get("rootAssembly") or {}] + list(d.get("subAssemblies") or []):
                for feature in group.get("parametricInstances") or []:
                    for child in (feature.get("children") or []) if isinstance(feature, dict) else []:
                        for iid in (child.get("instanceIds") or []) if isinstance(child, dict) else []:
                            if feature.get("id"):
                                made_by[str(iid)] = str(feature["id"])
            paths: Dict[str, List[List[str]]] = {}
            for occ in (d.get("rootAssembly") or {}).get("occurrences") or []:
                path = occ.get("path") if isinstance(occ, dict) else None
                if isinstance(path, list) and path and path[-1] in found:
                    full: List[str] = []
                    for x in path:
                        full += [made_by[str(x)], str(x)] if str(x) in made_by else [str(x)]
                    paths.setdefault(path[-1], []).append(full)
            if not hasattr(self, "_instances"):
                self._instances: Dict[Tuple[str, str, str, str], _Entry] = {}
            self._instances[key] = _Entry(self.clock(), (found, paths))
            return found

    def assembly_occurrences(self, did: str, wvm: str, wvmid: str, aid: str
                             ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[List[str]]]]:
        """The same read as assembly_instances, with each part instance's occurrence paths (every copy of it)."""
        self.assembly_instances(did, wvm, wvmid, aid)
        return self._instances[(did, wvm, wvmid, aid)].value

    def cached_elements(self, did: str, wvm: str, wvmid: str) -> Optional[List[Dict[str, Any]]]:
        hit = self._elements.get((did, wvm, wvmid))
        return None if hit is None else hit.value

    def cached_bom(self, did: str, wvm: str, wvmid: str, aid: str) -> Optional[List[Dict[str, Any]]]:
        """The last BOM read, however old (None: never read). Never calls Onshape."""
        hit = self._boms.get((did, wvm, wvmid, aid))
        return None if hit is None else hit.value

    def bom(self, did: str, wvm: str, wvmid: str, aid: str, fresh: bool = False) -> List[Dict[str, Any]]:
        """An assembly's parts, flattened (subassemblies counted in): [{"did", "wvm", "wvmid", "eid", "pid", "name",
        "qty", "material", "configuration" ("" the default), "standard"}]. One call (GET /assemblies/.../bom), kept parts_cache_s.

        Read off a real response (2026-11-01): rows[].itemSource gives where the part comes from (its document,
        "w" or "v" with its id, Part Studio and part); headers name which column is the quantity, name and material."""
        key = (did, wvm, wvmid, aid)
        with self._lock:
            hit = self._boms.get(key)
            age = None if hit is None else self.clock() - hit.at
            if hit is not None and (age < REFRESH_MIN_S or (not fresh and age < self.parts_cache_s)):
                return hit.value
            client = self._client()
            try:
                data = client.get_json(f"{API}/assemblies/d/{did}/{wvm}/{wvmid}/e/{aid}/bom",
                                       query={"indented": "false", "generateIfAbsent": "false"}, purpose="panel_bom")
            except BudgetExceeded as e:
                raise OutOfCalls(str(e)) from e
            except OnshapeError as e:
                if e.status in (403, 404):
                    raise NoAccess(str(e)) from e
                raise PanelOnshapeError(f"Onshape couldn't list the assembly's parts ({e.status or 'no answer'}).") \
                    from e
            rows = bom_rows(data if isinstance(data, dict) else {})
            self._boms[key] = _Entry(self.clock(), rows)
            return rows

    # ---- a sheet metal part's bends
    def bends(self, did: str, wvm: str, wvmid: str, eid: str, pid: str, microversion: str,
              cfg: str = "") -> List[Dict[str, Any]]:
        """Its bend table (one call, kept on disk with the Part Studio's state)."""
        from ..onshape.export import parse_bend_table
        path = self.cache_root / "onshape" / (f"panel_bends_{did}_{wvm}{wvmid}_{eid}_{safe_token(pid)}_"
                                              f"{safe_token(microversion)}{configuration_tag(cfg)}.json")
        with self._lock:
            if microversion and path.is_file():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
            client = self._client()
            try:
                data = client.get_json(f"{API}/parts/d/{did}/{wvm}/{wvmid}/e/{eid}/partid/{quote(pid, safe='')}"
                                       "/sheetmetal/bendtable", query=_q(cfg) or None, purpose="panel_bends")
            except BudgetExceeded as e:
                raise OutOfCalls(str(e)) from e
            except OnshapeError as e:
                raise PanelOnshapeError(f"Onshape didn't give the bends ({e.status or 'no answer'}).") from e
            bends = parse_bend_table(data if isinstance(data, dict) else {})
            if microversion:
                path.parent.mkdir(parents=True, exist_ok=True)
                write_atomic(path, json.dumps(bends).encode("utf-8"))
            return bends

    # ---- quantities from the document's assemblies
    def assembly_counts(self, did: str, wvm: str, wvmid: str, eid: str, pid: str,
                        fresh: bool = False, cfg: str = "") -> Dict[str, Any]:
        """{"assemblies": [{"name", "count"}] (count > 0, most first), "counted": n, "total": n, "note": str}.
        A configured part counts only its instances in that configuration (the instance's configuration string,
        not yet seen on a real assembly: no match counts 0 and the student types the quantity)."""
        doc = self._document_assemblies(did, wvm, wvmid, fresh)
        found = []
        for name, counts in doc.counted:
            n = counts.get((did, eid, pid, cfg), 0) if cfg else counts.get((did, eid, pid, ""), counts.get((did, eid, pid), 0))
            if n:
                found.append({"name": name, "count": n})
        found.sort(key=lambda a: -a["count"])
        return {"assemblies": found, "counted": len(doc.counted), "total": doc.total, "note": doc.note}

    def _document_assemblies(self, did: str, wvm: str, wvmid: str, fresh: bool) -> _Assemblies:
        key = (did, wvm, wvmid)
        with self._lock:
            hit = self._assemblies.get(key)
            age = None if hit is None else self.clock() - hit.at
            if hit is not None and (age < REFRESH_MIN_S or (not fresh and age < self.assembly_cache_s)):
                return hit.value
            result = _Assemblies()
            try:
                client = self._client(1 if key in self._elements and not fresh else 2)   # tabs + one assembly at least
                elements = self.elements(did, wvm, wvmid, fresh)
                chosen = pick_assemblies(elements, self.max_assemblies)
                result.total = sum(1 for e in elements if (e.get("elementType") or "").upper() == "ASSEMBLY")
                for a in chosen:
                    if self.calls_left() < 1:
                        result.note = "out of Onshape calls for today"
                        break
                    definition = client.get_json(f"{API}/assemblies/d/{did}/{wvm}/{wvmid}/e/{a['id']}",
                                                 query={"excludeSuppressed": "true"}, purpose="panel_assembly")
                    result.counted.append((a.get("name") or "Assembly", occurrence_counts(definition or {})))
                if not result.note and result.total > len(result.counted):
                    result.note = f"counted in {len(result.counted)} of the document's {result.total} assemblies"
            except OutOfCalls as e:
                result.note = str(e)
                if hit is not None:
                    return hit.value                     # stale, but better than nothing
                return result                            # not cached: no counts this time
            except (BudgetExceeded, OnshapeError, PanelOnshapeError) as e:
                log.warning("panel: reading %s's assemblies: %s", did, e)
                result.note = "Onshape didn't answer about the assemblies"
                return result
            self._assemblies[key] = _Entry(self.clock(), result)
            return result

    # ---- setting a material
    def material_object(self, onshape_name: str, studio_parts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """A material object with this display name, from a part that has it: this Part Studio's parts first,
        then the parts lists AutoCAM's runs cached."""
        def find(parts) -> Optional[Dict[str, Any]]:
            for p in parts or []:
                m = p.get("material") if isinstance(p, dict) else None
                if isinstance(m, dict) and (m.get("displayName") or "").lower() == onshape_name.lower():
                    return m
            return None
        got = find(studio_parts)
        if got is not None:
            return got
        folder = self.cache_root / "onshape"
        for path in sorted(folder.glob("parts_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
            try:
                got = find(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
            if got is not None:
                return got
        return None

    def set_material(self, did: str, wid: str, eid: str, pid: str, onshape_name: str) -> Tuple[bool, str]:
        """(True, "") once Onshape took it; (False, why) otherwise (the card then carries a Material: line)."""
        with self._lock:
            studio = self.cached_parts(did, "w", wid, eid) or []
            material = self.material_object(onshape_name, studio)
            if material is None:
                return False, f"AutoCAM hasn't seen {onshape_name} on a part in Onshape yet"
            try:
                client = self._client()
                client.post_json(f"{API}/metadata/d/{did}/w/{wid}/e/{eid}/p/{pid}",
                                 {"jsonType": "metadata-part", "partId": pid,
                                  "properties": [{"propertyId": MATERIAL_PROPERTY, "value": material}]},
                                 purpose="panel_material")
            except OutOfCalls as e:
                return False, str(e)
            except (BudgetExceeded, OnshapeError) as e:
                log.warning("panel: setting %s's material in Onshape: %s", pid, e)
                return False, f"Onshape refused ({getattr(e, 'status', 0) or 'no answer'})"
            for p in studio:                             # what Onshape has now, without reading it again
                if p.get("partId") == pid:
                    p["material"] = material
            return True, ""


    # ---- the company, its webhook, part pictures (an enterprise admin's keys, 2026-11-01)
    def _call(self, what: str, fn):
        with self._lock:
            client = self._client()
            try:
                return fn(client)
            except BudgetExceeded as e:
                raise OutOfCalls(str(e)) from e
            except OnshapeError as e:
                if e.status in (403, 404):
                    raise NoAccess(str(e)) from e
                raise PanelOnshapeError(f"Onshape didn't {what} ({e.status or 'no answer'}: "
                                        f"{str(e).split(': ', 1)[-1][:160]}).") from e

    def raw_get(self, path: str, query: Dict[str, str], purpose: str, what: str) -> Any:
        """One GET counted like the panel's other calls (usage.py's team API count)."""
        return self._call(what, lambda c: c.get_json(path, query=query, purpose=purpose))

    def webhooks(self) -> List[Dict[str, Any]]:
        """The webhooks AutoCAM's keys registered."""
        data = self._call("list the webhooks", lambda c: c.get_json(f"{API}/webhooks", purpose="panel_webhooks"))
        items = data.get("items") if isinstance(data, dict) else data
        return [w for w in items or [] if isinstance(w, dict)]

    def create_webhook(self, body: Dict[str, Any]) -> Dict[str, Any]:
        data = self._call("register the webhook", lambda c: c.post_json(
            f"{API}/webhooks", body, purpose="panel_webhook_create"))
        return data if isinstance(data, dict) else {}

    def delete_webhook(self, webhook_id: str) -> None:
        self._call("remove the webhook", lambda c: c.delete(f"{API}/webhooks/{quote(webhook_id, safe='')}",
                                                            purpose="panel_webhook_delete"))

    def configuration_label(self, did: str, wvm: str, wvmid: str, eid: str, cfg: str, cached_only: bool = False) -> str:
        """"Length: 19 in; Plate: Wide" for a configuration, from the Part Studio's configuration definition
        (GET /elements/.../configuration, one call kept assembly_cache_s; the old panel's way). Without it (out of
        calls, Onshape not answering), the configuration string made readable as it is."""
        if not cfg:
            return ""
        key = (did, wvm, wvmid, eid)
        with self._lock:
            hit = self._configs.get(key)
            if not cached_only and (hit is None or self.clock() - hit.at >= self.assembly_cache_s):
                try:
                    data = self._call("read the configurations", lambda c: c.get_json(
                        f"{API}/elements/d/{did}/{wvm}/{wvmid}/e/{eid}/configuration", purpose="panel_configuration"))
                    hit = _Entry(self.clock(), data if isinstance(data, dict) else {})
                    self._configs[key] = hit
                except (OutOfCalls, NoAccess, PanelOnshapeError) as e:
                    log.info("panel: the configuration names of %s: %s", eid, e)
        return configuration_label(cfg, hit.value if hit is not None else {})

    def picture(self, did: str, wvm: str, wvmid: str, eid: str, pid: str, cfg: str = "") -> bytes:
        """A shaded picture of the part (PNG), kept for good per state (a version or microversion never changes)."""
        path = self.cache_root / "onshape" / "pictures" / (f"{did}_{wvm}{wvmid}_{eid}_{pid.replace('/', '_')}"
                                                           f"{configuration_tag(cfg)}.png")
        try:
            return path.read_bytes()
        except OSError:
            pass
        data = self._call("draw the part", lambda c: c.get_json(
            f"{API}/parts/d/{did}/{wvm}/{wvmid}/e/{eid}/partid/{quote(pid, safe='')}/shadedviews",
            query=_q(cfg, viewMatrix="isometric", outputHeight=300, outputWidth=300, pixelSize=0, edges="show",
                     useAntiAliasing="true"), purpose="panel_picture"))
        images = (data or {}).get("images") if isinstance(data, dict) else None
        if not images:
            raise PanelOnshapeError("Onshape sent no picture.")
        png = base64.b64decode(images[0])
        if wvm in ("m", "v"):
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(path, png)
        return png


def _config_value(v: str) -> Any:
    m = _QUANTITY_RE.match(v)
    if m and m[2].lower() in _TO_IN:
        try:
            return round(float(m[1]) * _TO_IN[m[2].lower()], 5)
        except ValueError:
            pass
    return v.strip().lower()


def row_for_instance(inst: Dict[str, Any], rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The BOM row a part instance is counted in. An instance's configuration string isn't spelled like the BOM's:
    it carries every parameter (Auto_Name_Part=true too) where the row leaves defaults out, and the float noise can
    differ (the Shooter's standoffs, 2026-11-02). So: the same part, and every parameter the row names has the
    instance's value; the row naming the most parameters wins."""
    same = [r for r in rows if (r["did"], r["eid"], r["pid"]) ==
            (inst.get("documentId"), inst.get("elementId"), inst.get("partId"))]
    if len(same) <= 1:
        return same[0] if same else None

    def values(cfg: str) -> Dict[str, Any]:
        out = {}
        for entry in (cfg or "").split(";"):
            k, eq, v = entry.partition("=")
            if eq:
                out[unquote(k).strip().lower()] = _config_value(unquote(v))
        return out

    have = values(normal_configuration(inst.get("configuration")))
    best, most = None, -1
    for r in same:
        want = values(r["configuration"])
        if all(have.get(k) == v for k, v in want.items()) and len(want) > most:
            best, most = r, len(want)
    return best


def bom_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    headers = {h.get("propertyName"): h.get("id") for h in data.get("headers") or [] if isinstance(h, dict)}
    out = []
    for row in data.get("rows") or []:
        src = row.get("itemSource") or {}
        values = row.get("headerIdToValue") or {}
        if not src.get("partId") or not src.get("elementId"):
            continue
        material = values.get(headers.get("material"))
        material = (material or {}).get("displayName") if isinstance(material, dict) else (material or "")
        try:
            qty = int(round(float(values.get(headers.get("quantity")) or 0)))
        except (TypeError, ValueError):
            qty = 0
        vendor = values.get(headers.get("vendor")) if headers.get("vendor") else ""   # a bought part's (if set)
        number = values.get(headers.get("partNumber")) if headers.get("partNumber") else ""
        out.append({"did": src.get("documentId") or "", "wvm": src.get("wvmType") or "w", "wvmid": src.get("wvmId") or "",
                    "eid": src["elementId"], "pid": src["partId"], "name": str(values.get(headers.get("name")) or ""),
                    "qty": qty, "material": material or "",
                    "configuration": normal_configuration(src.get("configuration")),
                    "standard": bool(src.get("isStandardContent")),
                    "vendor": vendor.strip() if isinstance(vendor, str) else "",
                    "number": number.strip() if isinstance(number, str) else ""})
    return out
