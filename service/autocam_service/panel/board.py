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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from autocam_core.hotfolder import write_atomic

from ..autostart import box
from ..cards import MATERIAL_RE, PART_ID_RE, QTY_LINE_RES, README_CARD, TITLE_QTY_RE, _plain
from ..config import Config
from ..fits import _FIT, parse_fit
from ..own_programs import OWN_PROGRAM_RE
from ..onshape.urls import LinkError, first_onshape_url, parse_link
from ..run_state import RunStore
from ..tracker.base import Card, Tracker

log = logging.getLogger("autocam.panel")

ACTIVE = ("drafts", "queued", "back", "sheet")
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


def read_part_card(card: Card, status: str, order: int = 0) -> Optional[PartCard]:
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
    return PartCard(card, status, link.did, link.eid, norm(name), ids[0] if ids else None, qty, order)


def is_this_part(pc: PartCard, did: str, eid: str, pid: str, name: str) -> bool:
    if (pc.did, pc.eid) != (did, eid):
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
    sender: str = ""
    notes: str = ""                # one line


SENT_BY_RE = re.compile(r"^\s*Sent from the Onshape panel by ", re.IGNORECASE)
NOTE_RE = re.compile(r"^\s*Note\s*:\s*(.*)$", re.IGNORECASE)


def _panel_line(line: str) -> bool:
    """A line the panel writes itself (so a card made over doesn't get it twice)."""
    plain = _plain(line)
    return bool(first_onshape_url(line) or any(rx.match(plain) for rx in QTY_LINE_RES) or PART_ID_RE.match(plain)
                or MATERIAL_RE.match(plain) or _FIT.match(plain) or SENT_BY_RE.match(plain) or NOTE_RE.match(plain)
                or OWN_PROGRAM_RE.match(plain))


def kept_lines(desc: str) -> List[str]:
    """What someone typed on a card that the panel doesn't write: kept, below the panel's lines, when the panel
    makes the card over."""
    return [ln for ln in (desc or "").split("\n") if ln.strip() and not _panel_line(ln)]


def card_desc(s: Send, keep: Optional[List[str]] = None) -> str:
    lines = [f"https://{s.host}/documents/{s.did}/{s.wvm}/{s.wvmid}/e/{s.eid}", f"Qty: {s.qty}", f"Part ID: {s.pid}"]
    if s.material_line:
        lines.append(f"Material: {s.material_line}")
    if s.fit_line:
        lines.append(f"Fit: {s.fit_line}")
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
                    pc = read_part_card(card, status, len(found))
                    if pc is not None:
                        found.append(pc)
            self._cards = (self.clock(), found)
            return found

    def invalidate(self) -> None:
        with self._lock:
            self._cards = None

    def for_part(self, did: str, eid: str, pid: str, name: str, cards: Optional[List[PartCard]] = None,
                 card_id: str = "") -> Optional[PartCard]:
        """The part's card: the newest one still going (Drafts to On a sheet), else the newest cut one.
        card_id: that card, if it's still one of this part's."""
        mine = [pc for pc in (cards if cards is not None else self.part_cards())
                if is_this_part(pc, did, eid, pid, name)]
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
            return {"status": "sheet", "label": f"On {labels[-1]}" if labels else "On a sheet"}
        return {"status": pc.status, "label": {"drafts": "In Drafts", "queued": "Waiting to nest",
                                               "back": "Sent back", "cut": "Cut"}[pc.status]}

    def detail(self, pc: PartCard) -> Dict[str, Any]:
        """Everything the panel shows about a part's card."""
        out: Dict[str, Any] = {**self.chip(pc), "card": pc.card.id, "url": pc.card.url, "qty": pc.qty, "sheets": []}
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
        return {"qty": pc.qty, "face": norm(lb.face) in labels, "rush": norm(lb.rush) in labels,
                "smoked": norm(lb.smoked) in labels, "tool_eighth": norm(lb.tool_eighth) in labels,
                "nest": box(card, tc.nest_checklist, tc.nest_item, True),
                "tabs": box(card, tc.tabs_checklist, tc.tabs_item, self.cfg.tabs.default),
                "fit": ({"kind": "outline"} if fit[0] == "outline" else {"kind": "hole", "size": fit[1]})
                if isinstance(fit, tuple) else {"kind": "none"},
                "notes": " / ".join(n for n in notes if n), "kept": kept_lines(card.desc)}

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

    def flag_sheet(self, sheet_id: str, card_id: str, name: str, what: List[str], key: str) -> bool:
        """One comment on the sheet card per change of this part (`key`: its shape now)."""
        with self._lock:
            try:
                flags = dict(json.loads(self.flags_file.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                flags = {}
            if flags.get(f"{sheet_id}:{card_id}") == key:
                return False
            self.t.comment(sheet_id, f"{name} was changed in Onshape after this sheet was CAM'd ({', '.join(what)}). "
                                     "Check it before cutting; the Onshape panel can nest the new version.")
            flags[f"{sheet_id}:{card_id}"] = key
            self.flags_file.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(self.flags_file, (json.dumps(flags, indent=1) + "\n").encode("utf-8"))
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
        for name, on in ((labels.face, s.face), (labels.rush, s.rush), (labels.smoked, s.smoked),
                         (labels.tool_eighth, s.tool_eighth)):
            if on and norm(name) not in have:
                t.add_label(card.id, name)
            elif not on and norm(name) in have:
                t.remove_label(card.id, name)
        if existing is None:
            self._one_box_each(card.id, s)
        elif existing.card.list_key != target:
            t.move(card.id, target)
        self.invalidate()
        return t.get_card(card.id)

    def _one_box_each(self, card_id: str, s: Send) -> None:
        """A service tick can give a brand-new card its own Nest and Tabs boxes between our create and our boxes
        (it adds them to cards that have none): keep exactly one of each, as the form said."""
        tc = self.cfg.trello
        checks = self.t.get_card(card_id).checks
        for name, item, on in ((tc.nest_checklist, tc.nest_item, s.nest), (tc.tabs_checklist, tc.tabs_item, s.tabs)):
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


def _run_number(stem: str) -> int:
    m = re.match(r"r(\d+)", stem)
    return int(m.group(1)) if m else -1
