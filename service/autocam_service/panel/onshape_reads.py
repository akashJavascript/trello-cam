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

from ..onshape.client import API, BudgetExceeded, OnshapeClient, OnshapeError
from ..onshape.ledger import Ledger

log = logging.getLogger("autocam.panel")

MATERIAL_PROPERTY = "57f3fb8efa3416c06701d60f"   # Onshape's Material property (parts lists' propertySourceTypes)
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
                                       query={"withThumbnails": "false"}, purpose="panel_parts")
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
                client = self._client(2)
                elements = client.get_json(f"{API}/documents/d/{did}/{wvm}/{wvmid}/elements",
                                           query={"elementType": "ASSEMBLY"}, purpose="panel_assemblies")
                elements = [e for e in elements if isinstance(e, dict)] if isinstance(elements, list) else []
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
            except (BudgetExceeded, OnshapeError) as e:
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
