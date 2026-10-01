"""One service tick: guard Ready to cut, collect finished jobs, or start a run when triggered.

Trigger (decision 9): the control card is dragged into the `Run nest` list. The service moves it
back to `Control` and comments what it did. One run at a time.

Start: parse every card in Ready for CAM (problems -> Needs fixing, before any Onshape call),
estimate and check the Onshape budget, export STEP (cached by version), resolve materials,
batch by material, write one job per batch into the hot folder.

Collect: for each finished job, re-check every program (results.py), create one sheet card per
sheet in Sheet review with the .tap (only if it passed), the preview and the checklist, then
comment on and move each part card. Every Trello write is recorded in the run state first time
it succeeds, so a restart resumes without duplicates. Nothing is ever moved to Ready to cut.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from autocam_core.hotfolder import Queue, write_atomic
from autocam_core.schema_job import job_json, load_job

from . import sheet_cards as text
from .batching import ReadyPart, make_batches
from .cards import CardProblem, PartRequest, parse_card
from .config import Config
from .jobs import JobBuildError, build_job
from .materials import resolve_material
from .onshape.budget import REFUSE, WARN, decide, estimate_calls
from .onshape.cache import sha256_file
from .onshape.client import BudgetExceeded, QuotaExhausted, RateLimited
from .onshape.export import Exporter, ExportError, TryAgainLater
from .onshape.ledger import Ledger, utc_now
from .results import IngestedJob, ingest
from .run_state import FAILED, PUBLISHED, QUEUED, JobState, RunState, RunStore, once
from .tracker.base import Card, Tracker
from .tracker.dryrun import DryRunTracker

log = logging.getLogger("autocam.runner")
STOPPING = (BudgetExceeded, QuotaExhausted, RateLimited)


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


class Runner:
    def __init__(self, s: Services):
        self.s = s
        self.cfg = s.cfg
        self.t = s.tracker
        self.targets = s.cfg.trello.targets

    # ------------------------------------------------------------ tick
    def tick(self) -> None:
        self.check_ready_to_cut()
        state = self.s.store.active()
        trigger = self.trigger_card()
        if state is not None:
            if trigger is not None:
                self.t.comment(trigger.id, f"Run {state.run_id} is still in progress; wait for it to finish.")
                self.t.move(trigger.id, self.targets["control_return"])
            self.collect(state)
        elif trigger is not None:
            self.start_run(trigger)

    def trigger_card(self) -> Optional[Card]:
        cards = self.t.list_cards("run_nest")
        wanted = self.cfg.trello.cards.get("run_nest_control")
        for c in cards:
            if not wanted or c.id == wanted:
                return c
        return None

    # ------------------------------------------------------------ guard
    def check_ready_to_cut(self) -> None:
        """Decision 16: a sheet card in Ready to cut with an incomplete checklist goes back."""
        name = self.cfg.trello.checklist_name
        for card in self.t.list_cards("ready_to_cut"):
            state = self.t.checklist(card.id, name)
            if state is None or state.complete:
                continue  # not one of our sheet cards, or properly reviewed
            self.t.move(card.id, self.targets["checklist_return"])
            self.t.comment(card.id, f"Moved back to Sheet review: the {name} checklist is {state.done}/{state.total} "
                                    "done. Tick every item after checking it, then move the card again.")

    # ------------------------------------------------------------ start
    def start_run(self, trigger: Card) -> Optional[RunState]:
        store, cfg = self.s.store, self.cfg
        run_id = store.next_run_id()
        cards = self.t.list_cards("ready_for_cam")
        if not cards:
            self.t.comment(trigger.id, "Nothing in Ready for CAM; no run started.")
            self.t.move(trigger.id, self.targets["control_return"])
            return None

        requests: List[PartRequest] = []
        rejected = 0
        for card in cards:
            parsed = parse_card(card, cfg.labels.smoked, cfg.labels.tool_eighth)
            if isinstance(parsed, CardProblem):
                self._reject(card, parsed.comment(cfg.labels.smoked, cfg.labels.tool_eighth))
                rejected += 1
            else:
                requests.append(parsed)

        exporter = self.s.exporter_for(run_id)
        linked = [r for r in requests if r.link is not None]
        studios = {r.link.key: r.link for r in linked}
        uncached_studios = sum(1 for link in studios.values() if exporter.cache.parts(link) is None)
        uncached_parts = sum(1 for r in linked if not exporter.is_cached(r.link, r.name))
        estimate = estimate_calls(uncached_parts, uncached_studios, cfg.onshape.calls_per_part_estimate)
        decision = decide(estimate, month_used=self.s.ledger.month_count(),
                          year_used=self.s.ledger.year_count(cfg.onshape.budget_year_start),
                          latched=bool(self.s.ledger.latched()), per_run_max=cfg.onshape.per_run_max_calls,
                          monthly_soft=cfg.onshape.monthly_soft_calls, yearly_cap=cfg.onshape.yearly_cap_calls)
        if decision.action == REFUSE and estimate > 0:
            self.t.comment(trigger.id, f"Run not started: {decision.reason}.")
            self.t.move(trigger.id, self.targets["control_return"])
            return None
        if decision.action == WARN:
            self.t.comment(cfg.trello.cards.get("system") or trigger.id, f"Onshape budget warning: {decision.reason}.")

        ready: List[ReadyPart] = []
        stop_reason = None
        untouched = 0
        for req in requests:
            if stop_reason:
                untouched += 1
                continue
            try:
                ready_part = self._prepare(req, exporter)
            except STOPPING as e:
                stop_reason = str(e)
                untouched += 1
                continue
            except TryAgainLater as e:
                log.info("%s: %s", req.name, e)
                untouched += 1
                continue
            except ExportError as e:
                self._reject(req.card, text.part_problem_comment(run_id, [str(e)]))
                rejected += 1
                continue
            if isinstance(ready_part, str):
                self._reject(req.card, text.part_problem_comment(run_id, [ready_part]))
                rejected += 1
            else:
                ready.append(ready_part)

        state = RunState(run_id=run_id, started_utc=_iso(self.s.clock()), trigger_card=trigger.id,
                         dry_run=isinstance(self.t, DryRunTracker))
        queued: Dict[str, int] = {}
        for batch in make_batches(ready):
            try:
                job = build_job(cfg, batch, run_id, _iso(self.s.clock()))
            except JobBuildError as e:
                untouched += len(batch.parts)
                self.t.comment(trigger.id, f"Can't CAM {batch.material_key} parts yet: {e}. They stay in Ready for CAM.")
                continue
            job_text = job_json(job)
            store.save_job(job.job_id, job_text)
            self.s.queue.submit(job.job_id, job_text)
            state.jobs[job.job_id] = JobState(material=batch.material_key,
                                              parts={k: p.request.card.id for k, p in batch.parts},
                                              queued_utc=_iso(self.s.clock()))
            for _, p in batch.parts:
                state.cards[p.request.card.id] = {"name": p.request.name, "url": p.request.card.url}
            queued[job.job_id] = len(batch.parts)

        self.t.comment(trigger.id, text.run_started_comment(run_id, queued, rejected, untouched, stop_reason))
        self.t.move(trigger.id, self.targets["control_return"])
        if not state.jobs:
            return None
        store.save(state)
        log.info("run %s started: %s", run_id, queued)
        return state

    def _prepare(self, req: PartRequest, exporter: Exporter):
        """ReadyPart, or a problem string for the card."""
        if req.link is not None:
            exported = exporter.export(req.link, req.name)
            choice = resolve_material(exported.material, None, req.smoked, self.cfg.onshape.material_map,
                                      self.cfg.materials)
            step_path, sha, part_id = exported.step_path, exported.step_sha256, exported.part_id
        else:
            step_path = self._download_step(req)
            sha, part_id = sha256_file(step_path), None
            choice = resolve_material(None, req.material_hint, req.smoked, self.cfg.onshape.material_map,
                                      self.cfg.materials)
        if choice.key is None:
            return choice.problem
        return ReadyPart(req, choice.key, step_path, sha, part_id)

    def _download_step(self, req: PartRequest) -> Path:
        att = req.step_attachment
        path = self.cfg.paths.cache / "trello" / f"{att.id}.step"   # attachments never change once uploaded
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(path, self.t.download(att))
        return path

    def _reject(self, card: Card, comment: str) -> None:
        self.t.comment(card.id, comment)
        self.t.move(card.id, self.targets["part_rejected"])

    # ------------------------------------------------------------ collect
    def collect(self, state: RunState) -> None:
        store = self.s.store
        finished = set(self.s.queue.finished())
        now = self.s.clock()
        for job_id, js in state.jobs.items():
            if js.status != QUEUED:
                continue
            if job_id in finished:
                job_text = store.job_text(job_id)
                job = load_job(json.loads(job_text)) if job_text else None
                ing = ingest(self.s.queue, job_id, job)
                self.publish(state, js, ing)
                js.status = FAILED if ing.failure else PUBLISHED
                store.save(state)
            elif js.queued_utc and not js.timeout_reported:
                queued = datetime.strptime(js.queued_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=now.tzinfo)
                if (now - queued).total_seconds() > self.cfg.trello.job_timeout_s:
                    self.t.comment(state.trigger_card,
                                   f"Fusion hasn't finished {job_id} after {self.cfg.trello.job_timeout_s // 60} min. "
                                   "Check that Fusion and the auto-CAM add-in are running on the shop PC.")
                    js.timeout_reported = True
                    store.save(state)
        if all(js.status != QUEUED for js in state.jobs.values()):
            ingested = []
            for job_id in state.jobs:
                job_text = store.job_text(job_id)
                ingested.append(ingest(self.s.queue, job_id, load_job(json.loads(job_text)) if job_text else None))
            once(store, state, "summary", lambda: self.t.comment(state.trigger_card,
                                                                 text.run_summary_comment(state.run_id, ingested)))
            state.done = True
            store.save(state)
            log.info("run %s finished", state.run_id)

    # ------------------------------------------------------------ publish
    def publish(self, state: RunState, js: JobState, ing: IngestedJob) -> None:
        store, cfg = self.s.store, self.cfg
        run_id, job_id = state.run_id, ing.job_id
        if ing.failure or ing.result is None:
            once(store, state, f"{job_id}:failed", lambda: self.t.comment(
                state.trigger_card, text.job_failed_comment(job_id, ing.failure or "no result", len(js.parts))))
            for key, card_id in js.parts.items():
                once(store, state, f"{job_id}:{key}:failed", lambda c=card_id: self.t.comment(
                    c, f"The CAM job for this part ({job_id}) failed: {ing.failure}. The card stays in Ready for CAM."))
            return

        part_cards = {key: (state.cards.get(cid, {}).get("name", key), state.cards.get(cid, {}).get("url", ""))
                      for key, cid in js.parts.items()}
        sheet_urls: Dict[int, str] = {}
        cuttable: Dict[int, bool] = {}
        f3d = ing.file(ing.result.f3d)
        for vs in ing.sheets:
            key = f"{job_id}:S{vs.sheet.index}"
            desc = text.sheet_description(ing.job, ing, vs, resume_key=cfg.pauses.resume_key, part_cards=part_cards)
            card_ref = once(store, state, f"{key}:card", lambda: self._new_card(
                self.targets["sheet_created"], text.sheet_title(ing.job, vs), desc))
            card_id, url = card_ref.split(" ", 1)
            sheet_urls[vs.sheet.index] = url
            cuttable[vs.sheet.index] = vs.cuttable
            if vs.cuttable:
                once(store, state, f"{key}:tap", lambda: self.t.attach_program(
                    card_id, vs.sheet.tap, vs.tap_bytes, vs.check.guard))
                once(store, state, f"{key}:checklist", lambda: self.t.add_checklist(
                    card_id, cfg.trello.checklist_name, cfg.trello.checklist))
            png = ing.file(vs.sheet.preview_png)
            if png is not None:
                once(store, state, f"{key}:png", lambda: self.t.attach_file(
                    card_id, png.name, png.read_bytes(), "image/png"))
            if f3d is not None and f3d.stat().st_size <= self.t.attachment_limit_bytes:
                once(store, state, f"{key}:f3d", lambda: self.t.attach_file(
                    card_id, f3d.name, f3d.read_bytes(), "application/octet-stream"))

        for part in ing.result.parts:
            card_id = js.parts.get(part.part_key)
            if card_id is None:
                continue
            key = f"{job_id}:{part.part_key}"
            links = [(i, sheet_urls[i]) for i in part.sheets if i in sheet_urls]
            if part.errors:
                once(store, state, f"{key}:comment", lambda: self.t.comment(
                    card_id, text.part_problem_comment(run_id, [e.msg for e in part.errors])))
                once(store, state, f"{key}:move", lambda: self.t.move(card_id, self.targets["part_rejected"]))
            elif part.deferred:
                once(store, state, f"{key}:comment", lambda: self.t.comment(
                    card_id, text.part_deferred_comment(run_id, part)))
            else:
                for i, url in links:
                    once(store, state, f"{key}:link:S{i}", lambda: self.t.attach_link(card_id, url, f"Sheet S{i} ({run_id})"))
                if all(cuttable.get(i, False) for i in part.sheets):
                    once(store, state, f"{key}:comment", lambda: self.t.comment(
                        card_id, text.part_nested_comment(run_id, part, links)))
                    once(store, state, f"{key}:move", lambda: self.t.move(card_id, self.targets["part_nested"]))
                else:
                    once(store, state, f"{key}:comment", lambda: self.t.comment(
                        card_id, text.part_bad_sheet_comment(run_id, links)))
                    once(store, state, f"{key}:move", lambda: self.t.move(card_id, self.targets["part_rejected"]))

    def _new_card(self, list_key: str, title: str, desc: str) -> str:
        card = self.t.create_card(list_key, title, desc)
        return f"{card.id} {card.url}"
