"""Onshape tells AutoCAM when a model changes (2026-11-01; the user is an enterprise admin), so a part on a sheet that
changed after it was CAM'd is found within minutes, not at the next periodic check (precut.py).

- A webhook per document workspace with parts on an open sheet (a company-wide one is refused for model changes),
  event onshape.model.lifecycle.changed, collapseEvents on, calling https://<[panel] public_url>/api/onshape-hook/<secret>
  (AUTOCAM_WEBHOOK_SECRET in .env: the address is the password, since Onshape can't send the panel's key). One call
  to make each, a call a day to check they're there, one to remove each a week after it's needed; the events
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
RETRY_S = 3600.0             # after a failure, tried again this often: a call each time
UNUSED_S = 7 * 24 * 3600.0   # a webhook no open sheet needs is removed after this long
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
    """A webhook per document with parts on an open sheet (Onshape refuses model changes for a whole company: "lifecycle.changed
    event is not supported at enterprise level", 2026-11-01), filtered to that document and workspace. Made when a
    sheet first needs it (a few per tick), checked to be there once a day, removed a week after no open sheet needs
    it; one call each (state/panel_webhook.json)."""

    def __init__(self, onshape: PanelOnshape, public_url: str, secret: str, state_path: Path,
                 clock: Callable[[], float] = time.time, per_tick: int = 3):
        self.onshape = onshape
        self.url = f"{public_url.rstrip('/')}{HOOK_PREFIX}{secret}"
        self.path = Path(state_path)
        self.clock = clock
        self.per_tick = per_tick

    def _state(self) -> Dict[str, Any]:
        try:
            state = dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            state = {}
        state.setdefault("hooks", {})
        return state

    def _save(self, state: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, (json.dumps(state, indent=1, sort_keys=True) + "\n").encode("utf-8"))

    def covered(self, did: str, wid: str) -> bool:
        """That workspace's changes reach AutoCAM: its sheets need the periodic check only rarely."""
        return bool(self._state()["hooks"].get(f"{did}/{wid}", {}).get("id"))

    def ensure(self, wanted: Set[Tuple[str, str]]) -> str:
        """The documents' workspaces (did, wid) with parts on open sheets each have a webhook. Returns what it did."""
        state = self._state()
        hooks: Dict[str, Dict[str, Any]] = state["hooks"]
        now = self.clock()
        if state.get("error") and now - float(state.get("tried_at") or 0) < RETRY_S:
            return "failed recently"
        done = []
        try:
            if hooks and now - float(state.get("listed_at") or 0) >= CHECK_EVERY_S:
                there = {str(w.get("id")) for w in self.onshape.webhooks()}
                for key in [k for k, h in hooks.items() if h.get("id") not in there]:
                    hooks.pop(key)                               # gone (Onshape drops failing ones): made again
                state["listed_at"] = now
            for key, h in list(hooks.items()):
                if tuple(key.split("/")) in wanted:
                    h["used_at"] = now
                elif now - float(h.get("used_at") or 0) > UNUSED_S:
                    self.onshape.delete_webhook(str(h["id"]))
                    hooks.pop(key)
                    done.append(f"removed {key}")
            for did, wid in sorted(wanted):
                if f"{did}/{wid}" in hooks or len(done) >= self.per_tick:
                    continue
                made = self.onshape.create_webhook({
                    "url": self.url, "events": [EVENT], "options": {"collapseEvents": True},
                    "filter": f"{{$DocumentId}} = '{did}' && {{$WorkspaceId}} = '{wid}'",
                    "name": "AutoCAM", "description": "AutoCAM: a part on a sheet changed after it was CAM'd"})
                hooks[f"{did}/{wid}"] = {"id": str(made.get("id") or ""), "made_at": now, "used_at": now}
                done.append(f"made {did}/{wid}")
                log.info("panel: Onshape webhook for %s/%s registered", did, wid)
            state["error"] = ""
        except PanelOnshapeError as e:
            state.update(error=str(e), tried_at=now)
            self._save(state)
            log.warning("panel: Onshape webhook: %s", e)
            return f"failed: {e}"
        self._save(state)
        return "; ".join(done) or "nothing to do"


def secret_ok(path: str, secret: str) -> bool:
    given = path[len(HOOK_PREFIX):] if path.startswith(HOOK_PREFIX) else ""
    return bool(secret) and hmac.compare_digest(given.encode("utf-8"), secret.encode("utf-8"))
