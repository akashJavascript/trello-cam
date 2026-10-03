"""The tracker interface the service is built against (brief, "Tracker choice").

Trello is the first adapter. A Notion or GitHub Projects adapter implements the same `_` methods.
Two safety rules live here so no adapter can skip them:
- automation never moves or creates anything in Ready to cut (decision 8);
- a .tap is only uploaded with a passing guard report for exactly those bytes (decision 22).
"""

import abc
import hashlib
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from autocam_core.tapguard import GuardReport

NEVER_AUTOMATED = "ready_to_cut"


class AutomationForbidden(Exception):
    pass


class UploadRefused(Exception):
    pass


@dataclass(frozen=True)
class Attachment:
    id: str
    name: str
    url: str
    mime: str = ""
    size: int = 0
    is_upload: bool = True


@dataclass(frozen=True)
class Check:
    checklist: str
    item: str
    done: bool


@dataclass(frozen=True)
class Card:
    id: str
    name: str
    desc: str
    list_key: Optional[str]          # which configured list it's in (None if it isn't one of ours)
    url: str
    labels: Tuple[str, ...] = ()
    attachments: Tuple[Attachment, ...] = ()
    checks: Tuple[Check, ...] = ()   # every checklist item on the card
    is_template: bool = False
    closed: bool = False             # archived (only get_card returns archived cards)


class CardNotFound(Exception):
    """get_card: no such card (deleted). Anything else (Trello down) raises something else."""


@dataclass(frozen=True)
class ChecklistState:
    done: int
    total: int

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.done == self.total


class Tracker(abc.ABC):
    def __init__(self, attachment_limit_mb: float = 10.0):
        self.attachment_limit_bytes = int(attachment_limit_mb * 1024 * 1024)

    # ---- reads
    @abc.abstractmethod
    def list_cards(self, list_key: str) -> List[Card]: ...

    @abc.abstractmethod
    def get_card(self, card_id: str) -> Card: ...

    @abc.abstractmethod
    def checklist(self, card_id: str, name: str) -> Optional[ChecklistState]: ...

    @abc.abstractmethod
    def download(self, attachment: Attachment) -> bytes: ...

    # ---- writes (checked here, implemented by adapters)
    def move(self, card_id: str, list_key: str) -> None:
        self._refuse_ready_to_cut(list_key)
        self._move(card_id, list_key)

    def create_card(self, list_key: str, title: str, desc: str) -> Card:
        self._refuse_ready_to_cut(list_key)
        return self._create_card(list_key, title, desc)

    def comment(self, card_id: str, text: str) -> None:
        self._comment(card_id, text)

    def attach_file(self, card_id: str, name: str, data: bytes, mime: str) -> str:
        if name.lower().endswith(".tap"):
            raise UploadRefused("programs go through attach_program, which checks the guard report")
        return self._attach_checked(card_id, name, data, mime)

    def attach_program(self, card_id: str, name: str, data: bytes, report: GuardReport) -> str:
        """Upload a .tap only if this exact byte string passed the guard."""
        if not report.passed:
            raise UploadRefused(f"{name}: guard failed: {report.summary()}")
        if hashlib.sha256(data).hexdigest() != report.sha256:
            raise UploadRefused(f"{name}: these bytes are not the ones the guard checked")
        if ".rejected." in name.lower():
            raise UploadRefused(f"{name}: rejected programs are never uploaded")
        return self._attach_checked(card_id, name, data, "text/plain")

    def attach_link(self, card_id: str, url: str, name: str) -> str:
        return self._attach_link(card_id, url, name)

    def add_checklist(self, card_id: str, name: str, items: Sequence[str], checked: bool = False) -> str:
        return self._add_checklist(card_id, name, list(items), checked)

    def remove_checklists(self, card_id: str, name: str) -> None:
        """Delete every checklist with this name (a rebuilt sheet gets fresh, unticked ones)."""
        self._remove_checklists(card_id, name)

    def update_card(self, card_id: str, title: str, desc: str) -> None:
        self._update_card(card_id, title, desc)

    def delete_attachment(self, card_id: str, attachment_id: str) -> None:
        self._delete_attachment(card_id, attachment_id)

    def archive(self, card_id: str) -> None:
        self._archive(card_id)

    def add_label(self, card_id: str, name: str) -> None:
        """Put the board's label with this name on the card (made if the board doesn't have it)."""
        self._add_label(card_id, name)

    def set_cover(self, card_id: str, attachment_id: str) -> None:
        """Show an attached image on the card's front (the nest preview on sheet cards)."""
        self._set_cover(card_id, attachment_id)

    def _attach_checked(self, card_id: str, name: str, data: bytes, mime: str) -> str:
        if len(data) > self.attachment_limit_bytes:
            raise UploadRefused(f"{name} is {len(data) / 1048576:.1f} MB; the attachment limit is "
                                f"{self.attachment_limit_bytes / 1048576:.0f} MB")
        return self._attach_file(card_id, name, data, mime)

    @staticmethod
    def _refuse_ready_to_cut(list_key: str) -> None:
        if list_key == NEVER_AUTOMATED:
            raise AutomationForbidden("automation never puts anything in Ready to cut; a human reviews every sheet")

    @abc.abstractmethod
    def _move(self, card_id: str, list_key: str) -> None: ...

    @abc.abstractmethod
    def _create_card(self, list_key: str, title: str, desc: str) -> Card: ...

    @abc.abstractmethod
    def _comment(self, card_id: str, text: str) -> None: ...

    @abc.abstractmethod
    def _attach_file(self, card_id: str, name: str, data: bytes, mime: str) -> str: ...

    @abc.abstractmethod
    def _attach_link(self, card_id: str, url: str, name: str) -> str: ...

    @abc.abstractmethod
    def _add_checklist(self, card_id: str, name: str, items: List[str], checked: bool) -> str: ...

    @abc.abstractmethod
    def _remove_checklists(self, card_id: str, name: str) -> None: ...

    @abc.abstractmethod
    def _update_card(self, card_id: str, title: str, desc: str) -> None: ...

    @abc.abstractmethod
    def _delete_attachment(self, card_id: str, attachment_id: str) -> None: ...

    @abc.abstractmethod
    def _archive(self, card_id: str) -> None: ...

    @abc.abstractmethod
    def _set_cover(self, card_id: str, attachment_id: str) -> None: ...

    @abc.abstractmethod
    def _add_label(self, card_id: str, name: str) -> None: ...
