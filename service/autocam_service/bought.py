"""The shop's bought-parts list (2026-11-02, docs/bought-parts.md). Bought parts in an assembly (a vendor's library,
McMaster numbers, belts) are sent from the Onshape panel like shafts, and checked off on the phone page's Parts tab:
**In the bin** when the team has it (put in the subsystem's bin), **Order** when it has to be bought.

AutoCAM never writes to the team's order sheet (the user: people write the row themselves, so it's checked before
money is spent). Order opens the sheet in a new tab and copies the part's name; the part only shows "On the order
sheet" once the person says they added it.

Screws and nuts aren't on the list (the user): Onshape's standard content never is, and a bought part named like a
screw, bolt or nut is left out too (HARDWARE_RE).
"""

import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .shoplist import ListError, ShopList

HARDWARE_RE = re.compile(r"screw|bolt|\bnuts?\b|locknut|nylock", re.IGNORECASE)
ACTIVE = ("needed", "ordered")
SHEET_RE = re.compile(r"^https://docs\.google\.com/spreadsheets/[^\s\"'<>\\]*$")


def is_hardware(name: str) -> bool:
    """A screw, bolt or nut: not on the list (the user, 2026-11-02)."""
    return bool(HARDWARE_RE.search(name or ""))


class PartList(ShopList):
    """state/bought_parts.json: every bought part sent, newest last. Parts in the bin stay `keep_days`, then drop off."""
    FILE_KEY, NOUN, ACTIVE, FINISHED, FINISHED_AT = "parts", "part", ACTIVE, "bin", "bin_at"

    def __init__(self, path: Path, keep_days: int = 14, now: Callable[[], datetime] = datetime.now):
        super().__init__(path, keep_days=keep_days, now=now)

    # ---- from the panel
    def add(self, entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        made = []

        def go(items):
            for e in entries:
                if any(x.get("source") == e["source"] and x.get("status") in ACTIVE for x in items):
                    raise ListError("is already on the parts list", status=409)
                x = {**e, "id": secrets.token_hex(6), "status": "needed", "sent_at": self._stamp(), "changed": False}
                items.append(x)
                made.append(x)
        self._change(go)
        return made

    def remove(self, pid: str) -> None:
        def go(items):
            x = self._find(items, pid)
            if x.get("status") != "needed":
                raise ListError("It's on the order sheet already, so it stays on the list." if x.get("status") == "ordered"
                                else "It's in the bin already.", status=409)
            items.remove(x)
        self._change(go)

    # ---- from the shop page
    def ordered(self, pid: str, device: str) -> None:
        """Someone wrote it on the order sheet themselves (AutoCAM never writes there)."""
        def go(items):
            x = self._find(items, pid)
            if x["status"] == "bin":
                raise ListError("Someone already put this in the bin", status=409, kind="taken")
            if x["status"] == "needed":
                x.update(status="ordered", ordered_by=device, ordered_at=self._stamp())
        self._change(go)

    def bin(self, pid: str, device: str) -> None:
        def go(items):
            x = self._find(items, pid)
            if x["status"] != "bin":
                x.update(was=x["status"], status="bin", bin_by=device, bin_at=self._stamp())
        self._change(go)

    def undo(self, pid: str, device: str) -> None:
        def go(items):
            x = self._find(items, pid)
            if x["status"] != "bin" or x.get("bin_by") != device or not self._today(x.get("bin_at")):
                raise ListError("Only the phone that put it in the bin can undo it, the same day.", status=409)
            x.update(status=x.get("was") if x.get("was") in ACTIVE else "needed", bin_by="", bin_at="", was="")
        self._change(go)

    def shop_view(self, device: str) -> Dict[str, Any]:
        """The Parts tab: everything needed or on the order sheet, and what went in the bin today."""
        out, binned = [], []
        for x in self.all():
            item = {"id": x["id"], "name": x.get("name") or "", "vendor": x.get("vendor") or "", "qty": x.get("qty") or 1,
                    "assembly": x.get("assembly", {}).get("name", ""), "url": x.get("assembly", {}).get("url", ""),
                    "changed": bool(x.get("changed"))}
            if x["status"] in ACTIVE:
                out.append({**item, "status": x["status"], "sentAt": x.get("sent_at") or ""})
            elif self._today(x.get("bin_at")):
                binned.append({**item, "when": self._clock(x["bin_at"]), "undo": x.get("bin_by") == device,
                               "binAt": x["bin_at"]})
        binned.sort(key=lambda d: d["binAt"], reverse=True)
        return {"parts": out, "bin": binned}


def status_chip(x: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """A bought part's tag in the panel: nothing while it's needed (like a waiting shaft)."""
    if x is None:
        return "", ""
    if x.get("status") == "bin":
        return "In bin", "good"
    if x.get("changed"):
        return "Changed in CAD", "bad"
    if x.get("status") == "ordered":
        return "Ordered", "queued"
    return "", ""
