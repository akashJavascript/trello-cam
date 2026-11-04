"""Part cards on the Trello board, for the Onshape panel: which card each part of a Part Studio already has, and
making or changing it. No Onshape calls.

Status comes from the part cards in Drafts, Ready for CAM, Needs fixing, On a sheet and Cut (read at most every
board_cache_s) and AutoCAM's state/sheet_cards.json (which sheets a card is on, and whether they're cut). A card
is a part's when it links the same Part Studio (any workspace or version of it) and has the part's Part ID line,
or, without one, the part's name (as the runner's duplicate check matches them).

The panel writes the card format students type (cards.py), plus a Part ID line and who sent it.
"""

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from autocam_core.hotfolder import write_atomic

from ..autostart import box
from ..cards import (MATERIAL_RE, MAX_QTY, PART_ID_RE, QTY_LINE_RES, README_CARD, SCRIBE_RE, TITLE_QTY_RE, _plain,
                     scribe_value)
from ..config import Config
from ..materials import _by_hint
from ..fits import _FIT, parse_fit
from ..own_programs import OWN_PROGRAM_RE
from ..onshape.urls import LinkError, configuration_query, first_onshape_url, parse_link
from ..run_state import RunStore
from ..tracker.base import Card, Tracker

log = logging.getLogger("autocam.panel")

ACTIVE = ("drafts", "queued", "back", "sheet")
# A part changed in Onshape after it was CAM'd (and before it's cut): the CAD changed label on its card and its sheet
# cards, and this box on its card to cut it as it was anyway. Nesting it again clears it too.
ACCEPT_LIST = "CAD changed"
ACCEPT_ITEM = "Cut it as it was CAM'd (the change doesn't matter here)"
PREVIEWS_KEPT = 20


def norm(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def created(card_id: str) -> int:
    """When Trello made the card (its id starts with the time, in hex); 0 if this id doesn't."""
    try:
        return int(card_id[:8], 16)
    except ValueError:
        return 0


@dataclass(frozen=True)
class PartCard:
    card: Card
    status: str                    # drafts | queued | back | sheet | cut
    did: str
    eid: str
    name_key: str
    part_id: Optional[str]
    qty: int
    order: int = 0                 # board order, for cards whose ids carry no time (tests)
    configuration: str = ""        # its link's configuration ("" the default): another configuration is another part
    spare: bool = False            # the Spare label: spares for the spares box, apart from the part's own card


def read_part_card(card: Card, status: str, order: int = 0, spare_label: str = "Spare") -> Optional[PartCard]:
    url = first_onshape_url(card.desc or "")
    if not url:
        return None
    try:
        link = parse_link(url)
    except LinkError:
        return None
    title = " ".join(card.name.split())
    m = TITLE_QTY_RE.match(title)
    name = m["name"] if m and m["name"] else title
    plain = _plain(card.desc)
    found = [v.strip() for rx in QTY_LINE_RES for v in rx.findall(plain)] + ([m["qty"]] if m and m["name"] else [])
    qty = int(found[0]) if found and found[0].isdigit() else 0
    ids = PART_ID_RE.findall(plain)
    return PartCard(card, status, link.did, link.eid, norm(name), ids[0] if ids else None, qty, order,
                    link.configuration, norm(spare_label) in {norm(l) for l in card.labels})


def is_this_part(pc: PartCard, did: str, eid: str, pid: str, name: str, configuration: str = "") -> bool:
    if (pc.did, pc.eid, pc.configuration) != (did, eid, configuration):
        return False
    if pc.part_id:
        return pc.part_id == pid
    return pc.name_key == norm(name)


@dataclass(frozen=True)
class Send:
    """What the panel's form says, checked by the api."""
    did: str
    wvm: str
    wvmid: str
    eid: str
    pid: str
    name: str                      # the part's name in Onshape (the card's title)
    host: str                      # the Onshape host for the link (cad.onshape.com or the enterprise's)
    qty: int
    nest: bool = True
    tabs: bool = True
    face: bool = False
    rush: bool = False
    smoked: bool = False
    tool_eighth: bool = False      # the Tool 1/8 label: the 1/8 in endmill even when the 4 mm could cut it
    drafts: bool = False
    material_line: str = ""        # e.g. "6061": only when Onshape doesn't have the material
    fit_line: str = ""             # e.g. "1.125 hole" or "outline"
    scribe: str = ""               # a bent part's bend lines scribed: "endmill" or "chamfer" (a Scribe: line)
    sender: str = ""
    notes: str = ""                # one line
    configuration: str = ""        # a configured Part Studio's configuration, in the link ("" the default)
    spare: bool = False            # spares (the Spare label): a card of their own, nested after the robot parts


SENT_BY_RE = re.compile(r"^\s*Sent from the Onshape panel by ", re.IGNORECASE)
SPARES_BY_RE = re.compile(r"^\s*Spares sent from ", re.IGNORECASE)      # a spares card copied from a part's card
NOTE_RE = re.compile(r"^\s*Note\s*:\s*(.*)$", re.IGNORECASE)


def _panel_line(line: str) -> bool:
    """A line the panel writes itself (so a card made over doesn't get it twice)."""
    plain = _plain(line)
    return bool(first_onshape_url(line) or any(rx.match(plain) for rx in QTY_LINE_RES) or PART_ID_RE.match(plain)
                or MATERIAL_RE.match(plain) or _FIT.match(plain) or SENT_BY_RE.match(plain) or NOTE_RE.match(plain)
                or SPARES_BY_RE.match(plain)
                or OWN_PROGRAM_RE.match(plain) or SCRIBE_RE.match(plain))


def kept_lines(desc: str) -> List[str]:
    """What someone typed on a card that the panel doesn't write: kept, below the panel's lines, when the panel
    makes the card over."""
    return [ln for ln in (desc or "").split("\n") if ln.strip() and not _panel_line(ln)]


def card_desc(s: Send, keep: Optional[List[str]] = None) -> str:
    lines = [f"https://{s.host}/documents/{s.did}/{s.wvm}/{s.wvmid}/e/{s.eid}{configuration_query(s.configuration)}",
             f"Qty: {s.qty}", f"Part ID: {s.pid}"]
    if s.material_line:
        lines.append(f"Material: {s.material_line}")
    if s.fit_line:
        lines.append(f"Fit: {s.fit_line}")
    if s.scribe:
        lines.append(f"Scribe: {'endmill' if s.scribe == 'endmill' else 'chamfer mill'}")
    lines.append(f"Sent from the Onshape panel by {s.sender or 'someone'}")
    if s.notes:
        lines.append(f"Note: {s.notes}")
    return "\n".join(lines + list(keep or []))


def with_qty(desc: str, qty: int) -> str:
    """The description with its quantity line(s) replaced by one Qty: line, after the link."""
    lines = [ln for ln in (desc or "").split("\n")
             if not any(rx.match(_plain(ln)) for rx in QTY_LINE_RES)]
    at = next((i + 1 for i, ln in enumerate(lines) if first_onshape_url(ln)), 0)
    return "\n".join(lines[:at] + [f"Qty: {qty}"] + lines[at:])


class PanelBoard:
    def __init__(self, tracker: Tracker, cfg: Config, store: RunStore, clock: Callable[[], float] = time.monotonic):
        self.t = tracker
        self.cfg = cfg
        self.store = store
        self.clock = clock
        targets = cfg.trello.targets
        self.status_of = {"inbox": "drafts", "ready_for_cam": "queued", "needs_fixing": "back",
                          targets["part_nested"]: "sheet", targets["part_cut"]: "cut"}
        self._lock = threading.RLock()
        self._cards: Optional[Tuple[float, List[PartCard]]] = None
        self._sheet_lists: Dict[str, Tuple[float, Optional[str]]] = {}
        self._previews: Dict[str, bytes] = {}
        self._jobs: Tuple[Tuple, Dict[str, Dict[str, Any]]] = ((), {})
        self._queue: Optional[Tuple[float, Dict[str, Any]]] = None
        self.flags_file = Path(store.sheets_file).with_name("panel_flags.json")

    # ---- reading
    def part_cards(self, fresh: bool = False) -> List[PartCard]:
        with self._lock:
            if (not fresh and self._cards is not None
                    and self.clock() - self._cards[0] < self.cfg.panel.board_cache_s):
                return self._cards[1]
            found: List[PartCard] = []
            for list_key, status in self.status_of.items():
                for card in self.t.list_cards(list_key):
                    if card.is_template or card.name.strip() == README_CARD:
                        continue
                    pc = read_part_card(card, status, len(found), self.cfg.labels.spare)
                    if pc is not None:
                        found.append(pc)
            self._cards = (self.clock(), found)
            return found

    def invalidate(self) -> None:
        with self._lock:
            self._cards = None

    def for_part(self, did: str, eid: str, pid: str, name: str, cards: Optional[List[PartCard]] = None,
                 card_id: str = "", cfg: str = "", spare: bool = False) -> Optional[PartCard]:
        """The part's card: the newest one still going (Drafts to On a sheet), else the newest cut one.
        card_id: that card, if it's still one of this part's. cfg: the configuration (each is its own part).
        spare: its spares' card instead (the Spare label); the part's own card never counts its spares."""
        mine = [pc for pc in (cards if cards is not None else self.part_cards())
                if is_this_part(pc, did, eid, pid, name, cfg) and pc.spare == spare]
        if card_id:
            return next((pc for pc in mine if pc.card.id == card_id), None)
        newest = sorted(mine, key=lambda pc: (created(pc.card.id), pc.order), reverse=True)
        going = [pc for pc in newest if pc.status in ACTIVE]
        return (going or newest or [None])[0]

    def sheets_of(self, card_id: str) -> List[Tuple[str, Dict[str, Any]]]:
        sheets = self.store.sheet_cards()
        return sorted(((sid, e) for sid, e in sheets.items() if card_id in (e.get("parts") or [])
                       and not e.get("archived")), key=lambda x: str(x[1].get("label") or x[1].get("run") or ""))

    def _sheet_list(self, sheet_id: str) -> Optional[str]:
        with self._lock:
            hit = self._sheet_lists.get(sheet_id)
            if hit is not None and self.clock() - hit[0] < self.cfg.panel.board_cache_s:
                return hit[1]
        try:
            key = self.t.get_card(sheet_id).list_key
        except Exception as e:  # noqa: BLE001 - the status still shows without it
            log.info("panel: reading sheet card %s: %s", sheet_id, e)
            key = None
        with self._lock:
            self._sheet_lists[sheet_id] = (self.clock(), key)
        return key

    def chip(self, pc: Optional[PartCard]) -> Dict[str, str]:
        """The short status shown beside a part in the panel's list."""
        if pc is None:
            return {"status": "none", "label": "Not sent"}
        if pc.status == "sheet":
            labels = [str(e.get("label") or "a sheet") for _, e in self.sheets_of(pc.card.id)]
            return {"status": "sheet", "label": f"On {labels[-1]}" if labels else "On a sheet",
                    **({"changed": True} if self.open_change(pc.card.id) is not None else {})}
        return {"status": pc.status, "label": {"drafts": "In Drafts", "queued": "Waiting to nest",
                                               "back": "Sent back", "cut": "Cut"}[pc.status]}

    def detail(self, pc: PartCard) -> Dict[str, Any]:
        """Everything the panel shows about a part's card."""
        out: Dict[str, Any] = {**self.chip(pc), "card": pc.card.id, "url": pc.card.url, "qty": pc.qty, "sheets": []}
        change = self.open_change(pc.card.id) if pc.status in ("sheet", "cut") else None
        if change is not None:
            out["cadChanged"] = {"what": change.get("what") or [], "cut": pc.status == "cut"}
        if pc.status in ("drafts", "back"):
            out["form"] = self.settings(pc)              # "Edit and send" / "Send again" start from the card
        sheets = self.sheets_of(pc.card.id) if pc.status in ("sheet", "cut") else []
        lists = []
        for sid, e in sheets:
            where = "cut" if e.get("cut") else self._sheet_list(sid)
            lists.append(where)
            out["sheets"].append({"id": sid, "label": e.get("label") or "a sheet", "url": e.get("url") or "",
                                  "where": where or ""})
        nested_w = ", ".join(s["label"] for s in out["sheets"])
        cut_list = self.cfg.trello.targets["part_cut"]
        steps = [["Sent", ""], ["Nested", nested_w], ["Reviewed", ""], ["Cut", ""]]
        if pc.status == "drafts":
            steps[0][1], done, now = "in Drafts: move it to Ready for CAM when it's ready", 0, 0
        elif pc.status == "queued":
            done, now = 1, 1
            steps[1][1] = "with the next run (about 2 minutes after the last card arrives)"
        elif pc.status == "back":
            done, now = 1, 1
            out["reason"] = self.reason(pc.card.id)
        elif pc.status == "cut":
            done, now = 4, 4
        else:                                            # on a sheet
            cut = sum(1 for w in lists if w in ("cut", cut_list))
            reviewed = sum(1 for w in lists if w in ("ready_to_cut", "cut", cut_list))
            if lists and cut == len(lists):
                done, now = 4, 4
            elif lists and reviewed == len(lists):
                done, now = 3, 3
                if cut:
                    steps[3][1] = f"{cut} of {len(lists)} sheets"
            else:
                done, now = 2, 2
        out["steps"] = [{"t": t, "w": w, "state": "done" if i < done else "now" if i == now else "todo"}
                        for i, (t, w) in enumerate(steps)]
        return out

    def settings(self, pc: PartCard) -> Dict[str, Any]:
        """What the card says now, to start the panel's form from when it's made over (Drafts, sent back)."""
        card, tc, lb = pc.card, self.cfg.trello, self.cfg.labels
        labels = {norm(l) for l in card.labels}
        fit = parse_fit(card.desc)
        notes = [m.group(1).strip() for m in (NOTE_RE.match(_plain(ln)) for ln in (card.desc or "").split("\n")) if m]
        scribes = [scribe_value(v) for v in SCRIBE_RE.findall(_plain(card.desc or ""))]
        return {"qty": pc.qty, "face": norm(lb.face) in labels, "rush": norm(lb.rush) in labels,
                "smoked": norm(lb.smoked) in labels, "tool_eighth": norm(lb.tool_eighth) in labels,
                "sheet": self._sheet(card, norm(lb.smoked) in labels),
                "nest": box(card, tc.nest_checklist, tc.nest_item, True),
                "tabs": box(card, tc.tabs_checklist, tc.tabs_item, self.cfg.tabs.default),
                "fit": ({"kind": "outline"} if fit[0] == "outline" else {"kind": "hole", "size": fit[1]})
                if isinstance(fit, tuple) else {"kind": "none"},
                "scribe": next((v for v in scribes if v), ""),
                "notes": " / ".join(n for n in notes if n), "kept": kept_lines(card.desc)}

    def _sheet(self, card: Card, smoked: bool) -> str:
        """Which sheet the card picks for Onshape's material, for the panel's Sheet choice: the smoked material for
        the Smoked label, a Material: line's (SRPP Twill), or "" (Onshape's own)."""
        materials, named = self.cfg.materials, set(self.cfg.onshape.material_map.values())
        if smoked:
            return next((k for k, m in materials.items() if m.color == "smoked"), "")
        for hint in MATERIAL_RE.findall(_plain(card.desc or "")):
            key = _by_hint(hint.strip(), materials) if hint.strip() else None
            if key is not None and key not in named:
                return key
        return ""

    def reason(self, card_id: str) -> str:
        """Why a card was sent back: its newest comment (AutoCAM's), without the closing how-to line."""
        try:
            text = self.t.last_comment(card_id) or ""
        except Exception as e:  # noqa: BLE001 - the card link still shows
            log.info("panel: reading %s's comments: %s", card_id, e)
            return ""
        text = re.split(r"\n\s*\nFix it, then move this card", text)[0].strip()
        lines = text.split("\n")
        if len(lines) > 1 and lines[0].rstrip().endswith(":") and all(l.startswith("- ") for l in lines[1:]):
            items = [l[2:] for l in lines[1:]]           # "Not queued:" and a list: just what to fix
            text = items[0] if len(items) == 1 else "\n".join("- " + i for i in items)
        return text[:1200]

    def preview(self, sheet_id: str) -> Optional[bytes]:
        """The sheet card's preview picture (as attached, with the part labels)."""
        if sheet_id not in self.store.sheet_cards():
            return None                                  # only AutoCAM's own sheet cards
        card = self.t.get_card(sheet_id)
        pngs = [a for a in card.attachments if a.name.lower().endswith(".png") and a.is_upload]
        if not pngs:
            return None
        att = pngs[-1]
        with self._lock:
            if att.id in self._previews:
                return self._previews[att.id]
        data = self.t.download(att)
        with self._lock:
            while len(self._previews) >= PREVIEWS_KEPT:
                self._previews.pop(next(iter(self._previews)))
            self._previews[att.id] = data
        return data

    # ---- the machine's queue
    def queue(self) -> Dict[str, Any]:
        """Sheets waiting at the machine (AutoCAM's sheet cards in Sheet review and Ready to cut) and their cutting
        time (from the cards' titles: "... - 25 min - r034 S2"), and part cards waiting to be nested."""
        with self._lock:
            if self._queue is not None and self.clock() - self._queue[0] < self.cfg.panel.board_cache_s:
                return self._queue[1]
        ours = self.store.sheet_cards()
        counts = {"sheet_review": 0, "ready_to_cut": 0}
        minutes = 0
        for list_key in counts:
            for card in self.t.list_cards(list_key):
                info = ours.get(card.id)
                if info is None or not info.get("cuttable") or info.get("cut"):
                    continue
                counts[list_key] += 1
                m = re.search(r"(\d+) min", card.name)
                minutes += int(m.group(1)) if m else 0
        waiting = sum(1 for pc in self.part_cards() if pc.status == "queued")
        out = {"sheets": counts["sheet_review"] + counts["ready_to_cut"], "review": counts["sheet_review"],
               "ready": counts["ready_to_cut"], "minutes": minutes, "waiting": waiting}
        with self._lock:
            self._queue = (self.clock(), out)
        return out

    # ---- the manufacturing version: what a card was CAM'd from
    def cammed(self, card_id: str) -> Optional[Dict[str, Any]]:
        """The newest job's record of this card's part ({"run", "job", "did", "vid", "eid", "part_id", "url",
        "microversion"}), or None (not CAM'd yet, or from a .step attachment). Read from state/jobs, re-read only
        when a job file changes."""
        folder = Path(self.store.jobs_dir)
        files = sorted(folder.glob("*.json")) if folder.is_dir() else []
        stamp = tuple((p.name, p.stat().st_mtime_ns) for p in files)
        with self._lock:
            if stamp != self._jobs[0]:
                index: Dict[str, Dict[str, Any]] = {}
                for p in sorted(files, key=lambda p: (_run_number(p.stem), p.stem)):
                    try:
                        job = json.loads(p.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    for part in job.get("parts") or []:
                        ref = part.get("onshape")
                        if part.get("card_id") and isinstance(ref, dict):
                            index[part["card_id"]] = {"run": job.get("run_id") or "", "job": p.stem, **ref}
                self._jobs = (stamp, index)
            return self._jobs[1].get(card_id)

    def flag_changed(self, pc: PartCard, name: str, what: List[str], key: str) -> int:
        """Comment once on each sheet the card is on that isn't cut yet: its part changed after it was CAM'd.
        Returns how many sheets were told."""
        return sum(self.flag_sheet(sid, pc.card.id, name, what, key) for sid, e in self.sheets_of(pc.card.id)
                   if not e.get("cut"))

    def _flags(self) -> Dict[str, Any]:
        try:
            return dict(json.loads(self.flags_file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}

    def _save_flags(self, flags: Dict[str, Any]) -> None:
        self.flags_file.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.flags_file, (json.dumps(flags, indent=1) + "\n").encode("utf-8"))

    def flag_sheet(self, sheet_id: str, card_id: str, name: str, what: List[str], key: str) -> bool:
        """Once per change of this part (`key`: its shape now): a comment and the CAD changed label on the sheet
        card, and on the part card the label, a comment and the box to cut it as it was anyway."""
        label = self.cfg.labels.cad_changed
        with self._lock:
            flags = self._flags()
            if flags.get(f"{sheet_id}:{card_id}") == key:
                return False
            self.t.comment(sheet_id, f"{name} was changed in Onshape after this sheet was CAM'd ({', '.join(what)}). "
                                     f"Check it before cutting: tick \"{ACCEPT_ITEM.split(' (')[0]}\" on its card to "
                                     "cut it anyway, or nest the new version (move its card back to Ready for CAM, or "
                                     "the Onshape panel's button).")
            self.t.add_label(sheet_id, label)
            flags[f"{sheet_id}:{card_id}"] = key
            part = flags.get(f"part:{card_id}")
            if not isinstance(part, dict) or part.get("key") != key or part.get("done"):
                self.t.add_label(card_id, label)
                self.t.remove_checklists(card_id, ACCEPT_LIST)
                self.t.add_checklist(card_id, ACCEPT_LIST, [ACCEPT_ITEM])
                self.t.comment(card_id, f"Changed in Onshape after it was CAM'd ({', '.join(what)}). To cut the new "
                                        "version, move this card back to Ready for CAM (or press \"Nest the new "
                                        "version\" in the Onshape panel). To cut it as it was, tick the CAD changed box.")
                part = {"key": key, "job": (self.cammed(card_id) or {}).get("job") or "", "name": name,
                        "what": list(what), "sheets": []}
            if sheet_id not in part["sheets"]:
                part["sheets"].append(sheet_id)
            flags[f"part:{card_id}"] = part
            self._save_flags(flags)
            self.invalidate()
            return True

    def open_change(self, card_id: str) -> Optional[Dict[str, Any]]:
        """The card's change not settled yet ({"what", "name", "sheets", ...}), or None."""
        with self._lock:
            part = self._flags().get(f"part:{card_id}")
        return part if isinstance(part, dict) and not part.get("done") else None

    def accept_change(self, pc: PartCard, by: str) -> bool:
        """Cut it as it was CAM'd (the panel's "Cut it as it is"): the same as ticking the box on its card."""
        return self._settle(pc.card.id, pc, "accepted", f"accepted in the Onshape panel by {by or 'someone'}")

    def _settle(self, card_id: str, pc: Optional[PartCard], how: str, why: str) -> bool:
        label = self.cfg.labels.cad_changed
        with self._lock:
            flags = self._flags()
            part = flags.get(f"part:{card_id}")
            if not isinstance(part, dict) or part.get("done"):
                return False
            if pc is not None:
                self.t.remove_label(card_id, label)
                if how == "accepted":
                    self.t.remove_checklists(card_id, ACCEPT_LIST)
                    self.t.add_checklist(card_id, ACCEPT_LIST, [ACCEPT_ITEM], checked=True)
            part["done"] = how
            flags[f"part:{card_id}"] = part
            still = {sid for k, f in flags.items() if k.startswith("part:") and isinstance(f, dict)
                     and not f.get("done") for sid in f.get("sheets") or []}
            for sid in part.get("sheets") or []:
                if how == "accepted":
                    self.t.comment(sid, f"{part.get('name') or 'A part'}: cut as it was CAM'd ({why}).")
                if sid not in still:
                    self.t.remove_label(sid, label)
            self._save_flags(flags)
            self.invalidate()
            log.info("panel: %s's CAD change settled: %s (%s)", part.get("name") or card_id, how, why)
            return True

    def settle_changes(self) -> int:
        """Clear the CAD changed labels of parts whose change is settled: the box ticked (cut it as it was), or the
        card back in Ready for CAM, sent back, nested again from Onshape as it is now, or gone. A card cut with
        its change unsettled keeps its label (what was cut is the old version)."""
        with self._lock:
            open_ = {k[5:]: f for k, f in self._flags().items()
                     if k.startswith("part:") and isinstance(f, dict) and not f.get("done")}
        if not open_:
            return 0
        cards = {pc.card.id: pc for pc in self.part_cards(fresh=True)}
        settled = 0
        for card_id, part in open_.items():
            pc = cards.get(card_id)
            if pc is not None and box(pc.card, ACCEPT_LIST, ACCEPT_ITEM, False):
                how, why = "accepted", "the box on its card was ticked"
            elif pc is None:
                how, why = "gone", "its card isn't on the board"
            elif pc.status in ("drafts", "queued", "back"):
                how, why = "renested", "its card went back"
            elif pc.status == "sheet" and (self.cammed(card_id) or {}).get("job", part.get("job")) != part.get("job"):
                how, why = "renested", "nested again as it is now"
            else:
                continue
            settled += self._settle(card_id, pc, how, why)
        return settled

    def relink(self, pc: PartCard, old_vid: str, new_vid: str, assembly: str, what: List[str]) -> bool:
        """A card sent from an assembly for a part from another document's saved version follows the assembly to the
        newer version it uses now (panel/assemblies.py): its link's version changes, with a comment. False when the
        link isn't that version any more (someone changed it by hand): left alone."""
        card = self.t.get_card(pc.card.id)
        lines = (card.desc or "").split("\n")
        at = next((i for i, ln in enumerate(lines) if first_onshape_url(ln)), None)
        if at is None or f"/v/{old_vid}/" not in lines[at]:
            return False
        lines[at] = lines[at].replace(f"/v/{old_vid}/", f"/v/{new_vid}/", 1)
        self.t.update_card(card.id, card.name, "\n".join(lines))
        change = f": {', '.join(what)}" if what else "; its shape is the same"
        self.t.comment(card.id, f"{assembly or 'Its assembly'} uses a newer version of this part now{change}. "
                                "This card links to that version now.")
        self.invalidate()
        return True

    def renest(self, pc: PartCard) -> Card:
        """A card on a sheet back to Ready for CAM, so the next run nests its part as it is now."""
        self.t.move(pc.card.id, "ready_for_cam")
        self.invalidate()
        return self.t.get_card(pc.card.id)

    # ---- writing
    def write(self, s: Send, existing: Optional[PartCard] = None) -> Card:
        """A new part card, or `existing` (sent back) made over with this form and moved to its list."""
        t, tc, labels = self.t, self.cfg.trello, self.cfg.labels
        target = "inbox" if s.drafts else "ready_for_cam"
        desc = card_desc(s, kept_lines(existing.card.desc) if existing is not None else None)
        if existing is None:
            card = t.create_card(target, s.name, desc)
        else:
            card = existing.card
            t.update_card(card.id, s.name, desc)
            t.remove_checklists(card.id, tc.nest_checklist)
            t.remove_checklists(card.id, tc.tabs_checklist)
        t.add_checklist(card.id, tc.nest_checklist, [tc.nest_item], checked=s.nest)
        t.add_checklist(card.id, tc.tabs_checklist, [tc.tabs_item], checked=s.tabs)
        have = {norm(l) for l in card.labels}
        for name, on in ((labels.spare, s.spare), (labels.face, s.face), (labels.rush, s.rush),
                         (labels.smoked, s.smoked), (labels.tool_eighth, s.tool_eighth)):
            if on and norm(name) not in have:
                t.add_label(card.id, name)
            elif not on and norm(name) in have:
                t.remove_label(card.id, name)
        if existing is None:
            self._one_box_each(card.id, s.nest, s.tabs)
        elif existing.card.list_key != target:
            t.move(card.id, target)
        self.invalidate()
        return t.get_card(card.id)

    def _one_box_each(self, card_id: str, nest: bool, tabs: bool) -> None:
        """A service tick can give a brand-new card its own Nest and Tabs boxes between our create and our boxes
        (it adds them to cards that have none): keep exactly one of each, as the form said."""
        tc = self.cfg.trello
        checks = self.t.get_card(card_id).checks
        for name, item, on in ((tc.nest_checklist, tc.nest_item, nest), (tc.tabs_checklist, tc.tabs_item, tabs)):
            if sum(1 for c in checks if c.checklist == name) > 1:
                self.t.remove_checklists(card_id, name)
                self.t.add_checklist(card_id, name, [item], checked=on)

    def change_qty(self, pc: PartCard, qty: int) -> Card:
        """A new quantity on a card still going. One already on a sheet goes back to Ready for CAM, so the next run
        nests the new quantity (as the how-to card says to do by hand)."""
        title = " ".join(pc.card.name.split())
        m = TITLE_QTY_RE.match(title)
        if m and m["name"]:
            title = m["name"]
        self.t.update_card(pc.card.id, title, with_qty(pc.card.desc, qty))
        if pc.status == "sheet":
            self.t.move(pc.card.id, "ready_for_cam")
        self.invalidate()
        return self.t.get_card(pc.card.id)

    # ---- spares (2026-11-04, the user's pick: a count per send, robot parts first): a card of their own with the
    # Spare label, never the part's own card, so a part can have both going at once
    def spare_line(self, pc: Optional[PartCard]) -> str:
        """What a part's newest spares card says in a few words ("" if it has none): "2 spares waiting to nest"."""
        if pc is None:
            return ""
        n = f"{pc.qty} spare{'' if pc.qty == 1 else 's'}"
        if pc.status == "back":
            return "Spares sent back"
        if pc.status in ("drafts", "queued"):
            return f"{n} {'in Drafts' if pc.status == 'drafts' else 'waiting to nest'}"
        sheets = self.sheets_of(pc.card.id)
        if pc.status == "sheet":
            label = str(sheets[-1][1].get("label") or "") if sheets else ""
            return f"{n} on {label}" if label else f"{n} on a sheet"
        when = max((str(e.get("cut_utc") or "") for _, e in sheets), default="")
        return f"{n} cut {short_day(when)}" if short_day(when) else f"{n} cut"

    def add_spares(self, s: Send) -> Card:
        """s.qty spares of a part (s.spare): added to its spares card if one waits in the list this send goes to
        (Drafts or Ready for CAM), else a new card."""
        pc = self.for_part(s.did, s.eid, s.pid, s.name, self.part_cards(fresh=True), cfg=s.configuration, spare=True)
        if pc is not None and pc.status == ("drafts" if s.drafts else "queued"):
            return self.change_qty(pc, min(MAX_QTY, pc.qty + s.qty))
        return self.write(s)

    def spares_from(self, src: PartCard, n: int, sender: str, where: str = "the shop page") -> Card:
        """n spares copied from a part's own card (the phone's Send spares, and the panel for a part that has a
        card going): its link and its Part ID, Material, Fit and Scribe lines, its Smoked, Tool 1/8 and Face
        labels and its Nest and Tabs boxes, with Qty: n and the Spare label, in Ready for CAM. Added to its spares
        card instead if one is waiting there."""
        t, tc, lb = self.t, self.cfg.trello, self.cfg.labels
        pc = self.for_part(src.did, src.eid, src.part_id or "", src.name_key, self.part_cards(fresh=True),
                           cfg=src.configuration, spare=True)
        if pc is not None and pc.status == "queued":
            return self.change_qty(pc, min(MAX_QTY, pc.qty + n))
        title = " ".join(src.card.name.split())
        m = TITLE_QTY_RE.match(title)
        if m and m["name"]:
            title = m["name"]
        keep = [ln.strip() for ln in (src.card.desc or "").split("\n")
                if first_onshape_url(ln) or any(rx.match(_plain(ln)) for rx in (PART_ID_RE, MATERIAL_RE, _FIT, SCRIBE_RE))]
        at = next((i + 1 for i, ln in enumerate(keep) if first_onshape_url(ln)), 0)
        desc = "\n".join(keep[:at] + [f"Qty: {n}"] + keep[at:] + [f"Spares sent from {where} by {sender or 'someone'}"])
        card = t.create_card("ready_for_cam", title, desc)
        t.add_label(card.id, lb.spare)
        nest = box(src.card, tc.nest_checklist, tc.nest_item, True)
        tabs = box(src.card, tc.tabs_checklist, tc.tabs_item, self.cfg.tabs.default)
        t.add_checklist(card.id, tc.nest_checklist, [tc.nest_item], checked=nest)
        t.add_checklist(card.id, tc.tabs_checklist, [tc.tabs_item], checked=tabs)
        have = {norm(l) for l in src.card.labels}
        for name in (lb.smoked, lb.tool_eighth, lb.face):
            if norm(name) in have:
                t.add_label(card.id, name)
        self._one_box_each(card.id, nest, tabs)
        self.invalidate()
        return t.get_card(card.id)


def short_day(utc: str) -> str:
    """"2026-11-03T22:10:00Z" -> "Nov 3" (the laptop's local day); "" if it isn't a time."""
    try:
        d = datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return ""
    return f"{d:%b} {d.day}"


def _run_number(stem: str) -> int:
    m = re.match(r"r(\d+)", stem)
    return int(m.group(1)) if m else -1
