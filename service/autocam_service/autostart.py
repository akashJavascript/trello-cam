"""When to start a run: a card arrived in Ready for CAM (or changed there), then nothing changed for a while.

The trigger is the cards themselves; there's no button. Every tick the runner shows the watch what's in Ready
for CAM. A card counts once it's new there, or its text, labels or attachments changed since a run last took
it, and its "Nest this part" box isn't unticked. A run starts once that set has stayed the same for
`trello.start_delay_s` (so dragging five cards in a row makes one run). Comments don't count as changes:
the service comments on cards it leaves in Ready for CAM, and that mustn't start another run.

A card the run took is remembered with what it looked like, so a part that didn't fit doesn't start a run
every two minutes. It's forgotten when it leaves Ready for CAM or its box is unticked, so moving it out and
back, or editing it, makes it count again.

    state/ready_watch.json   {"taken": {card_id: signature}, "waiting": signature of the waiting set, "since": ISO}
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from autocam_core.hotfolder import write_atomic

from .tracker.base import Card

_TIME = "%Y-%m-%dT%H:%M:%SZ"


def wants_nest(card: Card, checklist: str, item: str) -> bool:
    """On unless the card has that item and it's unticked (no box at all means on)."""
    states = [c.done for c in card.checks if c.checklist == checklist and c.item == item]
    return not states or any(states)


def box(card: Card, checklist: str, item: str, default: bool) -> bool:
    """The item's tick, or `default` if the card doesn't have it."""
    states = [c.done for c in card.checks if c.checklist == checklist and c.item == item]
    return any(states) if states else default


def signature(card: Card) -> str:
    """What the card says, not when it was last touched (comments must not count)."""
    data = [card.name, card.desc, sorted(card.labels), sorted(a.id for a in card.attachments)]
    return hashlib.sha256(json.dumps(data).encode("utf-8")).hexdigest()[:16]


class ReadyWatch:
    def __init__(self, path: Path, delay_s: float):
        self.path = Path(path)
        self.delay_s = delay_s
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.taken: Dict[str, str] = dict(data.get("taken", {}))
        self.waiting: str = data.get("waiting", "")
        self.since: str = data.get("since", "")

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, (json.dumps({"taken": self.taken, "waiting": self.waiting, "since": self.since},
                                            indent=1) + "\n").encode("utf-8"))

    def waiting_cards(self, eligible: Iterable[Card]) -> List[Card]:
        return [c for c in eligible if self.taken.get(c.id) != signature(c)]

    def observe(self, eligible: List[Card], now: datetime) -> bool:
        """eligible: the cards in Ready for CAM whose box is ticked. True when a run should start."""
        ids = {c.id for c in eligible}
        changed = False
        for card_id in [k for k in self.taken if k not in ids]:
            del self.taken[card_id]
            changed = True
        waiting = self.waiting_cards(eligible)
        key = ",".join(sorted(f"{c.id}:{signature(c)}" for c in waiting))
        if key != self.waiting:
            self.waiting, self.since = key, now.strftime(_TIME)
            changed = True
        if changed:
            self._save()
        if not waiting:
            return False
        since = datetime.strptime(self.since, _TIME).replace(tzinfo=now.tzinfo)
        return (now - since).total_seconds() >= self.delay_s

    def took(self, cards: Iterable[Card]) -> None:
        """A run started with these cards: they don't start another one until they change."""
        for c in cards:
            self.taken[c.id] = signature(c)
        self.waiting, self.since = "", ""
        self._save()

    def forget(self, card_ids: Iterable[str]) -> None:
        """These cards should be tried again (e.g. their sheet changed while they were being nested)."""
        for card_id in card_ids:
            self.taken.pop(card_id, None)
        self._save()

    def remaining_s(self, now: datetime) -> Optional[float]:
        if not self.waiting or not self.since:
            return None
        since = datetime.strptime(self.since, _TIME).replace(tzinfo=now.tzinfo)
        return max(0.0, self.delay_s - (now - since).total_seconds())
