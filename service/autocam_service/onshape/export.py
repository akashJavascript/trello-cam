"""Find a part by name in a Part Studio (version or workspace) and export it as STEP (brief, "Onshape").

Calls per uncached part: parts list (shared by every card in the same Part Studio version and
cached forever), translation POST, a few status polls, one download. A translation that doesn't
finish in time is remembered, so the next run polls it instead of starting (and paying for) a new one.

Workspace links cost one more call per Part Studio per run: the workspace's current microversion, which
pins it. The cache is keyed by that microversion, so an edited Part Studio is exported again and an
unchanged one isn't.

Response field names (partId, bodyType, material.displayName, requestState,
resultExternalDataIds) follow Onshape's docs; confirm them on the first recorded responses (M3).
"""

import difflib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .cache import OnshapeCache, sha256_file
from .client import API, OnshapeClient
from .urls import OnshapeLink


def _norm(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def pick_part(parts: List[Dict[str, Any]], name: str) -> Dict[str, Any]:
    """The card's part: same name (ignoring case and extra spaces). A Part Studio with one solid part needs no
    name match at all."""
    solids = [p for p in parts if p.get("bodyType") in (None, "solid")]
    exact = [p for p in parts if p.get("name") == name]
    loose = exact or [p for p in parts if _norm(p.get("name", "")) == _norm(name)]
    if len(loose) == 1:
        return loose[0]
    if len(loose) > 1:
        raise ExportError(f"{len(loose)} parts in that Part Studio are named '{name}'. Rename them in Onshape so "
                          "only one has this name")
    if len(solids) == 1:
        return solids[0]
    names = ", ".join(sorted(str(p.get("name")) for p in solids)[:8]) or "none"
    guess = closest_name(name, [str(p.get("name")) for p in solids])
    raise ExportError(f"no part named '{name}' in that Part Studio. "
                      + (f"Did you mean {guess}? Make the card title that. " if guess else
                         "Make the card title the part's name. ")
                      + f"Parts there: {names}")


def closest_name(name: str, names: List[str]) -> Optional[str]:
    """The part name most like `name` (ignoring case and spaces), if one is close enough to be a likely typo."""
    by_norm = {_norm(n): n for n in names}
    got = difflib.get_close_matches(_norm(name), list(by_norm), n=1, cutoff=0.6)
    return by_norm[got[0]] if got else None


class ExportError(Exception):
    """A problem with this part (goes back to its card), not with the whole run."""


class TryAgainLater(ExportError):
    """Not the part's fault (not cached while offline, or Onshape's export still running): leave the card queued."""


@dataclass(frozen=True)
class ExportedPart:
    part_id: str
    material: Optional[str]
    step_path: Path
    step_sha256: str
    from_cache: bool
    microversion: Optional[str] = None    # workspace links: the state that was exported


@dataclass(frozen=True)
class PollSchedule:
    first_s: float
    factor: float
    max_s: float
    max_count: int

    def waits(self) -> List[float]:
        out, wait = [], self.first_s
        for _ in range(self.max_count):
            out.append(wait)
            wait = min(wait * self.factor, self.max_s)
        return out


class Exporter:
    def __init__(self, client: Optional[OnshapeClient], cache: OnshapeCache, polls: PollSchedule,
                 sleep: Callable[[float], None] = time.sleep):
        self.client = client          # None: cache only (offline / no budget)
        self.cache = cache
        self.polls = polls
        self.sleep = sleep
        self._pins: Dict[Tuple[str, str], str] = {}   # (did, wid) -> microversion, for this run

    def _need_client(self, what: str) -> OnshapeClient:
        if self.client is None:
            raise TryAgainLater(f"{what} isn't cached and Onshape calls are off for this run")
        return self.client

    def resolve(self, link: OnshapeLink) -> OnshapeLink:
        """Workspace links: pin to the workspace's current microversion (once per run). Versions as they are."""
        if not link.is_workspace or link.mid:
            return link
        pin = self._pins.get((link.did, link.vid))
        if pin is None:
            client = self._need_client("the workspace's current state")
            data = client.get_json(f"{API}/documents/d/{link.did}/w/{link.vid}/currentmicroversion",
                                   purpose="pin_workspace")
            pin = (data or {}).get("microversion")
            if not pin:
                raise ExportError("Onshape didn't say which state the workspace is in")
            self._pins[(link.did, link.vid)] = pin
        return link.pinned(pin)

    def parts_list(self, link: OnshapeLink) -> List[Dict[str, Any]]:
        link = self.resolve(link)
        cached = self.cache.parts(link)
        if cached is not None:
            return cached
        client = self._need_client("the Part Studio's parts list")
        data = client.get_json(f"{API}/parts/d/{link.did}/{link.wvm}/{link.vid}/e/{link.eid}",
                               query={"withThumbnails": "false"}, purpose="parts_list")
        if not isinstance(data, list):
            raise ExportError("Onshape didn't return a parts list; is the link to a Part Studio tab?")
        self.cache.put_parts(link, data)
        return data

    def find_part(self, link: OnshapeLink, name: str) -> Dict[str, Any]:
        part = pick_part(self.parts_list(link), name)
        body = part.get("bodyType")
        if body not in (None, "solid"):
            raise ExportError(f"'{name}' is a {body} body, not a solid part")
        if not part.get("partId"):
            raise ExportError(f"Onshape returned no partId for '{name}'")
        return part

    def is_cached(self, link: OnshapeLink, name: str) -> bool:
        """Without spending a call: workspace links aren't pinned yet, so they count as not cached."""
        if link.is_workspace and not link.mid:
            return False
        parts = self.cache.parts(link)
        if parts is None:
            return False
        try:
            part = pick_part(parts, name)
        except ExportError:
            return False
        return bool(part.get("partId")) and self.cache.has_step(link, part["partId"])

    def export(self, link: OnshapeLink, name: str, part_key: Optional[str] = None) -> ExportedPart:
        link = self.resolve(link)
        part = self.find_part(link, name)
        pid = part["partId"]
        material = (part.get("material") or {}).get("displayName")
        if self.cache.has_step(link, pid):
            path = self.cache.step_path(link, pid)
            return ExportedPart(pid, material, path, sha256_file(path), from_cache=True, microversion=link.mid)

        client = self._need_client(f"the STEP file for '{name}'")
        tid = self.cache.translation(link, pid)
        if tid is None:
            started = client.post_json(
                f"{API}/partstudios/d/{link.did}/{link.wvm}/{link.vid}/e/{link.eid}/translations",
                {"formatName": "STEP", "partIds": pid, "storeInDocument": False},
                purpose="export_step", part_key=part_key)
            tid = started.get("id")
            if not tid:
                raise ExportError("Onshape didn't start the STEP export")
            self.cache.put_translation(link, pid, tid)

        for wait in self.polls.waits():
            self.sleep(wait)
            status = client.get_json(f"{API}/translations/{tid}", purpose="export_poll", part_key=part_key)
            state = status.get("requestState")
            if state == "DONE":
                ids = status.get("resultExternalDataIds") or []
                if not ids:
                    raise ExportError("Onshape finished the export but returned no file")
                data = client.get_bytes(f"{API}/documents/d/{link.did}/externaldata/{ids[0]}",
                                        purpose="export_download", part_key=part_key)
                path, sha = self.cache.put_step(link, pid, data)
                return ExportedPart(pid, material, path, sha, from_cache=False, microversion=link.mid)
            if state == "FAILED":
                self.cache.forget_translation(link, pid)
                raise ExportError(f"Onshape couldn't export '{name}': {status.get('failureReason', 'no reason given')}")
        raise TryAgainLater(f"Onshape's STEP export of '{name}' didn't finish yet; the next run picks it up")
