"""Changed before it's cut: a sheet that reaches Ready to cut gets each of its parts compared with Onshape as it is now,
by the service itself (the panel's "changed since it was CAM'd", without anyone opening the panel). Sheets in Sheet
review are compared too, every [panel] change_check_hours. A part that changed gets the CAD changed label and an
accept box on its card (panel/board.py), cleared once that's ticked or the part is nested again.

Once per sheet program (sheet card + the job that made it): for each part on it CAM'd from a workspace, one call asks
the workspace's current state. The same state as when it was CAM'd: nothing in that document changed, done. Otherwise
the part's shape then (kept for good) and now are compared, and a part that really changed gets one comment on the
sheet card. Parts linked to a version can't change; parts from a .step attachment have nothing to compare.

Its calls are the panel's (ledger run "panel"), so runs never pay for it. Out of calls: tried again later.
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from autocam_core.hotfolder import write_atomic

from ..run_state import RunStore
from ..tracker.base import Tracker
from .board import PanelBoard
from .geometry import differences, shape_key
from .onshape_reads import OutOfCalls, PanelOnshape, PanelOnshapeError

log = logging.getLogger("autocam.panel")

CHECK_EVERY_S = 120.0


class PrecutWatch:
    def __init__(self, onshape: PanelOnshape, board: PanelBoard, store: RunStore, tracker: Tracker):
        self.onshape = onshape
        self.board = board
        self.store = store
        self.t = tracker
        self.done_file = Path(store.sheets_file).with_name("panel_precut.json")

    def _done(self) -> Dict[str, Any]:
        try:
            return dict(json.loads(self.done_file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}

    def _mark(self, sheet_id: str, job: Any) -> None:
        done = self._done()
        done[sheet_id] = job
        self.done_file.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.done_file, (json.dumps(done, indent=1) + "\n").encode("utf-8"))

    def tick(self, now: Optional[float] = None) -> int:
        """Check the sheets in Ready to cut not checked yet, and those in Sheet review every change_check_hours (not
        when first seen: they were just CAM'd), then settle the changes that were (panel/board.py). Returns how many
        parts were found changed."""
        now = time.time() if now is None else now
        every = self.board.cfg.panel.change_check_hours * 3600
        ours, done = self.store.sheet_cards(), self._done()
        changed = 0
        states: Dict[Tuple[str, str], Optional[str]] = {}   # one call per document state per tick
        for list_key in ("ready_to_cut", "sheet_review"):
            for card in self.t.list_cards(list_key):
                info = ours.get(card.id)
                if not info or info.get("cut") or not info.get("job"):
                    continue
                if list_key == "ready_to_cut":
                    mark, seen = card.id, done.get(card.id)
                    if seen == info["job"]:
                        continue
                else:
                    mark, seen = f"review:{card.id}", done.get(f"review:{card.id}")
                    if not isinstance(seen, dict) or seen.get("job") != info["job"]:
                        self._mark(mark, {"job": info["job"], "at": now})
                        continue
                    if now - float(seen.get("at") or 0) < every:
                        continue
                found, finished = self._check(card, info, states)
                changed += found
                if finished:
                    self._mark(mark, info["job"] if list_key == "ready_to_cut" else {"job": info["job"], "at": now})
        try:
            self.board.settle_changes()
        except Exception as e:  # noqa: BLE001 - tried again next time
            log.info("panel: settling CAD changes: %s", e)
        return changed

    def _check(self, card, info, states) -> Tuple[int, bool]:
        """Each part on the sheet CAM'd from a workspace, compared with Onshape now. (changed, done: False when out of
        calls, so it's tried again later)."""
        try:
            job = json.loads(self.store.job_text(info["job"]) or "{}")
        except ValueError:
            job = {}
        changed = 0
        for part in job.get("parts") or []:
            ref = part.get("onshape")
            if part.get("card_id") not in (info.get("parts") or []) or not isinstance(ref, dict) \
                    or not ref.get("microversion"):
                continue
            name = part.get("name") or ref.get("part_id") or "A part"
            try:
                if (ref["did"], ref["vid"]) not in states:
                    states[(ref["did"], ref["vid"])] = self.onshape.current_microversion(ref["did"], ref["vid"])
                now_state = states[(ref["did"], ref["vid"])]
                if not now_state or now_state == ref["microversion"]:
                    continue
                mid = ref["microversion"]
                then, then_extra = self.onshape.geometry(ref["did"], "m", mid, ref["eid"], ref["part_id"], mid, name)
                now, now_extra = self.onshape.geometry(ref["did"], "w", ref["vid"], ref["eid"], ref["part_id"],
                                                       now_state, name)
            except OutOfCalls:
                return changed, False
            except PanelOnshapeError as e:
                log.info("panel: before-cut check of %s on %s: %s", name, info.get("label") or card.id, e)
                continue
            what = differences(then, now, float(then_extra.get("outline_area_in2") or 0),
                               float(now_extra.get("outline_area_in2") or 0))
            if what:
                changed += 1
                self.board.flag_sheet(card.id, part["card_id"], name, what, shape_key(now))
                log.info("panel: %s changed since %s was CAM'd: %s", name, info.get("label") or card.id, what)
        return changed, True

    def keep_running(self, sleep: Callable[[float], None] = time.sleep) -> threading.Thread:
        def loop() -> None:
            while True:
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 - try again next time
                    log.exception("panel: the before-cut check failed")
                sleep(CHECK_EVERY_S)

        thread = threading.Thread(target=loop, name="panel-precut", daemon=True)
        thread.start()
        return thread
