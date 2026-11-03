"""The shop's shaft list (2026-11-02, docs/shafts.md). Shafts are configured parts from the team's common parts library
(Hex Shaft, 3/8 Hex shaft, 3/8 hex lite, Standoff), cut on the bandsaw, sometimes turned and grooved on the lathe,
tapped at the drill. They're sent from the Onshape panel with an assembly's plates and made from a phone page
(/shop): one item per shaft, Start, Done. No Trello (the Free plan's 10 members) and no names: "yours" is what this
phone started.

What's here is pure, apart from ShaftList's one JSON file:
    recognize()   an assembly row (its BOM) is a library shaft: its stock and finished length, from the row's own
                  configuration string, so no Onshape call
    steps()       the card's lines ("Grooves 0.600 from both ends, 0.020 per side", "Tap 10-32 both ends")
    problems()    what can't be made (a turn-down that isn't smaller than the stock, a groove past the end)
    ShaftList     the list itself (state/shafts.json): add, edit, take off, Start / Stop / Done / Undo, and the
                  "Changed in CAD" tag when the shaft sent is no longer in its assembly
"""

import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote

from autocam_core.hotfolder import write_atomic


@dataclass(frozen=True)
class Stock:
    name: str
    shape: str                 # "hex" (size across the flats) or "round" (its diameter)
    size_in: float
    bore_in: float = 0.0       # 3/8 hex lite comes with a 0.159 hole through it (the 10-32 tap drill)


STOCKS: Dict[str, Stock] = {s.name: s for s in (
    Stock("1/2 hex", "hex", 0.5), Stock("3/8 hex", "hex", 0.375), Stock("3/8 hex lite", "hex", 0.375, bore_in=0.159),
    Stock("1/2 round", "round", 0.5), Stock("3/8 round", "round", 0.375))}
ORDER = list(STOCKS)                       # the shop page's groups, in this order
ROUND = "round"                            # a library tab whose stock is round bar picked by its OD (Standoff)
WALL_IN = 0.03                             # 3/8 hex lite: thinner than this around the bore is cutting into it
MAX_OPS = 8
TAP_RE = re.compile(r"^[A-Za-z0-9./ x-]{1,12}$")   # "10-32", "1/4-20", "8-32", "M5x0.8"
DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
ID_RE = re.compile(r"^[0-9a-f]{12}$")

_TO_IN = {"meter": 1 / 0.0254, "m": 1 / 0.0254, "millimeter": 1 / 25.4, "mm": 1 / 25.4, "centimeter": 1 / 2.54,
          "cm": 1 / 2.54, "inch": 1.0, "in": 1.0, "foot": 12.0, "ft": 12.0}
_LENGTH_RE = re.compile(r"^\s*(-?[0-9.]+(?:e-?[0-9]+)?)\s*\+?\s*([A-Za-z]*)\s*$")


class ShaftError(Exception):
    """Something the person can fix, said plainly. status: the HTTP status the panel answers with."""

    def __init__(self, message: str, status: int = 400, kind: str = ""):
        super().__init__(message)
        self.status = status
        self.kind = kind


# ---------------------------------------------------------------- reading a library shaft off an assembly row

def configuration_values(cfg: str) -> Dict[str, str]:
    """"Length=18.25+in;OD=0.5+in" -> {"length": "18.25+in", "od": "0.5+in"} (keys lowercased)."""
    out: Dict[str, str] = {}
    for entry in (cfg or "").split(";"):
        key, eq, value = entry.partition("=")
        if eq:
            out[unquote(key).strip().lower()] = unquote(value).strip()
    return out


def inches(value: Optional[str]) -> Optional[float]:
    """A configuration's length in inches ("0.0047625+meter", "18.25+in", "18.25 in"); None if it isn't one."""
    m = _LENGTH_RE.match(value or "")
    if not m:
        return None
    unit = (m[2] or "in").lower()
    if unit not in _TO_IN:
        return None
    try:
        return round(float(m[1]) * _TO_IN[unit], 4)
    except ValueError:
        return None


@dataclass
class Op:
    kind: str                  # "turn" or "groove"
    end: int                   # 1 or 2; 0 = the whole length (a turn only)
    d: float = 0.0             # turn: the diameter
    l: float = 0.0             # turn: how far from the end
    at: float = 0.0            # groove: how far from the end
    auto: bool = False         # from the model (a Standoff's OD that isn't a bar size), not the sender's

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"kind": self.kind, "end": self.end}
        if self.kind == "turn":
            d["d"] = self.d
            if self.end:
                d["l"] = self.l
        else:
            d["at"] = self.at
        if self.auto:
            d["auto"] = True
        return d

    @staticmethod
    def from_dict(d: Any) -> "Op":
        """A step as the panel sent it, checked for shape (problems() checks it makes sense)."""
        if not isinstance(d, dict) or d.get("kind") not in ("turn", "groove"):
            raise ShaftError("A lathe step is missing its kind.")
        end = d.get("end")
        if isinstance(end, bool) or end not in ((0, 1, 2) if d.get("kind") == "turn" else (1, 2)):
            raise ShaftError("A lathe step needs end 1 or end 2.")

        def num(key: str) -> float:
            v = d.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= float(v) <= 100:
                raise ShaftError("A lathe step has a number that isn't a length.")
            return round(float(v), 4)

        if d["kind"] == "turn":
            return Op("turn", end, d=num("d"), l=0.0 if end == 0 else num("l"), auto=bool(d.get("auto")))
        return Op("groove", end, at=num("at"))


@dataclass
class Found:
    stock: str
    length_in: float
    ops: List[Op] = field(default_factory=list)     # from the model (a Standoff turned to its OD)
    problem: str = ""                               # it can't be made from any bar the shop has

    @property
    def label(self) -> str:
        return f"{self.stock}, {self.length_in:.3f} in"


def recognize(row: Dict[str, Any], shafts: Any) -> Optional[Found]:
    """A BOM row ({"did", "eid", "configuration", ...}) that's one of the library's shafts, or None. `shafts` is
    config.Shafts. The length (and a Standoff's OD) come from the row's configuration string; a row in the default
    configuration has the tab's defaults from the config."""
    if not shafts or not shafts.enabled or row.get("did") != shafts.library_document:
        return None
    tab = shafts.tabs.get(row.get("eid") or "")
    if tab is None:
        return None
    values = configuration_values(row.get("configuration") or "")
    length = inches(values.get("length")) or tab.length_in
    if tab.stock != ROUND:
        return Found(tab.stock, length)
    od = inches(values.get("od")) or tab.od_in
    rounds = sorted((s for s in STOCKS.values() if s.shape == "round"), key=lambda s: s.size_in)
    bar = next((s for s in rounds if s.size_in >= od - 0.0005), None)
    if bar is None:
        return Found(rounds[-1].name, length, problem=f"OD {od:.3f} is bigger than any round bar "
                                                       f"({rounds[-1].size_in:.3f} at most)")
    ops = [] if abs(bar.size_in - od) < 0.0005 else [Op("turn", 0, d=round(od, 4), auto=True)]
    return Found(bar.name, length, ops)


# ---------------------------------------------------------------- what the card says, and what can't be made

def _n(x: float) -> str:
    return f"{x:.3f}"


def steps(length_in: float, ends: List[str], ops: List[Op], groove_depth_in: float) -> List[str]:
    """One short line per step (the mockup the user approved, 2026-11-02): the same step on both ends is one line;
    a step on just one end says "one end" unless both ends have their own. No "Face to" line (people know) and
    every length in three decimals, like the calipers."""
    def twin(o: Op) -> bool:
        return any(p is not o and p.kind == o.kind and p.end not in (0, o.end) and
                   ((p.d == o.d and p.l == o.l) if o.kind == "turn" else p.at == o.at) for p in ops)

    both_tap = bool(ends[0]) and ends[0] == ends[1]
    single = [o.end for o in ops if o.end and not twin(o)] + ([] if both_tap else [i + 1 for i, t in enumerate(ends) if t])

    def end(e: int) -> str:
        return "one end" if len(single) == 1 else f"end {e}"

    out, seen = [], set()
    depth = f", {_n(groove_depth_in)} per side"
    for o in ops:
        if o.kind == "turn" and o.end == 0:
            out.append(f"Turn to Ø{_n(o.d)}")
        elif twin(o):
            key = (o.kind, o.d, o.l, o.at)
            if key in seen:
                continue
            seen.add(key)
            out.append(f"Turn both ends to Ø{_n(o.d)} × {_n(o.l)}" if o.kind == "turn"
                       else f"Grooves {_n(o.at)} from both ends{depth}")
        else:
            out.append(f"Turn {end(o.end)} to Ø{_n(o.d)} × {_n(o.l)}" if o.kind == "turn"
                       else f"Groove {_n(o.at)} from {end(o.end)}{depth}")
    if both_tap:
        out.append(f"Tap {ends[0]} both ends")
    else:
        out += [f"Tap {t} in {end(i + 1)}" for i, t in enumerate(ends) if t]
    return out


def problems(stock_name: str, length_in: float, ops: List[Op]) -> List[str]:
    """What can't be made, said shortly (the panel shows these and won't send until they're fixed)."""
    stock = STOCKS[stock_name]
    out: List[str] = []
    per_end = {1: 0.0, 2: 0.0}
    for o in ops:
        if o.kind == "turn":
            if o.d <= 0:
                out.append("A turn-down needs a diameter.")
            elif not o.auto and (o.d > stock.size_in + 1e-9 if stock.shape == "hex" else o.d >= stock.size_in - 1e-9):
                out.append(f"Ø{_n(o.d)} isn't smaller than the {stock.name}.")
            elif stock.bore_in and o.d < stock.bore_in + 2 * WALL_IN:
                out.append(f"Ø{_n(o.d)} cuts into the {stock.bore_in:.3f} hole of the {stock.name}.")
            if o.end:
                if not 0 < o.l < length_in:
                    out.append(f"A turn-down must be shorter than the shaft ({_n(length_in)}).")
                per_end[o.end] = max(per_end[o.end], o.l)
        elif not 0 < o.at < length_in:
            out.append(f"A groove {_n(o.at)} from the end is off the {_n(length_in)} shaft.")
    if per_end[1] and per_end[2] and per_end[1] + per_end[2] > length_in:
        out.append("The turn-downs on the two ends overlap.")
    return list(dict.fromkeys(out))


def clean_ends(ends: Any, default: str) -> List[str]:
    """Each end's tap ("" none); anything missing is the default (10-32)."""
    if ends is None:
        return [default, default]
    if not isinstance(ends, list) or len(ends) != 2:
        raise ShaftError("Pick a tap for each end.")
    out = []
    for t in ends:
        t = "" if t is None else str(t).strip()
        if t and not TAP_RE.match(t):
            raise ShaftError(f"\"{t[:20]}\" isn't a tap size (like 8-32 or M5).")
        out.append(t)
    return out


def clean_ops(ops: Any) -> List[Op]:
    if ops is None:
        return []
    if not isinstance(ops, list) or len(ops) > MAX_OPS:
        raise ShaftError(f"At most {MAX_OPS} lathe steps.")
    return [Op.from_dict(o) for o in ops]


# ---------------------------------------------------------------- the list

ACTIVE = ("waiting", "started")


class ShaftList:
    """state/shafts.json: every shaft sent, newest last. Done ones stay `keep_done_days`, then drop off."""

    def __init__(self, path: Path, groove_depth_in: float = 0.020, keep_done_days: int = 14,
                 now: Callable[[], datetime] = datetime.now):
        self.path = Path(path)
        self.groove_depth_in = groove_depth_in
        self.keep_done_days = keep_done_days
        self.now = now                           # the laptop's local time: "Done today" is the shop's day
        self._lock = threading.RLock()

    # ---- the file
    def _load(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [x for x in data.get("shafts", []) if isinstance(x, dict)] if isinstance(data, dict) else []

    def _save(self, items: List[Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, (json.dumps({"shafts": items}, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))

    def _pruned(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cut = (self.now() - timedelta(days=self.keep_done_days)).isoformat(timespec="seconds")
        return [x for x in items if x.get("status") != "done" or (x.get("done_at") or "") >= cut]

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
            raise ShaftError("Bad request")
        for x in items:
            if x.get("id") == sid:
                return x
        raise ShaftError("That shaft isn't on the list any more.", status=404, kind="gone")

    def _stamp(self) -> str:
        return self.now().isoformat(timespec="seconds")

    # ---- from the panel
    def active(self, source: str) -> Optional[Dict[str, Any]]:
        """The shaft on the list (waiting or started) for this assembly row, if there is one."""
        return next((x for x in reversed(self.all()) if x.get("source") == source and x.get("status") in ACTIVE), None)

    def latest(self, source: str) -> Optional[Dict[str, Any]]:
        """The newest shaft sent for this assembly row (done ones too, until they drop off)."""
        return next((x for x in reversed(self.all()) if x.get("source") == source), None)

    def add(self, entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        made = []

        def go(items):
            for e in entries:
                if any(x.get("source") == e["source"] and x.get("status") in ACTIVE for x in items):
                    raise ShaftError("is already on the shaft list", status=409)
                x = {**e, "id": secrets.token_hex(6), "status": "waiting", "sent_at": self._stamp(), "changed": False}
                items.append(x)
                made.append(x)
        self._change(go)
        return made

    def edit(self, sid: str, qty: int, ends: List[str], ops: List[Op]) -> Dict[str, Any]:
        def go(items):
            x = self._find(items, sid)
            if x.get("status") != "waiting":
                raise ShaftError("Someone already started it, so it can't be changed.", status=409)
            bad = problems(x["stock"], x["length_in"], ops)
            if bad:
                raise ShaftError(bad[0])
            x.update(qty=qty, ends=ends, ops=[o.to_dict() for o in ops])
            return dict(x)
        return self._change(go)

    def remove(self, sid: str) -> None:
        def go(items):
            x = self._find(items, sid)
            if x.get("status") != "waiting":
                raise ShaftError("Someone already started it, so it stays on the list.", status=409)
            items.remove(x)
        self._change(go)

    def mark_changed(self, assembly: str, present: Iterable[str]) -> int:
        """The assembly was just read: its shafts on the list that aren't in it any more (their length changed, or
        they were taken out) get "Changed in CAD"; ones back in it lose it. Returns how many changed state."""
        here = set(present)

        def go(items):
            n = 0
            for x in items:
                if x.get("assembly", {}).get("key") != assembly or x.get("status") not in ACTIVE:
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

    # ---- from the shop page
    def start(self, sid: str, device: str) -> None:
        def go(items):
            x = self._find(items, sid)
            if x["status"] == "started" and x.get("started_by") != device:
                raise ShaftError("Someone already started this", status=409, kind="taken")
            if x["status"] == "done":
                raise ShaftError("Someone already finished this", status=409, kind="taken")
            x.update(status="started", started_by=device, started_at=self._stamp())
        self._change(go)

    def stop(self, sid: str, device: str) -> None:
        def go(items):
            x = self._find(items, sid)
            if x["status"] == "started":
                x.update(status="waiting", started_by="", started_at="")
        self._change(go)

    def done(self, sid: str, device: str) -> None:
        def go(items):
            x = self._find(items, sid)
            if x["status"] != "done":
                x.update(status="done", done_by=device, done_at=self._stamp())
        self._change(go)

    def undo(self, sid: str, device: str) -> None:
        def go(items):
            x = self._find(items, sid)
            if x["status"] != "done" or x.get("done_by") != device or not self._today(x.get("done_at")):
                raise ShaftError("Only the phone that marked it done can undo it, the same day.", status=409)
            back = "started" if x.get("started_by") == device else "waiting"
            x.update(status=back, done_by="", done_at="", **({} if back == "started" else {"started_by": ""}))
        self._change(go)

    def _today(self, stamp: Optional[str]) -> bool:
        return bool(stamp) and str(stamp)[:10] == self.now().date().isoformat()

    def shop_view(self, device: str) -> Dict[str, Any]:
        """What the shop page shows: everything waiting or started, and what was done today. Nobody's device id
        leaves the server: each shaft just says whether it's this phone's."""
        out, done = [], []
        for x in self.all():
            ends, ops = x.get("ends") or ["", ""], [Op.from_dict(o) for o in x.get("ops") or []]
            item = {"id": x["id"], "stock": x["stock"], "length": x["length_in"], "qty": x["qty"],
                    "assembly": x.get("assembly", {}).get("name", ""), "url": x.get("assembly", {}).get("url", ""),
                    "rush": bool(x.get("rush")), "changed": bool(x.get("changed")),
                    "steps": steps(x["length_in"], ends, ops, self.groove_depth_in)}
            if x["status"] in ACTIVE:
                out.append({**item, "started": x["status"] == "started", "startedAt": x.get("started_at") or "",
                            "mine": x["status"] == "started" and x.get("started_by") == device})
            elif self._today(x.get("done_at")):
                when = datetime.fromisoformat(x["done_at"]).strftime("%I:%M %p").lstrip("0")
                done.append({**item, "when": when, "undo": x.get("done_by") == device, "doneAt": x["done_at"]})
        done.sort(key=lambda d: d["doneAt"], reverse=True)
        return {"shafts": out, "done": done, "order": ORDER}


def status_chip(x: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """A shaft's tag in the panel: ("", "") while it waits (no "On the list"; the user, 2026-11-02)."""
    if x is None:
        return "", ""
    if x.get("status") == "done":
        return "Done", "good"
    if x.get("changed"):
        return "Changed in CAD", "bad"
    if x.get("status") == "started":
        return "In progress", "queued"
    return "", ""
