"""In-memory tracker for tests and offline end-to-end runs."""

import itertools
from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple

from .base import Attachment, Card, CardNotFound, Check, ChecklistState, Tracker


class FakeTracker(Tracker):
    def __init__(self, cards: Sequence[Card] = (), attachment_limit_mb: float = 10.0):
        super().__init__(attachment_limit_mb)
        self.cards: Dict[str, Card] = {c.id: c for c in cards}
        self.comments: List[Tuple[str, str]] = []
        self.files: Dict[str, Tuple[str, str, bytes]] = {}      # attachment id -> (card, name, data)
        self.links: List[Tuple[str, str, str]] = []
        self.checklists: Dict[Tuple[str, str], List[List]] = {}  # (card, name) -> [[item, done], ...]
        self.downloads: Dict[str, bytes] = {}                    # attachment id -> bytes to return
        self.covers: Dict[str, str] = {}                         # card id -> attachment id
        self.link_ids: Dict[str, Tuple[str, str, str]] = {}      # attachment id -> (card, url, name)
        self.archived: List[str] = []
        self.log: List[Tuple] = []
        self._ids = itertools.count(1)

    def _id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids):04d}"

    # reads: cards come back with their current attachments and checklist items, like Trello's
    def _view(self, c: Card) -> Card:
        atts = tuple(Attachment(aid, name, f"https://trello.example/a/{aid}") for aid, (cid, name, _) in self.files.items()
                     if cid == c.id)
        atts += tuple(Attachment(aid, name, url, is_upload=False) for aid, (cid, url, name) in self.link_ids.items()
                      if cid == c.id)
        checks = tuple(Check(name, item, done) for (cid, name), items in self.checklists.items() if cid == c.id
                       for item, done in items)
        return replace(c, attachments=c.attachments + atts, checks=c.checks + checks)

    def list_cards(self, list_key: str) -> List[Card]:
        return [self._view(c) for c in self.cards.values() if c.list_key == list_key and c.id not in self.archived]

    def get_card(self, card_id: str) -> Card:
        if card_id not in self.cards:
            raise CardNotFound(card_id)
        return replace(self._view(self.cards[card_id]), closed=card_id in self.archived)

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
        aid = self._id("att")
        self.link_ids[aid] = (card_id, url, name)
        return aid

    def _add_checklist(self, card_id: str, name: str, items: List[str], checked: bool) -> str:
        self.checklists[(card_id, name)] = [[item, checked] for item in items]
        self.log.append(("checklist", card_id, name))
        return self._id("cl")

    def _remove_checklists(self, card_id: str, name: str) -> None:
        self.checklists.pop((card_id, name), None)
        self.log.append(("remove_checklist", card_id, name))

    def _update_card(self, card_id: str, title: str, desc: str) -> None:
        self.cards[card_id] = replace(self.cards[card_id], name=title, desc=desc)
        self.log.append(("update", card_id, title))

    def _delete_attachment(self, card_id: str, attachment_id: str) -> None:
        if attachment_id in self.files:
            del self.files[attachment_id]
        elif attachment_id in self.link_ids:
            _, url, name = self.link_ids.pop(attachment_id)
            self.links.remove((card_id, url, name))
        self.covers = {c: a for c, a in self.covers.items() if a != attachment_id}
        self.log.append(("delete_attachment", card_id, attachment_id))

    def _archive(self, card_id: str) -> None:
        self.archived.append(card_id)
        self.log.append(("archive", card_id))

    def _set_cover(self, card_id: str, attachment_id: str) -> None:
        self.covers[card_id] = attachment_id
        self.log.append(("cover", card_id, attachment_id))

    # test helpers
    def tick_all(self, card_id: str, name: str, done: bool = True) -> None:
        for item in self.checklists[(card_id, name)]:
            item[1] = done

    def comments_on(self, card_id: str) -> List[str]:
        return [text for cid, text in self.comments if cid == card_id]
