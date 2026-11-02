"""The whole enterprise's Onshape API count for the System card (2026-11-01, once the user was an enterprise admin).

Onshape's public API has no documented call for it. The enterprise admin's Developer page (Enterprise settings >
Developer) reads it from GET /api/v14/metrics/api/summary?startDate=<the cycle's start>&ownerId=<company id>
(seen in the page's requests, 2026-11-01): {"keyCount": calls with API keys, "clientCount": calls by OAuth apps,
"allocationInfo": {"apiLimit": 10000, "allocationStartDate", "allocationEndDate"}, ...}. That count covers
everyone's API use (AutoCAM, teammates' tools), unlike AutoCAM's own ledger.

One call every [panel] team_usage_hours, through the panel's limits. The count runs from startDate, so the first
read (which doesn't know the cycle yet) may take a second call once it learns the cycle's start. If AutoCAM's keys
aren't allowed to read it, it's tried again a week later and the System card leaves the line out.
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from autocam_core.hotfolder import write_atomic

from .onshape_reads import NoAccess, OutOfCalls, PanelOnshape, PanelOnshapeError

log = logging.getLogger("autocam.panel")

PATH = "/api/v14/metrics/api/summary"
TEAM_USAGE_FILE = "onshape_team_usage.json"   # in paths.state
REFUSED_RETRY_S = 7 * 24 * 3600.0
ERROR_RETRY_S = 6 * 3600.0                     # Onshape not answering


def _day(text: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(text)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso(day: datetime) -> str:
    return day.strftime("%Y-%m-%dT00:00:00.000Z")


def summary(body: Any) -> Optional[Dict[str, Any]]:
    """The parts of the response the card uses (None if it isn't the expected shape)."""
    if not isinstance(body, dict) or not isinstance(body.get("allocationInfo"), dict):
        return None
    alloc = body["allocationInfo"]
    try:
        count = int(body.get("keyCount") or 0) + int(body.get("clientCount") or 0)
        limit = int(alloc["apiLimit"])
    except (KeyError, TypeError, ValueError):
        return None
    start, end = _day(alloc.get("allocationStartDate")), _day(alloc.get("allocationEndDate"))
    if start is None or end is None:
        return None
    return {"count": count, "limit": limit, "cycle_start": start.strftime("%Y-%m-%d"),
            "cycle_end": end.strftime("%Y-%m-%d"), "counted_from": str(body.get("startDate") or "")[:10]}


class TeamUsage:
    def __init__(self, onshape: PanelOnshape, company_id: str, state_file: Path, every_hours: float,
                 clock: Callable[[], float] = time.time):
        self.onshape = onshape
        self.company_id = company_id
        self.state_file = Path(state_file)
        self.every_s = every_hours * 3600.0
        self.clock = clock

    def state(self) -> Dict[str, Any]:
        try:
            return dict(json.loads(self.state_file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}

    def _save(self, state: Dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.state_file, (json.dumps(state, indent=1) + "\n").encode("utf-8"))

    def _read(self, start: datetime) -> Optional[Dict[str, Any]]:
        body = self.onshape.raw_get(PATH, {"startDate": _iso(start), "ownerId": self.company_id}, "panel_api_usage",
                                    "read the team's API count")
        return summary(body)

    def tick(self) -> bool:
        """Reads the count if it's due. True when it read one."""
        if not self.company_id or self.every_s <= 0:
            return False
        state, now = self.state(), self.clock()
        if now - float(state.get("refused_at") or 0) < REFUSED_RETRY_S:
            return False
        if now - float(state.get("error_at") or 0) < ERROR_RETRY_S:
            return False
        if now - float(state.get("read_at") or 0) < self.every_s:
            return False
        today = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        known = _day(state.get("cycle_start") or "")
        if known is not None and state.get("cycle_end") and today >= _day(state["cycle_end"]):
            known = None                                     # a new cycle started: find its start again
        try:
            got = self._read(known or today - timedelta(days=366))
            if got is not None and got["counted_from"] != got["cycle_start"]:
                got = self._read(_day(got["cycle_start"]))   # counted from the wrong day: once a cycle
        except OutOfCalls:
            return False
        except NoAccess as e:
            log.info("panel: AutoCAM's Onshape keys can't read the team's API count (%s); trying again in a week", e)
            self._save({**state, "refused_at": now, "why": str(e)[:200]})
            return False
        except PanelOnshapeError as e:
            status = getattr(e.__cause__, "status", 0) or 0
            if 400 <= status < 500:                          # refused (401, 400...): asking hourly would waste calls
                log.info("panel: Onshape refused the team's API count (%s); trying again in a week", e)
                self._save({**state, "refused_at": now, "why": str(e)[:200]})
            else:
                log.warning("panel: reading the team's API count: %s", e)
                self._save({**state, "error_at": now})
            return False
        if got is None:
            log.warning("panel: the team's API count came back in a shape AutoCAM doesn't know")
            self._save({**state, "error_at": now})
            return False
        self._save({**got, "read_at": now})
        return True


def card_line(state_file: Path, now: datetime) -> str:
    """The System card's line for it ("" when there's no count, or it's over two days old)."""
    try:
        s = json.loads(Path(state_file).read_text(encoding="utf-8"))
        read_at = datetime.fromtimestamp(float(s["read_at"]), timezone.utc)
        count, limit, end = int(s["count"]), int(s["limit"]), _day(s["cycle_end"])
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    if end is None or now - read_at > timedelta(days=2):
        return ""
    share = f" ({count / limit:.0%})" if limit else ""
    return (f"Whole team's Onshape API calls (everyone's, from Onshape): {count:,} of {limit:,}{share} this year, "
            f"which ends {end:%b} {end.day}, {end.year}.")
