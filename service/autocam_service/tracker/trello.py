"""Trello adapter (REST API with an API key + token). Free plan: no custom fields, no Butler.

Every list is addressed by a key from config (`trello.lists`), never by name, so renaming a list
on the board doesn't break anything. `python -m autocam_service trello-discover` prints the IDs.
"""

import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from .base import Attachment, Card, CardNotFound, Check, ChecklistState, Tracker

API = "https://api.trello.com/1"
TEXT_LIMIT = 16000          # Trello caps comments and descriptions at 16384 characters
TRELLO_HOSTS = ("trello.com", "api.trello.com")


class TrelloError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class TrelloConfigError(Exception):
    pass


def _requests_transport(method, url, params, files, headers):
    import requests
    r = requests.request(method, url, params=params, files=files, headers=headers, timeout=60)
    return r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content


class TrelloHttp:
    """Key/token auth, JSON in and out.

    429 is retried for every method (Trello rejected the request without doing it). 5xx is retried only
    for GET and PUT: a POST that timed out may have been done, and repeating it would duplicate a card,
    comment or attachment.
    """

    def __init__(self, key: str, token: str, transport: Callable = _requests_transport,
                 sleep: Callable[[float], None] = time.sleep, max_retries: int = 4):
        self._key = key
        self._token = token
        self.transport = transport
        self.sleep = sleep
        self.max_retries = max_retries

    def __repr__(self) -> str:
        return "TrelloHttp(<key>, <token>)"

    def call(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
             files: Optional[Dict[str, Tuple[str, bytes, str]]] = None) -> Any:
        import json
        query = {**(params or {}), "key": self._key, "token": self._token}
        for attempt in range(self.max_retries + 1):
            status, headers, body = self.transport(method, API + path, query, files, {"Accept": "application/json"})
            if status == 429 or (status >= 500 and method in ("GET", "PUT")):
                if attempt == self.max_retries:
                    break
                try:
                    wait = float(headers.get("retry-after", 2 ** attempt))
                except ValueError:
                    wait = 2.0 ** attempt
                self.sleep(min(wait, 60.0))
                continue
            if status >= 400:
                raise TrelloError(f"Trello {method} {path} -> {status}: {body[:200].decode('utf-8', 'replace')}", status)
            return json.loads(body.decode("utf-8")) if body else None
        raise TrelloError(f"Trello {method} {path} kept failing ({status})", status)

    def download(self, url: str) -> bytes:
        host = urlsplit(url).hostname or ""
        if host not in TRELLO_HOSTS:
            raise TrelloError(f"refusing to send Trello credentials to {host}")
        auth = f'OAuth oauth_consumer_key="{self._key}", oauth_token="{self._token}"'
        status, _, body = self.transport("GET", url, {}, None, {"Authorization": auth})
        if status >= 400:
            raise TrelloError(f"attachment download -> {status}", status)
        return body


class TrelloTracker(Tracker):
    CARD_PARAMS = {"fields": "id,name,desc,idList,labels,shortUrl,isTemplate,closed", "attachments": "true",
                   "attachment_fields": "id,name,url,mimeType,bytes,isUpload",
                   "checklists": "all", "checklist_fields": "name", "checkItem_fields": "name,state"}

    def __init__(self, http: TrelloHttp, lists: Mapping[str, str], attachment_limit_mb: float = 10.0):
        super().__init__(attachment_limit_mb)
        self.http = http
        self.lists = dict(lists)
        self.list_keys = {v: k for k, v in self.lists.items() if v}

    def _list_id(self, key: str) -> str:
        list_id = self.lists.get(key)
        if not list_id:
            raise TrelloConfigError(f"trello.lists.{key} is empty in config; run `python -m autocam_service trello-discover`")
        return list_id

    def _card(self, c: Dict[str, Any]) -> Card:
        atts = tuple(Attachment(a["id"], a.get("name") or "", a.get("url") or "", a.get("mimeType") or "",
                                int(a.get("bytes") or 0), bool(a.get("isUpload", True)))
                     for a in c.get("attachments") or [])
        checks = tuple(Check(cl.get("name") or "", i.get("name") or "", i.get("state") == "complete")
                       for cl in c.get("checklists") or [] for i in cl.get("checkItems") or [])
        return Card(id=c["id"], name=c.get("name") or "", desc=c.get("desc") or "",
                    list_key=self.list_keys.get(c.get("idList")), url=c.get("shortUrl") or "",
                    labels=tuple(l.get("name") or "" for l in c.get("labels") or []), attachments=atts,
                    checks=checks, is_template=bool(c.get("isTemplate")), closed=bool(c.get("closed")))

    # reads
    def list_cards(self, list_key: str) -> List[Card]:
        return [self._card(c) for c in self.http.call("GET", f"/lists/{self._list_id(list_key)}/cards", self.CARD_PARAMS)]

    def get_card(self, card_id: str) -> Card:
        try:
            return self._card(self.http.call("GET", f"/cards/{card_id}", self.CARD_PARAMS))
        except TrelloError as e:
            if e.status == 404:
                raise CardNotFound(card_id) from e
            raise

    def checklist(self, card_id: str, name: str) -> Optional[ChecklistState]:
        """All checklists with this name count together (a half-created duplicate can't be ticked instead)."""
        lists = [cl for cl in self.http.call("GET", f"/cards/{card_id}/checklists",
                                             {"fields": "name", "checkItem_fields": "state"})
                 if cl.get("name") == name]
        if not lists:
            return None
        items = [i for cl in lists for i in cl.get("checkItems") or []]
        return ChecklistState(sum(1 for i in items if i.get("state") == "complete"), len(items))

    def download(self, attachment: Attachment) -> bytes:
        return self.http.download(attachment.url)

    # writes
    def _move(self, card_id: str, list_key: str) -> None:
        self.http.call("PUT", f"/cards/{card_id}", {"idList": self._list_id(list_key)})

    def _create_card(self, list_key: str, title: str, desc: str) -> Card:
        c = self.http.call("POST", "/cards", {"idList": self._list_id(list_key), "name": title[:TEXT_LIMIT],
                                              "desc": desc[:TEXT_LIMIT], "pos": "bottom"})
        return self._card(c)

    def _comment(self, card_id: str, text: str) -> None:
        self.http.call("POST", f"/cards/{card_id}/actions/comments", {"text": text[:TEXT_LIMIT]})

    def _attach_file(self, card_id: str, name: str, data: bytes, mime: str) -> str:
        r = self.http.call("POST", f"/cards/{card_id}/attachments", {"name": name, "mimeType": mime},
                           files={"file": (name, data, mime)})
        return r["id"]

    def _attach_link(self, card_id: str, url: str, name: str) -> str:
        return self.http.call("POST", f"/cards/{card_id}/attachments", {"url": url, "name": name})["id"]

    def _add_checklist(self, card_id: str, name: str, items: List[str], checked: bool) -> str:
        cl = self.http.call("POST", f"/cards/{card_id}/checklists", {"name": name})
        for item in items:
            self.http.call("POST", f"/checklists/{cl['id']}/checkItems",
                           {"name": item, "checked": "true" if checked else "false"})
        return cl["id"]

    def _remove_checklists(self, card_id: str, name: str) -> None:
        for cl in self.http.call("GET", f"/cards/{card_id}/checklists", {"fields": "name"}):
            if cl.get("name") == name:
                self.http.call("DELETE", f"/checklists/{cl['id']}")

    def _update_card(self, card_id: str, title: str, desc: str) -> None:
        self.http.call("PUT", f"/cards/{card_id}", {"name": title[:TEXT_LIMIT], "desc": desc[:TEXT_LIMIT]})

    def _delete_attachment(self, card_id: str, attachment_id: str) -> None:
        self.http.call("DELETE", f"/cards/{card_id}/attachments/{attachment_id}")

    def _archive(self, card_id: str) -> None:
        self.http.call("PUT", f"/cards/{card_id}", {"closed": "true"})

    def _set_cover(self, card_id: str, attachment_id: str) -> None:
        self.http.call("PUT", f"/cards/{card_id}", {"idAttachmentCover": attachment_id})

    def _add_label(self, card_id: str, name: str) -> None:
        def norm(s: str) -> str:
            return " ".join(s.lower().split())
        board = self.http.call("GET", f"/cards/{card_id}", {"fields": "idBoard,idLabels"})
        found = [l for l in self.http.call("GET", f"/boards/{board['idBoard']}/labels", {"fields": "name"})
                 if norm(l.get("name") or "") == norm(name)]
        label = found[0]["id"] if found else self.http.call(
            "POST", "/labels", {"name": name, "color": "red", "idBoard": board["idBoard"]})["id"]
        if label not in (board.get("idLabels") or []):
            self.http.call("POST", f"/cards/{card_id}/idLabels", {"value": label})

    # setup helpers
    def board_lists(self, board_id: str) -> List[Tuple[str, str]]:
        return [(l["id"], l["name"]) for l in self.http.call("GET", f"/boards/{board_id}/lists", {"fields": "name"})]

    def board_cards(self, board_id: str) -> List[Tuple[str, str, str]]:
        return [(c["id"], c["name"], c["idList"])
                for c in self.http.call("GET", f"/boards/{board_id}/cards", {"fields": "name,idList"})]
