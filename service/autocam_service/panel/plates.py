"""The phone page's Plates tab (2026-11-04, the assigning mockup): the sheets in Ready to cut, who cuts each, and
the plates on it, who finishes each. Sheets cut today stay listed (their plates still need finishing).

From AutoCAM's sheet cards (state/sheet_cards.json: which part cards are on a sheet, how many of each since
2026-11-04, its material and label) and Trello: the Ready to cut list (in its order: the machine's queue) and the
part cards On a sheet, read at most every CACHE_S however many phones are open, and only while one shows this tab.
Names come from people.py. No Onshape calls.
"""

import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..cards import QTY_LINE_RES, TITLE_QTY_RE, _plain
from ..config import Config
from ..people import People
from ..run_state import RunStore
from ..sheet_cards import thickness_label
from ..tracker.base import Tracker

log = logging.getLogger("autocam.panel")

CACHE_S = 30.0


def _local_day(utc: str) -> str:
    """"2026-11-04T01:34:02Z" -> the laptop's local date of it ("2026-11-03")."""
    try:
        return datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone().date().isoformat()
    except ValueError:
        return ""


class PlatesView:
    def __init__(self, tracker: Tracker, cfg: Config, store: RunStore, people: People,
                 material_label: Callable[[str], str], clock: Callable[[], float] = time.monotonic,
                 today: Callable[[], str] = lambda: datetime.now().date().isoformat()):
        self.t = tracker
        self.cfg = cfg
        self.store = store
        self.people = people
        self.material_label = material_label          # material key -> "6061 aluminum"
        self.clock = clock
        self.today = today
        self._lock = threading.RLock()
        self._read: Optional[Tuple[float, List[str], Dict[str, str]]] = None
        self._parts: Dict[str, Tuple[str, int]] = {}  # part card id -> (name, qty): kept, for sheets cut since

    def _board(self) -> Tuple[List[str], Dict[str, str]]:
        """(the Ready to cut card ids in order, their names), read at most every CACHE_S."""
        with self._lock:
            if self._read is not None and self.clock() - self._read[0] < CACHE_S:
                return self._read[1], self._read[2]
            ready = self.t.list_cards("ready_to_cut")
            for card in self.t.list_cards(self.cfg.trello.targets["part_nested"]):
                self._parts[card.id] = self._part(card.name, card.desc)
            self._read = (self.clock(), [c.id for c in ready], {c.id: c.name for c in ready})
            return self._read[1], self._read[2]

    @staticmethod
    def _part(title: str, desc: str) -> Tuple[str, int]:
        title = " ".join((title or "").split())
        m = TITLE_QTY_RE.match(title)
        name = m["name"] if m and m["name"] else title
        found = [v.strip() for rx in QTY_LINE_RES for v in rx.findall(_plain(desc or ""))]
        found += [m["qty"]] if m and m["name"] else []
        return name, int(found[0]) if found and found[0].isdigit() else 0

    def _name(self, part_id: str) -> Tuple[str, int]:
        hit = self._parts.get(part_id)
        if hit is None:                               # moved to Cut before this page saw it: one read, kept
            try:
                card = self.t.get_card(part_id)
                hit = self._part(card.name, card.desc)
            except Exception as e:  # noqa: BLE001 - the sheet still shows
                log.info("panel: reading part card %s: %s", part_id, e)
                hit = ("A part", 0)
            with self._lock:
                self._parts[part_id] = hit
        return hit

    def _sheet(self, sheet_id: str, info: Dict[str, Any], title: str, cut: bool,
               cutters: Dict[str, str], finishers: Dict[str, str]) -> Dict[str, Any]:
        material = str(info.get("material") or "")
        stock = " ".join(x for x in (self.material_label(material) if material else "",
                                     thickness_label(float(info["thickness_in"])) if info.get("thickness_in") else "") if x)
        offcut = re.search(r"offcut #(\d+)", title or "")
        minutes = re.search(r"(\d+) min", title or "")
        sub = [str(info.get("label") or info.get("run") or "")]
        sub += [f"offcut #{offcut.group(1)}"] if offcut else []
        sub += [f"{minutes.group(1)} min"] if minutes and not cut else []
        counts = info.get("counts") or {}
        plates = []
        for part_id in info.get("parts") or []:
            name, qty = self._name(part_id)
            plates.append({"id": part_id, "name": name, "qty": int(counts.get(part_id) or qty or 0),
                           "who": finishers.get(part_id, "")})
        return {"id": sheet_id, "title": stock or (title or "A sheet"), "sub": " · ".join(x for x in sub if x),
                "rush": (title or "").upper().startswith("RUSH"), "cut": cut, "who": cutters.get(sheet_id, ""),
                "plates": plates}

    def view(self) -> Dict[str, Any]:
        """{"sheets": [...]}: Ready to cut in the machine's order, then the ones cut today."""
        order, titles = self._board()
        reg = self.store.sheet_cards()
        cutters, finishers = self.people.assigned("sheets"), self.people.assigned("plates")
        out = []
        for sheet_id in order:
            info = reg.get(sheet_id)
            if info is None or not info.get("cuttable") or info.get("cut") or info.get("archived"):
                continue                              # not one of AutoCAM's sheets (or not a program to cut)
            out.append(self._sheet(sheet_id, info, titles.get(sheet_id, ""), False, cutters, finishers))
        today = self.today()
        cut_today = sorted(((sid, info) for sid, info in reg.items() if info.get("cut") and not info.get("archived")
                            and _local_day(str(info.get("cut_utc") or "")) == today),
                           key=lambda x: str(x[1].get("cut_utc") or ""), reverse=True)
        for sheet_id, info in cut_today:
            out.append(self._sheet(sheet_id, info, "", True, cutters, finishers))
        return {"sheets": out}
