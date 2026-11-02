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

import json
import logging
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

from autocam_core.geometry import PartGeometry
from autocam_core.hotfolder import write_atomic
from autocam_core.names import safe_token

from ..onshape.client import API, BudgetExceeded, OnshapeClient, OnshapeError
from ..onshape.ledger import Ledger
from .geometry import read_body, solid_body

log = logging.getLogger("autocam.panel")

MATERIAL_PROPERTY = "57f3fb8efa3416c06701d615"   # Onshape's Material property (a real BOM's headers, 2026-11-01;
                                                 # ...d60f is Part number)
REFRESH_MIN_S = 10.0                             # a Refresh this soon after a read uses that read
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


def occurrence_counts(definition: Dict[str, Any]) -> Dict[Tuple[str, str, str], int]:
    """(documentId, elementId, partId) -> how many occurrences of that part the assembly has, at any depth.

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
        counts[(inst.get("documentId"), inst.get("elementId"), inst.get("partId"))] += 1
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
        self._parts: Dict[Tuple[str, str, str, str], _Entry] = {}
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
    def parts(self, did: str, wvm: str, wvmid: str, eid: str, fresh: bool = False) -> List[Dict[str, Any]]:
        key = (did, wvm, wvmid, eid)
        with self._lock:
            hit = self._parts.get(key)
            age = None if hit is None else self.clock() - hit.at
            if hit is not None and (age < REFRESH_MIN_S or (not fresh and age < self.parts_cache_s)):
                return hit.value
            client = self._client()
            try:
                data = client.get_json(f"{API}/parts/d/{did}/{wvm}/{wvmid}/e/{eid}",
                                       query={"withThumbnails": "false", "includeFlatParts": "true"},
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

    def cached_parts(self, did: str, wvm: str, wvmid: str, eid: str) -> Optional[List[Dict[str, Any]]]:
        hit = self._parts.get((did, wvm, wvmid, eid))
        return None if hit is None else hit.value

    # ---- a part's shape, for the design checks
    def _shape_path(self, did: str, wvm: str, wvmid: str, eid: str, pid: str, microversion: str) -> Path:
        return self.cache_root / "onshape" / (f"geometry_{did}_{wvm}{wvmid}_{eid}_{safe_token(pid)}_"
                                              f"{safe_token(microversion)}.json")

    def cached_geometry(self, did: str, wvm: str, wvmid: str, eid: str, pid: str,
                        microversion: str) -> Optional[Tuple[PartGeometry, Dict[str, Any]]]:
        """Only what's already here (no call): memory, then disk."""
        key = (did, wvm, wvmid, eid, pid, microversion)
        with self._lock:
            if key in self._shapes:
                return self._shapes[key]
            path = self._shape_path(did, wvm, wvmid, eid, pid, microversion)
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
                 fresh: bool = False) -> Tuple[PartGeometry, Dict[str, Any]]:
        """The part as AutoCAM's model (and geometry.read_body's extras), from memory, disk (the same Part Studio
        state), or one bodydetails call. A version or microversion ("v"/"m") never changes: pass its id as
        `microversion`."""
        key = (did, wvm, wvmid, eid, pid, microversion)
        path = self._shape_path(did, wvm, wvmid, eid, pid, microversion)
        with self._lock:
            if not fresh:
                found = self.cached_geometry(did, wvm, wvmid, eid, pid, microversion)
                if found is not None:
                    return found
            client = self._client()
            try:
                data = client.get_json(f"{API}/parts/d/{did}/{wvm}/{wvmid}/e/{eid}/partid/{quote(pid, safe='')}"
                                       "/bodydetails", purpose="panel_bodydetails")
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

    def bom(self, did: str, wvm: str, wvmid: str, aid: str, fresh: bool = False) -> List[Dict[str, Any]]:
        """An assembly's parts, flattened (subassemblies counted in): [{"did", "wvm", "wvmid", "eid", "pid", "name",
        "qty", "material", "configured", "standard"}]. One call (GET /assemblies/.../bom), kept parts_cache_s.

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
    def bends(self, did: str, wvm: str, wvmid: str, eid: str, pid: str, microversion: str) -> List[Dict[str, Any]]:
        """Its bend table (one call, kept on disk with the Part Studio's state)."""
        from ..onshape.export import parse_bend_table
        path = self.cache_root / "onshape" / (f"panel_bends_{did}_{wvm}{wvmid}_{eid}_{safe_token(pid)}_"
                                              f"{safe_token(microversion)}.json")
        with self._lock:
            if microversion and path.is_file():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
            client = self._client()
            try:
                data = client.get_json(f"{API}/parts/d/{did}/{wvm}/{wvmid}/e/{eid}/partid/{quote(pid, safe='')}"
                                       "/sheetmetal/bendtable", purpose="panel_bends")
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
                        fresh: bool = False) -> Dict[str, Any]:
        """{"assemblies": [{"name", "count"}] (count > 0, most first), "counted": n, "total": n, "note": str}."""
        doc = self._document_assemblies(did, wvm, wvmid, fresh)
        found = []
        for name, counts in doc.counted:
            n = counts.get((did, eid, pid), 0)
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
        out.append({"did": src.get("documentId") or "", "wvm": src.get("wvmType") or "w", "wvmid": src.get("wvmId") or "",
                    "eid": src["elementId"], "pid": src["partId"], "name": str(values.get(headers.get("name")) or ""),
                    "qty": qty, "material": material or "",
                    "configured": str(src.get("configuration") or "default").lower() not in ("", "default"),
                    "standard": bool(src.get("isStandardContent"))})
    return out
