"""Assemblies AutoCAM keeps an eye on without anyone opening the panel (2026-11-02; the user: fix the two gaps).

1. **The shop's lists** (shafts.py, bought.py). Each shaft or bought part on a list came from an assembly row. When
   the assembly changes, a shaft whose length changed (or a part taken out) gets "Changed in CAD", and a bought part
   whose count changed gets "CAD has 6". Before, that happened only when someone opened that assembly in the panel.
2. **Parts from another document's saved version.** A card sent from an assembly for a part the assembly takes from
   another document's version links to that version. When the assembly is updated to a newer version of that
   document, the card follows it: its link moves to the new version (with a comment), and if the part's shape really
   changed while it's on a sheet not cut yet, it gets CAD changed like a part from a workspace (board.flag_changed),
   so "Nest the new version" nests the new one. Before, a version counted as never changing. A card already cut is
   left alone (what was cut is the old version), and so is a link changed by hand.

How: one read of the assembly's parts list (its BOM, one call) when Onshape's webhook says the assembly's document
changed (the same webhooks as precut.py; the documents watched here are added to them), at most once per
[panel] assembly_check_minutes per assembly. With no change reported: every change_check_hours, or
webhook_fallback_hours while the webhook works. An assembly is first read change_check_hours after it's first
watched (it was just read when something was sent from it). A moved version's shape compare is two bodydetails
reads, kept for good (a version never changes). The panel's calls (ledger run "panel"); out of calls, it's read again
later. State: state/panel_assemblies.json.
"""

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from autocam_core.hotfolder import write_atomic

from ..config import Config
from .board import PanelBoard
from .geometry import differences, shape_key
from .onshape_reads import OutOfCalls, PanelOnshape, PanelOnshapeError, row_key

log = logging.getLogger("autocam.panel")

ASM_URL_RE = re.compile(r"/documents/([0-9a-f]{24})/([wv])/([0-9a-f]{24})/e/([0-9a-f]{24})")
FORGET_CARD_S = 180 * 24 * 3600.0     # a card still followed after this long is dropped (it was cut or left)


def assembly_of(url: str) -> Optional[Tuple[str, str, str, str]]:
    """(did, wvm, wvmid, eid) from an item's assembly link ("https://<host>/documents/<d>/w/<w>/e/<e>")."""
    m = ASM_URL_RE.search(url or "")
    return (m.group(1), m.group(2), m.group(3), m.group(4)) if m else None


class AssemblyWatch:
    def __init__(self, onshape: PanelOnshape, board: PanelBoard, cfg: Config, path: Path, shafts: Any = None,
                 parts: Any = None, hooks: Any = None, clock: Callable[[], float] = time.time):
        self.onshape = onshape
        self.board = board
        self.cfg = cfg
        self.path = Path(path)
        self.shafts = shafts                  # shafts.ShaftList (None: off)
        self.parts = parts                    # bought.PartList (None: off)
        self.hooks = hooks                    # hooks.WebhookSetup: whether an assembly's changes reach AutoCAM
        self.clock = clock
        self._lock = threading.RLock()

    # ---- the file
    def _state(self) -> Dict[str, Any]:
        try:
            state = dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            state = {}
        state.setdefault("cards", {})
        state.setdefault("asms", {})
        return state

    def _save(self, state: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, (json.dumps(state, indent=1, sort_keys=True) + "\n").encode("utf-8"))

    # ---- from the panel
    def remember(self, card_id: str, asm: Dict[str, str], row: Dict[str, Any]) -> bool:
        """A card just sent from an assembly (asm: did, wvm, wvmid, eid, name; in a workspace) for a part from another
        document's saved version (row: its BOM row): followed from now on."""
        if asm.get("wvm") != "w" or row.get("wvm") != "v" or not card_id:
            return False
        with self._lock:
            state = self._state()
            state["cards"][card_id] = {
                "asm": {"did": asm["did"], "wid": asm["wvmid"], "eid": asm["eid"], "name": asm.get("name") or ""},
                "did": row["did"], "vid": row["wvmid"], "eid": row["eid"], "pid": row["pid"],
                "configuration": row.get("configuration") or "", "since": self.clock()}
            self._save(state)
        return True

    # ---- what's watched
    def watched(self) -> Dict[str, Dict[str, str]]:
        """"did/wid/eid" -> {did, wid, eid, name}: every assembly (in a workspace) with shafts or bought parts still
        open on the shop's lists, or cards following a saved version."""
        out: Dict[str, Dict[str, str]] = {}
        for lst in (self.shafts, self.parts):
            if lst is None:
                continue
            for x in lst.all():
                if x.get("status") not in lst.ACTIVE:
                    continue
                a = assembly_of(str(x.get("assembly", {}).get("url") or ""))
                if a and a[1] == "w":
                    out.setdefault(f"{a[0]}/{a[2]}/{a[3]}", {"did": a[0], "wid": a[2], "eid": a[3],
                                                             "name": x.get("assembly", {}).get("name", "")})
        for c in self._state()["cards"].values():
            a = c["asm"]
            out.setdefault(f"{a['did']}/{a['wid']}/{a['eid']}", dict(a))
        return out

    def wanted(self) -> Set[Tuple[str, str]]:
        """The documents' workspaces whose changes should reach AutoCAM (webhooks, hooks.py)."""
        return {(a["did"], a["wid"]) for a in self.watched().values()}

    # ---- the background read
    def tick(self, told: Set[Tuple[str, str, str]] = frozenset(), now: Optional[float] = None) -> int:
        """Read the watched assemblies that are due (told: what Onshape's webhook said changed, quiet now). Returns
        how many cards moved to a newer version."""
        now = self.clock() if now is None else now
        panel = self.cfg.panel
        watched = self.watched()
        moved = 0
        with self._lock:
            state = self._state()
            asms: Dict[str, Dict[str, Any]] = state["asms"]
            for key in [k for k in asms if k not in watched]:
                asms.pop(key)
            for key, a in sorted(watched.items()):
                st = asms.setdefault(key, {"read_at": now, "dirty": False})   # just read by the panel's send
                if any(d == a["did"] and w == a["wid"] for d, w, _ in told):
                    st["dirty"] = True
                live = self.hooks is not None and self.hooks.covered(a["did"], a["wid"])
                every = (panel.webhook_fallback_hours if live else panel.change_check_hours) * 3600
                since = now - float(st.get("read_at") or 0)
                if not ((st.get("dirty") and since >= panel.assembly_check_minutes * 60) or since >= every):
                    continue
                try:
                    rows = self.onshape.bom(a["did"], "w", a["wid"], a["eid"], fresh=True)
                    st.update(read_at=now, dirty=False)
                    moved += self._apply(a, rows, state)
                except OutOfCalls:
                    st["dirty"] = True                 # read again once there are calls
                    break
                except PanelOnshapeError as e:
                    st.update(read_at=now)             # e.g. no access any more: tried again on the slow schedule
                    log.info("panel: reading the %s assembly again: %s", a.get("name") or key, e)
            for card_id in [k for k, c in state["cards"].items() if now - float(c.get("since") or now) > FORGET_CARD_S]:
                state["cards"].pop(card_id)
            self._save(state)
        return moved

    def _apply(self, a: Dict[str, str], rows: List[Dict[str, Any]], state: Dict[str, Any]) -> int:
        """The assembly as it is now: the shop's lists marked, the cards following it moved to its versions."""
        asm = f"{a['did']}/{a['eid']}"
        present = {f"{asm}|{row_key(r)}": max(1, int(r.get("qty") or 0)) for r in rows if not r.get("standard")}
        if self.shafts is not None:
            n = self.shafts.mark_changed(asm, list(present))
            if n:
                log.info("panel: %d shafts of %s changed in CAD", n, a.get("name") or asm)
        if self.parts is not None:
            n = self.parts.mark_read(asm, present)
            if n:
                log.info("panel: %d bought parts of %s changed in CAD", n, a.get("name") or asm)
        mine = {cid: c for cid, c in state["cards"].items()
                if (c["asm"]["did"], c["asm"]["wid"], c["asm"]["eid"]) == (a["did"], a["wid"], a["eid"])}
        if not mine:
            return 0
        cards = {pc.card.id: pc for pc in self.board.part_cards(fresh=True)}
        moved = 0
        for card_id, c in mine.items():
            pc = cards.get(card_id)
            if pc is None or pc.status == "cut":
                state["cards"].pop(card_id)            # gone, or cut: what was cut stays what it was
                continue
            row = next((r for r in rows if (r["did"], r["eid"], r["pid"]) == (c["did"], c["eid"], c["pid"])
                        and (r.get("configuration") or "") == c["configuration"]), None)
            if row is None or row.get("wvm") != "v" or row.get("wvmid") == c["vid"]:
                continue                               # the same version (or the part isn't in it any more)
            moved += self._follow(card_id, c, pc, row, a)
        return moved

    def _follow(self, card_id: str, c: Dict[str, Any], pc: Any, row: Dict[str, Any], a: Dict[str, str]) -> int:
        old, new, name = c["vid"], row["wvmid"], pc.card.name
        what: List[str] = []
        key = ""
        try:
            then, then_x = self.onshape.geometry(c["did"], "v", old, c["eid"], c["pid"], old, name, cfg=c["configuration"])
            now, now_x = self.onshape.geometry(c["did"], "v", new, c["eid"], c["pid"], new, name, cfg=c["configuration"])
            what = differences(then, now, float(then_x.get("outline_area_in2") or 0),
                               float(now_x.get("outline_area_in2") or 0))
            key = shape_key(now)
        except OutOfCalls:
            raise
        except PanelOnshapeError as e:
            log.info("panel: comparing %s's versions: %s", name, e)
            what = ["a newer version (its shape couldn't be compared)"]
            key = f"version:{new}"
        if not self.board.relink(pc, old, new, a.get("name") or "", what):
            log.info("panel: %s's link was changed by hand, so it doesn't follow %s", name, a.get("name"))
            c["vid"] = new                             # don't ask again for this version
            return 0
        c["vid"] = new
        log.info("panel: %s follows %s to version %s%s", name, a.get("name") or "its assembly", new,
                 f" ({', '.join(what)})" if what else "")
        if what and pc.status == "sheet":
            self.board.flag_changed(pc, name, what, key)
        return 1
