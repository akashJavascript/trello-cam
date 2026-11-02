"""Onshape tells AutoCAM when a model changes (2026-11-01; the user is an enterprise admin), so a part on a sheet that
changed after it was CAM'd is found within minutes, not at the next periodic check (precut.py).

- One webhook for the whole company: Onshape lets a company admin's keys register for every company event. Event
  onshape.model.lifecycle.changed, collapseEvents on, calling https://<[panel] public_url>/api/onshape-hook/<secret>
  (AUTOCAM_WEBHOOK_SECRET in .env: the address is the password, since Onshape can't send the panel's key). The
  service registers it when it starts and makes sure it's still there once a day: a call or two a day; the events
  themselves cost nothing.
- Each event names the document, workspace and element that changed. ChangeInbox keeps the newest time per element
  (state/panel_changes.json); PrecutWatch checks the open sheets with parts from it once it's been quiet QUIET_S
  (students edit in bursts) and then forgets it. Elements on no open sheet are forgotten unchecked.
"""

import hmac
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from autocam_core.hotfolder import write_atomic

from .onshape_reads import PanelOnshape, PanelOnshapeError

log = logging.getLogger("autocam.panel")

EVENT = "onshape.model.lifecycle.changed"
QUIET_S = 300.0              # an element is checked once nobody has changed it for this long
MAX_PENDING = 500            # elements kept at most (anything past this is dropped: only a flood gets there)
CHECK_EVERY_S = 24 * 3600.0  # the webhook is checked to be there this often
RETRY_S = 3600.0             # after a failure (no admin keys, say), tried again this often: a call each time
HOOK_PREFIX = "/api/onshape-hook/"
ID_RE = re.compile(r"^[0-9a-f]{24}$")


class ChangeInbox:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.Lock()

    def _all(self) -> Dict[str, float]:
        try:
            return {k: float(v) for k, v in json.loads(self.path.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save(self, data: Dict[str, float]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, (json.dumps(data, indent=1, sort_keys=True) + "\n").encode("utf-8"))

    def note(self, body: Dict[str, Any]) -> bool:
        """One event from Onshape. True if it was a model change worth keeping."""
        if body.get("event") != EVENT:
            return False
        did, wid, eid = (str(body.get(k) or "") for k in ("documentId", "workspaceId", "elementId"))
        if not ID_RE.match(did) or not ID_RE.match(wid) or (eid and not ID_RE.match(eid)):
            return False
        with self._lock:
            data = self._all()
            if len(data) >= MAX_PENDING and f"{did}/{wid}/{eid}" not in data:
                return False
            data[f"{did}/{wid}/{eid}"] = self.clock()
            self._save(data)
        return True

    def quiet(self, quiet_s: float = QUIET_S) -> Set[Tuple[str, str, str]]:
        """Elements changed and left alone for quiet_s: (document, workspace, element; "" when Onshape didn't say)."""
        now = self.clock()
        return {tuple(k.split("/")) for k, at in self._all().items() if now - at >= quiet_s}   # type: ignore[misc]

    def forget(self, keys) -> None:
        with self._lock:
            data = self._all()
            for k in keys:
                data.pop("/".join(k), None)
            self._save(data)


def touches(changed: Set[Tuple[str, str, str]], did: str, wid: str, eid: str) -> bool:
    return (did, wid, eid) in changed or (did, wid, "") in changed


class WebhookSetup:
    """Registers AutoCAM's company webhook if it isn't there, at most once a day (state/panel_webhook.json)."""

    def __init__(self, onshape: PanelOnshape, public_url: str, secret: str, state_path: Path, company_id: str = "",
                 clock: Callable[[], float] = time.time):
        self.onshape = onshape
        self.url = f"{public_url.rstrip('/')}{HOOK_PREFIX}{secret}"
        self.company_id = company_id
        self.path = Path(state_path)
        self.clock = clock

    def _state(self) -> Dict[str, Any]:
        try:
            return dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}

    def _save(self, state: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, (json.dumps(state, indent=1, sort_keys=True) + "\n").encode("utf-8"))

    def active(self) -> bool:
        """Seen registered within the last two days: the periodic sheet checks can be rarer."""
        state = self._state()
        return bool(state.get("id")) and self.clock() - float(state.get("checked_at") or 0) < 2 * CHECK_EVERY_S

    def _company(self, state: Dict[str, Any]) -> str:
        if self.company_id:
            return self.company_id
        if state.get("company_id"):
            return str(state["company_id"])
        companies = self.onshape.companies()
        named = [c for c in companies if "5940" in f"{c.get('name', '')} {c.get('domainPrefix', '')}"]
        pick = named if len(named) == 1 else companies if len(companies) == 1 else []
        if not pick:
            raise PanelOnshapeError(f"which company? Onshape lists {len(companies)}; set [panel] company_id")
        return str(pick[0].get("id") or "")

    def ensure(self) -> str:
        """Once a day: AutoCAM's webhook is registered (it registers it if not). Returns what it did."""
        state = self._state()
        now = self.clock()
        if state.get("checked_at") is not None and now - float(state["checked_at"]) < CHECK_EVERY_S:
            return "checked recently"
        if state.get("error") and now - float(state.get("tried_at") or 0) < RETRY_S:
            return "failed recently"
        state["tried_at"] = now
        try:
            company = self._company(state)
            state["company_id"] = company
            mine = [w for w in self.onshape.webhooks(company) if w.get("url") == self.url]
            if mine:
                state.update(id=str(mine[0].get("id") or ""), checked_at=self.clock(), error="")
                what = "already registered"
            else:
                made = self.onshape.create_webhook({
                    "url": self.url, "companyId": company, "events": [EVENT], "options": {"collapseEvents": True},
                    "name": "AutoCAM", "description": "AutoCAM: a part on a sheet changed after it was CAM'd"})
                state.update(id=str(made.get("id") or ""), checked_at=self.clock(), error="")
                what = "registered"
                log.info("panel: Onshape webhook registered (%s)", state["id"])
        except PanelOnshapeError as e:
            state["error"] = str(e)
            self._save(state)
            log.warning("panel: Onshape webhook not set up: %s", e)
            return f"failed: {e}"
        self._save(state)
        return what


def secret_ok(path: str, secret: str) -> bool:
    given = path[len(HOOK_PREFIX):] if path.startswith(HOOK_PREFIX) else ""
    return bool(secret) and hmac.compare_digest(given.encode("utf-8"), secret.encode("utf-8"))
