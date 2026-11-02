"""A picture of each part on its card (2026-11-01): Onshape's shaded view of the part as it was CAM'd, attached to
the part card once and made its cover, so cards are told apart at a glance in Trello. One panel call per part
state (cached for good), a few per tick: out of calls, the rest wait for tomorrow."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Set

from autocam_core.hotfolder import write_atomic
from autocam_core.names import safe_token

from ..run_state import RunStore
from ..tracker.base import Tracker
from .onshape_reads import OutOfCalls, PanelOnshape, PanelOnshapeError

log = logging.getLogger("autocam.panel")

PER_TICK = 3


class PictureWatch:
    def __init__(self, onshape: PanelOnshape, store: RunStore, tracker: Tracker, per_tick: int = PER_TICK):
        self.onshape = onshape
        self.store = store
        self.t = tracker
        self.per_tick = per_tick
        self.done_file = Path(store.sheets_file).with_name("panel_pictures.json")

    def _done(self) -> Dict[str, str]:
        try:
            return dict(json.loads(self.done_file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}

    def _mark(self, card_id: str, key: str) -> None:
        done = self._done()
        done[card_id] = key
        self.done_file.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.done_file, (json.dumps(done, indent=1) + "\n").encode("utf-8"))

    def tick(self, sheet_ids: Optional[Set[str]] = None) -> int:
        """Pictures for the parts on sheets not cut yet (those of sheet_ids: the ones on the board) that don't have one
        for the state they were CAM'd from."""
        done, made = self._done(), 0
        for sheet_id, info in self.store.sheet_cards().items():
            if made >= self.per_tick:
                break
            if sheet_ids is not None and sheet_id not in sheet_ids:
                continue
            if info.get("cut") or info.get("archived") or not info.get("job"):
                continue
            try:
                job = json.loads(self.store.job_text(info["job"]) or "{}")
            except ValueError:
                continue
            for part in job.get("parts") or []:
                ref: Any = part.get("onshape")
                card = part.get("card_id")
                if not card or card not in (info.get("parts") or []) or not isinstance(ref, dict):
                    continue
                state = ("m", ref["microversion"]) if ref.get("microversion") else \
                    ("v", ref.get("vid", "")) if "/v/" in (ref.get("url") or "") else None
                if state is None:
                    continue
                key = f"{ref['did']}/{state[1]}/{ref['eid']}/{ref.get('part_id')}"
                if done.get(card) == key:
                    continue
                try:
                    png = self.onshape.picture(ref["did"], state[0], state[1], ref["eid"], ref.get("part_id") or "")
                    name = f"{safe_token(part.get('name') or 'part')}.png"
                    att = self.t.attach_file(card, name, png, "image/png")
                    self.t.set_cover(card, att)
                except OutOfCalls:
                    return made
                except (PanelOnshapeError, Exception) as e:  # noqa: BLE001 - tried again next time
                    log.info("panel: picture of %s: %s", part.get("name") or card, e)
                    continue
                self._mark(card, key)
                done[card] = key
                made += 1
                if made >= self.per_tick:
                    break
        return made
