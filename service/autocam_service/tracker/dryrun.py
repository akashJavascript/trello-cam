"""Dry-run: read the real board, write nothing. Every write is recorded so the run can be reviewed."""

from typing import List, Optional, Tuple

from .base import Attachment, Card, ChecklistState, Tracker


class DryRunTracker(Tracker):
    def __init__(self, inner: Tracker):
        super().__init__(inner.attachment_limit_bytes / 1048576)
        self.inner = inner
        self.intended: List[Tuple] = []
        self._n = 0

    def _fake_id(self, prefix: str) -> str:
        self._n += 1
        return f"dryrun-{prefix}-{self._n}"

    def list_cards(self, list_key: str) -> List[Card]:
        return self.inner.list_cards(list_key)

    def get_card(self, card_id: str) -> Card:
        return self.inner.get_card(card_id)

    def checklist(self, card_id: str, name: str) -> Optional[ChecklistState]:
        return self.inner.checklist(card_id, name)

    def download(self, attachment: Attachment) -> bytes:
        return self.inner.download(attachment)

    def _move(self, card_id, list_key):
        self.intended.append(("move", card_id, list_key))

    def _create_card(self, list_key, title, desc):
        self.intended.append(("create_card", list_key, title))
        cid = self._fake_id("card")
        return Card(cid, title, desc, list_key, f"https://trello.com/c/{cid}")

    def _comment(self, card_id, text):
        self.intended.append(("comment", card_id, text))

    def _attach_file(self, card_id, name, data, mime):
        self.intended.append(("attach_file", card_id, name, len(data)))
        return self._fake_id("att")

    def _attach_link(self, card_id, url, name):
        self.intended.append(("attach_link", card_id, url))
        return self._fake_id("att")

    def _add_checklist(self, card_id, name, items):
        self.intended.append(("add_checklist", card_id, name, tuple(items)))
        return self._fake_id("cl")
