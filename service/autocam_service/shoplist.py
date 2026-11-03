"""What the shop's two lists share (shafts.py: shafts to make; bought.py: bought parts to put in the bin; 2026-11-02).

Each is one JSON file in state/, every item sent from the Onshape panel, newest last, with a random id. Finished ones
(a shaft done, a part in the bin) drop off after `keep_days`. Reading an assembly again marks its open items that
aren't in it any more "Changed in CAD". Phones are a random id each (no names): what a phone did, only it can undo,
the same day.
"""

import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from autocam_core.hotfolder import write_atomic

DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
ID_RE = re.compile(r"^[0-9a-f]{12}$")


class ListError(Exception):
    """Something the person can fix, said plainly. status: the HTTP status the panel answers with."""

    def __init__(self, message: str, status: int = 400, kind: str = ""):
        super().__init__(message)
        self.status = status
        self.kind = kind


class ShopList:
    FILE_KEY = "items"                       # the list's name in its file
    NOUN = "item"                            # "That shaft isn't on the list any more."
    ACTIVE: Tuple[str, ...] = ()             # still to do
    FINISHED = "done"
    FINISHED_AT = "done_at"

    def __init__(self, path: Path, keep_days: int = 14, now: Callable[[], datetime] = datetime.now):
        self.path = Path(path)
        self.keep_days = keep_days
        self.now = now                       # the laptop's local time: "today" is the shop's day
        self._lock = threading.RLock()

    # ---- the file
    def _load(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [x for x in data.get(self.FILE_KEY, []) if isinstance(x, dict)] if isinstance(data, dict) else []

    def _save(self, items: List[Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, (json.dumps({self.FILE_KEY: items}, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))

    def _pruned(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cut = (self.now() - timedelta(days=self.keep_days)).isoformat(timespec="seconds")
        return [x for x in items if x.get("status") != self.FINISHED or (x.get(self.FINISHED_AT) or "") >= cut]

    def all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return self._pruned(self._load())

    def _change(self, fn: Callable[[List[Dict[str, Any]]], Any]) -> Any:
        with self._lock:
            items = self._pruned(self._load())
            result = fn(items)
            self._save(items)
            return result

    def _find(self, items: List[Dict[str, Any]], sid: Any) -> Dict[str, Any]:
        if not isinstance(sid, str) or not ID_RE.match(sid):
            raise ListError("Bad request")
        for x in items:
            if x.get("id") == sid:
                return x
        raise ListError(f"That {self.NOUN} isn't on the list any more.", status=404, kind="gone")

    def _stamp(self) -> str:
        return self.now().isoformat(timespec="seconds")

    def _today(self, stamp: Optional[str]) -> bool:
        return bool(stamp) and str(stamp)[:10] == self.now().date().isoformat()

    @staticmethod
    def _clock(stamp: str) -> str:
        """"2026-11-03T16:10:00" -> "4:10 PM"."""
        return datetime.fromisoformat(stamp).strftime("%I:%M %p").lstrip("0")

    # ---- from the panel
    def active(self, source: str) -> Optional[Dict[str, Any]]:
        """The open item for this assembly row, if there is one."""
        return next((x for x in reversed(self.all()) if x.get("source") == source and x.get("status") in self.ACTIVE), None)

    def latest(self, source: str) -> Optional[Dict[str, Any]]:
        """The newest item sent for this assembly row (finished ones too, until they drop off)."""
        return next((x for x in reversed(self.all()) if x.get("source") == source), None)

    def mark_changed(self, assembly: str, present: Iterable[str]) -> int:
        """The assembly was just read: its open items that aren't in it any more (a shaft's length changed, a part
        was taken out) get "Changed in CAD"; ones back in it lose it. Returns how many changed state."""
        here = set(present)

        def go(items):
            n = 0
            for x in items:
                if x.get("assembly", {}).get("key") != assembly or x.get("status") not in self.ACTIVE:
                    continue
                gone = x.get("source") not in here
                if bool(x.get("changed")) != gone:
                    x["changed"] = gone
                    n += 1
            return n
        with self._lock:
            items = self._pruned(self._load())
            n = go(items)
            if n:
                self._save(items)
            return n
