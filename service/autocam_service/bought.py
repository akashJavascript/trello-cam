"""The shop's bought-parts list (2026-11-02, docs/bought-parts.md). Bought parts in an assembly (a vendor's library,
McMaster numbers, belts) are sent from the Onshape panel like shafts, and checked off on the phone page's Parts tab:
**In the bin** when the team has them (put in the subsystem's bin), **Order** when they have to be bought.

Counts, not just a state (the user, 2026-11-02): a part needs `qty`; `in_bin` of them are in the bin, `on_order` are
on the order sheet, and the rest are still needed. "2 of 4 in the bin" and ordering only the other 2 both work.
Each In the bin is logged, so a phone can undo its own last one the same day.

AutoCAM never writes to the team's order sheet (the user: people write the row themselves, so it's checked before
money is spent). Order opens the sheet in a new tab and copies the part's row (Description, Link, Supplier, Part
Number, Units: the sheet's columns E to I, pasted at Description); the part only counts as ordered once the person
says they added it.

When the assembly's count changes after a part was sent (4 wheels become 6), the part says "CAD has 6"; the panel's
Make it 6 (or changing the quantity) settles it. The list never changes by itself.

Screws and nuts aren't on the list (the user): Onshape's standard content never is, and a bought part named like a
screw, bolt or nut is left out too (HARDWARE_RE).
"""

import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from .shoplist import ListError, ShopList

HARDWARE_RE = re.compile(r"screw|bolt|\bnuts?\b|locknut|nylock", re.IGNORECASE)
ACTIVE = ("needed", "ordered")
SHEET_RE = re.compile(r"^https://docs\.google\.com/spreadsheets/[^\s\"'<>\\]*$")
LATE_DAYS = 14                 # an order this old shows in orange on the phone


def is_hardware(name: str) -> bool:
    """A screw, bolt or nut: not on the list (the user, 2026-11-02)."""
    return bool(HARDWARE_RE.search(name or ""))


def sheet_link(url: str) -> str:
    """The order sheet's link as everyone's phone should open it ("" if it isn't a Google Sheet): without "/u/3/",
    which is one person's third signed-in Google account, and without the "#gid=" that repeats the tab."""
    url = (url or "").strip()
    if not SHEET_RE.match(url):
        return ""
    url = re.sub(r"^(https://docs\.google\.com/spreadsheets/)u/\d+/", r"\1", url)
    return url.split("#", 1)[0]


def counts(x: Dict[str, Any]) -> Tuple[int, int, int]:
    """(needed in all, in the bin, on the order sheet), from an item (or one saved before the counts: its status)."""
    qty = max(1, int(x.get("qty") or 1))
    if "in_bin" not in x:
        return qty, qty if x.get("status") == "bin" else 0, qty if x.get("status") == "ordered" else 0
    in_bin = min(qty, max(0, int(x.get("in_bin") or 0)))
    return qty, in_bin, min(qty - in_bin, max(0, int(x.get("on_order") or 0)))


class PartList(ShopList):
    """state/bought_parts.json: every bought part sent, newest last. Parts all in the bin stay `keep_days`, then drop
    off."""
    FILE_KEY, NOUN, ACTIVE, FINISHED, FINISHED_AT = "parts", "part", ACTIVE, "bin", "bin_at"

    def __init__(self, path: Path, keep_days: int = 14, now: Callable[[], datetime] = datetime.now):
        super().__init__(path, keep_days=keep_days, now=now)

    def _settle(self, x: Dict[str, Any], qty: int, in_bin: int, on_order: int) -> None:
        """Store the counts and the status they make: all in the bin, all of the rest on order, or some needed."""
        on_order = min(on_order, qty - in_bin)
        status = "bin" if in_bin >= qty else "ordered" if in_bin + on_order >= qty else "needed"
        if status == "bin" and x.get("status") != "bin":
            x["bin_at"] = self._stamp()
        elif status != "bin":
            x["bin_at"] = ""
        x.update(qty=qty, in_bin=in_bin, on_order=on_order, status=status)

    # ---- from the panel
    def add(self, entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        made = []

        def go(items):
            for e in entries:
                if any(x.get("source") == e["source"] and x.get("status") in ACTIVE for x in items):
                    raise ListError("is already on the parts list", status=409)
                x = {"cad_qty": e.get("qty"), "note": "", **e, "id": secrets.token_hex(6), "status": "needed",
                     "sent_at": self._stamp(), "changed": False, "in_bin": 0, "on_order": 0, "bin_log": [],
                     "cad_now": None}
                items.append(x)
                made.append(x)
        self._change(go)
        return made

    def edit(self, pid: str, qty: int, note: str) -> Dict[str, Any]:
        """A new quantity or note from the panel. Seeing the CAD's count and setting one settles "CAD has 6"."""
        def go(items):
            x = self._find(items, pid)
            _, in_bin, on_order = counts(x)
            if qty < in_bin:
                raise ListError(f"{in_bin} are in the bin already, so it can't be fewer.")
            if x.get("cad_now") is not None:
                x["cad_qty"], x["cad_now"] = x["cad_now"], None
            x["note"] = note
            self._settle(x, qty, in_bin, on_order)
            return dict(x)
        return self._change(go)

    def remove(self, pid: str) -> None:
        def go(items):
            x = self._find(items, pid)
            _, in_bin, on_order = counts(x)
            if on_order:
                raise ListError("It's on the order sheet already, so it stays on the list.", status=409)
            if in_bin:
                raise ListError("Some are in the bin already, so it stays on the list.", status=409)
            items.remove(x)
        self._change(go)

    def mark_read(self, assembly: str, present: Mapping[str, int]) -> int:
        """The assembly was just read (present: each bought row's source -> its count there). Its open parts that
        aren't in it any more get "Changed in CAD"; a part whose count isn't the one it was sent with gets "CAD has N"
        (all in the bin too: more may be needed now). Returns how many changed."""
        def go(items):
            n = 0
            for x in items:
                if x.get("assembly", {}).get("key") != assembly:
                    continue
                if x.get("status") in ACTIVE:
                    gone = x.get("source") not in present
                    if bool(x.get("changed")) != gone:
                        x["changed"], n = gone, n + 1
                now = present.get(x.get("source"))
                cad_now = now if now is not None and now != x.get("cad_qty") else None
                if x.get("cad_now") != cad_now:
                    x["cad_now"], n = cad_now, n + 1
            return n
        with self._lock:
            items = self._pruned(self._load())
            n = go(items)
            if n:
                self._save(items)
            return n

    # ---- from the shop page
    def ordered(self, pid: str, device: str) -> None:
        """Someone wrote the rest of it on the order sheet themselves (AutoCAM never writes there)."""
        def go(items):
            x = self._find(items, pid)
            qty, in_bin, on_order = counts(x)
            if in_bin >= qty:
                raise ListError("Someone already put this in the bin", status=409, kind="taken")
            if in_bin + on_order < qty:
                x.update(ordered_by=device, ordered_at=self._stamp())
                self._settle(x, qty, in_bin, qty - in_bin)
        self._change(go)

    def bin(self, pid: str, device: str, n: Any = None) -> None:
        """n of them went in the bin (None: all the rest). Ones on order count as come in once the rest are there."""
        def go(items):
            x = self._find(items, pid)
            qty, in_bin, on_order = counts(x)
            most = qty - in_bin
            if most <= 0:
                return
            k = most if n is None else n
            if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= most:
                raise ListError(f"Between 1 and {most} can go in the bin.")
            x.setdefault("bin_log", []).append({"n": k, "at": self._stamp(), "by": device, "on_order": on_order})
            self._settle(x, qty, in_bin + k, on_order)
        self._change(go)

    def undo(self, pid: str, device: str) -> None:
        """Take back this phone's last In the bin, the same day (only the part's latest one)."""
        def go(items):
            x = self._find(items, pid)
            qty, in_bin, _ = counts(x)
            log = x.get("bin_log") or []
            last = log[-1] if log else None
            if last is None or last.get("by") != device or not self._today(last.get("at")):
                raise ListError("Only the phone that put it in the bin can undo it, the same day.", status=409)
            log.pop()
            self._settle(x, qty, max(0, in_bin - int(last.get("n") or 0)), int(last.get("on_order") or 0))
        self._change(go)

    def shop_view(self, device: str) -> Dict[str, Any]:
        """The Parts tab: everything still needed or on the order sheet, and what went in the bin today."""
        out, binned = [], []
        today = self.now().date()
        for x in self.all():
            qty, in_bin, on_order = counts(x)
            item = {"id": x["id"], "name": x.get("name") or "", "vendor": x.get("vendor") or "", "qty": qty,
                    "number": x.get("number") or "", "link": x.get("link") or "",
                    "assembly": x.get("assembly", {}).get("name", ""), "url": x.get("assembly", {}).get("url", "")}
            if x["status"] in ACTIVE:
                stamp = x.get("ordered_at") or ""
                days = (today - datetime.fromisoformat(stamp).date()).days if stamp and on_order else None
                out.append({**item, "status": x["status"], "inBin": in_bin, "onOrder": on_order,
                            "left": qty - in_bin - on_order, "changed": bool(x.get("changed")),
                            "cadNow": x.get("cad_now"), "note": x.get("note") or "", "orderedDays": days,
                            "late": days is not None and days >= LATE_DAYS, "sentAt": x.get("sent_at") or ""})
            log = x.get("bin_log") or []
            for i, e in enumerate(log):
                if self._today(e.get("at")):
                    binned.append({**item, "n": int(e.get("n") or 0), "when": self._clock(e["at"]), "binAt": e["at"],
                                   "undo": i == len(log) - 1 and e.get("by") == device})
        binned.sort(key=lambda d: d["binAt"], reverse=True)
        return {"parts": out, "bin": binned}


def status_chip(x: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """A bought part's tag in the panel: nothing while it's all still needed (like a waiting shaft)."""
    if x is None:
        return "", ""
    qty, in_bin, on_order = counts(x)
    if in_bin >= qty:
        return "In bin", "good"
    if x.get("changed"):
        return "Changed in CAD", "bad"
    if in_bin:
        return f"{in_bin} of {qty} in bin", "good"
    if on_order and in_bin + on_order >= qty:
        return "Ordered", "queued"
    return "", ""
