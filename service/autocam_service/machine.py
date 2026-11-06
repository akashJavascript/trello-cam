"""The machine page (2026-11-05, the mockup the user approved): the operator's screen on the ShopSabre's own PC.

The shop page's own pieces, one card at a time: the sheets in Ready to cut (placed-by-hand ones apart), the programs
that passed the Check label, then for the one being cut, only the step to do now: find the offcut, load, change the
cutter, run a program (Download puts it in the PC's Downloads, where WinCNC's folder button finds it), try a fit,
cut the fitted parts out, keep or scrap what's left.

Everything it does is something the board already understands, so the service's own passes do the rest as if
someone had done it on Trello:
- ticks on the sheet card: Options (the no-stop version), Offcut (keep the rest), Stock (the offcut isn't on the
  rack; re-nest, or a new sheet), the Fit checklists (which run fit), At the machine (loaded);
- moving the card: to Sheet review when something went wrong, or to Cut once it's done, UNDO_S later, so a mistake
  can be undone (automation never moves a card into Ready to cut, so a card already in Cut can't come back).

Where each sheet is (its step), and when each was loaded (the runner reads that to tell which offcut is still on the
bed), is kept here (state/machine.json), so a closed browser opens on the same step. No
sign-in: the page has its own key (machine_key), bookmarked on that PC. No Onshape calls; Trello reads cached.
"""

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from autocam_core.airtest import SUFFIX as AIR_SUFFIX
from autocam_core.hotfolder import Queue, write_atomic
from autocam_core.offcuts import add_used, free_length
from autocam_core.schema_job import Job, load_job
from autocam_core.schema_result import Result, SheetResult, read_result

from .config import Config
from .jobs import sheet_fixture
from .offcuts import OffcutStore, cut_end_at_front
from .program_checks import CheckedPrograms
from .run_state import RunStore
from .sheet_cards import clamp_edges, material_label, thickness_label
from .tracker.base import Card, CardNotFound, Tracker

log = logging.getLogger("autocam.machine")

UNDO_S = 180.0              # "Move to Cut" waits this long before the card moves, so it can be undone
CACHE_S = 15.0              # Trello list reads
CARD_S = 8.0                # one card's read (the open sheet's page asks every few seconds)
LOADED_KEEP = 20            # Loaded presses remembered (the runner only needs the latest few)
NO_STOP_SUFFIX = "_NOSTOP"  # runner.NO_STOP_SUFFIX
NOTES = {"face": "Skims the tops", "scribe": "Draws the bend lines", "small": "The small holes and cutouts",
         "outlines": "Cuts the fitted parts out"}
BEFORE = ("face", "scribe", "small")
ID_RE = re.compile(r"^[A-Za-z0-9]{1,40}$")           # a Trello card id (24 hex; tests use shorter)
PIECE_RE = re.compile(r"\(([\d.]+) x ([\d.]+) in\), so the piece must cover that")
TIMES_RE = re.compile(r" x(\d+) - ")


class MachineError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _inch(t: float) -> str:
    return thickness_label(t).replace("in", " in")


def _minutes(seconds: Optional[float]) -> int:
    return max(1, round(seconds / 60)) if seconds else 0


def _norm(label: str) -> str:
    return " ".join((label or "").lower().split())


class MachineView:
    def __init__(self, tracker: Tracker, cfg: Config, store: RunStore, offcuts: OffcutStore,
                 checked: CheckedPrograms, plates: Any, state_path: Path,
                 clock: Callable[[], float] = time.time,
                 today: Callable[[], str] = lambda: datetime.now().date().isoformat()):
        self.t = tracker
        self.cfg = cfg
        self.store = store
        self.offcuts = offcuts
        self.checked = checked
        self.plates = plates                  # panel.plates.PlatesView: the plates on a sheet, by name
        self.path = Path(state_path)
        self.clock = clock
        self.today = today
        self._lock = threading.RLock()
        self._lists: Dict[str, Tuple[float, List[Card]]] = {}
        self._cards: Dict[str, Tuple[float, Card]] = {}
        self._results: Dict[str, Optional[Result]] = {}
        self._jobs: Dict[str, Optional[Job]] = {}
        self._files: Dict[str, bytes] = {}    # attachment id -> bytes (attachments never change)

    # ---------------------------------------------------------------- state
    def _state(self) -> Dict[str, Dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {k: dict(data.get(k) or {}) for k in ("progress", "pending", "ran", "loaded", "racked")}

    def _save(self, st: Dict[str, Dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, (json.dumps(st, indent=1, sort_keys=True) + "\n").encode("utf-8"))

    def _iso(self) -> str:
        return datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ---------------------------------------------------------------- Trello, cached
    def _list(self, key: str) -> List[Card]:
        with self._lock:
            hit = self._lists.get(key)
            if hit is not None and self.clock() - hit[0] < CACHE_S:
                return hit[1]
            cards = self.t.list_cards(key)
            self._lists[key] = (self.clock(), cards)
            return cards

    def _card(self, card_id: str, fresh: bool = False) -> Card:
        with self._lock:
            hit = self._cards.get(card_id)
            if hit is not None and not fresh and self.clock() - hit[0] < CARD_S:
                return hit[1]
            card = self.t.get_card(card_id)
            self._cards[card_id] = (self.clock(), card)
            return card

    def _stale(self, card_id: str = "") -> None:
        with self._lock:
            self._lists.clear()
            self._cards.pop(card_id, None)

    # ---------------------------------------------------------------- AutoCAM's records
    def _result(self, job_id: str) -> Optional[Result]:
        if job_id not in self._results:
            try:
                self._results[job_id] = read_result(Queue(self.cfg.paths.queue).done / job_id / "result.json")
            except Exception as e:  # noqa: BLE001 - an unreadable result: the sheet shows without its programs
                log.info("machine: no result for %s: %s", job_id, e)
                self._results[job_id] = None
        return self._results[job_id]

    def _job(self, job_id: str) -> Optional[Job]:
        if job_id not in self._jobs:
            text = self.store.job_text(job_id)
            try:
                self._jobs[job_id] = load_job(json.loads(text)) if text else None
            except (ValueError, KeyError, TypeError) as e:
                log.info("machine: job %s unreadable: %s", job_id, e)
                self._jobs[job_id] = None
        return self._jobs[job_id]

    def _sheet_result(self, info: Dict) -> Optional[SheetResult]:
        res = self._result(str(info.get("job") or "")) if info.get("job") else None
        return next((s for s in res.sheets if s.index == info.get("index")), None) if res else None

    def _stock(self, info: Dict) -> str:
        key = str(info.get("material") or "")
        m = self.cfg.materials.get(key)
        name = material_label(m) if m is not None else key
        return " ".join(x for x in (name, _inch(float(info["thickness_in"])) if info.get("thickness_in") else "") if x)

    def _ours(self, card: Card, info: Optional[Dict]) -> bool:
        return bool(info) and bool(info.get("cuttable")) and not info.get("cut") and not info.get("archived")

    def _is_check(self, card: Card) -> bool:
        return _norm(self.cfg.labels.check) in {_norm(l) for l in card.labels}

    def _passed(self, card: Card) -> List[str]:
        """The names of a Check card's programs whose last check passed, still on the card."""
        rec = self.checked.all().get(card.id) or {}
        on = {a.id: a.name for a in card.attachments}
        return [on[a] for a, r in (rec.get("files") or {}).items() if r.get("passed") and a in on]

    # ---------------------------------------------------------------- the list
    def view(self) -> Dict[str, Any]:
        self.flush()
        st = self._state()
        reg = self.store.sheet_cards()
        sheets = []
        for card in self._list("ready_to_cut"):
            info = reg.get(card.id)
            if not self._ours(card, info) or card.id in st["pending"]:
                continue
            sheets.append(self._summary(card, info, st))
        for card in self._list("inbox"):
            if not self._is_check(card) or card.id in st["pending"] or card.id in st["ran"]:
                continue
            files = self._passed(card)
            if not files:
                continue
            rec = self.checked.all().get(card.id) or {}
            sheets.append({"id": card.id, "group": "check", "title": " ".join(card.name.split()) or "A program",
                           "sub": " · ".join(x for x in (rec.get("stock") or "", "passed the checks") if x),
                           "rush": False, "plates": [{"name": f, "qty": 1} for f in files],
                           "progress": self._progress(st, card.id, 1)})
        return {"sheets": sheets, "cut": self._cut_today(st, reg)}

    def _progress(self, st: Dict, card_id: str, steps: int) -> Optional[Dict[str, int]]:
        p = st["progress"].get(card_id)
        return {"step": min(int(p.get("step", 0)), steps - 1) + 1, "of": steps} if p and p.get("step") else None

    def _summary(self, card: Card, info: Dict, st: Dict) -> Dict[str, Any]:
        by_hand = bool(info.get("by_hand"))
        minutes = re.search(r"(\d+) min", card.name or "")
        offcut = (self.offcuts.get(info["offcut_id"]) or {}) if info.get("offcut_id") else {}
        times = self._times(card, info)
        where = ("placed by hand" + (f" · run it {times} times" if times > 1 else "") if by_hand else
                 f"offcut #{offcut.get('number')}" if offcut.get("number") else "new sheet")
        sub = [where[0].upper() + where[1:]]
        sub += [f"{minutes.group(1)} min"] if minutes else []
        sub += ["own program"] if info.get("own") else []
        sub += [str(info.get("label") or info.get("run") or "")]
        progress = self._progress(st, card.id, len(self.steps(card, info))) if card.id in st["progress"] else None
        try:
            plates = self.plates.plates_for(info) if self.plates is not None else []
        except Exception as e:  # noqa: BLE001 - the sheet still shows
            log.info("machine: plates for %s: %s", card.id, e)
            plates = []
        return {"id": card.id, "group": "hand" if by_hand else "ready", "title": self._stock(info),
                "sub": " · ".join(x for x in sub if x), "rush": (card.name or "").upper().startswith("RUSH"),
                "plates": [{"name": p["name"], "qty": p["qty"]} for p in plates], "progress": progress}

    def _times(self, card: Card, info: Dict) -> int:
        """How many times a placed-by-hand program runs (once per copy)."""
        if not info.get("by_hand"):
            return 1
        sr = self._sheet_result(info)
        if sr is not None:
            return max(1, int(sr.repeat or 1))
        m = TIMES_RE.search(card.name or "")
        return int(m.group(1)) if m else 1

    def _cut_today(self, st: Dict, reg: Dict) -> List[Dict[str, Any]]:
        now, out = self.clock(), []
        for cid, p in sorted(st["pending"].items(), key=lambda x: -float(x[1].get("due", 0))):
            out.append({"id": cid, "title": p.get("title", ""), "sub": p.get("sub", ""),
                        "undo": max(0, int(float(p.get("due", 0)) - now))})
        made = {piece.get("made_from"): piece.get("number") for piece in self.offcuts.all().values()}
        for cid, r in sorted(st["ran"].items(), key=lambda x: str(x[1].get("when", "")), reverse=True):
            if _local_day(str(r.get("when") or "")) != self.today():
                continue
            sub = r.get("sub", "")
            if made.get(cid):
                sub = f"{sub} · write #{made[cid]} on the rest" if sub else f"Write #{made[cid]} on the rest"
            out.append({"id": cid, "title": r.get("title", ""), "sub": sub, "undo": 0})
        return out

    # ---------------------------------------------------------------- one sheet: its steps
    def sheet(self, card_id: str) -> Dict[str, Any]:
        card, info = self._open(card_id)
        st = self._state()
        steps = self.steps(card, info) if info is not None else self._check_steps(card)
        p = st["progress"].get(card_id) or {}
        title = self._stock(info) if info is not None else " ".join(card.name.split())
        label = str(info.get("label") or "") if info is not None else "Check"
        return {"id": card_id, "title": title, "label": label, "steps": steps,
                "at": {"step": min(int(p.get("step", 0)), len(steps) - 1), "rep": int(p.get("rep", 1)),
                       "lap": int(p.get("lap", 0)), "lapRan": bool(p.get("lapRan"))}}

    def _open(self, card_id: str, fresh: bool = True) -> Tuple[Card, Optional[Dict]]:
        """A sheet card in Ready to cut that AutoCAM made (with its record), or a Check card that passed (None)."""
        if not ID_RE.match(card_id or ""):
            raise MachineError(400, "Bad request")
        info = self.store.sheet_cards().get(card_id)
        try:
            card = self._card(card_id, fresh=fresh)
        except CardNotFound:
            raise MachineError(404, "That card isn't on the board any more.")
        if info is not None and self._ours(card, info):
            if card.list_key != "ready_to_cut":
                raise MachineError(409, "This sheet isn't in Ready to cut any more.")
            return card, info
        if self._is_check(card) and self._passed(card) and not card.closed:
            return card, None
        raise MachineError(404, "That isn't a sheet to cut.")

    def _check_steps(self, card: Card) -> List[Dict[str, Any]]:
        rec = self.checked.all().get(card.id) or {}
        files = self._passed(card)
        zero = "the sheet's front-left corner" if rec.get("on_sheet", True) else "wherever you set it in Fusion"
        return [{"t": "checkrun", "file": f, "zero": zero} for f in files]

    def steps(self, card: Card, info: Dict) -> List[Dict[str, Any]]:
        t = self.cfg.trello
        checks = {(c.checklist, c.item): c.done for c in card.checks}
        names = [a.name for a in card.attachments]
        key = str(info.get("material") or "")
        m = self.cfg.materials.get(key)
        mist = bool(m.use_mist) if m is not None else False
        stock = self._stock(info)
        sr = self._sheet_result(info)
        by_hand = bool(info.get("by_hand"))
        steps: List[Dict[str, Any]] = []
        runs = self._runs(card, info, sr, checks, names)
        first = next((r for r in runs if r.get("cutter")), {})
        cutter = " · ".join(x for x in (first.get("cutter", ""), first.get("tool", "")) if x)
        if by_hand:
            steps.append({"t": "piece", "stock": stock, "size": self._piece(card, info)})
            steps.append({"t": "loadhand", "mist": mist, "cutter": cutter})
        else:
            if info.get("offcut_id"):
                steps.append(self._find(card, info, checks))
            width = self.cfg.sheet_for(key).width_in
            zones = sheet_fixture(self.cfg, key).clamp_zones_in
            piece = (self.offcuts.get(info["offcut_id"]) or {}) if info.get("offcut_id") else {}
            steps.append({"t": "load", "stock": stock, "mist": mist, "cutter": cutter,
                          "where": f"Offcut #{piece['number']}" if piece.get("number") else "New sheet",
                          "clamps": clamp_edges(zones, width), "how": self._how(info, piece),
                          "picture": any(n.lower().endswith(".png") for n in names)})
        tool = (first.get("cutter"), first.get("tool"))
        for r in runs:
            if r.get("cutter") and (r.get("cutter"), r.get("tool")) != tool:
                steps.append({"t": "cutter", "cutter": r["cutter"], "tool": r.get("tool", "")})
                tool = (r.get("cutter"), r.get("tool"))
            steps.append({k: v for k, v in r.items() if k not in ("cutter", "tool")})
        if by_hand:
            plates = self.plates.plates_for(info) if self.plates is not None else []
            what = ", ".join(f"{p['qty']} × {p['name']}" if p["qty"] > 1 else p["name"] for p in plates)
            steps.append({"t": "finishhand", "what": what or "the part"})
        else:
            steps.append(self._finish(card, info, checks))
        return steps

    def _runs(self, card: Card, info: Dict, sr: Optional[SheetResult], checks: Dict, names: List[str]) -> List[Dict]:
        """The programs in the order they're run, each with its cutter: the ones before the sheet's (face, scribe,
        small), the air test if it's ticked, the sheet's own, each fit series, the outlines cut last."""
        t = self.cfg.trello
        if sr is None or not sr.tap:                  # an own program: the .tap on its card
            taps = [n for n in names if n.lower().endswith((".tap", ".nc"))]
            own = info.get("own") or {}
            file = own.get("program") if own.get("program") in taps else (taps[0] if taps else "")
            return [{"t": "run", "file": file, "repeat": self._times(card, info) if info.get("by_hand") else 1,
                     "main": True}]
        job = self._job(str(info.get("job")))
        tool = job.tooling.tools.get(sr.tool) if job is not None else None
        main_tool = (sr.cutter_label, f"T{tool.number}" if tool is not None else "")
        out: List[Dict] = []
        for kind in BEFORE:
            for p in (p for p in sr.programs if p.kind == kind and p.tap):
                out.append({"t": "run", "file": p.tap, "min": _minutes(p.machining_time_s), "note": NOTES[kind],
                            "cutter": p.cutter_label, "tool": f"T{p.tool_number}"})
        stem = sr.tap[:-4] if sr.tap.lower().endswith(".tap") else sr.tap
        stops = len(sr.pauses.entries) if sr.pauses is not None else 0
        nostop_on = bool(checks.get((t.options_checklist, t.no_stop_item)))
        nostop = f"{stem}{NO_STOP_SUFFIX}.tap"
        if checks.get((t.options_checklist, t.air_test_item)):
            air = next((n for n in names if n.endswith(f"{AIR_SUFFIX}.tap")), "")
            out.append({"t": "air", "file": air, "gap": f"{self.cfg.machine.air_test_gap_in:g} in",
                        "cutter": main_tool[0], "tool": main_tool[1]})
        times = max(1, int(sr.repeat or 1)) if sr.by_hand else 1
        out.append({"t": "run", "file": sr.tap, "min": _minutes(sr.machining_time_s), "main": True,
                    "stops": stops, "repeat": times, "cutter": main_tool[0], "tool": main_tool[1],
                    **({"nostop": {"file": nostop, "on": nostop_on, "ready": nostop in names}} if stops else {})})
        series = sorted({p.series for p in sr.programs if p.kind == "fit"})
        for n, f in zip(series, info.get("fits") or []):
            laps = sorted((p for p in sr.programs if p.kind == "fit" and p.series == n), key=lambda p: p.lap)
            answered = next((label for label, _ in f.get("items") or [] if checks.get((f["checklist"], label))), None)
            out.append({"t": "fit", "series": n, "feature": f.get("feature", ""), "part": f.get("name", ""),
                        "outline": f.get("feature") == "outline", "cutout": bool(laps and laps[0].cutout),
                        "laps": [{"file": p.tap, "stock": p.stock_in} for p in laps],
                        "items": [label for label, _ in f.get("items") or []], "answered": answered,
                        "cutter": main_tool[0], "tool": main_tool[1]})
        for p in (p for p in sr.programs if p.kind == "outlines" and p.tap):
            out.append({"t": "run", "file": p.tap, "min": _minutes(p.machining_time_s), "note": NOTES["outlines"],
                        "cutter": main_tool[0], "tool": main_tool[1]})
        return out

    def _piece(self, card: Card, info: Dict) -> str:
        """How big a piece a placed-by-hand program needs (its cutter's reach, from the card)."""
        own = info.get("own") or {}
        if own.get("box"):
            x0, y0, x1, y1 = own["box"]
            margin = 2 * self.cfg.part_spacing_for(info.get("material"))
            return f"{x1 - x0 + margin:.1f} × {y1 - y0 + margin:.1f} in"
        m = PIECE_RE.search(card.desc or "")
        return f"{m.group(1)} × {m.group(2)} in" if m else ""

    def _how(self, info: Dict, piece: Dict) -> str:
        """Which way round an offcut goes in (offcuts.load_line, short): the end with its earlier cuts."""
        last = piece.get("last") or {}
        if not piece or not last.get("stretch"):
            return ""
        length = self.cfg.sheet_for(piece.get("material")).length_in
        front = cut_end_at_front([tuple(s) for s in piece.get("used") or ()], tuple(last["stretch"]),
                                 bool(info.get("turned")), length)
        end = "at the front" if front else "at the back"
        spin = bool(info.get("turned")) != bool(last.get("turned"))
        return (f"Spin it round from last time: the cut end goes {end}" if spin else f"The cut end goes {end}")

    def _find(self, card: Card, info: Dict, checks: Dict) -> Dict[str, Any]:
        t = self.cfg.trello
        piece = self.offcuts.get(info["offcut_id"]) or {}
        material = piece.get("material") or info.get("material")
        free = self._free(piece.get("used") or [], material) if piece else 0.0
        width = self.cfg.sheet_for(material).width_in
        asked = (t.stock_checklist, t.renest_item) in checks
        missing = bool(checks.get((t.stock_checklist, t.not_found_item)))
        return {"t": "find", "n": piece.get("number"), "size": f"{free:.0f} × {width:g} free" if free else "",
                "picture": True, "missing": missing and not asked, "asked": asked,
                "instead": info.get("instead_of"), "canMiss": (t.stock_checklist, t.not_found_item) in checks}

    def _free(self, used, material: Optional[str]) -> float:
        region = sheet_fixture(self.cfg, material).nest_region_in
        return free_length([tuple(s) for s in used], self.cfg.sheet_for(material).length_in, region[1], region[3],
                           self.cfg.nest.offcut_gap_in)

    def _finish(self, card: Card, info: Dict, checks: Dict) -> Dict[str, Any]:
        """The Done step. racked: the piece left goes back on the rack (the button); else it stays clamped, and the
        runner nests the next parts on it with the still-clamped slack (Runner.on_bed, 2026-11-06)."""
        t = self.cfg.trello
        racked = card.id in self._state()["racked"]
        key = info.get("material")
        width = self.cfg.sheet_for(key).width_in
        if info.get("offcut_id"):
            piece = self.offcuts.get(info["offcut_id"]) or {}
            length = self.cfg.sheet_for(piece.get("material") or key).length_in
            used = tuple(tuple(s) for s in piece.get("used") or [])
            bands = [tuple(b) for b in info.get("bands") or []] or \
                ([tuple(info["used_along"])] if info.get("used_along") else [])
            for b in bands:
                used = add_used(used, b, length, bool(info.get("turned")))
            free = self._free(used, piece.get("material") or key)
            gone = free < self.cfg.nest.offcut_min_in and not piece.get("beside")
            return {"t": "finish", "kind": "offcut", "n": piece.get("number"), "size": f"{free:.0f} × {width:g}",
                    "usedUp": gone, "racked": racked}
        rest = info.get("rest_in")
        size = f"{rest:.0f} × {width:g}" if rest else ""
        has = (t.offcut_checklist, t.offcut_item) in checks
        return {"t": "finish", "kind": "new", "size": size, "can": has, "racked": racked,
                "keep": bool(checks.get((t.offcut_checklist, t.offcut_item))) if has else False}

    # ---------------------------------------------------------------- actions
    def act(self, data: Dict[str, Any]) -> Dict[str, Any]:
        card_id = str(data.get("id") or "")
        do = str(data.get("do") or "")
        if do == "undo":
            return self._undo(card_id)
        card, info = self._open(card_id, fresh=False)
        t = self.cfg.trello
        if do == "progress":
            st = self._state()
            st["progress"][card_id] = {"step": max(0, int(data.get("step") or 0)), "rep": max(1, int(data.get("rep") or 1)),
                                       "lap": max(0, int(data.get("lap") or 0)), "lapRan": bool(data.get("lapRan")),
                                       "when": self._iso()}
            self._save(st)
            return {"ok": True}
        if do == "loaded":                             # best effort: an older card may not have these boxes
            st = self._state()                         # when: the runner's on_bed (the last sheet's offcut is off)
            st["loaded"] = dict(sorted({**st["loaded"], card_id: self._iso()}.items(), key=lambda x: x[1])[-LOADED_KEEP:])
            self._save(st)
            for item in self.cfg.trello.machine_checklist:
                self.t.set_check(card_id, t.machine_checklist_name, item, True)
            self._stale(card_id)
            return {"ok": True}
        if info is None and do not in ("finish", "problem"):
            raise MachineError(400, "Bad request")
        if do == "stops":                              # the switch: on = stop after each part
            self._tick(card_id, t.options_checklist, t.no_stop_item, not bool(data.get("on")))
        elif do == "fit":
            f = self._fit(info, int(data.get("series") or 0))
            at = int(data.get("at") or 0)
            items = [label for label, _ in f.get("items") or []]
            if not 0 <= at < len(items):
                raise MachineError(400, "Bad request")
            self._tick(card_id, f["checklist"], items[at], True)
        elif do == "cantfind":
            self._tick(card_id, t.stock_checklist, t.not_found_item, True)
        elif do == "stock":
            item = {"renest": t.renest_item, "newsheet": t.new_sheet_item}.get(str(data.get("choice") or ""))
            if item is None:
                raise MachineError(400, "Bad request")
            self._tick(card_id, t.stock_checklist, item, True)
        elif do == "keep":
            self._tick(card_id, t.offcut_checklist, t.offcut_item, bool(data.get("on")))
        elif do == "rack":                             # the piece left goes back on the rack: not still clamped
            st = self._state()
            st["racked"].pop(card_id, None)
            if data.get("on"):
                st["racked"] = dict(sorted({**st["racked"], card_id: self._iso()}.items(),
                                           key=lambda x: x[1])[-LOADED_KEEP:])
            self._save(st)
        elif do == "finish":
            return self._finish_now(card, info)
        elif do == "problem":
            return self._problem(card, info, data.get("what"))
        else:
            raise MachineError(400, "Bad request")
        return {"ok": True}

    def _tick(self, card_id: str, checklist: str, item: str, done: bool) -> None:
        if not self.t.set_check(card_id, checklist, item, done):
            raise MachineError(422, f"The card has no \"{item}\" box. Do it on Trello.")
        self._stale(card_id)

    def _fit(self, info: Dict, series: int) -> Dict:
        sr = self._sheet_result(info)
        numbers = sorted({p.series for p in sr.programs if p.kind == "fit"}) if sr is not None else []
        for n, f in zip(numbers, info.get("fits") or []):
            if n == series:
                return f
        raise MachineError(400, "Bad request")

    def _finish_now(self, card: Card, info: Optional[Dict]) -> Dict[str, Any]:
        """Done: the card moves to Cut UNDO_S from now (flush), unless Undo."""
        st = self._state()
        if info is None:
            title, sub = " ".join(card.name.split()), "ran at the machine"
        else:
            title = self._stock(info)
            sub = str(info.get("label") or "")
            fin = self._finish(card, info, {(c.checklist, c.item): c.done for c in card.checks}) \
                if not info.get("by_hand") else {}
            where = "back on the rack" if fin.get("racked") else "kept clamped"
            if fin.get("kind") == "new" and fin.get("can"):
                sub += f" · rest kept, {where}" if fin.get("keep") else " · rest scrapped"
            elif fin.get("kind") == "offcut" and fin.get("n"):
                sub += f" · #{fin['n']} used up" if fin.get("usedUp") else f" · #{fin['n']} {where}"
        st["pending"][card.id] = {"due": self.clock() + UNDO_S, "title": title, "sub": sub,
                                  "check": info is None}
        st["progress"].pop(card.id, None)
        self._save(st)
        return {"ok": True, "undo": int(UNDO_S)}

    def _undo(self, card_id: str) -> Dict[str, Any]:
        st = self._state()
        p = st["pending"].pop(card_id, None)
        if p is None:
            raise MachineError(409, "It's already in Cut. Move it on Trello if it shouldn't be.")
        self._save(st)
        return {"ok": True}

    def _problem(self, card: Card, info: Optional[Dict], what: Any) -> Dict[str, Any]:
        said = [" ".join(str(w).split())[:40] for w in (what if isinstance(what, list) else [])][:6]
        st = self._state()
        st["progress"].pop(card.id, None)
        st["pending"].pop(card.id, None)
        self._save(st)
        reason = ", ".join(said) if said else "something went wrong"
        if info is None:
            self.t.comment(card.id, f"At the machine: {reason}.")
        else:
            self.t.move(card.id, self.cfg.trello.targets["sheet_created"])
            self.t.comment(card.id, f"Sent back from the machine: {reason}. Fix it, then move it to Ready to cut "
                                    "again.")
        self._stale(card.id)
        log.info("machine: %s sent back (%s)", card.id, reason)
        return {"ok": True}

    def flush(self) -> None:
        """Cards whose Undo time is up move to Cut (a Check card too: it was run)."""
        with self._lock:
            st = self._state()
            due = [cid for cid, p in st["pending"].items() if float(p.get("due", 0)) <= self.clock()]
            if not due:
                return
            for cid in due:
                p = st["pending"][cid]
                try:
                    self.t.move(cid, self.cfg.trello.targets["part_cut"])
                    if p.get("check"):
                        self.t.comment(cid, "Run at the machine.")
                except CardNotFound:
                    pass
                except Exception as e:  # noqa: BLE001 - Trello down: try again next time
                    log.warning("machine: couldn't move %s to Cut: %s", cid, e)
                    continue
                st["pending"].pop(cid)
                st["ran"][cid] = {"when": self._iso(), "title": p.get("title", ""), "sub": p.get("sub", "")}
                log.info("machine: %s moved to Cut", cid)
            st["ran"] = {k: v for k, v in st["ran"].items()
                         if _local_day(str(v.get("when") or "")) >= _days_ago(self.today(), 7)}
            self._save(st)
            self._stale()

    def keep_running(self, every_s: float = 15.0) -> None:
        def loop() -> None:
            while True:
                time.sleep(every_s)
                try:
                    self.flush()
                except Exception:  # noqa: BLE001 - never stop the loop
                    log.exception("machine: flush")
        threading.Thread(target=loop, name="machine-flush", daemon=True).start()

    # ---------------------------------------------------------------- files
    def file(self, card_id: str, name: str) -> Tuple[str, bytes]:
        card, _ = self._open(card_id, fresh=False)
        att = next((a for a in card.attachments if a.name == name and name.lower().endswith((".tap", ".nc"))), None)
        if att is None:
            raise MachineError(404, "That program isn't on the card (yet).")
        return att.name, self._download(att)

    def picture(self, card_id: str, what: str) -> bytes:
        card, info = self._open(card_id, fresh=False)
        if what == "offcut":
            if not info or not info.get("offcut_id"):
                raise MachineError(404, "No picture")
            try:
                source = self._card(info["offcut_id"])
            except CardNotFound:
                raise MachineError(404, "No picture")
            pngs = [a for a in source.attachments if a.name.lower().endswith(".png")]
        else:
            pngs = sorted((a for a in card.attachments if a.name.lower().endswith(".png")),
                          key=lambda a: not a.name.lower().endswith("_toolpath.png"))
        if not pngs:
            raise MachineError(404, "No picture")
        return self._download(pngs[-1] if what == "offcut" else pngs[0])

    def _download(self, att) -> bytes:
        with self._lock:
            if att.id not in self._files:
                if len(self._files) > 64:
                    self._files.clear()
                self._files[att.id] = self.t.download(att)
            return self._files[att.id]


def _local_day(utc: str) -> str:
    try:
        return datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone().date().isoformat()
    except ValueError:
        return ""


def _days_ago(day: str, n: int) -> str:
    try:
        from datetime import date, timedelta
        return (date.fromisoformat(day) - timedelta(days=n)).isoformat()
    except ValueError:
        return ""
