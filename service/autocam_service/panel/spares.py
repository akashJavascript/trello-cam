"""Spares on the phone page (2026-11-04, the mockup the user approved: a count per send, robot parts first, a
Spares line in the Part Studio too).

A spares card is a part card with the Spare label (board.PanelBoard.add_spares / spares_from): never the part's own
card, nested after the robot parts (the pipeline gives spares only the room robot parts leave), marked "spare" on
the sheet card. The Onshape panel sends them from a Part Studio or an assembly; the phone sends them for any plate
AutoCAM has nested this season, from that plate's own card (no Onshape: the phone can't see inside it).

Here: the plates the phone offers (one row per part, its newest card, grouped by the stock its sheets were cut
from), the spares on the way (the Plates tab's Spares section) and sending. From the part cards the panel already
reads (board.part_cards, cached) and AutoCAM's sheet cards (state/sheet_cards.json). No Onshape calls.
"""

import re
import threading
import time
from typing import Any, Callable, Dict, List, Tuple

from ..cards import MAX_QTY, TITLE_QTY_RE
from ..own_programs import OWN_PROGRAM_RE
from ..run_state import RunStore
from ..sheet_cards import thickness_label
from .board import PanelBoard, PartCard, created

CACHE_S = 30.0               # the Spares section is worked out at most this often (the phone asks every 10 s)
MAX_SEND = 60                # plates in one Send spares


class SpareError(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.status = 400


def _natural(name: str):
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", name or "")]


def _identity(pc: PartCard) -> Tuple[str, str, str, str]:
    return pc.did, pc.eid, pc.configuration, pc.part_id or pc.name_key


def _name(pc: PartCard) -> str:
    title = " ".join(pc.card.name.split())
    m = TITLE_QTY_RE.match(title)
    return m["name"] if m and m["name"] else title


class SparesView:
    def __init__(self, board: PanelBoard, store: RunStore, material_label: Callable[[str], str],
                 now: Callable[[], float] = time.time, clock: Callable[[], float] = time.monotonic):
        self.board = board
        self.store = store
        self.material_label = material_label          # material key -> "6061 aluminum"
        self.now = now
        self.clock = clock
        self._lock = threading.Lock()
        self._shown: Tuple[float, List[Dict[str, Any]]] = (-1e18, [])

    def _sheets(self) -> Dict[str, List[Dict[str, Any]]]:
        """Part card id -> every sheet card it was on (archived ones too: cut and gone, still its stock)."""
        out: Dict[str, List[Dict[str, Any]]] = {}
        for sid, info in self.store.sheet_cards().items():
            for card_id in info.get("parts") or []:
                out.setdefault(card_id, []).append(info)
        return out

    def _stock(self, infos: List[Dict[str, Any]]) -> str:
        info = next((i for i in reversed(infos) if i.get("material") and i.get("thickness_in")), None)
        if info is None:
            return ""
        key = str(info["material"])
        return f"{self.material_label(key)} {thickness_label(float(info['thickness_in']))}"

    def choices(self) -> Dict[str, Any]:
        """{"choices": [{"title": "6061 aluminum 1/4in", "rows": [{"id", "name", "sub"}]}]}: every plate AutoCAM has
        nested (its newest own card on a sheet or cut), once, with what its spares say."""
        cards = self.board.part_cards()
        sheets = self._sheets()
        newest: Dict[Tuple[str, str, str, str], PartCard] = {}
        for pc in cards:
            if pc.spare or pc.status not in ("sheet", "cut") or OWN_PROGRAM_RE.search(pc.card.desc or ""):
                continue
            if not self._stock(sheets.get(pc.card.id, [])):
                continue                              # never nested by AutoCAM: nothing says what it's cut from
            key = _identity(pc)
            if key not in newest or (created(pc.card.id), pc.order) > (created(newest[key].card.id), newest[key].order):
                newest[key] = pc
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for key, pc in newest.items():
            spares = self.board.for_part(pc.did, pc.eid, pc.part_id or "", pc.name_key, cards, cfg=pc.configuration,
                                         spare=True)
            groups.setdefault(self._stock(sheets[pc.card.id]), []).append(
                {"id": pc.card.id, "name": _name(pc), "sub": self.board.spare_line(spares)})
        return {"choices": [{"title": title, "rows": sorted(rows, key=lambda r: _natural(r["name"]))}
                            for title, rows in sorted(groups.items(), key=lambda g: _natural(g[0]))]}

    def on_the_way(self) -> List[Dict[str, Any]]:
        """The spares cards still going (not cut yet): newest first. Worked out at most every CACHE_S (sending spares
        starts it again). Cut ones aren't listed: they're in the spares box (until 2026-11-08 they stayed a week)."""
        with self._lock:
            if self.clock() - self._shown[0] < CACHE_S:
                return self._shown[1]
        shown = self._on_the_way()
        with self._lock:
            self._shown = (self.clock(), shown)
        return shown

    def _on_the_way(self) -> List[Dict[str, Any]]:
        out = []
        for pc in self.board.part_cards():
            if not pc.spare or pc.status == "cut":    # cut: in the spares box, not on the way (the user, 2026-11-08)
                continue
            if pc.status == "sheet":
                labels = [str(e.get("label") or "") for _, e in self.board.sheets_of(pc.card.id)]
                where = f"On {labels[-1]}" if labels and labels[-1] else "On a sheet"
            else:
                where = {"drafts": "In Drafts", "queued": "Waiting to nest", "back": "Sent back"}[pc.status]
            out.append({"id": pc.card.id, "name": _name(pc), "qty": pc.qty, "where": where, "cut": pc.status == "cut",
                        "at": created(pc.card.id), "order": pc.order})
        out.sort(key=lambda x: (x["cut"], -x["at"], -x["order"]))
        return [{k: v for k, v in x.items() if k not in ("at", "order")} for x in out]

    def send(self, items: Any, sender: str) -> Dict[str, Any]:
        """items: [{"id": a part's own card id (from choices), "n": how many spares}]."""
        if not isinstance(items, list) or not items or len(items) > MAX_SEND:
            raise SpareError("Pick how many spares.")
        cards = {pc.card.id: pc for pc in self.board.part_cards(fresh=True) if not pc.spare}
        results, sent = [], 0
        for item in items:
            card_id = str((item or {}).get("id") or "") if isinstance(item, dict) else ""
            n = item.get("n") if isinstance(item, dict) else None
            if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_QTY:
                raise SpareError(f"Spares are a whole number from 1 to {MAX_QTY}.")
            pc = cards.get(card_id)
            if pc is None:
                results.append({"id": card_id, "sent": False, "why": "isn't on the board any more"})
                continue
            card = self.board.spares_from(pc, n, sender)
            with self._lock:
                self._shown = (-1e18, [])                 # the Spares section shows it at once
            results.append({"id": card_id, "name": _name(pc), "sent": True, "n": n, "url": card.url})
            sent += n
        return {"results": results, "sent": sent}
