"""In-memory tracker for tests and offline end-to-end runs."""

import itertools
from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple

from .base import Attachment, Card, ChecklistState, Tracker


class FakeTracker(Tracker):
    def __init__(self, cards: Sequence[Card] = (), attachment_limit_mb: float = 10.0):
        super().__init__(attachment_limit_mb)
        self.cards: Dict[str, Card] = {c.id: c for c in cards}
        self.comments: List[Tuple[str, str]] = []
        self.files: Dict[str, Tuple[str, str, bytes]] = {}      # attachment id -> (card, name, data)
        self.links: List[Tuple[str, str, str]] = []
        self.checklists: Dict[Tuple[str, str], List[List]] = {}  # (card, name) -> [[item, done], ...]
        self.downloads: Dict[str, bytes] = {}                    # attachment id -> bytes to return
        self.log: List[Tuple] = []
        self._ids = itertools.count(1)

    def _id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids):04d}"

    # reads
    def list_cards(self, list_key: str) -> List[Card]:
        return [c for c in self.cards.values() if c.list_key == list_key]

    def get_card(self, card_id: str) -> Card:
        return self.cards[card_id]

    def checklist(self, card_id: str, name: str) -> Optional[ChecklistState]:
        items = self.checklists.get((card_id, name))
        if items is None:
            return None
        return ChecklistState(done=sum(1 for _, done in items if done), total=len(items))

    def download(self, attachment: Attachment) -> bytes:
        return self.downloads[attachment.id]

    # writes
    def _move(self, card_id: str, list_key: str) -> None:
        self.cards[card_id] = replace(self.cards[card_id], list_key=list_key)
        self.log.append(("move", card_id, list_key))

    def _create_card(self, list_key: str, title: str, desc: str) -> Card:
        cid = self._id("card")
        self.cards[cid] = Card(cid, title, desc, list_key, f"https://trello.example/c/{cid}")
        self.log.append(("create", cid, list_key, title))
        return self.cards[cid]

    def _comment(self, card_id: str, text: str) -> None:
        self.comments.append((card_id, text))
        self.log.append(("comment", card_id))

    def _attach_file(self, card_id: str, name: str, data: bytes, mime: str) -> str:
        aid = self._id("att")
        self.files[aid] = (card_id, name, data)
        self.log.append(("file", card_id, name))
        return aid

    def _attach_link(self, card_id: str, url: str, name: str) -> str:
        self.links.append((card_id, url, name))
        self.log.append(("link", card_id, url))
        return self._id("att")

    def _add_checklist(self, card_id: str, name: str, items: List[str]) -> str:
        self.checklists[(card_id, name)] = [[item, False] for item in items]
        self.log.append(("checklist", card_id, name))
        return self._id("cl")

    # test helpers
    def tick_all(self, card_id: str, name: str) -> None:
        for item in self.checklists[(card_id, name)]:
            item[1] = True

    def comments_on(self, card_id: str) -> List[str]:
        return [text for cid, text in self.comments if cid == card_id]
