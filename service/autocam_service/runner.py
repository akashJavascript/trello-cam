"""One service tick: guard Ready to cut, collect finished jobs, or start a run when cards are waiting.

Trigger: cards arriving in Ready for CAM (autostart.py). A run starts `trello.start_delay_s` after the last
one arrived, takes every card there whose "Nest this part" box is ticked, and comments on the System card.
One run at a time; cards that arrive meanwhile start the next one.

Open sheets: a sheet card still in Sheet review that nobody has ticked a Review item on is "open". A run
nests the parts on the open sheets of a material again together with the new parts of that material, so
new parts fill the space left on them. The open sheet cards are then updated in place: the old program and
preview are deleted first, then the new ones attached and the checklists reset. Parts already on them cost
no Onshape calls (their STEP files are cached). If someone starts reviewing an open sheet while the run is
going, that sheet is left as it was, and the new parts that were going on it wait for the next run.

Start: the run is saved first (phase "starting"), so a crash anywhere later resumes the same run
instead of starting a new one and paying for Onshape exports again. Then: parse every waiting card
(problems -> Needs fixing, before any Onshape call), take as many as fit the per-run Onshape limit
(the rest start the next run), export STEP (cached by version), resolve materials, batch by material,
and write one job per batch into the hot folder, recording each job before submitting it.

Collect: for each finished job, re-check every program (results.py), put each sheet on a sheet card
in Sheet review (a new card, or an open one updated in place) with the .tap (only if it passed), the
preview and the checklists, then comment on and move each part card. Every Trello write is recorded
in the run state the first time it succeeds, so a restart resumes without duplicates. Nothing is ever
moved to Ready to cut.

A dry run (DryRunTracker) keeps its own run state and "d" run ids, so it never touches a real
run and a real run never finishes a dry one.
"""

import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from autocam_core import CORE_VERSION
from autocam_core.airtest import SUFFIX as AIR_SUFFIX, AirTestError, air_test_name, air_test_program, check_air_test
from autocam_core.fixture import clamp_clear_z
from autocam_core.hotfolder import Queue, write_atomic
from autocam_core.offcuts import add_used, as_loaded, compact, cut_away, free_length, to_own, turn_rect
from autocam_core.pauses import PauseError, remove as remove_pauses
from autocam_core.sheetcheck import check_sheet_program, pause_spec
from autocam_core.schema import SchemaError
from autocam_core.schema_job import OffcutSpec, PartSpec, job_json, load_job
from autocam_core.tapguard import GuardSpec, check_program

from . import health, labels, tally
from . import sheet_cards as text
from .autostart import ReadyWatch, box, signature, wants_nest
from .batching import ReadyPart, make_batches
from .cards import (MATERIAL_RE, README_CARD, STEP_SUFFIXES, TITLE_QTY_RE, CardProblem, PartRequest, parse_card,
                    problem_comment)
from .offcuts import _number as _scrap_number
from .config import Config
from .handprograms import ProgramCut, footprint, is_program, parse_cutter, program_cuts
from .own_programs import (OWN_PROGRAM_RE, PLACE_MYSELF_RE, own_qty, pack_copies, parse_thickness, placed_name,
                           program_attachments, repeat_program, shift_program)
from .fits import FitStore, key as fit_key, parse_fit, stocks as fit_stocks
from .jobs import JobBuildError, build_job, face_spec, fit_spec, sheet_fixture
from .materials import resolve_material
from .panel.usage import TEAM_USAGE_FILE, card_line as team_usage_line
from .offcuts import (SCRAP_HELP, OffcutStore, card_text, hand_cut_line, load_line, parse_hand_cuts, parse_scrap,
                      same_cut)
from .onshape.budget import REFUSE, WARN, decide, estimate_calls
from .onshape.cache import sha256_file
from .onshape.client import BudgetExceeded, OnshapeError, QuotaExhausted, RateLimited
from .onshape.export import Exporter, ExportError, TryAgainLater
from .onshape.ledger import Ledger, budget_year_start, utc_now
from .onshape.urls import first_onshape_url
from .results import IngestedJob, VerifiedSheet, ingest
from .run_state import (
    COLLECTING, FAILED, GAVE_UP, PUBLISHED, QUEUED, STARTING, JobState, RunState, RunStore, once,
)
from .tracker.base import Card, CardNotFound, Tracker
from .tracker.dryrun import DryRunTracker

log = logging.getLogger("autocam.runner")
STOPPING = (BudgetExceeded, QuotaExhausted, RateLimited)


def onshape_problem(e: OnshapeError) -> str:
    """What to tell the card when Onshape refuses a request about that part (seen 2026-10-02: 400 "Error
    retrieving Part Metadata" on the first real card). One card's problem never stops the run."""
    m = re.search(r'"message"\s*:\s*"([^"]*)"', str(e))
    said = m.group(1) if m else str(e).split(": ", 1)[-1][:200]
    return (f"Onshape couldn't read that link ({e.status}: {said}). Check that the link opens a **Part Studio** "
            "tab (not an Assembly or Drawing), and that the part is in it")
NO_STOP_SUFFIX = "_NOSTOP"
LAYOUT_CORE = (0, 4, 0)   # jobs before this laid the sheet's length along X; the machine's X runs across the bed


def _label(name: str) -> str:
    """Label names match whatever their case and spacing."""
    return re.sub(r"\s+", " ", name).strip().lower()


def _version(v: str) -> Tuple[int, ...]:
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return (0,)



LEGACY_AIR_CHECKLIST = "Air test"   # the first version's own box (on r005's card), now an item in Options
WRITE_ATTEMPTS = 3   # a Trello write that fails on this many ticks is given up (logged), so no run is stuck forever


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Services:
    cfg: Config
    tracker: Tracker
    queue: Queue
    store: RunStore
    ledger: Ledger
    exporter_for: Callable[[str], Exporter]      # run_id -> exporter (with or without an Onshape client)
    clock: Callable[[], datetime] = utc_now


def _thickness(value: float) -> float:
    return round(float(value), 4)


class Runner:
    def __init__(self, s: Services, start_delay_s: Optional[float] = None):
        """start_delay_s: overrides trello.start_delay_s (0 for one-off passes: `tick --now`, `dry-run`)."""
        self.s = s
        self.cfg = s.cfg
        self.t = s.tracker
        self.targets = s.cfg.trello.targets
        self.dry_run = isinstance(s.tracker, DryRunTracker)
        delay = s.cfg.trello.start_delay_s if start_delay_s is None else start_delay_s
        self.watch = ReadyWatch(s.store.watch_file, delay)
        self.offcuts = OffcutStore(s.store.offcuts_file)
        self.fits = FitStore(s.store.offcuts_file.with_name("fits.json"))
        self.waiting_count = 0                                   # cards waiting for a run (for the status)
        self.rush_count = 0                                      # ...of which have the Rush label
        self._status_sent: Optional[Tuple[str, datetime]] = None  # (status without its time, when it was sent)

    # ------------------------------------------------------------ tick
    def tick(self) -> None:
        self.check_ready_to_cut()
        self.follow_cut()
        self.free_offcuts()
        self.scan_offcuts()
        self.retire_old_layout()
        self.sheet_options()
        self.fit_results()
        self.own_programs()
        waiting = self.ready_cards()
        state = self.s.store.active()
        if state is not None and state.dry_run != self.dry_run:
            log.warning("active run %s is a %s run; this %s tick leaves it alone", state.run_id,
                        "dry" if state.dry_run else "real", "dry" if self.dry_run else "real")
            return
        start = self.watch.observe(waiting, self.s.clock())
        fresh = self.watch.waiting_cards(waiting)
        rush = [c for c in fresh if self._rush(c)]
        self.waiting_count, self.rush_count = len(fresh), len(rush)
        if state is None:
            if rush:
                self.start_run(rush, rush=True)       # no waiting for more cards, and before anything else
            elif start:
                self.start_run(waiting)
            return
        if state.phase == STARTING:
            self.resume_start(state)
        self.finish_start(state)
        self.collect(state)

    # ------------------------------------------------------------ status (M5)
    def status(self, last_error: Optional[str] = None) -> health.Health:
        led, cfg, now = self.s.ledger, self.cfg, self.s.clock()
        active = self.s.store.active()
        return health.Health(
            now=now, heartbeat=self.s.queue.read_heartbeat(), service_core=CORE_VERSION,
            queue_incoming=len(self.s.queue.pending()),
            queue_processing=len(list(self.s.queue.processing.glob("*.json"))),
            month_calls=led.month_count(panel=False), month_soft=cfg.onshape.monthly_soft_calls,
            year_calls=led.year_count(cfg.onshape.budget_year_start, panel=False), year_cap=cfg.onshape.yearly_cap_calls,
            latch=led.latched(), active_run=active.run_id if active else None, last_error=last_error,
            waiting_cards=self.waiting_count, next_run_in_s=self.watch.remaining_s(now), rush_cards=self.rush_count,
            season=self.season_text(now), team_usage=team_usage_line(cfg.paths.state / TEAM_USAGE_FILE, now,
                                                          cfg.panel.team_usage_hours))

    def season_text(self, now: datetime) -> str:
        """The System card's stock tally (tally.py): sheet cards that went to Cut since status.season_start."""
        since = budget_year_start(now, self.cfg.status.season_start)
        cut = []
        for info in self.s.store.sheet_cards().values():
            if not info.get("cut") or not info.get("job") or info.get("material") not in self.cfg.materials:
                continue
            when = info.get("cut_utc")
            if when and datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=since.tzinfo) < since:
                continue
            area = info.get("parts_area_in2")
            size = self._size(info["material"])
            cut.append(tally.CutSheet(self._stock_label(info["material"], info["thickness_in"]),
                                      bool(info.get("offcut_id")),
                                      area if area is not None else self._result_area(info),
                                      bool(info.get("by_hand")), size[0] * size[1]))
        return tally.season_text(cut, since, self.cfg.sheet.width_in * self.cfg.sheet.length_in,
                                 len(self.offcuts.all()))

    def _result_area(self, info: Dict) -> Optional[float]:
        """A sheet's parts area from its job's result.json (sheet cards published before it was recorded)."""
        try:
            result = json.loads((self.s.queue.done / info["job"] / "result.json").read_text(encoding="utf-8"))
            sheet = next(s for s in result.get("sheets", []) if s.get("index") == info.get("index"))
            return float(sheet["parts_area_in2"]) if sheet.get("parts_area_in2") is not None else None
        except (OSError, ValueError, StopIteration, KeyError, TypeError):
            return None

    def _rush(self, card: Card) -> bool:
        return _label(self.cfg.labels.rush) in {_label(l) for l in card.labels}

    def report_health(self, last_error: Optional[str] = None) -> None:
        """The System card's description (health.py): rewritten when anything in it changes, and every
        status.update_every_s so its time shows the service is alive. One comment when jobs are waiting for a
        Fusion that isn't running, and one when Fusion is back."""
        card = self.cfg.trello.cards.get("system")
        if not card or self.dry_run:
            return
        h = self.status(last_error)
        stale_after = self.cfg.status.fusion_stale_after_s
        same = health.render(h, stale_after, with_time=False)
        if (self._status_sent is None or self._status_sent[0] != same
                or (h.now - self._status_sent[1]).total_seconds() >= self.cfg.status.update_every_s):
            self.t.update_card(card, "System", health.render(h, stale_after))
            self._status_sent = (same, h.now)
        said = self.s.store.alerts().get("fusion_down", False)
        if health.stuck(h, stale_after) and not said:
            self.t.comment(card, text.fusion_down_comment(h.queue_incoming + h.queue_processing))
            self.s.store.set_alert("fusion_down", True)
        elif said and not health.is_stale(h, stale_after):
            self.t.comment(card, text.fusion_back_comment())
            self.s.store.set_alert("fusion_down", False)

    def ready_cards(self) -> List[Card]:
        """The part cards in Ready for CAM: nested if their "Nest this part" box is ticked, placed by hand if not
        (decided in start_run). Part cards in Drafts and Ready for CAM that have no box get one, ticked, and a
        "Hold it in with tabs" box, ticked if tabs.default."""
        t = self.cfg.trello
        ready: List[Card] = []
        fresh: List[Card] = []
        for list_key in ("inbox", "ready_for_cam"):
            for card in self.t.list_cards(list_key):
                if card.is_template or card.name.strip() == README_CARD or self._own_program_card(card):
                    continue                          # own programs: placed by own_programs, not nested
                if not any(c.checklist == t.nest_checklist for c in card.checks):
                    try:
                        self.t.add_checklist(card.id, t.nest_checklist, [t.nest_item], checked=True)
                    except Exception as e:  # noqa: BLE001 - a missing box mustn't stop the tick (no box = nest)
                        log.warning("couldn't add the %s box to %s: %s", t.nest_checklist, card.id, e)
                if not any(c.checklist == t.tabs_checklist for c in card.checks):
                    try:
                        self.t.add_checklist(card.id, t.tabs_checklist, [t.tabs_item], checked=self.cfg.tabs.default)
                    except Exception as e:  # noqa: BLE001 - no box means tabs.default
                        log.warning("couldn't add the %s box to %s: %s", t.tabs_checklist, card.id, e)
                if list_key == "ready_for_cam":
                    ready.append(card)
                fresh.append(card)
        try:
            self.warn_duplicates(fresh)
        except Exception as e:  # noqa: BLE001 - a warning mustn't stop the tick
            log.warning("checking for duplicate part cards: %s", e)
        return ready

    def warn_duplicates(self, fresh: Sequence[Card]) -> None:
        """A part card in Drafts or Ready for CAM for a part another active card already has (the same name, and
        the same Part Studio when both link one; Cut doesn't count, cutting again is normal): one comment naming
        the other card(s). Checked again only when the card changes."""
        if self.dry_run:
            return
        path = self.s.store.sheets_file.with_name("duplicates.json")
        try:
            checked = dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            checked = {}
        todo = [c for c in fresh if checked.get(c.id) != signature(c)]
        if not todo:
            return
        cfg = self.cfg
        others = list(fresh) + [c for key in ("needs_fixing", self.targets["part_nested"])
                                for c in self.t.list_cards(key) if not c.is_template and c.name.strip() != README_CARD]

        def identity(card: Card):
            parsed = parse_card(card, cfg.labels.smoked, cfg.labels.tool_eighth, cfg.labels.face)
            if isinstance(parsed, CardProblem):           # still the same part: matched by its title alone
                m = TITLE_QTY_RE.match(card.name.strip())
                return " ".join((m["name"] if m else card.name).split()).casefold(), None
            link = parsed.link                            # another configuration is another part
            return " ".join(parsed.name.split()).casefold(), (link.did, link.eid, link.configuration) if link else None

        ids = {c.id: identity(c) for c in {c.id: c for c in others}.values()}
        names = {"inbox": "Drafts", "ready_for_cam": "Ready for CAM", "needs_fixing": "Needs fixing",
                 self.targets["part_nested"]: "On a sheet"}
        for card in todo:
            mine = ids.get(card.id)
            same = [] if mine is None else [
                o for o in {c.id: c for c in others}.values()
                if o.id != card.id and ids.get(o.id) and ids[o.id][0] == mine[0]
                and (mine[1] is None or ids[o.id][1] is None or ids[o.id][1] == mine[1])]
            if same:
                where = "; ".join(f"{o.url} ({names.get(o.list_key, o.list_key)})" for o in same)
                self.t.comment(card.id, f"Another card has this part too: {where}. If it's the same part, archive "
                                        "one so it isn't cut twice (this card's Qty is added, not shared).")
            checked[card.id] = signature(card)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, (json.dumps(checked, indent=1) + "\n").encode("utf-8"))

    # ------------------------------------------------------------ guard
    def check_ready_to_cut(self) -> None:
        """Decision 16: a sheet card in Ready to cut with an incomplete checklist goes back.

        Sheet cards whose program was rejected never stay there either. Cards the service didn't
        make and that have no review checklist are left alone.
        """
        name = self.cfg.trello.checklist_name
        expected = len(self.cfg.trello.checklist)
        ours = self.s.store.sheet_cards()
        for card in self.t.list_cards("ready_to_cut"):
            info = ours.get(card.id)
            if info is not None and not info.get("cuttable"):
                self._send_back(card.id, "Moved back: this sheet failed the safety checks, so it can't be cut.")
                continue
            state = self.t.checklist(card.id, name)
            if state is None:
                if info is not None:
                    self._send_back(card.id, f"Moved back: the {name} checklist is missing.")
                continue
            if state.complete and state.total >= expected:
                continue
            self._send_back(card.id, f"Moved back: tick every {name} item first ({state.done} of {state.total} done).")

    def follow_cut(self) -> None:
        """Part cards follow their sheet(s) to Cut: once every sheet a part is on is in Cut, the part card moves
        there too (only from On a sheet, so a card someone moved by hand is left alone)."""
        ours = self.s.store.sheet_cards()
        newly = [c.id for c in self.t.list_cards(self.targets["part_cut"]) if c.id in ours and not ours[c.id].get("cut")]
        if not newly:
            return
        cut_cards = {c.id: c for c in self.t.list_cards(self.targets["part_cut"])}
        for sheet_id in newly:
            try:
                self._offcut_after_cut(cut_cards[sheet_id], ours[sheet_id])
            except Exception as e:  # noqa: BLE001 - the offcut is a nice-to-have; the parts still follow
                log.warning("offcut for sheet card %s: %s", sheet_id, e)
            self.s.store.mark_sheet_cut(sheet_id, _iso(self.s.clock()))
        ours = self.s.store.sheet_cards()
        for part_id in dict.fromkeys(p for s in newly for p in ours[s].get("parts", [])):
            on = [info for info in ours.values() if part_id in info.get("parts", [])]
            if not all(info.get("cut") for info in on):
                continue
            try:
                if self.t.get_card(part_id).list_key == self.targets["part_nested"]:
                    self.t.move(part_id, self.targets["part_cut"])
            except Exception as e:  # noqa: BLE001 - a deleted card mustn't stop the tick
                log.warning("couldn't move part card %s to Cut: %s", part_id, e)

    # ------------------------------------------------------------ offcuts
    def free_offcuts(self) -> None:
        """An offcut held by a sheet card that was archived or deleted without being cut is free again (its
        sheet card won't be cut, so nothing else would release it). A card archived while in Cut counts as
        cut. One Trello read per held offcut."""
        if self.dry_run:
            return
        reg = self.s.store.sheet_cards()
        for offcut_id, piece in self.offcuts.all().items():
            holder = piece.get("reserved_by")
            if not holder:
                continue
            try:
                card: Optional[Card] = self.t.get_card(holder)
            except CardNotFound:
                card = None                           # deleted
            except Exception as e:  # noqa: BLE001 - Trello down: try again next pass
                log.warning("couldn't check sheet card %s holding offcut %s: %s", holder, offcut_id, e)
                continue
            if card is not None and not card.closed:
                continue
            info = reg.get(holder, {})
            if card is not None and card.list_key == self.targets["part_cut"] and not info.get("cut"):
                try:
                    self._offcut_after_cut(card, info)    # archived from Cut before a pass saw it there
                except Exception as e:  # noqa: BLE001
                    log.warning("offcut for sheet card %s: %s", holder, e)
                self.s.store.mark_sheet_cut(holder, _iso(self.s.clock()))
                self.offcuts.release_all([holder])
                continue
            self.offcuts.release_all([holder])
            if holder in reg and not info.get("cut"):
                reg[holder]["archived"] = True        # its parts are free to go on another sheet too
                self.s.store._write_sheets(reg)
            label = info.get("label") or "its sheet card"
            how = "deleted" if card is None else "archived"
            self.t.comment(offcut_id, f"Free again: {label} was {how} without being cut.")
            log.info("offcut %s is free again: sheet card %s was %s", offcut_id, holder, how)

    def _size(self, material: Optional[str]) -> Tuple[float, float]:
        """A sheet of this material's (length, width): its own size, else [sheet]'s (Config.sheet_for)."""
        sheet = self.cfg.sheet_for(material)
        return sheet.length_in, sheet.width_in

    def _free_after(self, used, material: Optional[str] = None) -> float:
        """The longest free stretch a sheet (of this material) with these used stretches would offer the next run."""
        region = sheet_fixture(self.cfg, material).nest_region_in
        return free_length(used, self._size(material)[0], region[1], region[3], self.cfg.nest.offcut_gap_in)

    def _stock_label(self, material_key: str, thickness_in: float) -> str:
        return f"{text.material_label(self.cfg.materials[material_key])} {text.thickness_label(thickness_in)}"

    def available_offcuts(self, material: str, reopened: Sequence[str]) -> List[OffcutSpec]:
        """The offcuts a job of this material may use: in the Offcuts list, and not reserved by a sheet card
        (except one this run is rebuilding). Offcuts whose card left the list are forgotten."""
        on_board = {c.id for c in self.t.list_cards("offcuts")}
        out = []
        for offcut_id, piece in self.offcuts.all().items():
            if offcut_id not in on_board:
                if not piece.get("missing"):          # a missing one is kept on record in case it turns up
                    self.offcuts.remove(offcut_id)
                continue
            reserved = piece.get("reserved_by")
            if piece["material"] != material or (reserved and reserved not in reopened) or piece.get("missing"):
                continue
            out.append(OffcutSpec(offcut_id, piece["thickness_in"], tuple(tuple(s) for s in piece["used"]),
                                  bool((piece.get("last") or {}).get("turned", False)),
                                  tuple(tuple(r) for r in piece.get("beside", [])), not piece.get("scrap")))
        return out

    def _stock_line(self, vs: VerifiedSheet, info: Optional[Dict] = None) -> Optional[str]:
        """The sheet card's Stock line when the sheet isn't a plain new sheet: which offcut and which end goes
        where, or (info: its stock was changed at the machine) the offcut or new sheet it changed to."""
        if info and info.get("converted"):
            return self._stock_text(info.get("offcut_id"), info["material"], info["thickness_in"],
                                    bool(info.get("turned")), info.get("bands") or [], info.get("instead_of"), True)
        if not vs.sheet.offcut_id:
            return None
        return self._stock_text(vs.sheet.offcut_id, "", vs.sheet.thickness_in, vs.sheet.offcut_turned)

    def _stock_text(self, offcut_id: Optional[str], material: str, thickness: float, turned: bool,
                    bands: Sequence[Sequence[float]] = (), instead_of: Optional[int] = None,
                    changed: bool = False) -> str:
        missing = f"offcut #{instead_of}" if instead_of else "the offcut it was made for"
        if not offcut_id:
            a, b = min(x for x, _ in bands), max(y for _, y in bands)
            length, width = self._size(material)
            return (f"Stock: a new {self._stock_label(material, thickness)} sheet, {width:g} x "
                    f"{length:g} ({missing} wasn't on the rack; the program is the same). The parts "
                    f"are cut {a:.0f} to {b:.0f} in from the front; the rest of the sheet becomes an offcut.")
        piece = self.offcuts.get(offcut_id)
        if piece is None:
            return "Stock: an offcut that's no longer in the Offcuts list. Check with whoever archived it."
        last = piece.get("last") or {}
        line = load_line(self._stock_label(piece["material"], thickness), piece.get("url", ""),
                         last.get("label", "its last sheet"), tuple(last.get("stretch", (0.0, 0.0))), turned,
                         self._size(piece["material"])[0], bool(last.get("turned", False)), piece.get("number"),
                         piece.get("length_in") if piece.get("scrap") else None, bool(piece.get("hand_cuts")),
                         gap_in=self._gap_after_cuts())
        return line + (f" ({missing[0].upper() + missing[1:]} wasn't on the rack; this one fits the same program.)"
                       if changed else "")

    def _offcut_after_cut(self, card: Card, info: Dict) -> None:
        """A sheet card just went to Cut: update the offcut it was cut from, or keep the rest as a new one. The
        rest is the free stretch along the length and the room beside the parts cut (offcuts.py); an offcut with
        neither left is used up."""
        if info.get("own"):
            self._own_after_cut(card, info)
            return
        used_along = info.get("used_along") or info.get("used_x")      # used_x: sheets published before 2026-10-03
        if info.get("converted"):                     # its stock was changed at the machine: the same program cut
            bands = [tuple(b) for b in info.get("bands") or []]     # these bands, nothing beside earlier cuts
            beside_used: Set[int] = set()
        else:
            bands = [tuple(used_along)] if used_along else []
            beside_used = set(info.get("beside_used") or ())
        if not info.get("job") or not (bands or beside_used) or not info.get("cuttable") \
                or info.get("offcut_done"):
            return
        cfg, (length, width) = self.cfg, self._size(info.get("material"))
        label = info.get("label") or f"{info.get('run')} S{info.get('index')}"
        turned = bool(info.get("turned"))
        own = [add_used((), b, length, turned)[0] for b in bands]          # in its own coordinates
        cut = (min(a for a, _ in own), max(b for _, b in own)) if own else None
        left = [list(to_own(tuple(r), width, length, turned)) for r in info.get("beside_left") or ()]
        offcut_id = info.get("offcut_id")
        if offcut_id:
            piece = self.offcuts.get(offcut_id)
            if piece is None:
                return                                # its card was archived: the sheet is gone
            used = tuple(tuple(s) for s in piece["used"])
            for b in bands:
                used = add_used(used, b, length, turned)
            beside = compact([r for i, r in enumerate(piece.get("beside", [])) if i not in beside_used] + left)
            free = self._free_after(used, piece["material"])
            if free < cfg.nest.offcut_min_in and not beside:
                self.t.comment(offcut_id, f"Used up: {label} was cut from it.")
                self.t.archive(offcut_id)
                self.offcuts.remove(offcut_id)
                return
            stretch = list(cut) if cut else (piece.get("last") or {}).get("stretch", [0.0, 0.0])
            piece.update(used=[list(s) for s in used], beside=beside, reserved_by=None,
                         last={"label": label, "stretch": stretch, "turned": turned})
            self.offcuts.put(offcut_id, piece)
            self.t.update_card(offcut_id, *self._offcut_card_text(piece))
            self.t.comment(offcut_id, f"{label} was cut from it. "
                           + (f"{free:.0f} in free now" if free >= cfg.nest.offcut_min_in else "No free stretch now")
                           + (", plus room beside the cuts for small parts." if beside else "."))
            return
        keep = [c.done for c in card.checks if c.checklist == cfg.trello.offcut_checklist]
        if not any(keep) or cut is None:
            return                                    # no box (an older card), or someone unticked it
        used: Tuple = ()                              # a new sheet: end A was at the front
        for b in bands:
            used = add_used(used, b, length, False)
        free = self._free_after(used, info["material"])
        if free < cfg.nest.offcut_min_in and not left:
            return
        piece = {"material": info["material"], "thickness_in": info["thickness_in"], "used": [list(s) for s in used],
                 "beside": left, "last": {"label": label, "stretch": list(cut), "turned": False},
                 "reserved_by": None, "number": self.offcuts.next_number()}
        new = self.t.create_card("offcuts", *self._offcut_card_text(piece))
        piece["url"] = new.url
        self.offcuts.put(new.id, piece)
        sheets = self.s.store.sheet_cards()
        sheets.get(card.id, {})["offcut_done"] = True
        self.s.store._write_sheets(sheets)
        n = piece["number"]
        self.t.comment(card.id, f"The rest of the sheet is offcut #{n}: {new.url}. Write #{n} on it before it goes "
                                "on the rack.")

    def _offcut_card_text(self, piece: Dict) -> Tuple[str, str]:
        used = [tuple(s) for s in piece["used"]]
        return card_text(self._stock_label(piece["material"], piece["thickness_in"]),
                         self._free_after(used, piece["material"]), used,
                         (piece.get("last") or {}).get("label", ""), compact(piece.get("beside", [])),
                         self.cfg.nest.offcut_min_in, piece.get("number"),
                         piece.get("length_in") if piece.get("scrap") else None, piece.get("hand_cuts", []),
                         self._size(piece["material"])[1], self.cfg.hand_cuts.programs,
                         gap_in=self._gap_after_cuts() if piece.get("used") else 0.0)

    def _gap_after_cuts(self) -> float:
        """How far past an offcut's earlier cuts the next parts start: the part spacing kept round the earlier
        cuts plus the offcut slack (1 in until 2026-11-04, when the slack went from 0.5 to 0.25)."""
        return round(self.cfg.nest.part_spacing_in + self.cfg.nest.offcut_gap_in, 3)

    def scan_offcuts(self) -> None:
        """The Offcuts list each pass: offcuts without a number get one (their card says it), a missing offcut
        whose card is back gets offered again, a card someone made there is a scrap to add (parse_scrap), and
        "Cut by hand:" lines on an offcut's card are recorded (_hand_cuts)."""
        if self.dry_run:
            return
        cards = self.t.list_cards("offcuts")
        on_board = {c.id for c in cards}
        for offcut_id, piece in self.offcuts.all().items():
            if piece.get("missing") and offcut_id in on_board:
                piece["missing"] = False
                self.offcuts.put(offcut_id, piece)
                self.t.comment(offcut_id, "Back on the rack: runs can use it again.")
        pieces = self.offcuts.all()
        for card in cards:
            piece = pieces.get(card.id)
            try:
                if piece is None:
                    self._scrap_card(card)
                elif not piece.get("number"):
                    piece["number"] = self.offcuts.next_number()
                    self.offcuts.put(card.id, piece)
                    self.t.update_card(card.id, *self._offcut_card_text(piece))
                    self.t.comment(card.id, f"This is offcut #{piece['number']}: write #{piece['number']} on it.")
                else:
                    self._hand_cuts(card, piece)
            except Exception as e:  # noqa: BLE001 - one card mustn't stop the tick
                log.warning("offcut card %s: %s", card.id, e)

    def _scrap_card(self, card: Card) -> None:
        """A card someone made in Offcuts: a full-width piece of stock to add, or one reply saying what's
        missing (again only once the card changes)."""
        said = signature(card)
        if self.offcuts.replies().get(card.id) == said:
            return
        cfg = self.cfg

        def resolve(hint: str):
            choice = resolve_material(None, hint, False, cfg.onshape.material_map, cfg.materials)
            return choice.key, choice.problem or ""
        parsed = parse_scrap(f"{card.name}\n{card.desc}", cfg.materials, lambda key: self._size(key)[0], resolve)
        if isinstance(parsed, str):
            self.t.comment(card.id, f"Not added as an offcut yet: {parsed}\n\n{SCRAP_HELP}")
            self.offcuts.set_reply(card.id, said)
            return
        material, thickness, length = parsed
        full, width = self._size(material)
        used = [[round(length, 3), full]] if length < full - 1e-6 else []
        if self._free_after([tuple(u) for u in used], material) < cfg.nest.offcut_min_in:
            self.t.comment(card.id, f"Not added as an offcut: a {length:g} in piece is too short to nest on (it "
                                    f"needs about {cfg.nest.offcut_min_in + 2 * cfg.nest.offcut_gap_in + 0.5:g} in).")
            self.offcuts.set_reply(card.id, said)
            return
        piece = {"material": material, "thickness_in": thickness, "used": used, "beside": [],
                 "last": {"label": "", "stretch": [0.0, 0.0], "turned": False}, "reserved_by": None,
                 "url": card.url, "number": self.offcuts.next_number(), "scrap": True, "length_in": round(length, 3)}
        cuts, problems = parse_hand_cuts(f"{card.name}\n{card.desc}", width, length)
        made, more, ids = self._program_cuts(card, thickness, length, (), material)
        problems += more
        cuts += [pc.cut for pc in made if not any(same_cut(pc.cut, c) for c in cuts)]
        piece["programs"] = sorted({ids[pc.program] for pc in made})
        if cuts:
            self._apply_hand_cuts(piece, cuts)
        self.offcuts.put(card.id, piece)
        self.t.update_card(card.id, *self._offcut_card_text(piece))
        n = piece["number"]
        self.t.comment(card.id, f"Added as offcut #{n}. Write #{n} on it"
                       + (" and mark the end its hand cuts were measured from" if cuts else "")
                       + f". Runs put {self._stock_label(material, thickness)} parts on it before starting a new sheet."
                       + (f" Recorded {len(cuts)} hand cut{'s' if len(cuts) != 1 else ''}: runs keep clear of "
                          f"{'them' if len(cuts) != 1 else 'it'}." if cuts else "")
                       + (" Couldn't read: " + " ".join(problems) if problems else ""))
        log.info("added scrap %s as offcut #%s", card.id, n)

    def _apply_hand_cuts(self, piece: Dict, cuts: Sequence[Sequence[float]], record: bool = True) -> None:
        """Take parts cut out by hand (across, along, in the piece's own coordinates) out of an offcut's record.
        record: list them as "Cut by hand" on its card (not for an own program AutoCAM placed: that's a sheet)."""
        cfg = self.cfg
        region = sheet_fixture(cfg, piece["material"]).nest_region_in
        used, beside = [tuple(s) for s in piece["used"]], [tuple(r) for r in piece.get("beside", [])]
        for c in cuts:
            used, beside = cut_away(used, beside, tuple(c), self._size(piece["material"])[0], (region[0], region[2]),
                                    cfg.nest.part_spacing_in, cfg.nest.offcut_gap_in, cfg.nest.offcut_beside_min_in)
        if not piece.get("reserved_by"):
            beside = compact(beside)                  # no sheet holds a room by its place in the list
        piece.update(used=[list(s) for s in used], beside=[list(r) for r in beside])
        if record:
            piece["hand_cuts"] = [list(c) for c in piece.get("hand_cuts", [])] + [list(c) for c in cuts]

    def _hand_cuts(self, card: Card, piece: Dict) -> None:
        """"Cut by hand:" lines on an offcut's card: each new one is taken out of the offcut once (the card then
        shows it as read), a line that can't be read gets one reply, and a sheet card already nested on the
        offcut where the cut is gets told how to move."""
        cfg, t = self.cfg, self.cfg.trello
        full, width = self._size(piece["material"])
        piece_length = piece["length_in"] if piece.get("scrap") else full
        cuts, problems = parse_hand_cuts(card.desc, width, piece_length)
        new = [c for c in cuts if not any(same_cut(c, k) for k in piece.get("hand_cuts", []))]
        made, more, ids = self._program_cuts(card, piece["thickness_in"], piece_length, piece.get("programs", []),
                                             piece["material"])
        problems += more
        sources: Dict[int, ProgramCut] = {}
        for pc in made:                               # a program's cut, unless it's recorded already
            if not any(same_cut(pc.cut, k) for k in list(piece.get("hand_cuts", [])) + new):
                sources[len(new)] = pc
                new.append(pc.cut)
        if made:
            piece["programs"] = sorted(set(piece.get("programs", [])) | {ids[pc.program] for pc in made})
        if problems:
            said = signature(card)
            if self.offcuts.replies().get(card.id) != said:
                self.t.comment(card.id, "Couldn't record a hand cut: " + " ".join(problems))
                self.offcuts.set_reply(card.id, said)
        if not new:
            if made:
                self.offcuts.put(card.id, piece)      # its programs are read: they were recorded before
            return
        holder = piece.get("reserved_by")
        info = self.s.store.sheet_cards().get(holder, {}) if holder else {}
        bands = (info.get("bands") or self._bands(info.get("used_along"), info.get("beside_used") or (), card.id,
                                                  bool(info.get("turned")))) if holder else []
        self._apply_hand_cuts(piece, new)
        what = "; ".join(hand_cut_line(c, width).split(": ", 1)[1]
                         + (f" (from {sources[i].program})" if i in sources else "") for i, c in enumerate(new))
        notes = "".join(f" {pc.program}: {pc.note}." for pc in sources.values() if pc.note)
        free = self._free_after([tuple(s) for s in piece["used"]], piece["material"])
        rooms = compact(piece["beside"])
        if free < cfg.nest.offcut_min_in and not rooms and not holder:
            self.t.comment(card.id, f"Recorded the hand cut ({what}). Nothing worth nesting on is left, so it's "
                                    "archived." + notes)
            self.t.archive(card.id)
            self.offcuts.remove(card.id)
            return
        self.offcuts.put(card.id, piece)
        self.t.update_card(card.id, *self._offcut_card_text(piece))
        self.t.comment(card.id, f"Recorded the hand cut ({what}): runs keep clear of it. "
                       + (f"{free:.0f} in free now" if free >= cfg.nest.offcut_min_in else "No free stretch now")
                       + (", plus room beside the cuts for small parts." if rooms else ".") + notes)
        length, slack = full, cfg.nest.part_spacing_in + cfg.nest.offcut_gap_in
        turned = bool(info.get("turned"))
        spans = [(length - c[3], length - c[1]) if turned else (c[1], c[3]) for c in new]
        if any(s0 < b1 + slack and s1 > b0 - slack for s0, s1 in spans for b0, b1 in bands):
            n = piece.get("number")
            self.t.comment(holder, f"Offcut #{n} has a part cut out of it by hand ({what}) along the stretch this "
                                   f"program cuts. Check it before loading: if it's in the way, tick "
                                   f"\"{t.cut_away_item}\" under {t.stock_checklist} and this sheet moves to "
                                   "other stock.")

    def _program_cuts(self, card: Card, thickness: float, length: float, read: Sequence[str],
                      material: Optional[str] = None):
        """The programs attached to an offcut card that aren't recorded yet (handprograms.py): (the cuts they made
        where their Zeroed at lines say, problems, file name -> attachment id). Each is downloaded once."""
        hc = self.cfg.hand_cuts
        atts = [a for a in card.attachments if hc.programs and is_program(a.name) and a.id not in read]
        if not atts:
            return [], [], {}
        programs = []
        for a in atts:
            path = self.cfg.paths.cache / "trello" / f"{a.id}.tap"      # attachments never change once uploaded
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                write_atomic(path, self.t.download(a))
            programs.append((a.name, path.read_bytes()))
        made, problems = program_cuts(card.desc, programs, thickness, self._size(material)[1], length, hc.cutter_in)
        return made, problems, {a.name: a.id for a in atts}

    def _bands(self, used_along, beside_used: Sequence[int], offcut_id: Optional[str],
               turned: bool) -> List[List[float]]:
        """Where along the length (machine Y, as loaded) a sheet's program cuts: its free-stretch band and the
        bands of the rooms beside earlier cuts it used. Another piece of stock takes the same program if
        these are clear on it."""
        bands = [list(used_along)] if used_along else []
        piece = (self.offcuts.get(offcut_id) or {}) if offcut_id else {}
        rooms = piece.get("beside", [])
        length, width = self._size(piece.get("material"))
        for i in beside_used:
            if 0 <= i < len(rooms):
                r = turn_rect(tuple(rooms[i]), width, length) if turned \
                    else tuple(rooms[i])
                bands.append([r[1], r[3]])
        return bands

    def _fitting_offcut(self, material: str, thickness: float, bands: Sequence[Sequence[float]],
                        skip: str) -> Optional[Tuple[str, bool]]:
        """An offcut on the rack that the same program fits as it is (its used stretches clear of every band by
        the loading slack), and which way round: not spun if it can be helped, then the one with least room."""
        on_board = {c.id for c in self.t.list_cards("offcuts")}
        gap, length = self.cfg.nest.offcut_gap_in, self._size(material)[0]
        best = None
        for offcut_id, piece in self.offcuts.all().items():
            if (offcut_id == skip or offcut_id not in on_board or piece.get("missing") or piece.get("reserved_by")
                    or piece["material"] != material or abs(piece["thickness_in"] - thickness) > 1e-6):
                continue
            last = bool((piece.get("last") or {}).get("turned"))
            for turned in ((last,) if piece.get("scrap") else (last, not last)):
                used = as_loaded([tuple(s) for s in piece["used"]], length, turned)
                if all(b <= u0 - gap or a >= u1 + gap for a, b in bands for u0, u1 in used):
                    rank = (turned != last, self._free_after([tuple(s) for s in piece["used"]], material))
                    if best is None or rank < best[0]:
                        best = (rank, offcut_id, turned)
                    break
        return (best[1], best[2]) if best else None

    def _stock(self, card: Card, info: Dict) -> bool:
        """The Stock checklist on a sheet card nested onto an offcut: "the offcut isn't on the rack". Ticked,
        the missing offcut is archived (kept on record) and the card switches to another offcut the same
        program fits, or asks: re-nest on other stock, or cut it on a new sheet. True if the card changed."""
        t = self.cfg.trello
        if not info.get("offcut_id") or info.get("cut"):
            return False
        items = {c.item: c.done for c in card.checks if c.checklist == t.stock_checklist}
        if not items:
            self.t.add_checklist(card.id, t.stock_checklist, [t.not_found_item, t.cut_away_item])
            return False
        if items.get(t.renest_item):
            self._renest(card, info)
            return True
        if items.get(t.new_sheet_item):
            self._change_stock(card, info, None, False)
            self.t.comment(card.id, "Cut it on a new sheet: the program is the same, so it doesn't need another "
                                    "review. The Stock line says where on the sheet the parts are. The rest of the "
                                    "sheet becomes an offcut when this card goes to Cut.")
            return True
        blocked = items.get(t.cut_away_item)                # it's there, but already cut where this goes
        if not (items.get(t.not_found_item) or blocked) or info.get("asked"):
            return False
        missing = info["offcut_id"]
        piece = self.offcuts.get(missing) or {}
        n = piece.get("number")
        name = f"Offcut #{n}" if n else "The offcut"
        why = "has something cut where this program goes" if blocked else "isn't on the rack"
        if blocked and piece:
            self.t.comment(missing, f"At the machine, {info.get('label') or 'a sheet'} found something already cut "
                                    "where its program goes. Record it on this card with a line like Cut by hand: "
                                    "12 to 18 along, 5 to 10 across, so runs keep clear of it.")
        elif piece and not piece.get("missing"):
            piece["missing"] = True
            self.offcuts.put(missing, piece)
            self.t.comment(missing, f"Not found at the machine for {info.get('label') or 'a sheet'}. Archived; if it "
                                    "turns up, send this card back to Offcuts and runs use it again.")
            self.t.archive(missing)
        bands = info.get("bands") or self._bands(info.get("used_along"), info.get("beside_used") or (), missing,
                                                 bool(info.get("turned")))
        fit = self._fitting_offcut(info["material"], info["thickness_in"], bands, missing) if bands else None
        if fit is not None:
            self._change_stock(card, info, *fit)
            m = (self.offcuts.get(fit[0]) or {}).get("number")
            self.t.comment(card.id, f"{name} {why}, but this program fits offcut #{m} as it is: no "
                                    "new run, no new review. The Stock line says how to load it.")
            return True
        reg = self.s.store.sheet_cards()
        reg[card.id]["asked"] = True
        self.s.store._write_sheets(reg)
        self.t.add_checklist(card.id, t.stock_checklist, [t.renest_item, t.new_sheet_item])
        self.t.comment(card.id, f"{name} {why}, and no other offcut fits this program as it is. Tick "
                                f"one under {t.stock_checklist}: \"{t.renest_item}\" (its parts go back to Ready "
                                "for CAM with the Rush label and land on whatever stock there is) or "
                                f"\"{t.new_sheet_item}\".")
        return True

    def _change_stock(self, card: Card, info: Dict, offcut_id: Optional[str], turned: bool) -> None:
        """The same program, cut on another offcut (offcut_id, loaded `turned`) or a new sheet (None)."""
        old = (self.offcuts.get(info["offcut_id"]) or {}) if info.get("offcut_id") else {}
        bands = info.get("bands") or self._bands(info.get("used_along"), info.get("beside_used") or (),
                                                 info.get("offcut_id"), bool(info.get("turned")))
        self.offcuts.release_all([card.id])
        if offcut_id:
            self.offcuts.reserve(offcut_id, card.id)
        reg = self.s.store.sheet_cards()
        entry = reg[card.id]
        entry.update(offcut_id=offcut_id, turned=turned, converted=True, bands=bands, used_along=None,
                     beside_used=[], asked=False, instead_of=old.get("number"))
        if offcut_id is None:
            used: Tuple = ()
            for b in bands:
                used = add_used(used, tuple(b), self._size(info.get("material"))[0], False)
            entry["rest_in"] = round(self._free_after(used, info.get("material")), 2)
        self.s.store._write_sheets(reg)
        number = (self.offcuts.get(offcut_id) or {}).get("number") if offcut_id else None
        title = re.sub(r" offcut( #\d+)?", f" offcut #{number}" if offcut_id else "", card.name, count=1)
        lines = card.desc.split("\n")
        line = self._stock_line(None, entry)
        at = next((i for i, l in enumerate(lines) if l.startswith("Stock:")), None)
        if at is None:
            lines.insert(1 if lines and lines[0] == "LOAD" else 0, line)
        else:
            lines[at] = line
        self.t.update_card(card.id, title, "\n".join(lines))
        self.t.remove_checklists(card.id, self.cfg.trello.stock_checklist)

    def _renest(self, card: Card, info: Dict) -> None:
        """Give up on this sheet: its card is emptied and archived, and its parts go back to Ready for CAM with
        the Rush label, so they're nested at once on whatever stock there is."""
        n = (self.offcuts.get(info["offcut_id"]) or {}).get("number")
        label = info.get("label") or "its sheet"
        self.offcuts.release_all([card.id])
        self._clear_sheet(card.id)
        self.t.comment(card.id, "Re-nested on other stock: this card is archived, and its parts are back in Ready "
                                "for CAM with the Rush label.")
        self.t.archive(card.id)
        parts = info.get("parts", [])
        self.s.store.retire_sheet(card.id)
        nested = {c.id for c in self.t.list_cards(self.targets["part_nested"])}
        for part in parts:
            if part not in nested:
                continue
            self.t.move(part, self.targets["part_deferred"])
            self.t.add_label(part, self.cfg.labels.rush)
            self.t.comment(part, f"Back for a re-nest: {f'offcut #{n}' if n else 'the offcut'} for {label} wasn't on "
                                 "the rack. It has the Rush label, so it's nested again within a minute.")

    # ------------------------------------------------------------ fit results
    def fit_results(self) -> None:
        """A sheet card's Fit checklists (fits.py): the first run ticked as the one that fit is kept for that
        material and feature, so the next series starts there; "still tight after the last one" counts as one
        step past the last lap. Recorded again only if the ticks change."""
        if self.dry_run or not self.cfg.fit.enabled:
            return
        reg = self.s.store.sheet_cards()
        step = self.cfg.fit.step_in
        changed = False
        for list_key in (self.targets["sheet_created"], "ready_to_cut", self.targets["part_cut"]):
            for card in self.t.list_cards(list_key):
                info = reg.get(card.id)
                if not info or not info.get("fits"):
                    continue
                for f in info["fits"]:
                    ticked = [c.item for c in card.checks if c.checklist == f["checklist"] and c.done]
                    items = {label: stock for label, stock in f["items"]}
                    first = next((label for label, _ in f["items"] if label in ticked), None)
                    if first is None or first == f.get("recorded"):
                        continue
                    stock = items[first]
                    if stock is None:                 # still tight after the last lap
                        stock = round([s for _, s in f["items"] if s is not None][-1] - step, 4)
                    self.fits.record(f["key"], stock, info.get("label") or card.id)
                    f["recorded"] = first
                    changed = True
                    nxt = fit_stocks(self.cfg.fit.start_in, step, self.cfg.fit.laps, stock)[0]
                    self.t.comment(card.id, text.fit_recorded_comment(f["name"], f["feature"], stock, nxt))
        if changed:
            self.s.store._write_sheets(reg)

    # ------------------------------------------------------------ own programs (option 3)
    def _own_program_card(self, card: Card) -> bool:
        """A part card carrying someone's own program instead of a part: a .tap, and no Onshape link and no STEP
        file, or an "Own program:" line (the Onshape panel's cards link the part too)."""
        if not self.cfg.hand_cuts.place_programs or not program_attachments(card):
            return False
        if OWN_PROGRAM_RE.search(card.desc or ""):
            return True
        return not first_onshape_url(card.desc or "") and not any(
            a.name.lower().endswith(STEP_SUFFIXES) for a in card.attachments)

    def own_programs(self) -> None:
        """Cards in Ready for CAM with someone's own program (own_programs.py): each placed at once, on a card in
        Sheet review with the program moved there, or sent to Needs fixing saying why."""
        if self.dry_run or not self.cfg.hand_cuts.place_programs:
            return
        for card in self.t.list_cards("ready_for_cam"):
            if card.is_template or not self._own_program_card(card):
                continue
            try:
                self._place_own(card)
            except Exception as e:  # noqa: BLE001 - one card mustn't stop the tick
                log.warning("own program card %s: %s", card.id, e)

    def _refuse_own(self, card: Card, problems: Sequence[str]) -> None:
        self.t.move(card.id, self.targets["part_rejected"])
        self.t.comment(card.id, problem_comment("Not placed", problems))

    def _place_own(self, card: Card) -> None:
        cfg = self.cfg
        problems: List[str] = []
        progs = program_attachments(card)
        if len(progs) > 1:
            problems.append("More than one program attached. Make a card for each.")
        hints = [v.strip() for v in MATERIAL_RE.findall(card.desc or "") if v.strip()]
        key = None
        if not hints:
            problems.append("No Material line. Add one like Material: 6061.")
        else:
            smoked = _label(cfg.labels.smoked) in {_label(l) for l in card.labels}
            choice = resolve_material(None, hints[0], smoked, cfg.onshape.material_map, cfg.materials)
            key = choice.key
            if key is None:
                problems.append((choice.problem or "unknown material")[:1].upper() + (choice.problem or "")[1:] + ".")
        thickness = None
        if key is not None:
            thickness, why = parse_thickness(card.desc or "", cfg.materials[key].thicknesses_in, _scrap_number)
            if why:
                problems.append(why)
        cutter, why = parse_cutter(card.desc or "")
        problems += why
        qty, why_qty = own_qty(card.name, card.desc or "")
        if why_qty:
            problems.append(why_qty)
        if problems:
            self._refuse_own(card, problems)
            return
        att = progs[0]
        path = cfg.paths.cache / "trello" / f"{att.id}.tap"          # attachments never change once uploaded
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(path, self.t.download(att))
        data = path.read_bytes()
        cutter = cutter or cfg.hand_cuts.cutter_in
        box, why, note = footprint(data, thickness, cutter)
        if box is None:
            self._refuse_own(card, [f"{att.name}: " + "; ".join(why) + "."])
            return
        if note:
            self._refuse_own(card, [f"{att.name}: {note}. AutoCAM checks programs with Z0 on the spoilboard (the "
                                    "fixed tool setter): in Fusion put the setup's origin at the stock's bottom, "
                                    "post it again and attach that."])
            return
        self._retire_own(card.id, "its part card came back to Ready for CAM")
        if PLACE_MYSELF_RE.search(card.desc or ""):
            self._own_by_hand(card, att, data, key, thickness, cutter, box, qty)
            return
        fx = sheet_fixture(cfg, key)
        length, width = self._size(key)
        on_board = {c.id for c in self.t.list_cards("offcuts")}
        pieces = {oid: p for oid, p in self.offcuts.all().items()
                  if oid in on_board and not p.get("reserved_by") and not p.get("missing")
                  and p["material"] == key and abs(p["thickness_in"] - thickness) < 1e-6}
        m = cfg.nest.part_spacing_in / 2               # the footprint has the cutter in it: a little to spare
        mx = cfg.hand_cuts.place_spare_across_in       # less across, where the region already clears the clamps
        r = fx.nest_region_in
        region = (r[0] + mx, r[1] + m, r[2] - mx, r[3] - m)
        source = data.decode("ascii", errors="replace")
        repeat = qty > 1 and repeat_program(source, [(0.0, 0.0), (0.0, 0.0)]) is not None
        groups, pool, left = [], dict(pieces), qty
        while left:
            group = pack_copies(box, left, pool, region, width, length, cfg.nest.offcut_gap_in,
                                cfg.nest.part_spacing_in, repeat)
            if group is None:
                self._refuse_own(card, [f"{att.name} cuts {box[2] - box[0]:.1f} x {box[3] - box[1]:.1f} in (across "
                                        f"x along): more than a sheet's cutting area ({region[2] - region[0]:.1f} x "
                                        f"{region[3] - region[1]:.1f})."])
                return
            groups.append(group)
            left -= len(group.offsets)
            pool.pop(group.spot.offcut_id, None)

        def program_for(group, label):
            spot = group.spot
            many = len(group.offsets) > 1
            note = (f"AUTOCAM {label} - {att.name}" + (f" CUT {len(group.offsets)} TIMES" if many else
                                                       f" MOVED X+{spot.dx:g} Y+{spot.dy:g}")
                    + " - ZERO X/Y AT THE PIECE FRONT-LEFT CORNER")
            text_out = (repeat_program(source, group.offsets, note) if many else
                        shift_program(source, spot.dx, spot.dy, note=note))
            moved = text_out.encode("ascii", errors="replace")
            piece_length = (pieces[spot.offcut_id].get("length_in") if spot.offcut_id and
                            pieces[spot.offcut_id].get("scrap") else None) or length
            spec = GuardSpec(z_floor_in=cfg.machine.z_floor_in, allowed_g=frozenset(cfg.tapguard.allowed_g),
                             allowed_m=frozenset(cfg.tapguard.allowed_m), park=cfg.machine.park,
                             clamp_zones_in=fx.clamp_zones_in,
                             clamp_clear_z_in=clamp_clear_z(thickness, cfg.clamps.height_in,
                                                            cfg.tapguard.clamp_margin_in),
                             tool_radius_in=cutter / 2, reach_y_in=cfg.machine.reach_y_in, stock_top_in=thickness,
                             sheet_in=(width, piece_length))
            return moved, check_program(moved, spec)

        for group in groups:                           # every copy checked before any card is made
            _, report = program_for(group, "H0")
            if not report.passed:
                self._refuse_own(card, [f"{att.name}, moved to its spot, failed AutoCAM's checks: {report.summary()}"])
                return
        stock = self._stock_label(key, thickness)
        part = " ".join(card.name.split())
        placed: List[str] = []
        for group in groups:
            spot, copies = group.spot, len(group.offsets)
            label = self._own_label()
            name = placed_name(att.name, label)
            moved, report = program_for(group, label)
            number = (pieces[spot.offcut_id].get("number") if spot.offcut_id else None)
            title = (f"{stock}" + (f" offcut #{number}" if number else " offcut" if spot.offcut_id else "")
                     + f" - own program {att.name}" + (f" x{copies}" if copies > 1 else "") + f" - {part} - {label}")
            stock_line = (self._stock_text(spot.offcut_id, key, thickness, spot.turned) if spot.offcut_id else
                          f"Stock: a new {stock} sheet, {width:g} x {length:g}.")
            desc = text.own_program_description(
                program=att.name, placed=name, part=part, part_url=card.url, stock=stock_line, cutter_in=cutter,
                clamps=text.clamp_edges(fx.clamp_zones_in, width), spot=spot, lowest_z=report.min_z_in,
                offsets=group.offsets, of_qty=qty)
            new = self.t.create_card(self.targets["sheet_created"], title, desc)
            # On record at once (not cuttable yet), so a pass that stops half way replaces this card next time.
            self.s.store.register_sheet(new.id, label, False, [card.id])
            reg = self.s.store.sheet_cards()
            spacing = cfg.nest.part_spacing_in
            reg[new.id].update(
                label=label, url=new.url, material=key, thickness_in=thickness, offcut_id=spot.offcut_id,
                turned=spot.turned, bands=[[spot.cut_loaded[1] - spacing, spot.cut_loaded[3] + spacing]],
                own={"program": att.name, "placed": name, "cut_own": list(spot.cut_own),
                     "cut_loaded": list(spot.cut_loaded), "dx": spot.dx, "dy": spot.dy, "copies": copies,
                     "cuts_own": [list(c) for c in group.cuts_own]})
            self.s.store._write_sheets(reg)
            if spot.offcut_id:
                self.offcuts.reserve(spot.offcut_id, new.id)
            self.t.attach_program(new.id, name, moved, report)
            self.t.add_checklist(new.id, cfg.trello.checklist_name, cfg.trello.checklist)
            self.t.add_checklist(new.id, cfg.trello.machine_checklist_name, cfg.trello.machine_checklist)
            if spot.offcut_id is None:
                self.t.add_checklist(new.id, cfg.trello.offcut_checklist, [cfg.trello.offcut_item], checked=True)
            reg = self.s.store.sheet_cards()
            reg[new.id]["cuttable"] = True
            self.s.store._write_sheets(reg)
            where = f"offcut #{number}" if number else "an offcut" if spot.offcut_id else f"a new {stock} sheet"
            placed.append(f"{copies if copies > 1 else 'one'} on {where}: {new.url} ({label})" if qty > 1 else
                          f"{where}: {new.url} ({label})")
            log.info("own program %s on card %s placed as %s on %s (%d of %d)", att.name, card.id, label,
                     spot.offcut_id or "a new sheet", copies, qty)
        self.t.move(card.id, self.targets["part_nested"])
        if qty > 1:
            self.t.comment(card.id, f"Placed all {qty}: " + "; ".join(placed) + f". Each card has {att.name} moved "
                                    "there (cut once per copy where there's more than one), checked; review them like "
                                    "any sheet.")
        else:
            self.t.comment(card.id, f"Placed on {placed[0]}. Its card has {att.name} moved there, checked; review it "
                                    "like any sheet.")

    def _own_by_hand(self, card: Card, att, data: bytes, key: str, thickness: float, cutter: float, box,
                     qty: int = 1) -> None:
        """An own program with "Place it myself": its card in Sheet review with the program as it is, zeroed on
        whatever piece the operator picks (own_programs.py)."""
        cfg = self.cfg
        spec = GuardSpec(z_floor_in=cfg.machine.z_floor_in, allowed_g=frozenset(cfg.tapguard.allowed_g),
                         allowed_m=frozenset(cfg.tapguard.allowed_m), park=cfg.machine.park,
                         tool_radius_in=cutter / 2, stock_top_in=thickness)
        report = check_program(data, spec)
        if not report.passed:
            self._refuse_own(card, [f"{att.name} failed AutoCAM's checks: {report.summary()}"])
            return
        label = self._own_label()
        stock = self._stock_label(key, thickness)
        part = " ".join(card.name.split())
        title = (f"{stock} - placed by hand - own program {att.name}" + (f" x{qty}" if qty > 1 else "")
                 + f" - {part} - {label}")
        desc = text.own_by_hand_description(program=att.name, part=part, part_url=card.url, stock=stock,
                                            cutter_in=cutter, box=box, margin_in=cfg.nest.part_spacing_in,
                                            lowest_z=report.min_z_in, qty=qty)
        new = self.t.create_card(self.targets["sheet_created"], title, desc)
        self.s.store.register_sheet(new.id, label, False, [card.id])
        reg = self.s.store.sheet_cards()
        reg[new.id].update(label=label, url=new.url, material=key, thickness_in=thickness, offcut_id=None,
                           by_hand=True, own={"program": att.name, "by_hand": True, "box": list(box)})
        self.s.store._write_sheets(reg)
        self.t.attach_program(new.id, att.name, data, report)
        self.t.add_checklist(new.id, cfg.trello.checklist_name, cfg.trello.checklist)
        self.t.add_checklist(new.id, cfg.trello.machine_checklist_name, cfg.trello.machine_checklist)
        reg = self.s.store.sheet_cards()
        reg[new.id]["cuttable"] = True
        self.s.store._write_sheets(reg)
        self.t.move(card.id, self.targets["part_nested"])
        self.t.comment(card.id, f"In Sheet review to be placed by hand: {new.url} ({label}). {att.name} is checked "
                                "as it is; the operator zeros it on a piece big enough.")
        log.info("own program %s on card %s: placed by hand as %s", att.name, card.id, label)

    def _own_label(self) -> str:
        f = self.s.store.offcuts_file.with_name("own_programs.json")
        try:
            last = int(json.loads(f.read_text(encoding="utf-8"))["last"])
        except (OSError, ValueError, KeyError, TypeError):
            last = 0
        f.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(f, (json.dumps({"last": last + 1}) + "\n").encode("utf-8"))
        return f"H{last + 1}"

    def _retire_own(self, part_card: str, why: str) -> None:
        """An own program's open sheet card is replaced: archived, its offcut free again."""
        for card_id, info in self.s.store.sheet_cards().items():
            if info.get("own") and part_card in info.get("parts", []) and not info.get("cut") \
                    and not info.get("archived"):
                self.t.comment(card_id, f"Replaced: {why}.")
                self.t.archive(card_id)
                self.offcuts.release_all([card_id])
                self.s.store.retire_sheet(card_id)
                sheets = self.s.store.sheet_cards()
                sheets[card_id]["archived"] = True
                self.s.store._write_sheets(sheets)

    def _own_after_cut(self, card: Card, info: Dict) -> None:
        """An own program's card went to Cut: its cut is taken out of the offcut it was on (like a hand cut, the
        room either side kept), or the rest of the new sheet becomes an offcut if its Offcut box is ticked."""
        if info.get("offcut_done") or not info.get("cuttable") or info["own"].get("by_hand"):
            return                                   # placed by hand: AutoCAM doesn't know which piece it was cut from
        cfg, own = self.cfg, info["own"]
        label = info.get("label") or info.get("run")
        cuts = [tuple(c) for c in own.get("cuts_own") or [own["cut_own"]]]   # each copy (older cards: one)
        turned = bool(info.get("turned"))
        last = {"label": label, "stretch": [min(c[1] for c in cuts), max(c[3] for c in cuts)], "turned": turned}
        offcut_id = info.get("offcut_id")
        if offcut_id:
            piece = self.offcuts.get(offcut_id)
            if piece is None:
                return                                # its card was archived: the piece is gone
            piece["reserved_by"] = None
            self._apply_hand_cuts(piece, cuts, record=False)
            piece["last"] = last
            free = self._free_after([tuple(s) for s in piece["used"]], piece["material"])
            rooms = compact(piece["beside"])
            if free < cfg.nest.offcut_min_in and not rooms:
                self.t.comment(offcut_id, f"Used up: {label} ({own['program']}) was cut from it.")
                self.t.archive(offcut_id)
                self.offcuts.remove(offcut_id)
            else:
                self.offcuts.put(offcut_id, piece)
                self.t.update_card(offcut_id, *self._offcut_card_text(piece))
                self.t.comment(offcut_id, f"{label} ({own['program']}) was cut from it. "
                               + (f"{free:.0f} in free now" if free >= cfg.nest.offcut_min_in else "No free stretch now")
                               + (", plus room beside the cuts for small parts." if rooms else "."))
        else:
            keep = [c.done for c in card.checks if c.checklist == cfg.trello.offcut_checklist]
            if not any(keep):
                return
            piece = {"material": info["material"], "thickness_in": info["thickness_in"], "used": [], "beside": [],
                     "last": last, "reserved_by": None}
            self._apply_hand_cuts(piece, cuts, record=False)
            free = self._free_after([tuple(s) for s in piece["used"]], piece["material"])
            if free < cfg.nest.offcut_min_in and not piece["beside"]:
                return
            piece["number"] = self.offcuts.next_number()
            new = self.t.create_card("offcuts", *self._offcut_card_text(piece))
            piece["url"] = new.url
            self.offcuts.put(new.id, piece)
            n = piece["number"]
            self.t.comment(card.id, f"The rest of the sheet is offcut #{n}: {new.url}. Write #{n} on it before it "
                                    "goes on the rack.")
        sheets = self.s.store.sheet_cards()
        sheets.get(card.id, {})["offcut_done"] = True
        self.s.store._write_sheets(sheets)

    # ------------------------------------------------------------ sheet options
    def sheet_options(self) -> None:
        """The Options checklist on sheet cards (both unticked to start), applied within a minute:
        - "Cut the whole sheet without stopping": the card's program is the posted program without the stop
          after each part (pauses.remove, which proves nothing else changed), named <program>_NOSTOP.tap,
          checked again with pauses off, and the description says it doesn't stop.
        - "Add an air test program": the card also gets <its program>_AIRTEST.tap, raised so it cuts nothing
          (autocam_core/airtest.py).
        Unticking undoes it. Only cuttable sheets this service made, in Sheet review or Ready to cut. A rebuilt
        sheet gets the ticked options applied to its new program."""
        reg = self.s.store.sheet_cards()
        for list_key in (self.targets["sheet_created"], "ready_to_cut"):
            for card in self.t.list_cards(list_key):
                info = reg.get(card.id)
                if not info or not info.get("cuttable") or not info.get("job") or info.get("archived"):
                    continue
                try:
                    if self._stock(card, info):
                        continue                      # the card changed (or is gone): the rest next pass
                    self._options(card, info)
                except Exception as e:  # noqa: BLE001 - one card mustn't stop the tick
                    log.warning("options on sheet card %s: %s", card.id, e)

    def _options(self, card: Card, info: Dict) -> None:
        t = self.cfg.trello
        if (info.get("rest_in", 0) >= self.cfg.nest.offcut_min_in or info.get("beside_left")) and \
                not any(c.checklist == t.offcut_checklist for c in card.checks):
            self.t.add_checklist(card.id, t.offcut_checklist, [t.offcut_item], checked=True)
        if any(c.checklist == LEGACY_AIR_CHECKLIST for c in card.checks):
            self.t.remove_checklists(card.id, LEGACY_AIR_CHECKLIST)
        items = {c.item: c.done for c in card.checks if c.checklist == t.options_checklist}
        if not items:
            self.t.add_checklist(card.id, t.options_checklist, [t.no_stop_item, t.air_test_item])
            return
        air_kind = f"one-lap@{self.cfg.machine.air_test_feed_ipm:g}"   # a new kind of air test remakes old ones
        want = {"job": info["job"], "index": info.get("index"), "nostop": items.get(t.no_stop_item, False),
                "air": air_kind if items.get(t.air_test_item, False) else False}
        had = info.get("options") or {"job": info["job"], "index": info.get("index"), "nostop": False, "air": False}
        same = all(had.get(k) == v for k, v in want.items())
        if same and had.get("error"):
            return                                    # already said why it can't be done
        if same:
            files = had.get("files")
            if files is None:                         # nothing applied since publishing: only the posted program
                extras = [a for a in card.attachments
                          if a.name.endswith((f"{AIR_SUFFIX}.tap", f"{NO_STOP_SUFFIX}.tap"))]
                if not extras:
                    return
            elif all(att in {a.id for a in card.attachments} for att in files.values()):
                return                                # up to date
        made = self._programs(info, want["nostop"], want["air"])
        if isinstance(made, str):
            self.s.store.set_options(card.id, {**want, "error": made})
            self.t.comment(card.id, text.options_failed_comment(made))
            return
        programs, stem, desc, lift, stops = made
        files: Dict[str, str] = {}
        same_air = all(had.get(k) == want[k] for k in ("job", "index", "air"))   # an air test made the same way
        for a in card.attachments:                    # this sheet's programs: keep the wanted ones, once each
            if a.name.startswith(stem) and a.name.lower().endswith(".tap"):
                reusable = same_air or not a.name.endswith(f"{AIR_SUFFIX}.tap")
                if a.name in programs and a.name not in files and reusable:
                    files[a.name] = a.id
                else:
                    self.t.delete_attachment(card.id, a.id)
        for name, (data, report) in programs.items():
            if name not in files:
                files[name] = self.t.attach_program(card.id, name, data, report)
        if desc != card.desc:
            self.t.update_card(card.id, card.name, desc)
        self.s.store.set_options(card.id, {**want, "files": files})
        main = next(iter(programs))
        if want["nostop"] != had.get("nostop", False) and stops:
            self.t.comment(card.id, text.no_stop_comment(main, want["nostop"]))
        if want["air"] and not (had.get("air") and same):
            name = next(n for n in programs if n.endswith(f"{AIR_SUFFIX}.tap"))
            self.t.comment(card.id, text.air_test_comment(name, lift, self.cfg.machine.air_test_gap_in,
                                                          self.cfg.machine.air_test_feed_ipm))

    def _programs(self, info: Dict, nostop: bool, air: bool):
        """({file name: (bytes, guard report)} with the card's program first, the sheet's program stem, the
        description that goes with it, the air test lift, whether the program has stops at all), or why it
        can't be done."""
        job = self._job(info["job"])
        if job is None:
            return "the service has no record of this sheet's job"
        ing = ingest(self.s.queue, info["job"], job)
        vs = next((v for v in ing.sheets if v.sheet.index == info.get("index")), None)
        if vs is None or not vs.cuttable:
            return "this sheet's checked program isn't in the job folder any more"
        sheet = vs.sheet
        stem = Path(sheet.tap).stem
        counts = {p.part_key: p.count for p in sheet.parts}
        data, report, main, run_job, stops = vs.tap_bytes, vs.check.guard, sheet.tap, job, True
        if nostop and vs.pause_count:
            try:
                plain = remove_pauses(data.decode("ascii"), sheet.outer_order, pause_spec(job), job.pauses.after_last_part)
            except PauseError as e:
                return f"the stops couldn't be taken out ({e})"
            run_job = replace(job, pauses=replace(job.pauses, enabled=False))
            data = plain.encode("ascii")
            check = check_sheet_program(data, run_job, sheet.thickness_in, sheet.tool, sheet.outer_order, counts,
                                        sheet.by_hand)
            if not check.passed:
                return "the program without stops failed its check (" + "; ".join(check.problems()) + ")"
            report, main, stops = check.guard, f"{stem}{NO_STOP_SUFFIX}.tap", False
        programs = {main: (data, report)}
        for vp in vs.programs:                        # the FACE program stays as posted, whatever the options
            programs[vp.program.tap] = (vp.tap_bytes, vp.check.guard)
        gap = self.cfg.machine.air_test_gap_in
        lift = sheet.thickness_in + gap
        if air:
            try:
                air_text = air_test_program(data.decode("ascii"), sheet.thickness_in, gap,
                                            self.cfg.machine.air_test_feed_ipm)
            except AirTestError as e:
                return f"the air test couldn't be made ({e})"
            air_data = air_text.encode("ascii")
            check = check_air_test(air_data, run_job, sheet.thickness_in, sheet.tool, sheet.outer_order, counts, gap,
                                   sheet.by_hand)
            if not check.passed:
                return "the air test failed its check (" + "; ".join(check.problems()) + ")"
            programs[air_test_name(main)] = (air_data, check.guard)
        part_cards = {p.part_key: (p.name, p.card_url) for p in job.parts}
        desc = text.sheet_description(job, ing, vs, resume_key=self.cfg.pauses.resume_key, part_cards=part_cards,
                                      program=main, stops=stops, stock=self._stock_line(vs, info))
        return programs, stem, desc, lift, vs.pause_count > 0

    def _send_back(self, card_id: str, comment: str) -> None:
        self.t.move(card_id, self.targets["checklist_return"])
        self.t.comment(card_id, comment)

    # ------------------------------------------------------------ start
    def start_run(self, cards: Sequence[Card], rush: bool = False) -> Optional[RunState]:
        """cards: what's in Ready for CAM with the box ticked (a run takes them all, not only the new ones).
        rush: only the Rush cards, nested on their own: on an offcut or a new sheet, not folded into a sheet
        waiting in review (only a sheet a rush card is already on is rebuilt: it holds an old copy)."""
        store, cfg = self.s.store, self.cfg
        if not cards:
            return None
        state = RunState(run_id=self._new_run_id(), started_utc=_iso(self.s.clock()),
                         trigger_card=cfg.trello.cards.get("system", ""), dry_run=self.dry_run, phase=STARTING,
                         rush=rush)
        store.save(state)   # reserve the run before any Onshape call or Trello write
        self.watch.took(cards)
        run_id = state.run_id
        summary = {"queued": {}, "rejected": 0, "untouched": 0, "stop_reason": None, "carried": 0, "later": 0}

        requests: List[PartRequest] = []
        for card in cards:
            parsed = parse_card(card, cfg.labels.smoked, cfg.labels.tool_eighth, cfg.labels.face)
            if not isinstance(parsed, CardProblem):
                parsed = replace(parsed, tabs=box(card, cfg.trello.tabs_checklist, cfg.trello.tabs_item,
                                                  cfg.tabs.default),
                                 by_hand=not wants_nest(card, cfg.trello.nest_checklist, cfg.trello.nest_item))
                if parsed.face and face_spec(cfg) is None:
                    parsed = CardProblem(card, (f"The {cfg.labels.face} label needs the face template, which isn't "
                                                "set up yet. Take the label off to cut it unfaced.",))
            if not isinstance(parsed, CardProblem) and cfg.fit.enabled:
                fit = parse_fit(card.desc)
                if isinstance(fit, str):
                    parsed = CardProblem(card, (fit,))
                elif fit and parsed.by_hand:
                    parsed = CardProblem(card, ("A part placed by hand can't have a Fit: line yet. Tick \"Nest this "
                                                "part\" or take the line off.",))
                elif fit:
                    parsed = replace(parsed, fit=fit, tabs=True)    # held by tabs: its laps run after it's cut
            if isinstance(parsed, CardProblem):
                self._reject(state, card.id, parsed.comment(cfg.labels.smoked, cfg.labels.tool_eighth))
                summary["rejected"] += 1
            else:
                requests.append(parsed)

        exporter = self.s.exporter_for(run_id)
        requests, later = self._fit_budget(requests, exporter)
        estimate = self._estimate(requests, exporter)
        decision = decide(estimate, month_used=self.s.ledger.month_count(panel=False),
                          year_used=self.s.ledger.year_count(cfg.onshape.budget_year_start, panel=False),
                          latched=self.s.ledger.latched() is not None, per_run_max=cfg.onshape.per_run_max_calls,
                          monthly_soft=cfg.onshape.monthly_soft_calls, yearly_cap=cfg.onshape.yearly_cap_calls)
        if decision.action == REFUSE and estimate > 0:
            state.notes.append(f"Run not started: {decision.reason}.")
            self._end_start(state, summary, refused=True)
            return None
        if decision.action == WARN:
            self._notice(state, "start:warn", lambda: f"Onshape budget warning: {decision.reason}.")
        if later:
            # they start the next run as soon as this one is done (not after a refusal: that would repeat it)
            self.watch.forget(r.card.id for r in later)
            summary["later"] = len(later)

        # Every STEP export starts before the run waits on any: Onshape works on them side by side, so the
        # parts after the first usually need one status check each (Exporter.start keeps any problem for
        # _prepare to meet in order, as before).
        for req in requests:
            if req.link is not None and not exporter.start(req.link, req.name, part_id=req.part_id):
                break

        ready: List[ReadyPart] = []
        for req in requests:
            if summary["stop_reason"]:
                summary["untouched"] += 1
                continue
            try:
                prepared = self._prepare(req, exporter)
            except STOPPING as e:
                summary["stop_reason"] = str(e)
                summary["untouched"] += 1
                continue
            except TryAgainLater as e:
                log.info("%s: %s", req.name, e)
                summary["untouched"] += 1
                continue
            except ExportError as e:
                prepared = str(e)
            except OnshapeError as e:
                if e.status in (401, 403):
                    summary["stop_reason"] = (f"Onshape refused the API keys ({e.status}); check .env and "
                                              f"onshape.base_url ({self.cfg.onshape.base_url})")
                    summary["untouched"] += 1
                    continue
                prepared = onshape_problem(e)
            if isinstance(prepared, str):
                self._reject(state, req.card.id, text.part_problem_comment(run_id, [prepared]))
                summary["rejected"] += 1
            else:
                ready.append(prepared)

        renesting = {p.request.card.id for p in ready}
        open_sheets = self.open_sheets(renesting) if ready else {}
        if rush:
            open_sheets = {m: [s for s in sheets if set(s[1]["parts"]) & renesting]
                           for m, sheets in open_sheets.items()}
            state.notes.append("Rush: nested right away and on their own (sheets in review were left alone).")
        for batch in make_batches(ready):
            reopened = open_sheets.get(batch.material_key, [])
            carried = list({p.card_id: p for _, _, specs in reopened for p in specs}.values())   # dedupe
            try:
                offcuts = self.available_offcuts(batch.material_key, [card_id for card_id, _, _ in reopened])
                job = build_job(cfg, batch, run_id, _iso(self.s.clock()), carried, offcuts, self.fits)
            except JobBuildError as e:
                summary["untouched"] += len(batch.parts)
                state.notes.append(f"Can't CAM {batch.material_key} parts yet: {e}. They stay in Ready for CAM.")
                continue
            job_text = job_json(job)
            store.save_job(job.job_id, job_text)
            state.jobs[job.job_id] = JobState(material=batch.material_key,
                                              parts={p.part_key: p.card_id for p in job.parts},
                                              queued_utc=_iso(self.s.clock()),
                                              carried=[p.part_key for p in job.parts[len(batch.parts):]],
                                              reopened={card_id: {k: info.get(k) for k in ("thickness_in", "parts", "url")}
                                                        for card_id, info, _ in reopened})
            for p in job.parts:
                state.cards[p.card_id] = {"name": p.name, "url": p.card_url}
            summary["carried"] += len(carried)
            store.save(state)                                     # recorded before it's submitted
            self.s.queue.submit(job.job_id, job_text)             # a fresh run never reuses a queued job id
            summary["queued"][job.job_id] = len(batch.parts)

        self._end_start(state, summary)
        log.info("run %s started: %s", run_id, summary["queued"])
        return state if state.jobs else None

    def _estimate(self, requests: Sequence[PartRequest], exporter: Exporter) -> int:
        linked = [r for r in requests if r.link is not None]
        studios = {r.link.studio: r.link for r in linked}
        # Workspace links aren't pinned yet: count their pin call and assume nothing is cached.
        uncached_studios = sum(1 for link in studios.values() if link.is_workspace or exporter.cache.parts(link) is None)
        pins = sum(1 for link in studios.values() if link.is_workspace)
        uncached_parts = sum(1 for r in linked if not exporter.is_cached(r.link, r.name, r.part_id))
        return estimate_calls(uncached_parts, uncached_studios, self.cfg.onshape.calls_per_part_estimate) + pins

    def _fit_budget(self, requests: List[PartRequest], exporter: Exporter) -> Tuple[List[PartRequest], List[PartRequest]]:
        """As many cards (in board order) as fit the per-run Onshape limit; the rest wait for the next run.
        A first card that alone needs more than the limit is still taken, so the budget check reports it."""
        taken: List[PartRequest] = []
        later: List[PartRequest] = []
        for req in requests:
            if not taken or self._estimate(taken + [req], exporter) <= self.cfg.onshape.per_run_max_calls:
                taken.append(req)
            else:
                later.append(req)
        return taken, later

    def _carried_fit(self, spec: PartSpec, card: Card, material: str) -> Optional[PartSpec]:
        """A carried part follows its card's Fit: line now (and what was learned since); None if it can't be read."""
        fit = parse_fit(card.desc) if self.cfg.fit.enabled else None
        if isinstance(fit, str):
            return None
        return replace(spec, fit=fit_spec(self.cfg, self.fits, material, fit, spec.name),
                       tabs=spec.tabs or bool(fit))

    def open_sheets(self, renesting: Set[str] = frozenset()) -> Dict[str, List[Tuple[str, Dict, List[PartSpec]]]]:
        """Material -> the open sheet cards a run may nest again, with the parts to carry over from them (from
        the jobs that made them). Open: made by this service with everything a re-nest needs, cuttable, still
        in Sheet review, no Review item ticked, none of its parts also on a sheet that isn't open (a part's
        copies always move together), and every part on it either in On a sheet (carried over; its STEP file
        must still be there) or back in Ready for CAM and in this run (`renesting`: someone changed it, so the
        sheet is rebuilt with the new version)."""
        reg = self.s.store.sheet_cards()
        in_review = {c.id: c for c in self.t.list_cards(self.targets["sheet_created"])}
        nested = {c.id: c for c in self.t.list_cards(self.targets["part_nested"])}
        review = self.cfg.trello.checklist_name
        t = self.cfg.trello
        found: Dict[str, Tuple[Dict, List[PartSpec]]] = {}
        for card_id, info in reg.items():
            card = in_review.get(card_id)
            if (card is None or not info.get("job") or not info.get("cuttable") or info.get("cut")
                    or info.get("archived") or not info.get("parts")):
                continue
            if any(c.done for c in card.checks if c.checklist == review):
                continue
            if info.get("by_hand"):
                continue                              # one part placed by hand: nothing joins it
            if info.get("rush") and not set(info["parts"]) & set(renesting):
                continue                              # a rush sheet is for its own parts, unless one changed
            if not all(p in nested or p in renesting for p in info["parts"]):
                continue
            job = self._job(info["job"])
            specs = [p for p in (job.parts if job else ()) if p.card_id in info["parts"]]
            if {p.card_id for p in specs} != set(info["parts"]):
                continue
            specs = [p for p in specs if p.card_id not in renesting]
            if not all(Path(p.step).is_file() for p in specs):
                continue
            # a carried part keeps its STEP file but follows its card's tabs box (no box: tabs.default) and its
            # Face label now
            face = _label(self.cfg.labels.face)
            specs = [replace(p, tabs=box(nested[p.card_id], t.tabs_checklist, t.tabs_item, self.cfg.tabs.default),
                             face=face in {_label(l) for l in nested[p.card_id].labels})
                     for p in specs]
            specs = [self._carried_fit(p, nested[p.card_id], job.material.key) for p in specs]
            if any(s is None for s in specs):
                continue                              # its Fit: line can't be read any more: left as it is
            if any(p.face for p in specs) and face_spec(self.cfg) is None:
                continue                              # can't face it any more: left as it is
            found[card_id] = (info, specs)
        while True:
            # parts also on a sheet that isn't open (cut and archived sheets are history: a part card can come
            # back to be cut again)
            elsewhere = {p for card_id, info in reg.items()
                         if card_id not in found and not info.get("cut") and not info.get("archived")
                         for p in info.get("parts", [])}
            closed = [card_id for card_id, (info, _) in found.items() if elsewhere & set(info["parts"])]
            if not closed:
                break
            for card_id in closed:
                del found[card_id]
        out: Dict[str, List[Tuple[str, Dict, List[PartSpec]]]] = {}
        order = sorted(found.items(), key=lambda kv: (kv[1][0]["thickness_in"], kv[1][0]["run"], kv[1][0].get("index", 0)))
        for card_id, (info, specs) in order:
            out.setdefault(info["material"], []).append((card_id, info, specs))
        return out

    def _new_run_id(self) -> str:
        """Next run id that neither the run store nor the hot folder has seen (state/ may have been wiped)."""
        store = self.s.store
        used = {job_id.split("-", 1)[0] for job_id in self.s.queue.job_ids()}
        n = int(store.next_run_id()[len(store.prefix):])
        while f"{store.prefix}{n:03d}" in used:
            n += 1
        return f"{store.prefix}{n:03d}"

    def _end_start(self, state: RunState, summary: Dict, refused: bool = False) -> None:
        if not refused:
            state.notes.insert(0, text.run_started_comment(state.run_id, summary["queued"], summary["rejected"],
                                                           summary["untouched"], summary["stop_reason"],
                                                           summary["carried"], summary["later"]))
        state.phase = COLLECTING
        self.s.store.save(state)
        self.finish_start(state)          # if this fails, the next tick retries it (the run is still active)
        if not state.jobs:
            state.done = True
            self.s.store.save(state)

    def resume_start(self, state: RunState) -> None:
        """The service died while starting this run: keep the jobs it had recorded, start nothing new."""
        for job_id in state.jobs:
            job_text = self.s.store.job_text(job_id)
            if job_text is not None:
                self.s.queue.submit(job_id, job_text, resume=True)
        state.notes.insert(0, f"Run {state.run_id} was interrupted while starting and carries on with what it had "
                              "queued. Cards it hadn't reached stay in Ready for CAM.")
        state.phase = COLLECTING
        self.s.store.save(state)

    def _notice(self, state: RunState, key: str, message: Callable[[], str]) -> None:
        """A run-level comment on the System card (at most once per run and key; logged if there's no card).
        Runs started before the Run nest card was retired recorded that card; theirs go to System too."""
        card = self.cfg.trello.cards.get("system") or state.trigger_card
        if not card:
            if key not in state.writes:
                log.info("%s: %s", state.run_id, message())
                state.writes[key] = ""
                self.s.store.save(state)
            return
        once(self.s.store, state, key, lambda: self.t.comment(card, message()), WRITE_ATTEMPTS)

    def finish_start(self, state: RunState) -> None:
        """Say on the System card what the run started with (at most once per run)."""
        self._notice(state, "start:comment", lambda: "\n".join(state.notes))

    def _prepare(self, req: PartRequest, exporter: Exporter):
        """ReadyPart, or a problem string for the card."""
        if req.link is not None:
            exported = exporter.export(req.link, req.name, part_id=req.part_id)
            # Onshape's material wins; a Material: line covers a part that has none there (the panel writes one
            # when it couldn't set the material in Onshape), or names the kind of SRPP for Onshape's Polycarbonate
            choice = resolve_material(exported.material, req.material_hint, req.smoked,
                                      self.cfg.onshape.material_map, self.cfg.materials)
            step_path, sha, part_id = exported.step_path, exported.step_sha256, exported.part_id
            mid, bends = exported.microversion, exported.bends
        else:
            step_path = self._download_step(req)
            sha, part_id, mid, bends = sha256_file(step_path), None, None, ()
            choice = resolve_material(None, req.material_hint, req.smoked, self.cfg.onshape.material_map,
                                      self.cfg.materials)
        if choice.key is None:
            return choice.problem
        return ReadyPart(req, choice.key, step_path, sha, part_id, mid, tuple(bends))

    def _download_step(self, req: PartRequest) -> Path:
        att = req.step_attachment
        path = self.cfg.paths.cache / "trello" / f"{att.id}.step"   # attachments never change once uploaded
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(path, self.t.download(att))
        return path

    def _reject(self, state: RunState, card_id: str, comment: str) -> None:
        store = self.s.store
        once(store, state, f"reject:{card_id}:comment", lambda: self.t.comment(card_id, comment), WRITE_ATTEMPTS)
        once(store, state, f"reject:{card_id}:move", lambda: self.t.move(card_id, self.targets["part_rejected"]), WRITE_ATTEMPTS)

    # ------------------------------------------------------------ collect
    def _job(self, job_id: str):
        """The service's copy of a job, or None (never written, or a format this version can't read)."""
        job_text = self.s.store.job_text(job_id)
        if not job_text:
            return None
        try:
            return load_job(json.loads(job_text))
        except (ValueError, SchemaError) as e:
            log.warning("job %s can't be read by this version: %s", job_id, e)
            return None

    def retire_old_layout(self) -> None:
        """Sheet cards from jobs made before the sheet was laid out in the machine's axes (core 0.4.0) have
        programs turned 90 degrees for the machine. Each one, once: files off, a comment, archived, its offcut
        released, and its parts that are still On a sheet sent back to Ready for CAM to be nested again."""
        reg = self.s.store.sheet_cards()
        stale = []
        for card_id, info in reg.items():
            if info.get("archived") or info.get("cut") or not info.get("job") or info.get("old_layout"):
                continue
            job_text = self.s.store.job_text(info["job"])
            if job_text is None:
                continue
            try:
                core = str(json.loads(job_text).get("core_version", "0"))
            except ValueError:
                core = "0"
            if _version(core) < LAYOUT_CORE:
                stale.append((card_id, info))
        if not stale:
            return
        on_sheet = {c.id for c in self.t.list_cards(self.targets["part_nested"])}
        for card_id, info in stale:
            try:
                self._clear_sheet(card_id)
                self.t.comment(card_id, text.old_layout_comment())
                self.t.archive(card_id)
            except Exception as e:  # noqa: BLE001 - try again next pass
                log.warning("couldn't retire old-layout sheet card %s: %s", card_id, e)
                continue
            for part in info.get("parts", []):
                if part in on_sheet:
                    try:
                        self.t.move(part, self.targets["part_deferred"])
                        self.t.comment(part, text.part_old_layout_comment())
                    except Exception as e:  # noqa: BLE001
                        log.warning("couldn't send part card %s back: %s", part, e)
            self.offcuts.release_all([card_id])
            self.s.store.retire_sheet(card_id)
            sheets = self.s.store.sheet_cards()
            sheets[card_id]["old_layout"] = True
            self.s.store._write_sheets(sheets)
            log.info("retired old-layout sheet card %s (%s)", card_id, info.get("label") or info.get("run"))

    def collect(self, state: RunState) -> None:
        store = self.s.store
        finished = set(self.s.queue.finished())
        now = self.s.clock()
        for job_id, js in state.jobs.items():
            if js.status != QUEUED:
                continue
            if job_id in finished:
                ing = ingest(self.s.queue, job_id, self._job(job_id))
                self.publish(state, js, ing)
                js.status = FAILED if ing.failure else PUBLISHED
                store.save(state)
                continue
            if self.s.queue.where(job_id) is None:
                job_text = store.job_text(job_id)    # recorded but never submitted (crash in between)
                if job_text is not None:
                    self.s.queue.submit(job_id, job_text, resume=True)
            if js.queued_utc and not js.timeout_reported:
                queued = datetime.strptime(js.queued_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=now.tzinfo)
                if (now - queued).total_seconds() > self.cfg.trello.job_timeout_s:
                    minutes = self.cfg.trello.job_timeout_s // 60
                    self._notice(state, f"{job_id}:timeout", lambda: (
                        f"Fusion hasn't finished {job_id} after {minutes} min. Check that Fusion and the "
                        "auto-CAM add-in are running on the shop PC."))
                    js.timeout_reported = True
                    store.save(state)
        if all(js.status != QUEUED for js in state.jobs.values()):
            ingested = [ingest(self.s.queue, job_id, self._job(job_id)) for job_id in state.jobs]
            if state.jobs:
                self._notice(state, "summary", lambda: text.run_summary_comment(
                    state.run_id, ingested, [js.sheets for js in state.jobs.values()]))
            state.done = True
            store.save(state)
            log.info("run %s finished", state.run_id)

    # ------------------------------------------------------------ publish
    def publish(self, state: RunState, js: JobState, ing: IngestedJob) -> None:
        store = self.s.store
        job_id = ing.job_id
        new_parts = {k: c for k, c in js.parts.items() if k not in js.carried}
        if ing.failure or ing.result is None:
            # parts carried from open sheets stay on them, untouched
            self._notice(state, f"{job_id}:failed", lambda: text.job_failed_comment(
                job_id, ing.failure or "no result", len(new_parts)))
            for key, card_id in new_parts.items():
                once(store, state, f"{job_id}:{key}:failed", lambda c=card_id: self.t.comment(
                    c, text.part_job_failed_comment(ing.failure or "no result")), WRITE_ATTEMPTS)
            return

        old_sheets = js.reopened                   # as they were when the run started (never re-read: a resumed
        part_cards = {key: (state.cards.get(cid, {}).get("name", key), state.cards.get(cid, {}).get("url", ""))
                      for key, cid in js.parts.items()}          # publish has already changed the live registry)
        carried_cards = {js.parts[k] for k in js.carried}
        old_by_t: Dict[float, List[str]] = {}
        for card_id, info in old_sheets.items():
            old_by_t.setdefault(_thickness(info["thickness_in"]), []).append(card_id)
        new_by_t: Dict[float, List[VerifiedSheet]] = {}
        for vs in sorted(ing.sheets, key=lambda v: v.sheet.index):
            new_by_t.setdefault(_thickness(vs.sheet.thickness_in), []).append(vs)
        in_review = {c.id: c for c in self.t.list_cards(self.targets["sheet_created"])} if js.reopened else {}

        on_card: Dict[int, Tuple[str, str]] = {}    # sheet index -> (card id, url), for sheets put on a card
        cuttable: Dict[int, bool] = {}
        left_alone: Set[str] = set()                # carried parts whose open sheets were kept as they were
        waiting: Set[str] = set()                   # new parts whose sheet someone started reviewing
        failed: Dict[str, List[str]] = {}           # new parts whose rebuilt sheet failed the checks -> why
        counts = {"new": 0, "updated": 0, "kept": 0, "bad": 0}
        for t in sorted(set(old_by_t) | set(new_by_t)):
            old, new = old_by_t.get(t, []), new_by_t.get(t, [])
            old_parts = {p for card_id in old for p in old_sheets[card_id]["parts"]}
            in_group = {js.parts.get(p.part_key) for vs in new for p in vs.sheet.parts}
            # Decided once per thickness and recorded, so a resumed publish carries on the same way.
            plan = once(store, state, f"{job_id}:plan:{t:g}", lambda: (
                "rebuild" if not old else "review" if self._being_reviewed(old, in_review)
                else "same" if in_group <= carried_cards and in_group == old_parts
                else "bad" if any(not vs.cuttable for vs in new) else "rebuild"))
            if plan != "rebuild":
                left_alone |= old_parts & carried_cards     # the open sheets stay exactly as they were
                counts["kept"] += len(old)
            if plan == "review":                # someone started on these sheets; the new parts wait for the next run
                waiting |= in_group - carried_cards
                continue
            if plan == "bad":                   # adding the new parts broke the sheet: it isn't changed, and
                why = [p for vs in new if not vs.cuttable for p in vs.problems]  # they go to Needs fixing
                for card_id in in_group - carried_cards:
                    failed[card_id] = why
                continue
            if plan == "same":                  # nothing new joined these sheets: keep the programs they have
                continue
            self.offcuts.release_all(old)             # the rebuilt sheets reserve what they use again
            for i, vs in enumerate(new):
                placed = self._publish_sheet(state, js, ing, vs, old[i] if i < len(old) else None,
                                             part_cards, counts)
                cuttable[vs.sheet.index] = placed is not None and vs.cuttable
                if placed is not None:
                    on_card[vs.sheet.index] = placed
            for card_id in old[len(new):]:
                self._retire(state, job_id, card_id)

        js.sheets = counts
        if waiting:
            self.watch.forget(waiting)
        for part_key, card_id in js.parts.items():
            if card_id in left_alone:
                continue
            self._publish_part(state, js, ing, part_key, card_id, on_card, cuttable, card_id in waiting,
                               failed.get(card_id))

    def _being_reviewed(self, card_ids: Sequence[str], in_review: Dict[str, Card]) -> bool:
        """Did someone move one of these open sheets, or tick a Review item on it, while the run was going?"""
        review = self.cfg.trello.checklist_name
        return any(card_id not in in_review or any(c.done for c in in_review[card_id].checks if c.checklist == review)
                   for card_id in card_ids)

    def _publish_sheet(self, state: RunState, js: JobState, ing: IngestedJob, vs: VerifiedSheet,
                       target: Optional[str], part_cards, counts) -> Optional[Tuple[str, str]]:
        """Put one sheet on a card: a new card, or `target` (an open sheet card) updated in place.
        Returns (card id, url), or None if no card could be made."""
        store, cfg = self.s.store, self.cfg
        run_id, job_id = state.run_id, ing.job_id
        key = f"{job_id}:S{vs.sheet.index}"
        number = (self.offcuts.get(vs.sheet.offcut_id) or {}).get("number") if vs.sheet.offcut_id else None
        title = ("RUSH - " if state.rush else "") + text.sheet_title(ing.job, vs, number)
        desc = text.sheet_description(ing.job, ing, vs, resume_key=cfg.pauses.resume_key, part_cards=part_cards,
                                      stock=self._stock_line(vs))
        card_id = url = None
        if target is not None:
            # From here until the new program is attached, the card offers nothing to cut.
            store.register_sheet(target, run_id, False, js.reopened[target]["parts"])
            cleared = once(store, state, f"{key}:clear", lambda: self._clear_sheet(target), WRITE_ATTEMPTS)
            if cleared == GAVE_UP:
                # The old program couldn't be taken off: retire the card and use a new one.
                self._retire(state, job_id, target)
            else:
                card_id, url = target, js.reopened[target].get("url") or ""
                once(store, state, f"{key}:text", lambda: self.t.update_card(card_id, title, desc), WRITE_ATTEMPTS)
                once(store, state, f"{key}:reset", lambda: self._reset_checklists(card_id), WRITE_ATTEMPTS)
                once(store, state, f"{key}:rebuilt", lambda: self.t.comment(card_id, text.sheet_rebuilt_comment(run_id)),
                     WRITE_ATTEMPTS)
                counts["updated"] += 1
        if card_id is None:
            card_ref = once(store, state, f"{key}:card", lambda: self._new_card(
                self.targets["sheet_created"], title, desc), WRITE_ATTEMPTS)
            if card_ref == GAVE_UP:
                log.error("%s: gave up creating the sheet card; its program was not offered", key)
                return None
            card_id, url = card_ref.split(" ", 1)
            counts["new"] += 1
        if not vs.cuttable:
            counts["bad"] += 1
        sheet = vs.sheet
        extra = {"label": f"{run_id} S{sheet.index}", "offcut_id": sheet.offcut_id, "turned": sheet.offcut_turned,
                 "used_along": list(sheet.used_y_in) if sheet.used_y_in else None,
                 "beside_used": list(sheet.beside_used), "beside_left": [list(r) for r in sheet.beside_left_in],
                 "rush": state.rush, "parts_area_in2": sheet.parts_area_in2, "by_hand": sheet.by_hand,
                 "bands": self._bands(sheet.used_y_in, sheet.beside_used, sheet.offcut_id, sheet.offcut_turned),
                 "converted": False, "asked": False, "instead_of": None,
                 "fits": [{"checklist": f["checklist"], "name": f["name"], "feature": f["feature"],
                           "key": fit_key(js.material, (f["fit"].kind, f["fit"].diameter_in), f["name"]),
                           "items": [[label, stock] for label, stock in text.fit_items(f)], "recorded": None}
                          for f in text.fit_series(ing.job, sheet)] if vs.cuttable else []}
        if sheet.used_y_in:
            before = (self.offcuts.get(sheet.offcut_id) or {}).get("used", []) if sheet.offcut_id else []
            extra["rest_in"] = round(self._free_after(add_used(before, sheet.used_y_in, self._size(js.material)[0],
                                                               sheet.offcut_turned), js.material), 2)
        # how many of each part card: the phone's Plates tab (2026-11-04)
        extra["counts"] = {js.parts[p.part_key]: p.count for p in vs.sheet.parts if p.part_key in js.parts}
        store.register_sheet(card_id, run_id, vs.cuttable,
                             [js.parts[p.part_key] for p in vs.sheet.parts if p.part_key in js.parts],
                             job=job_id, material=js.material, thickness_in=vs.sheet.thickness_in,
                             index=vs.sheet.index, url=url, extra=extra)
        if sheet.offcut_id:
            self.offcuts.reserve(sheet.offcut_id, card_id)
        if vs.cuttable:
            once(store, state, f"{key}:tap", lambda: self.t.attach_program(
                card_id, vs.sheet.tap, vs.tap_bytes, vs.check.guard), WRITE_ATTEMPTS)
            for vp in vs.programs:                    # the FACE program, fit laps: checked like the sheet's own
                step = f"{key}:face" if vp.program.kind == "face" else f"{key}:{vp.program.name}"
                once(store, state, step, lambda vp=vp: self.t.attach_program(
                    card_id, vp.program.tap, vp.tap_bytes, vp.check.guard), WRITE_ATTEMPTS)
            once(store, state, f"{key}:checklist", lambda: self.t.add_checklist(
                card_id, cfg.trello.checklist_name, cfg.trello.checklist), WRITE_ATTEMPTS)
            once(store, state, f"{key}:machine", lambda: self.t.add_checklist(
                card_id, cfg.trello.machine_checklist_name, cfg.trello.machine_checklist), WRITE_ATTEMPTS)
            for f in extra["fits"]:                   # which run made it fit: ticked at the machine, then learned
                once(store, state, f"{key}:fit:{f['checklist']}", lambda f=f: self.t.add_checklist(
                    card_id, f["checklist"], [label for label, _ in f["items"]]), WRITE_ATTEMPTS)
        png = ing.file(vs.sheet.preview_png)
        if png is not None:
            png_id = once(store, state, f"{key}:png", lambda: self.t.attach_file(
                card_id, png.name, self._labelled_preview(ing, png), "image/png"), WRITE_ATTEMPTS)
            if png_id != GAVE_UP:
                once(store, state, f"{key}:cover", lambda: self.t.set_cover(card_id, png_id) or png_id,
                     WRITE_ATTEMPTS)
        team = ing.result.fusion_team
        f3d = None if team and team.url else ing.file(ing.result.f3d)     # the Fusion Team link replaces it
        if f3d is not None and f3d.stat().st_size <= self.t.attachment_limit_bytes:
            once(store, state, f"{key}:f3d", lambda: self.t.attach_file(
                card_id, f3d.name, f3d.read_bytes(), "application/octet-stream"), WRITE_ATTEMPTS)
        return card_id, url

    @staticmethod
    def _labelled_preview(ing: IngestedJob, png: Path) -> bytes:
        """The preview with the part labels the worker placed (<sheet>.labels.json; labels.py)."""
        data = png.read_bytes()
        spots = ing.file(png.name[:-len(".png")] + ".labels.json") if png.name.endswith(".png") else None
        try:
            found = json.loads(spots.read_text(encoding="utf-8")) if spots else None
        except (OSError, ValueError):
            found = None
        return labels.labelled(data, found)

    def _clear_sheet(self, card_id: str) -> str:
        """Delete every file on a sheet card (program, preview, Fusion file) before it's rebuilt."""
        for a in self.t.get_card(card_id).attachments:
            if a.is_upload:
                self.t.delete_attachment(card_id, a.id)
        return ""

    def _reset_checklists(self, card_id: str) -> str:
        self.t.remove_checklists(card_id, self.cfg.trello.checklist_name)
        self.t.remove_checklists(card_id, self.cfg.trello.machine_checklist_name)
        for f in (self.s.store.sheet_cards().get(card_id) or {}).get("fits") or []:
            self.t.remove_checklists(card_id, f["checklist"])
        return ""

    def _retire(self, state: RunState, job_id: str, card_id: str) -> None:
        """An open sheet card that isn't needed any more (its parts fit on fewer sheets): empty and archive it."""
        store = self.s.store
        key = f"{job_id}:retire:{card_id}"
        store.register_sheet(card_id, state.run_id, False, store.sheet_cards().get(card_id, {}).get("parts", []))
        once(store, state, f"{key}:clear", lambda: self._clear_sheet(card_id), WRITE_ATTEMPTS)
        once(store, state, f"{key}:comment", lambda: self.t.comment(card_id, text.sheet_retired_comment(state.run_id)),
             WRITE_ATTEMPTS)
        once(store, state, f"{key}:archive", lambda: self.t.archive(card_id), WRITE_ATTEMPTS)
        store.retire_sheet(card_id)

    def _publish_part(self, state: RunState, js: JobState, ing: IngestedJob, part_key: str, card_id: str,
                      on_card: Dict[int, Tuple[str, str]], cuttable: Dict[int, bool], waiting: bool,
                      sheet_failed: Optional[List[str]] = None) -> None:
        store = self.s.store
        run_id, job_id = state.run_id, ing.job_id
        key = f"{job_id}:{part_key}"
        carried = part_key in js.carried
        if waiting:
            once(store, state, f"{key}:comment", lambda: self.t.comment(card_id, text.part_waiting_comment(run_id)),
                 WRITE_ATTEMPTS)
            return
        if sheet_failed is not None:
            self._sync_links(state, key, card_id, [])
            once(store, state, f"{key}:comment", lambda: self.t.comment(card_id, text.part_problem_comment(
                run_id, [text.joined_sheet_failed(sheet_failed)])), WRITE_ATTEMPTS)
            once(store, state, f"{key}:move", lambda: self.t.move(card_id, self.targets["part_rejected"]), WRITE_ATTEMPTS)
            return
        part = next((p for p in ing.result.parts if p.part_key == part_key), None)
        inconsistent = ing.part_problems.get(part_key)
        sheets = [i for i in (part.sheets if part else ()) if i in on_card]
        placed = part is not None and not inconsistent and not part.errors and not part.deferred
        self._sync_links(state, key, card_id, [(i, on_card[i][1]) for i in sheets] if placed else [])
        if part is None or inconsistent:
            reasons = list(inconsistent or ("Fusion reported nothing for this part",))
            once(store, state, f"{key}:comment", lambda: self.t.comment(card_id, text.part_problem_comment(
                run_id, [f"The CAM result doesn't add up ({'; '.join(reasons)}). A mentor should check "
                         f"run {run_id}."])), WRITE_ATTEMPTS)
            once(store, state, f"{key}:move", lambda: self.t.move(card_id, self.targets["part_rejected"]), WRITE_ATTEMPTS)
            return
        links = [(i, on_card[i][1]) for i in sheets]
        if part.errors:
            once(store, state, f"{key}:comment", lambda: self.t.comment(
                card_id, text.part_problem_comment(run_id, [e.msg for e in part.errors])), WRITE_ATTEMPTS)
            once(store, state, f"{key}:move", lambda: self.t.move(card_id, self.targets["part_rejected"]), WRITE_ATTEMPTS)
        elif part.deferred and carried:
            once(store, state, f"{key}:comment", lambda: self.t.comment(card_id, text.part_bumped_comment(run_id)),
                 WRITE_ATTEMPTS)
            once(store, state, f"{key}:move", lambda: self.t.move(card_id, self.targets["part_deferred"]), WRITE_ATTEMPTS)
        elif part.deferred:
            once(store, state, f"{key}:comment", lambda: self.t.comment(
                card_id, text.part_deferred_comment(run_id, part)), WRITE_ATTEMPTS)
        elif part.sheets and all(cuttable.get(i, False) for i in part.sheets):
            was_on = {cid for cid, info in js.reopened.items() if card_id in info["parts"]}
            if not carried or was_on != {on_card[i][0] for i in sheets}:
                once(store, state, f"{key}:comment", lambda: self.t.comment(
                    card_id, text.part_nested_comment(run_id, part, links)), WRITE_ATTEMPTS)
            if not carried:
                once(store, state, f"{key}:move", lambda: self.t.move(card_id, self.targets["part_nested"]), WRITE_ATTEMPTS)
        else:
            once(store, state, f"{key}:comment", lambda: self.t.comment(
                card_id, text.part_bad_sheet_comment(run_id, links)), WRITE_ATTEMPTS)
            once(store, state, f"{key}:move", lambda: self.t.move(card_id, self.targets["part_rejected"]), WRITE_ATTEMPTS)

    def _sync_links(self, state: RunState, key: str, card_id: str, wanted: Sequence[Tuple[int, str]]) -> None:
        """A part card's links to sheet cards: drop the ones to sheets it's no longer on (by the sheet registry,
        already updated for this job's sheets), add the new ones."""
        store = self.s.store
        ours = {info.get("url") for info in store.sheet_cards().values()
                if info.get("url") and card_id not in info.get("parts", [])}
        try:
            have = {a.url: a.id for a in self.t.get_card(card_id).attachments if not a.is_upload}
        except Exception as e:  # noqa: BLE001 - a deleted part card mustn't stop the publish
            log.warning("couldn't read part card %s: %s", card_id, e)
            return
        urls = {url for _, url in wanted}
        for url, att_id in have.items():
            if url in ours and url not in urls:
                once(store, state, f"{key}:unlink:{att_id}", lambda a=att_id: self.t.delete_attachment(card_id, a),
                     WRITE_ATTEMPTS)
        for i, url in wanted:
            if url not in have:
                once(store, state, f"{key}:link:S{i}", lambda u=url, n=i: self.t.attach_link(
                    card_id, u, f"Sheet S{n} ({state.run_id})"), WRITE_ATTEMPTS)

    def _new_card(self, list_key: str, title: str, desc: str) -> str:
        card = self.t.create_card(list_key, title, desc)
        return f"{card.id} {card.url}"
