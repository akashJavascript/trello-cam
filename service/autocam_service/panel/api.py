"""The Onshape panel's requests. Pure: handle() takes a request and returns the response, so tests need no sockets
(server.py feeds it from http.server).

    GET  /panel?key=...&documentId=...       the page (the extension's link in Onshape)
    GET  /static/<file>                      its CSS and JavaScript
    GET  /api/studio?d&wvm&w&e[&fresh][&u]   the Part Studio's parts, each with its card's status
    GET  /api/part?d&wvm&w&e&p               one part, and its card in detail
    GET  /api/qty?d&wvm&w&e&p[&fresh]        how many of it each assembly of the document uses
    POST /api/send                           make its card (or make a sent-back or Drafts card over)
    POST /api/qty                            change a card's quantity
    POST /api/me                             remember a student's name for their Onshape user id
    POST /api/selection                      log what Onshape's SELECTION messages look like (a few a day)
    GET  /api/preview?key=...&sheet=...      a sheet card's preview picture

Every /api request carries the key in an X-AutoCAM-Key header (the preview, an <img>, in its query instead).
"""

import hmac
import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from autocam_core.hotfolder import write_atomic

from ..cards import MAX_QTY
from ..config import Config, Material
from ..fits import parse_fit
from ..tracker.base import Card
from .board import ACTIVE, PanelBoard, Send, read_part_card
from .onshape_reads import NoAccess, OutOfCalls, PanelOnshape, PanelOnshapeError

log = logging.getLogger("autocam.panel")

STATIC = Path(__file__).with_name("static")
STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".svg": "image/svg+xml"}
ID_RE = re.compile(r"^[0-9a-f]{24}$")
PART_RE = re.compile(r"^[A-Za-z0-9_.+/=-]{1,64}$")
USER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_BODY = 16 * 1024
SELECTION_LOGS_PER_DAY = 30
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors https://*.onshape.com https://onshape.com; base-uri 'none'; form-action 'none'")
LIST_NAMES = {"inbox": "Drafts", "ready_for_cam": "Ready for CAM"}


@dataclass
class Response:
    status: int
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: Dict[str, str] = field(default_factory=dict)


class Refused(Exception):
    """A request the panel turns down: (HTTP status, what to show the student)."""

    def __init__(self, status: int, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.extra = extra


def _json(status: int, data: Any) -> Response:
    return Response(status, json.dumps(data).encode("utf-8"),
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def clean_text(text: Any, most: int) -> str:
    """One line: no control characters, spaces collapsed, at most `most` characters."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))
    return " ".join(text.split())[:most].strip()


def material_label(m: Material) -> str:
    if m.family == "aluminum":
        return f"{m.name} aluminum"
    if m.family == "polycarbonate":
        return f"Polycarbonate, {m.color}" if m.color else "Polycarbonate"
    return f"{m.name} {m.color}".strip()


def material_line(m: Material) -> str:
    """What a card's Material: line says for it (materials._by_hint reads it back)."""
    return f"{m.name} {m.color}".strip()


class Names:
    """Students' names by Onshape user id (state/panel_users.json): typed once, then every send says who."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _all(self) -> Dict[str, str]:
        try:
            return dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}

    def get(self, uid: str) -> str:
        return self._all().get(uid, "") if uid else ""

    def set(self, uid: str, name: str) -> None:
        with self._lock:
            names = self._all()
            names[uid] = name
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(self.path, (json.dumps(names, indent=1, sort_keys=True) + "\n").encode("utf-8"))


class PanelApp:
    def __init__(self, cfg: Config, key: str, onshape: PanelOnshape, board: PanelBoard, names: Names,
                 clock: Callable[[], float] = time.monotonic,
                 today: Callable[[], str] = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d")):
        if not key:
            raise ValueError("the panel needs AUTOCAM_PANEL_KEY")
        self.cfg = cfg
        self._key = key.encode("utf-8")
        self.onshape = onshape
        self.board = board
        self.names = names
        self.clock = clock
        self.today = today
        self._sends: Deque[float] = deque()
        self._selection_logs: Tuple[str, int] = ("", 0)
        self._lock = threading.Lock()

    # ---- entry
    def handle(self, method: str, path: str, query: Dict[str, str], headers: Dict[str, str],
               body: bytes = b"") -> Response:
        try:
            if path == "/panel" and method == "GET":
                return self._page(query)
            if path.startswith("/static/") and method == "GET":
                return self._static(path[len("/static/"):])
            if not path.startswith("/api/"):
                return Response(404, b"Not found", "text/plain; charset=utf-8")
            given = query.get("key", "") if path == "/api/preview" else headers.get("x-autocam-key", "")
            if not self._key_ok(given):
                return _json(403, {"error": "This isn't AutoCAM's panel link (the key is wrong). Ask a mentor."})
            if len(body) > MAX_BODY:
                return _json(413, {"error": "That's too much text."})
            data = json.loads(body.decode("utf-8")) if method == "POST" and body else {}
            if not isinstance(data, dict):
                raise Refused(400, "Bad request")
            route = {("GET", "/api/studio"): self.studio, ("GET", "/api/part"): self.part,
                     ("GET", "/api/qty"): self.qty, ("POST", "/api/send"): self.send,
                     ("POST", "/api/qty"): self.change_qty, ("POST", "/api/me"): self.me,
                     ("POST", "/api/selection"): self.selection}.get((method, path))
            if path == "/api/preview" and method == "GET":
                return self.preview(query)
            if route is None:
                return _json(404, {"error": "Not found"})
            return _json(200, route(query if method == "GET" else data))
        except Refused as e:
            return _json(e.status, {"error": str(e), **e.extra})
        except NoAccess:
            return _json(403, {"error": "AutoCAM's Onshape account can't open this document. Share it with the "
                                        "team in Onshape, then reopen this panel.", "kind": "noaccess"})
        except OutOfCalls as e:
            return _json(429, {"error": str(e), "kind": "calls"})
        except PanelOnshapeError as e:
            return _json(502, {"error": str(e)})
        except ValueError:
            return _json(400, {"error": "Bad request"})
        except Exception:  # noqa: BLE001 - one bad request never stops the server; the log has it
            log.exception("panel: %s %s failed", method, path)
            return _json(500, {"error": "Something went wrong in AutoCAM. Try again, or make the card on Trello."})

    def _key_ok(self, given: str) -> bool:
        return bool(given) and hmac.compare_digest(given.encode("utf-8"), self._key)

    # ---- the page
    def _page(self, query: Dict[str, str]) -> Response:
        headers = {"Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
                   "X-Content-Type-Options": "nosniff"}
        if not self._key_ok(query.get("key", "")):
            return Response(403, b"This isn't AutoCAM's panel link (the key is wrong). Ask a mentor.",
                            "text/plain; charset=utf-8", headers)
        return Response(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8", headers)

    def _static(self, name: str) -> Response:
        path = STATIC / name
        if "/" in name or "\\" in name or name.startswith(".") or path.suffix not in STATIC_TYPES or not path.is_file():
            return Response(404, b"Not found", "text/plain; charset=utf-8")
        return Response(200, path.read_bytes(), STATIC_TYPES[path.suffix],
                        {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})

    # ---- reading
    def _studio(self, q: Dict[str, str]) -> Tuple[str, str, str, str]:
        did, wvm, wvmid, eid = q.get("d", ""), q.get("wvm", ""), q.get("w", ""), q.get("e", "")
        if not (ID_RE.match(did) and ID_RE.match(wvmid) and ID_RE.match(eid) and wvm in ("w", "v")):
            raise Refused(400, "Open this panel from a Part Studio tab.")
        return did, wvm, wvmid, eid

    def _part_id(self, q: Dict[str, str]) -> str:
        pid = str(q.get("p", ""))
        if not PART_RE.match(pid):
            raise Refused(400, "Pick a part first.")
        return pid

    def _materials(self) -> Dict[str, Dict[str, Any]]:
        """Picker choices by material key, each with the Onshape material it is (smoked is Onshape's polycarbonate
        plus the Smoked label)."""
        cfg = self.cfg
        onshape_of = {key: name for name, key in cfg.onshape.material_map.items()}
        out: Dict[str, Dict[str, Any]] = {}
        for key, m in cfg.materials.items():
            name = onshape_of.get(key)
            if name is None and m.color == "smoked":
                name = next((onshape_of[k] for k, o in cfg.materials.items() if k in onshape_of
                             and o.family == m.family and o.name == m.name), None)
            if name is not None:
                out[key] = {"value": key, "label": material_label(m), "onshape": name, "family": m.family,
                            "smoked": m.color == "smoked"}
        return out

    def _part_material(self, part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        shown = ((part.get("material") or {}).get("displayName") or "").strip()
        if not shown:
            return None
        lowered = {k.lower(): v for k, v in self.cfg.onshape.material_map.items()}
        key = lowered.get(shown.lower())
        m = self.cfg.materials.get(key) if key else None
        return {"onshape": shown, "key": key, "label": material_label(m) if m else shown,
                "family": m.family if m else ""}

    def _solids(self, parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [p for p in parts if p.get("bodyType") in (None, "solid") and p.get("partId")]

    def _find(self, did: str, wvm: str, wvmid: str, eid: str, pid: str) -> Dict[str, Any]:
        part = next((p for p in self._solids(self.onshape.parts(did, wvm, wvmid, eid)) if p["partId"] == pid), None)
        if part is None:
            raise Refused(404, "That part isn't in this Part Studio any more. Press Refresh.")
        return part

    def studio(self, q: Dict[str, str]) -> Dict[str, Any]:
        did, wvm, wvmid, eid = self._studio(q)
        parts = self._solids(self.onshape.parts(did, wvm, wvmid, eid, fresh=q.get("fresh") == "1"))
        board_error, cards = "", None
        try:
            cards = self.board.part_cards(fresh=q.get("fresh") == "1")
        except Exception as e:  # noqa: BLE001 - parts still show; status says it couldn't be read
            log.warning("panel: reading the board: %s", e)
            board_error = "Couldn't read the Trello board just now, so statuses are missing."
        listed = []
        for p in sorted(parts, key=lambda p: _natural(p.get("name") or "")):
            chip = {"status": "unknown", "label": ""} if cards is None else \
                self.board.chip(self.board.for_part(did, eid, p["partId"], p.get("name") or "", cards))
            listed.append({"id": p["partId"], "name": p.get("name") or "", "material": self._part_material(p),
                           "chip": chip})
        uid = q.get("u", "")
        return {"parts": listed, "boardError": board_error, "writable": wvm == "w",
                "user": self.names.get(uid) if USER_RE.match(uid or "") else "",
                "materials": list(self._materials().values()), "fit": self.cfg.fit.enabled,
                "face": self.cfg.face is not None, "tabsDefault": self.cfg.tabs.default,
                "callsLeft": self.onshape.calls_left(), "maxQty": MAX_QTY}

    def part(self, q: Dict[str, str]) -> Dict[str, Any]:
        did, wvm, wvmid, eid = self._studio(q)
        pid = self._part_id(q)
        part = self._find(did, wvm, wvmid, eid, pid)
        pc = self.board.for_part(did, eid, pid, part.get("name") or "", self.board.part_cards())
        return {"part": {"id": pid, "name": part.get("name") or "", "material": self._part_material(part)},
                "card": self.board.detail(pc) if pc is not None else None}

    def qty(self, q: Dict[str, str]) -> Dict[str, Any]:
        did, wvm, wvmid, eid = self._studio(q)
        pid = self._part_id(q)
        try:
            return self.onshape.assembly_counts(did, wvm, wvmid, eid, pid, fresh=q.get("fresh") == "1")
        except OutOfCalls as e:
            return {"assemblies": [], "counted": 0, "total": 0, "note": str(e)}

    def preview(self, q: Dict[str, str]) -> Response:
        sheet = q.get("sheet", "")
        if not ID_RE.match(sheet):
            return _json(400, {"error": "Bad request"})
        data = self.board.preview(sheet)
        if data is None:
            return _json(404, {"error": "No preview"})
        return Response(200, data, "image/png", {"Cache-Control": "private, max-age=3600"})

    # ---- writing
    def me(self, d: Dict[str, Any]) -> Dict[str, Any]:
        name = clean_text(d.get("name"), 40)
        uid = str(d.get("u") or "")
        if not name:
            raise Refused(400, "Type your name.")
        if USER_RE.match(uid):
            self.names.set(uid, name)
        return {"user": name}

    def selection(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """Onshape's docs don't say what a SELECTION message holds; the first few each day go in the log."""
        with self._lock:
            day, n = self._selection_logs
            n = n + 1 if day == self.today() else 1
            self._selection_logs = (self.today(), n)
        if n <= SELECTION_LOGS_PER_DAY:
            log.info("panel: Onshape selection message: %s", json.dumps(d.get("message"))[:2000])
        return {}

    def _rate_limit(self) -> None:
        with self._lock:
            now = self.clock()
            while self._sends and now - self._sends[0] > 60:
                self._sends.popleft()
            if len(self._sends) >= self.cfg.panel.sends_per_minute:
                raise Refused(429, "Lots of cards were just sent. Wait a minute and try again.")
            self._sends.append(now)

    def _sender(self, d: Dict[str, Any]) -> str:
        name = clean_text(d.get("name"), 40)
        if not name:
            raise Refused(400, "Type your name first (it goes on the card).")
        uid = str(d.get("u") or "")
        if USER_RE.match(uid) and self.names.get(uid) != name:
            self.names.set(uid, name)
        return name

    def _qty(self, d: Dict[str, Any]) -> int:
        qty = d.get("qty")
        if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= MAX_QTY:
            raise Refused(400, f"The quantity must be a whole number from 1 to {MAX_QTY}.")
        return qty

    def _host(self, server: Any) -> str:
        host = (urlsplit(str(server or "")).hostname or "").lower()
        if host == "onshape.com" or host.endswith(".onshape.com"):
            return host
        return urlsplit(self.cfg.onshape.base_url).hostname or "cad.onshape.com"

    def _fit_line(self, fit: Any, nest: bool) -> str:
        fit = fit if isinstance(fit, dict) else {}
        kind = fit.get("kind") or "none"
        if kind == "none":
            return ""
        if not self.cfg.fit.enabled:
            raise Refused(400, "Fits are switched off in AutoCAM.")
        if not nest:
            raise Refused(400, "A part with a fit can't be placed by hand. Tick Nest it with other parts, or set "
                               "Fit to None.")
        if kind == "outline":
            return "outline"
        if kind != "hole":
            raise Refused(400, "Bad request")
        said = clean_text(fit.get("size"), 20)
        got = parse_fit(f"Fit: {said} hole")
        if not isinstance(got, tuple):
            raise Refused(400, got or "Type the hole's size, like 1.125 or 22 mm.")
        return f"{got[1]:.4f}".rstrip("0").rstrip(".") + " hole"

    def send(self, d: Dict[str, Any]) -> Dict[str, Any]:
        self._rate_limit()
        did, wvm, wvmid, eid = self._studio(d)
        pid = self._part_id(d)
        qty = self._qty(d)
        sender = self._sender(d)
        nest, tabs = bool(d.get("nest", True)), bool(d.get("tabs", self.cfg.tabs.default))
        fit_line = self._fit_line(d.get("fit"), nest)
        if fit_line:
            tabs = True                                  # a fitted part is always tabbed (fits.py)
        part = self._find(did, wvm, wvmid, eid, pid)
        name = part.get("name") or ""

        cards = self.board.part_cards(fresh=True)
        existing = None
        if d.get("card"):
            existing = self.board.for_part(did, eid, pid, name, cards, card_id=str(d["card"]))
            if existing is None or existing.status not in ("back", "drafts"):
                raise Refused(409, "That card has moved on since this panel looked. Press Refresh.")
        else:
            going = self.board.for_part(did, eid, pid, name, cards)
            if going is not None and going.status in ACTIVE:
                raise Refused(409, "This part already has a card. Change its quantity instead of sending it again.",
                              kind="duplicate", card=self.board.detail(going))

        materials = self._materials()
        have = self._part_material(part)
        smoked = bool(d.get("smoked"))
        line, set_in_onshape, why = "", False, ""
        if have and have["key"]:
            if have["family"] != "polycarbonate":
                smoked = False
        else:
            chosen = materials.get(str(d.get("material") or ""))
            if chosen is None:
                raise Refused(400, "Pick the part's material." if not have else
                              f"Onshape says {have['onshape']}, which AutoCAM doesn't cut. Pick the material.")
            smoked = chosen["smoked"]
            if wvm == "w":
                set_in_onshape, why = self.onshape.set_material(did, wvmid, eid, pid, chosen["onshape"])
            else:
                why = "this is a version, which can't be changed"
            if not set_in_onshape:
                line = material_line(self.cfg.materials[chosen["value"]])
                log.info("panel: %s's material not set in Onshape (%s); the card says Material: %s", name, why, line)

        face = bool(d.get("face")) and self.cfg.face is not None
        s = Send(did=did, wvm=wvm, wvmid=wvmid, eid=eid, pid=pid, name=name, host=self._host(d.get("server")),
                 qty=qty, nest=nest, tabs=tabs, face=face, rush=bool(d.get("rush")), smoked=smoked,
                 drafts=bool(d.get("drafts")), material_line=line, fit_line=fit_line, sender=sender,
                 notes=clean_text(d.get("notes"), 300))
        card = self.board.write(s, existing)
        where = "inbox" if s.drafts else "ready_for_cam"
        log.info("panel: %s sent %s x%d to %s: %s", sender, name, qty, LIST_NAMES[where], card.url)
        return {"card": self._detail(card, where), "list": LIST_NAMES[where],
                "material": {"setInOnshape": set_in_onshape, "why": why, "line": line}}

    def _detail(self, card: Card, list_key: str) -> Dict[str, Any]:
        pc = read_part_card(card, {"inbox": "drafts", "ready_for_cam": "queued"}.get(list_key, "queued"))
        return self.board.detail(pc) if pc is not None else {"url": card.url, "card": card.id}

    def change_qty(self, d: Dict[str, Any]) -> Dict[str, Any]:
        self._rate_limit()
        did, wvm, wvmid, eid = self._studio(d)
        pid = self._part_id(d)
        qty = self._qty(d)
        part = self._find(did, wvm, wvmid, eid, pid)
        pc = self.board.for_part(did, eid, pid, part.get("name") or "", self.board.part_cards(fresh=True),
                                 card_id=str(d.get("card") or ""))
        if pc is None or pc.status not in ("drafts", "queued", "sheet"):
            raise Refused(409, "That card has moved on since this panel looked. Press Refresh.")
        card = self.board.change_qty(pc, qty)
        log.info("panel: %s changed %s to x%d: %s", clean_text(d.get("name"), 40) or "someone", pc.card.name, qty,
                 card.url)
        status = "queued" if pc.status == "sheet" else pc.status
        fresh = read_part_card(card, status)
        return {"card": self.board.detail(fresh) if fresh is not None else None, "renest": pc.status == "sheet"}


def _natural(name: str):
    """P-2 before P-10."""
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", name)]
