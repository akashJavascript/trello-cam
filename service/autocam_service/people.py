"""Who does what in the shop (2026-11-04, the mockup the user approved: "Can you build it").

The user's answers: work is assigned from the Onshape panel when it's sent ("Assign to") and from the phone page
(a name tag on each item, "All" on a group); assigning is soft (anyone can still start something); plates are
assigned here, not on Trello ("this is what the app is for"), and any work can be assigned.

state/people.json, on the laptop only (the repo is public and these are students' names):
- names: the shop's names, as first typed: from the phone's picker ("Add"), or the sender's name in the panel.
- phones: phone id -> the name that phone picked ("Who are you?"): "Yours" and "Me" on that phone mean that person.
- sheets: sheet card id -> who cuts it; plates: part card id -> who finishes it. Shafts and bought parts carry their
  own name ("who" on the item: shoplist.ShopList.assign).
A sheet or plate entry drops off KEEP_DAYS after it was set (its card is cut and gone by then).
"""

import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

from autocam_core.hotfolder import write_atomic

from .shoplist import DEVICE_RE, ListError

NAME_RE = re.compile(r"^[^\W\d_][\w .'-]{0,23}$")      # a first name, maybe an initial: "Maya", "Sam K."
CARD_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")          # a card id (Trello's are 24 hex digits)
KINDS = ("sheets", "plates")
MAX_NAMES = 80
KEEP_DAYS = 60


def clean_name(raw: Any) -> str:
    """A name as typed, spaces tidied; "" for none. Raises ListError if it isn't a name."""
    name = " ".join(str(raw or "").split())
    if name and not NAME_RE.match(name):
        raise ListError("A name is up to 24 letters, like Maya or Sam K.")
    return name


class People:
    def __init__(self, path: Path, now: Callable[[], datetime] = datetime.now):
        self.path = Path(path)
        self.now = now
        self._lock = threading.RLock()

    # ---- the file
    def _load(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        out: Dict[str, Any] = {"names": [n for n in data.get("names", []) if isinstance(n, str)],
                               "phones": dict(data.get("phones") or {})}
        cut = (self.now() - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
        for kind in KINDS:
            out[kind] = {k: v for k, v in dict(data.get(kind) or {}).items()
                         if isinstance(v, dict) and v.get("who") and str(v.get("at") or "") >= cut}
        return out

    def _save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, (json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))

    @staticmethod
    def _known(data: Dict[str, Any], name: str) -> str:
        """The name as it's already on the list (Maya for maya), or "" if it's new."""
        return next((n for n in data["names"] if n.casefold() == name.casefold()), "")

    def _add(self, data: Dict[str, Any], raw: Any) -> str:
        name = clean_name(raw)
        if not name:
            return ""
        known = self._known(data, name)
        if known:
            return known
        if len(data["names"]) >= MAX_NAMES:
            raise ListError("The name list is full. Ask a mentor to tidy state/people.json.")
        data["names"].append(name)
        return name

    # ---- reading
    def names(self) -> List[str]:
        with self._lock:
            return sorted(self._load()["names"], key=str.casefold)

    def me(self, device: str) -> str:
        """The name this phone picked, "" if none (or it's no longer on the list)."""
        with self._lock:
            data = self._load()
            return self._known(data, str(data["phones"].get(device) or "")) if device else ""

    def who(self, kind: str, card_id: str) -> str:
        return self.assigned(kind).get(card_id, "")

    def assigned(self, kind: str) -> Dict[str, str]:
        """card id -> name, for "sheets" (who cuts) or "plates" (who finishes)."""
        with self._lock:
            return {k: v["who"] for k, v in self._load()[kind].items()}

    # ---- changing
    def add(self, raw: Any) -> str:
        """Put a name on the list (the sender's, from the panel); returns it as it's kept ("" for none)."""
        with self._lock:
            data = self._load()
            name = self._add(data, raw)
            if name:
                self._save(data)
            return name

    def set_me(self, device: str, raw: Any) -> str:
        if not DEVICE_RE.match(device or ""):
            raise ListError("Bad request")
        with self._lock:
            data = self._load()
            name = self._add(data, raw)
            if not name:
                raise ListError("Pick your name.")
            data["phones"][device] = name
            self._save(data)
            return name

    def assign(self, kind: str, ids: Iterable[str], raw: Any) -> str:
        """Who cuts these sheets / finishes these plates; "" takes the name off. Returns the name as kept."""
        if kind not in KINDS:
            raise ListError("Bad request")
        ids = list(ids)
        if not ids or any(not isinstance(i, str) or not CARD_RE.match(i) for i in ids):
            raise ListError("Bad request")
        with self._lock:
            data = self._load()
            name = self._add(data, raw)
            for card_id in ids:
                if name:
                    data[kind][card_id] = {"who": name, "at": self.now().isoformat(timespec="seconds")}
                else:
                    data[kind].pop(card_id, None)
            self._save(data)
            return name
