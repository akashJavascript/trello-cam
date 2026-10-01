"""Find a part by name in a Part Studio version and export it as STEP (brief, "Onshape").

Calls per uncached part: parts list (shared by every card in the same Part Studio version and
cached forever), translation POST, a few status polls, one download. A translation that doesn't
finish in time is remembered, so the next run polls it instead of starting (and paying for) a new one.

Response field names (partId, bodyType, material.displayName, requestState,
resultExternalDataIds) follow Onshape's docs; confirm them on the first recorded responses (M3).
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .cache import OnshapeCache, sha256_file
from .client import API, OnshapeClient
from .urls import OnshapeLink


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

    def _need_client(self, what: str) -> OnshapeClient:
        if self.client is None:
            raise TryAgainLater(f"{what} isn't cached and Onshape calls are off for this run")
        return self.client

    def parts_list(self, link: OnshapeLink) -> List[Dict[str, Any]]:
        cached = self.cache.parts(link)
        if cached is not None:
            return cached
        client = self._need_client("the Part Studio's parts list")
        data = client.get_json(f"{API}/parts/d/{link.did}/v/{link.vid}/e/{link.eid}",
                               query={"withThumbnails": "false"}, purpose="parts_list")
        if not isinstance(data, list):
            raise ExportError("Onshape didn't return a parts list; is the link to a Part Studio tab?")
        self.cache.put_parts(link, data)
        return data

    def find_part(self, link: OnshapeLink, name: str) -> Dict[str, Any]:
        matches = [p for p in self.parts_list(link) if p.get("name") == name]
        if not matches:
            raise ExportError(f"no part named '{name}' in that Part Studio version "
                              "(the card title must match the Onshape part name exactly)")
        if len(matches) > 1:
            raise ExportError(f"{len(matches)} parts are named '{name}' in that Part Studio; rename them so "
                              "the card title matches exactly one")
        part = matches[0]
        body = part.get("bodyType")
        if body not in (None, "solid"):
            raise ExportError(f"'{name}' is a {body} body, not a solid part")
        if not part.get("partId"):
            raise ExportError(f"Onshape returned no partId for '{name}'")
        return part

    def is_cached(self, link: OnshapeLink, name: str) -> bool:
        parts = self.cache.parts(link)
        if parts is None:
            return False
        matches = [p for p in parts if p.get("name") == name and p.get("partId")]
        return len(matches) == 1 and self.cache.has_step(link, matches[0]["partId"])

    def export(self, link: OnshapeLink, name: str, part_key: Optional[str] = None) -> ExportedPart:
        part = self.find_part(link, name)
        pid = part["partId"]
        material = (part.get("material") or {}).get("displayName")
        if self.cache.has_step(link, pid):
            path = self.cache.step_path(link, pid)
            return ExportedPart(pid, material, path, sha256_file(path), from_cache=True)

        client = self._need_client(f"the STEP file for '{name}'")
        tid = self.cache.translation(link, pid)
        if tid is None:
            started = client.post_json(
                f"{API}/partstudios/d/{link.did}/v/{link.vid}/e/{link.eid}/translations",
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
                return ExportedPart(pid, material, path, sha, from_cache=False)
            if state == "FAILED":
                self.cache.forget_translation(link, pid)
                raise ExportError(f"Onshape couldn't export '{name}': {status.get('failureReason', 'no reason given')}")
        raise TryAgainLater(f"Onshape's STEP export of '{name}' didn't finish yet; the next run picks it up")
