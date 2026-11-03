"""The Onshape panel's requests. Pure: handle() takes a request and returns the response, so tests need no sockets
(server.py feeds it from http.server).

    GET  /panel?key=...&documentId=...       the page (the extension's link in Onshape)
    GET  /static/<file>                      its CSS and JavaScript
    GET  /api/studio?d&wvm&w&e[&fresh][&u]   the Part Studio's parts, each with its card's status
    GET  /api/part?d&wvm&w&e&p               one part, and its card in detail
    GET  /api/qty?d&wvm&w&e&p[&fresh]        how many of it each assembly of the document uses
    GET  /api/check?d&wvm&w&e&p[&m][&fresh]  the design checks (checks.py) with that material (default: the part's)
    GET  /api/changed?d&wvm&w&e&p&card       has the part changed since that card's run CAM'd it (its manufacturing
                                             version, from state/jobs)? Tells the card's uncut sheets once
    POST /api/renest                         a card on a sheet back to Ready for CAM (nest the new version)
    POST /api/accept-change                  a part that changed after it was CAM'd: cut it as it was (clears CAD changed)
    GET  /api/assembly?d&wvm&w&e[&fresh][&u] an assembly's parts (its flattened BOM), each with its card's status
    POST /api/send-many                      cards for the ticked parts of an assembly
    POST /api/send-program                   a part someone CAM'd themselves: its .tap on a card linked to the part,
                                             checked by the guard first; AutoCAM places it (own_programs.py)
    POST /api/send                           make its card (or make a sent-back or Drafts card over)
    POST /api/qty                            change a card's quantity
    POST /api/me                             remember a student's name for their Onshape user id
    POST /api/selection                      log what Onshape's SELECTION messages look like (a few a day)
    GET  /api/preview?key=...&sheet=...      a sheet card's preview picture
    GET  /api/which-row?d&wvm&w&e&inst       a part clicked in an assembly (its instance id) -> its row in the list
    GET  /api/occurrences?d&wvm&w&e&key      a row's occurrence paths in the assembly (to highlight it in Onshape)
    POST /api/shaft                          change a shaft on the shop's list (while nobody has started it)
    POST /api/shaft-remove                   take a shaft off the list (same)

The shop's shaft list (shafts.py, 2026-11-02), with its own key (shop_key(): someone with the shop's link can start
and finish shafts, nothing else):
    GET  /shop?key=...                       the phone page
    GET  /api/shop?device=...                the shafts to make, and the ones done today
    POST /api/shop/start|stop|done|undo      {id, device}

Every /api request carries the key in an X-AutoCAM-Key header (the preview, an <img>, in its query instead).
"""

import hashlib
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

from autocam_core.tapguard import GuardSpec, check_program

from ..cards import MAX_QTY
from ..config import Config, Material
from ..fits import parse_fit
from ..handprograms import PROGRAM_SUFFIXES, footprint, parse_cutter
from ..tracker.base import Card
from . import checks
from .board import ACTIVE, PanelBoard, Send, read_part_card
from .geometry import differences, shape_key
from .hooks import HOOK_PREFIX, ChangeInbox, secret_ok
from .onshape_reads import NoAccess, OutOfCalls, PanelOnshape, PanelOnshapeError, row_for_instance
from ..shafts import (ACTIVE as SHAFT_ACTIVE, DEVICE_RE, MAX_NOTE, STOCKS, Found, ShaftError, ShaftList, clean_ends,
                      clean_ops, problems, recognize, status_chip)
from ..bought import ACTIVE as PART_ACTIVE, PartList, is_hardware, status_chip as part_chip
from ..onshape.urls import configuration_of, configuration_tag, normal_configuration

log = logging.getLogger("autocam.panel")

STATIC = Path(__file__).with_name("static")
STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".svg": "image/svg+xml"}
ID_RE = re.compile(r"^[0-9a-f]{24}$")
PART_RE = re.compile(r"^[A-Za-z0-9_.+/=-]{1,64}$")
USER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ENTITY_RE = re.compile(r"^[A-Za-z0-9]{1,24}$")       # a face or edge id from Onshape's SELECTION
INSTANCE_RE = re.compile(r"^[A-Za-z0-9+/=_-]{1,40}$")  # an assembly instance id ("IfBvAf3KuZudYUjEe", "Mpp/5tF1I3bXlPgoO")
MCMASTER_RE = re.compile(r"^\d{4,5}[A-Z]\d{1,5}(?:[_ .-]|$)")   # "91259A496_NO THREADS_Alloy Steel Shoulder Screw"
PRINTED_RE = re.compile(r"print|\bPLA\b|\bABS\b|PETG|TPU|nylon|onyx|\bASA\b", re.IGNORECASE)
BOUGHT = "Bought part"
RUBBER_RE = re.compile(r"rubber|neoprene|viton|urethane|silicone|EPDM|nitrile|buna|latex|elastomer|santoprene",
                       re.IGNORECASE)                    # belts, wheels, O-rings: bought
MAX_BODY = 16 * 1024
MAX_PROGRAM_BODY = 4 * 1024 * 1024        # /api/send-program carries the .tap
PROGRAM_PATH = "/api/send-program"
SELECTION_LOGS_PER_DAY = 30
MAX_ASSEMBLY_SEND = 60
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors https://*.onshape.com https://onshape.com; base-uri 'none'; form-action 'none'")
LIST_NAMES = {"inbox": "Drafts", "ready_for_cam": "Ready for CAM"}
SHOP_ACTIONS = ("start", "stop", "done", "undo")             # a shaft, on the phone page
PART_ACTIONS = {"part-ordered": "ordered", "part-bin": "bin", "part-undo": "undo"}   # a bought part


def shop_key(panel_key: str) -> str:
    """The shop page's key, worked out from the panel's: nothing new to keep in .env, and the shop's link (on a QR
    code at the saw) can't send cards."""
    return hmac.new(panel_key.encode("utf-8"), b"autocam shop page", hashlib.sha256).hexdigest()[:32]


def body_limit(path: str) -> int:
    return MAX_PROGRAM_BODY if path == PROGRAM_PATH else MAX_BODY


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
    if m.family == "polycarbonate" and m.name.upper() == "PC":
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


def static_hash(name: str) -> str:
    f = STATIC / name
    return hashlib.sha256(f.read_bytes()).hexdigest()[:12] if f.is_file() else "0"


def versioned_page(name: str = "index.html") -> bytes:
    """index.html (or shop.html) with each /static/ link stamped with its file's hash. Cloudflare tells browsers to
    keep static files for 4 hours whatever this server says (seen 2026-11-01: an old panel.js kept running after a
    deploy); the page itself is no-store, so a changed file gets a new address and is fetched at once."""
    html = (STATIC / name).read_text(encoding="utf-8")

    def stamp(m: "re.Match[str]") -> str:
        return f'"/static/{m.group(1)}?v={static_hash(m.group(1))}"'

    return re.sub(r'"/static/([A-Za-z0-9_.-]+)"', stamp, html).encode("utf-8")


class PanelApp:
    def __init__(self, cfg: Config, key: str, onshape: PanelOnshape, board: PanelBoard, names: Names,
                 clock: Callable[[], float] = time.monotonic,
                 today: Callable[[], str] = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                 inbox: Optional[ChangeInbox] = None, hook_secret: str = "", shafts: Optional[ShaftList] = None,
                 parts: Optional[PartList] = None):
        if not key:
            raise ValueError("the panel needs AUTOCAM_PANEL_KEY")
        self.shafts = shafts if cfg.shafts.enabled else None    # the shop's shaft list (None: off)
        self.parts = parts if cfg.parts.enabled else None       # the shop's bought-parts list (None: off)
        self._shop_key = shop_key(key).encode("utf-8")
        self.cfg = cfg
        self.inbox = inbox                    # Onshape's webhook events (hooks.py); None: no webhook
        self._hook_secret = hook_secret
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
            if path == "/shop" and method == "GET" and (self.shafts is not None or self.parts is not None):
                return self._page(query, shop=True)
            if path.startswith("/static/") and method == "GET":
                return self._static(path[len("/static/"):])
            if not path.startswith("/api/"):
                return Response(404, b"Not found", "text/plain; charset=utf-8")
            if path.startswith(HOOK_PREFIX):                 # Onshape's webhook: its address is its password
                return self.hook(method, path, body)
            given = query.get("key", "") if path == "/api/preview" else headers.get("x-autocam-key", "")
            if path == "/api/shop" or path.startswith("/api/shop/"):
                return self._shop(method, path, query, given, body)
            if not self._key_ok(given):
                return _json(403, {"error": "This isn't AutoCAM's panel link (the key is wrong). Ask a mentor."})
            if len(body) > body_limit(path):
                return _json(413, {"error": "That's too big." if path == PROGRAM_PATH else "That's too much text."})
            data = json.loads(body.decode("utf-8")) if method == "POST" and body else {}
            if not isinstance(data, dict):
                raise Refused(400, "Bad request")
            route = {("GET", "/api/studio"): self.studio, ("GET", "/api/part"): self.part,
                     ("GET", "/api/qty"): self.qty, ("GET", "/api/check"): self.check, ("POST", "/api/send"): self.send,
                     ("GET", "/api/changed"): self.changed, ("POST", "/api/renest"): self.renest,
                     ("POST", "/api/accept-change"): self.accept_change,
                     ("GET", "/api/assembly"): self.assembly, ("POST", "/api/send-many"): self.send_many,
                     ("POST", PROGRAM_PATH): self.send_program,
                     ("POST", "/api/qty"): self.change_qty, ("POST", "/api/me"): self.me,
                     ("POST", "/api/selection"): self.selection,
                     ("GET", "/api/which-part"): self.which_part, ("GET", "/api/which-row"): self.which_row,
                     ("GET", "/api/occurrences"): self.occurrences,
                     ("POST", "/api/shaft"): self.edit_shaft, ("POST", "/api/shaft-remove"): self.remove_shaft,
                     ("POST", "/api/part-remove"): self.remove_part,
                     }.get((method, path))
            if path == "/api/preview" and method == "GET":
                return self.preview(query)
            if route is None:
                return _json(404, {"error": "Not found"})
            return _json(200, route(query if method == "GET" else data))
        except Refused as e:
            return _json(e.status, {"error": str(e), **e.extra})
        except ShaftError as e:
            return _json(e.status, {"error": str(e), **({"kind": e.kind} if e.kind else {})})
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

    def _shop_key_ok(self, given: str) -> bool:
        return bool(given) and hmac.compare_digest(given.encode("utf-8"), self._shop_key)

    # ---- the page
    def _page(self, query: Dict[str, str], shop: bool = False) -> Response:
        headers = {"Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
                   "X-Content-Type-Options": "nosniff"}
        if not (self._shop_key_ok if shop else self._key_ok)(query.get("key", "")):
            return Response(403, b"This isn't AutoCAM's " + (b"shop" if shop else b"panel") +
                            b" link (the key is wrong). Ask a mentor.", "text/plain; charset=utf-8", headers)
        return Response(200, versioned_page("shop.html" if shop else "index.html"), "text/html; charset=utf-8", headers)

    # ---- the shop's lists: shafts and bought parts (their own key)
    def _shop(self, method: str, path: str, query: Dict[str, str], given: str, body: bytes) -> Response:
        if self.shafts is None and self.parts is None:
            return _json(404, {"error": "Not found"})
        if not self._shop_key_ok(given):
            return _json(403, {"error": "This isn't AutoCAM's shop link (the key is wrong). Ask a mentor."})
        if len(body) > MAX_BODY:
            return _json(413, {"error": "That's too much text."})
        if path == "/api/shop" and method == "GET":
            device = query.get("device", "")
            device = device if DEVICE_RE.match(device) else ""
            shafts = self.shafts.shop_view(device) if self.shafts is not None else {"shafts": [], "done": [], "order": []}
            parts = self.parts.shop_view(device) if self.parts is not None else {"parts": [], "bin": []}
            return _json(200, {**shafts, **parts, "shaftsOn": self.shafts is not None, "partsOn": self.parts is not None,
                               "orderSheet": self.cfg.parts.order_sheet_url if self.parts is not None else ""})
        action = path[len("/api/shop/"):]
        lst, verb = (self.shafts, action) if action in SHOP_ACTIONS else (self.parts, PART_ACTIONS.get(action))
        if method != "POST" or lst is None or verb is None:
            return _json(404, {"error": "Not found"})
        data = json.loads(body.decode("utf-8")) if body else {}
        device = str((data or {}).get("device") or "") if isinstance(data, dict) else ""
        if not DEVICE_RE.match(device):
            raise Refused(400, "Bad request")
        getattr(lst, verb)(data.get("id"), device)
        return _json(200, {"ok": True})

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

    def _cfg(self, q: Dict[str, Any]) -> str:
        """The Part Studio's configuration, from the Action URL's {$configuration} ("" the default; 2026-11-01)."""
        cfg = normal_configuration(str(q.get("c") or ""))
        if len(cfg) > 2000 or any(ord(ch) < 32 for ch in cfg):
            raise Refused(400, "Bad request")
        return cfg

    def _titled(self, did: str, wvm: str, wvmid: str, eid: str, name: str, cfg: str) -> str:
        """A configured part's card title: its name and its configuration ("P-2011 (Length: 19 in)")."""
        if not cfg:
            return name
        label = self.onshape.configuration_label(did, wvm, wvmid, eid, cfg)
        return f"{name} ({label})" if label else name

    def _part_id(self, q: Dict[str, str]) -> str:
        pid = str(q.get("p", ""))
        if not PART_RE.match(pid):
            raise Refused(400, "Pick a part first.")
        return pid

    def _materials(self) -> Dict[str, Dict[str, Any]]:
        """Picker choices by material key, each with the Onshape material it is: smoked is Onshape's polycarbonate
        plus the Smoked label; SRPP is Onshape's polycarbonate plus a Material: line ("line")."""
        cfg = self.cfg
        onshape_of = {key: name for name, key in cfg.onshape.material_map.items()}
        out: Dict[str, Dict[str, Any]] = {}
        for key, m in cfg.materials.items():
            name, line = onshape_of.get(key), False
            if name is None:
                same = [k for k, o in cfg.materials.items() if k in onshape_of and o.family == m.family]
                same.sort(key=lambda k: cfg.materials[k].name != m.name)     # smoked PC: clear PC's Onshape name
                name = onshape_of[same[0]] if same else None
                line = m.color != "smoked"
            if name is not None:
                out[key] = {"value": key, "label": material_label(m), "onshape": name, "family": m.family,
                            "smoked": m.color == "smoked", "line": line, "thicknesses": list(m.thicknesses_in)}
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
        """The parts a student sends: solids, not sheet metal flat patterns (they share the bent part's name)."""
        return [p for p in parts if p.get("bodyType") in (None, "solid") and p.get("partId")
                and not p.get("isFlattenedBody")]

    def _flat(self, did: str, wvm: str, wvmid: str, eid: str, pid: str, cfg: str = "") -> Optional[Dict[str, Any]]:
        """A sheet metal part's flat pattern (what's cut), from the parts list read with includeFlatParts."""
        from ..onshape.export import flat_of
        return flat_of(self.onshape.cached_parts(did, wvm, wvmid, eid, cfg) or [], pid)

    def _find(self, did: str, wvm: str, wvmid: str, eid: str, pid: str, cfg: str = "") -> Dict[str, Any]:
        part = next((p for p in self._solids(self.onshape.parts(did, wvm, wvmid, eid, cfg=cfg)) if p["partId"] == pid),
                    None)
        if part is None:
            raise Refused(404, "That part isn't in this Part Studio any more. Press Refresh.")
        return part

    def studio(self, q: Dict[str, str]) -> Dict[str, Any]:
        """cached=1 is the page's own refresh every few seconds: statuses from Trello, parts only from the last read
        (no Onshape call ever; {"stale": true} when there's none)."""
        did, wvm, wvmid, eid = self._studio(q)
        cfg = self._cfg(q)
        cached = q.get("cached") == "1"
        if cached:
            raw = self.onshape.cached_parts(did, wvm, wvmid, eid, cfg)
            if raw is None:
                return {"stale": True}
        else:
            raw = self.onshape.parts(did, wvm, wvmid, eid, fresh=q.get("fresh") == "1", cfg=cfg)
        parts = self._solids(raw)
        board_error, cards = "", None
        try:
            cards = self.board.part_cards(fresh=q.get("fresh") == "1")
        except Exception as e:  # noqa: BLE001 - parts still show; status says it couldn't be read
            log.warning("panel: reading the board: %s", e)
            board_error = "Couldn't read the Trello board just now, so statuses are missing."
        listed = []
        for p in sorted(parts, key=lambda p: _natural(p.get("name") or "")):
            chip = {"status": "unknown", "label": ""} if cards is None else \
                self.board.chip(self.board.for_part(did, eid, p["partId"], p.get("name") or "", cards, cfg=cfg))
            material = self._part_material(p)
            listed.append({"id": p["partId"], "name": p.get("name") or "", "material": material, "chip": chip,
                           "mark": self._mark(did, wvm, wvmid, eid, p, material, cfg),
                           "bent": self._flat(did, wvm, wvmid, eid, p["partId"], cfg) is not None})
        uid = q.get("u", "")
        return {"parts": listed, "boardError": board_error, "writable": wvm == "w", "queue": self._queue(),
                "configuration": self.onshape.configuration_label(did, wvm, wvmid, eid, cfg, cached_only=cached),
                "user": self.names.get(uid) if USER_RE.match(uid or "") else "",
                "materials": list(self._materials().values()), "fit": self.cfg.fit.enabled,
                "face": self.cfg.face is not None, "scribe": self.cfg.scribe is not None,
                "tabsDefault": self.cfg.tabs.default, "autoRefreshS": self.cfg.panel.auto_refresh_s,
                "build": static_hash("panel.js"),
                "callsLeft": self.onshape.calls_left(), "maxQty": MAX_QTY}

    def _queue(self) -> Optional[Dict[str, Any]]:
        try:
            return self.board.queue()
        except Exception as e:  # noqa: BLE001 - the queue line is a nicety
            log.warning("panel: reading the machine queue: %s", e)
            return None

    def _material_of(self, onshape_name: str) -> Optional[Dict[str, Any]]:
        return self._part_material({"material": {"displayName": onshape_name}}) if onshape_name else None

    def assembly(self, q: Dict[str, str]) -> Dict[str, Any]:
        """The assembly's parts from its BOM (one call): the ones AutoCAM cuts first, each with its count and card."""
        did, wvm, wvmid, eid = self._studio(q)
        cached = q.get("cached") == "1"                  # the page's own refresh: no Onshape call (see studio)
        rows = self.onshape.cached_bom(did, wvm, wvmid, eid) if cached else \
            self.onshape.bom(did, wvm, wvmid, eid, fresh=q.get("fresh") == "1")
        if rows is None:
            return {"stale": True}
        board_error, cards = "", None
        try:
            cards = self.board.part_cards(fresh=q.get("fresh") == "1")
        except Exception as e:  # noqa: BLE001
            log.warning("panel: reading the board: %s", e)
            board_error = "Couldn't read the Trello board just now, so statuses are missing."
        items, shaft_rows, bought_rows = [], [], []
        owners = self._owners(did, rows, cached_only=cached)
        for r in rows:
            if r["standard"]:
                continue                                 # Onshape's standard content: screws, nuts
            found = recognize(r, self.cfg.shafts) if self.shafts is not None else None
            if found is not None:                        # a library shaft (6061, but it's not a plate)
                shaft_rows.append((r, found))
                continue
            material = self._material_of(r["material"])
            not_ours = self._not_ours(did, r, owners)
            why = not_ours or _why_not_cut(r, material)
            if why == BOUGHT and self.parts is not None:
                if not is_hardware(r["name"]):           # screws and nuts aren't on the parts list (the user)
                    bought_rows.append(r)
                continue
            pc = None if cards is None else self.board.for_part(r["did"], r["eid"], r["pid"], r["name"], cards,
                                                                cfg=r["configuration"])
            chip = {"status": "unknown", "label": ""} if cards is None else self.board.chip(pc)
            shown = self._shown(r)
            items.append({"key": _row_key(r), "name": shown, "qty": r["qty"], "notOurs": bool(not_ours),
                          "material": material, "chip": chip, "why": why, "here": r["did"] == did,
                          "sendable": not why and cards is not None and (pc is None or pc.status not in ACTIVE)})
        # this document's parts first: parts from other documents are often bought ones (a vendor's library)
        items.sort(key=lambda i: (not i["here"], _natural(i["name"])))
        name = ""
        try:
            tabs = (self.onshape.cached_elements(did, wvm, wvmid) or []) if cached else self.onshape.elements(did, wvm, wvmid)
            name = next((e.get("name") or "" for e in tabs if e.get("id") == eid), "")
        except PanelOnshapeError:
            pass
        uid = q.get("u", "")
        shafts = self._assembly_shafts(did, eid, shaft_rows)
        bought = self._assembly_bought(did, eid, bought_rows)
        return {"name": name, "items": items, **shafts, **bought, "boardError": board_error, "queue": self._queue(), "face": self.cfg.face is not None,
                "user": self.names.get(uid) if USER_RE.match(uid or "") else "", "tabsDefault": self.cfg.tabs.default,
                "maxQty": MAX_QTY, "callsLeft": self.onshape.calls_left(), "autoRefreshS": self.cfg.panel.auto_refresh_s,
                "build": static_hash("panel.js")}

    def _shown(self, r: Dict[str, Any]) -> str:
        """A row's name in the panel, with its configuration if that was already read (no call)."""
        shown = r["name"] or r["pid"]
        if r["configuration"]:
            label = self.onshape.configuration_label(r["did"], r["wvm"], r["wvmid"], r["eid"], r["configuration"],
                                                     cached_only=True)
            shown = f"{shown} ({label})"
        return shown

    def _asm_ref(self, did: str, wvm: str, wvmid: str, eid: str, host: str) -> Dict[str, str]:
        """The assembly an item on a shop list came from: its key, tab name (already read) and link."""
        tabs = self.onshape.cached_elements(did, wvm, wvmid) or []
        return {"key": f"{did}/{eid}", "name": next((e.get("name") or "" for e in tabs if e.get("id") == eid), ""),
                "url": f"https://{host}/documents/{did}/{wvm}/{wvmid}/e/{eid}"}

    def _owners(self, did: str, rows: List[Dict[str, Any]], cached_only: bool) -> Dict[str, str]:
        """Who owns each other document the assembly's parts come from (only with [panel] company_id)."""
        if not self.cfg.panel.company_id:
            return {}
        lib = self.cfg.shafts.library_document
        others = [r["did"] for r in rows if r["did"] not in (did, lib) and not r["standard"]]
        try:
            return self.onshape.document_owners(others, cached_only=cached_only)
        except PanelOnshapeError:
            return {}

    def _not_ours(self, did: str, r: Dict[str, Any], owners: Dict[str, str]) -> str:
        """Why a part in the assembly isn't one AutoCAM cuts though its material might say so ("" it might be):
        the common parts library's parts that aren't shafts are printed or bought (the user: PLA-STNDF is a print,
        set to 6061), and a part from a document the team doesn't own is bought (a vendor's library)."""
        if r["did"] == did:
            return ""
        if self.cfg.shafts.library_document and r["did"] == self.cfg.shafts.library_document:
            return "Library part"
        owner = owners.get(r["did"], "")
        if self.cfg.panel.company_id and owner and owner != self.cfg.panel.company_id:
            return "Bought part"
        return ""

    def _assembly_shafts(self, did: str, eid: str, shaft_rows: List[Tuple[Dict[str, Any], Any]]) -> Dict[str, Any]:
        """The assembly's library shafts for the panel, each with what's on the list for it. Reading the assembly is
        also when a shaft on the list that's no longer in it (its length changed in CAD) gets "Changed in CAD"."""
        if self.shafts is None:
            return {}
        asm = f"{did}/{eid}"
        self.shafts.mark_changed(asm, [f"{asm}|{_row_key(r)}" for r, _ in shaft_rows])
        tap, out = self.cfg.shafts.tap, []
        for r, found in shaft_rows:
            x = self.shafts.latest(f"{asm}|{_row_key(r)}")
            label, kind = status_chip(x)
            listed = None if x is None else {"id": x["id"], "status": x["status"], "qty": x["qty"], "ends": x["ends"],
                                             "ops": x["ops"], "note": x.get("note") or "",
                                             "chip": {"label": label, "status": kind}}
            st = STOCKS[found.stock]
            out.append({"key": _row_key(r), "label": found.label, "stock": found.stock, "length": found.length_in,
                        "qty": max(1, r["qty"]), "ends": [tap, tap] if found.tapped else ["", ""],
                        "ops": [o.to_dict() for o in found.ops],
                        "problem": found.problem, "shape": st.shape, "size": st.size_in, "bore": st.bore_in,
                        "listed": listed, "sendable": not found.problem and (x is None or x["status"] not in SHAFT_ACTIVE)})
        out.sort(key=lambda i: (list(STOCKS).index(i["stock"]), i["length"]))
        gone = [{"id": x["id"], "label": Found(x["stock"], x["length_in"]).label, "waiting": x["status"] == "waiting"}
                for x in self.shafts.all() if x.get("assembly", {}).get("key") == asm and x.get("changed")
                and x.get("status") in SHAFT_ACTIVE]
        return {"shafts": out, "shaftsGone": gone, "shaftTap": tap, "grooveDepth": self.cfg.shafts.groove_depth_in}

    def _send_shafts(self, d: Dict[str, Any], did: str, wvm: str, wvmid: str, eid: str,
                     rows: Dict[str, Dict[str, Any]], sender: str, rush: bool, host: str) -> List[Dict[str, Any]]:
        """The ticked shafts onto the shop's list. "Send plates to Drafts" doesn't touch them (the user)."""
        wanted = d.get("shafts") if isinstance(d.get("shafts"), list) else []
        if not wanted:
            return []
        if self.shafts is None:
            raise Refused(400, "The shaft list is off.")
        ref = self._asm_ref(did, wvm, wvmid, eid, host)
        asm = ref["key"]
        results, seen = [], set()
        for item in wanted[:MAX_ASSEMBLY_SEND]:
            key = str((item or {}).get("key") or "")
            if key in seen:
                continue
            seen.add(key)
            r = rows.get(key)
            found = recognize(r, self.cfg.shafts) if r is not None else None
            label = found.label if found else key
            try:
                if found is None:
                    raise Refused(409, "isn't in this assembly any more")
                if found.problem:
                    raise Refused(400, found.problem)
                qty = self._qty(item)
                tap = self.cfg.shafts.tap if found.tapped else ""
                ends = clean_ends(item.get("ends"), tap)
                ops = found.ops + [o for o in clean_ops(item.get("ops")) if not o.auto]
                bad = problems(found.stock, found.length_in, ops)
                if bad:
                    raise Refused(400, bad[0])
                self.shafts.add([{"source": f"{asm}|{key}", "assembly": ref,
                                  "row": {"did": r["did"], "eid": r["eid"], "configuration": r["configuration"]},
                                  "stock": found.stock, "length_in": found.length_in, "qty": qty, "ends": ends,
                                  "ops": [o.to_dict() for o in ops], "rush": rush, "sender": sender,
                                  "note": clean_text(item.get("note"), MAX_NOTE)}])
                results.append({"key": key, "name": label, "sent": True, "shaft": True})
            except (Refused, ShaftError) as e:
                results.append({"key": key, "name": label, "sent": False, "why": str(e), "shaft": True})
        return results

    def _assembly_bought(self, did: str, eid: str, bought_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """The assembly's bought parts for the panel (no screws or nuts), each with what's on the parts list for it."""
        if self.parts is None:
            return {}
        asm = f"{did}/{eid}"
        self.parts.mark_changed(asm, [f"{asm}|{_row_key(r)}" for r in bought_rows])
        out = []
        for r in bought_rows:
            x = self.parts.latest(f"{asm}|{_row_key(r)}")
            label, kind = part_chip(x)
            listed = None if x is None else {"id": x["id"], "status": x["status"], "qty": x["qty"],
                                             "chip": {"label": label, "status": kind}}
            out.append({"key": _row_key(r), "name": self._shown(r), "qty": max(1, r["qty"]), "vendor": _vendor(r),
                        "listed": listed, "sendable": x is None or x["status"] not in PART_ACTIVE})
        out.sort(key=lambda i: _natural(i["name"]))
        gone = [{"id": x["id"], "name": x.get("name") or "", "waiting": x["status"] == "needed"}
                for x in self.parts.all() if x.get("assembly", {}).get("key") == asm and x.get("changed")
                and x.get("status") in PART_ACTIVE]
        return {"bought": out, "boughtGone": gone}

    def _send_bought(self, d: Dict[str, Any], did: str, wvm: str, wvmid: str, eid: str,
                     rows: Dict[str, Dict[str, Any]], owners: Dict[str, str], sender: str, host: str) -> List[Dict[str, Any]]:
        """The ticked bought parts onto the shop's parts list (never the order sheet: people write that themselves)."""
        wanted = d.get("bought") if isinstance(d.get("bought"), list) else []
        if not wanted:
            return []
        if self.parts is None:
            raise Refused(400, "The parts list is off.")
        ref = self._asm_ref(did, wvm, wvmid, eid, host)
        results, seen = [], set()
        for item in wanted[:MAX_ASSEMBLY_SEND]:
            key = str((item or {}).get("key") or "")
            if key in seen:
                continue
            seen.add(key)
            r = rows.get(key)
            label = self._shown(r) if r is not None else key
            try:
                if r is None:
                    raise Refused(409, "isn't in this assembly any more")
                why = self._not_ours(did, r, owners) or _why_not_cut(r, self._material_of(r["material"]))
                if r["standard"] or why != BOUGHT:
                    raise Refused(400, "isn't a bought part")
                if is_hardware(r["name"]):
                    raise Refused(400, "is a screw or nut: those aren't on the parts list")
                self.parts.add([{"source": f"{ref['key']}|{key}", "assembly": ref,
                                 "row": {"did": r["did"], "eid": r["eid"], "pid": r["pid"], "configuration": r["configuration"]},
                                 "name": label, "vendor": _vendor(r), "qty": self._qty(item), "sender": sender}])
                results.append({"key": key, "name": label, "sent": True, "bought": True})
            except (Refused, ShaftError) as e:
                results.append({"key": key, "name": label, "sent": False, "why": str(e), "bought": True})
        return results

    def remove_part(self, d: Dict[str, Any]) -> Dict[str, Any]:
        if self.parts is None:
            raise Refused(400, "The parts list is off.")
        self.parts.remove(d.get("id"))
        log.info("panel: bought part %s taken off the list", d.get("id"))
        return {"ok": True}

    def edit_shaft(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """A shaft on the list changed from the panel (its quantity, taps, lathe steps) while nobody has started it."""
        if self.shafts is None:
            raise Refused(400, "The shaft list is off.")
        sid = d.get("id")
        x = next((s for s in self.shafts.all() if s.get("id") == sid), None)
        if x is None:
            raise ShaftError("That shaft isn't on the list any more.", status=404)
        autos = [o for o in clean_ops(x.get("ops")) if o.auto]
        ops = autos + [o for o in clean_ops(d.get("ops")) if not o.auto]
        self.shafts.edit(sid, self._qty(d), clean_ends(d.get("ends"), self.cfg.shafts.tap), ops,
                         clean_text(d.get("note"), MAX_NOTE))
        log.info("panel: shaft %s changed (%s, %s in)", sid, x["stock"], x["length_in"])
        return {"ok": True}

    def remove_shaft(self, d: Dict[str, Any]) -> Dict[str, Any]:
        if self.shafts is None:
            raise Refused(400, "The shaft list is off.")
        self.shafts.remove(d.get("id"))
        log.info("panel: shaft %s taken off the list", d.get("id"))
        return {"ok": True}

    def send_many(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """Cards for the ticked parts of an assembly, each in the format a typed card has (its own Part Studio's
        link, with its configuration). Skipped, with the reason: no material AutoCAM cuts, parts that already have a
        card."""
        self._rate_limit()
        did, wvm, wvmid, eid = self._studio(d)
        sender = self._sender(d)
        nest, tabs = bool(d.get("nest", True)), bool(d.get("tabs", self.cfg.tabs.default))
        drafts, rush = bool(d.get("drafts")), bool(d.get("rush"))
        face = bool(d.get("face")) and self.cfg.face is not None
        wanted = d.get("items") if isinstance(d.get("items"), list) else []
        rows = {_row_key(r): r for r in self.onshape.bom(did, wvm, wvmid, eid)}
        owners = self._owners(did, list(rows.values()), cached_only=True)
        cards = self.board.part_cards(fresh=True)
        host = self._host(d.get("server"))
        results, seen = [], set()
        for item in wanted[:MAX_ASSEMBLY_SEND]:
            key = str((item or {}).get("key") or "")
            if key in seen:
                continue
            seen.add(key)
            r = rows.get(key)
            name = r["name"] if r else key
            try:
                if r is None:
                    raise Refused(409, "isn't in this assembly any more")
                if self.shafts is not None and recognize(r, self.cfg.shafts) is not None:
                    raise Refused(400, "is a shaft: it goes on the shaft list")
                not_ours = self._not_ours(did, r, owners)
                if not_ours:
                    raise Refused(400, "is a bought part" if not_ours == BOUGHT else
                                  "is in the common parts library (printed or bought)")
                qty = self._qty(item)
                material = self._material_of(r["material"])
                if material is None or not material["key"]:
                    raise Refused(400, "has no material AutoCAM cuts: open its Part Studio's panel")
                going = self.board.for_part(r["did"], r["eid"], r["pid"], r["name"], cards, cfg=r["configuration"])
                if going is not None and going.status in ACTIVE:
                    raise Refused(409, "already has a card")
                title = self._titled(r["did"], r["wvm"], r["wvmid"], r["eid"], r["name"], r["configuration"])
                s = Send(did=r["did"], wvm=r["wvm"], wvmid=r["wvmid"], eid=r["eid"], pid=r["pid"], name=title,
                         host=host, qty=qty, nest=nest, tabs=tabs, face=face, rush=rush, drafts=drafts, sender=sender,
                         configuration=r["configuration"])
                card = self.board.write(s)
                results.append({"key": key, "name": name, "sent": True, "url": card.url})
            except Refused as e:
                results.append({"key": key, "name": name, "sent": False, "why": str(e)})
        sent = sum(1 for x in results if x["sent"])
        log.info("panel: %s sent %d of %d parts of an assembly to %s", sender, sent, len(results),
                 "Drafts" if drafts else "Ready for CAM")
        shafts = self._send_shafts(d, did, wvm, wvmid, eid, rows, sender, rush, host)
        if shafts:
            log.info("panel: %s sent %d of %d shafts to the shaft list", sender, sum(1 for x in shafts if x["sent"]),
                     len(shafts))
        bought = self._send_bought(d, did, wvm, wvmid, eid, rows, owners, sender, host)
        if bought:
            log.info("panel: %s sent %d of %d bought parts to the parts list", sender,
                     sum(1 for x in bought if x["sent"]), len(bought))
        return {"results": results + shafts + bought, "list": "Drafts" if drafts else "Ready for CAM"}

    def _mark(self, did: str, wvm: str, wvmid: str, eid: str, part: Dict[str, Any],
              material: Optional[Dict[str, Any]], cfg: str = "") -> Optional[str]:
        """"ok" or "bad" for a part already checked in this state of the Part Studio (no Onshape call); None
        otherwise."""
        flat = self._flat(did, wvm, wvmid, eid, part["partId"], cfg)
        found = self.onshape.cached_geometry(did, wvm, wvmid, eid, (flat or part)["partId"],
                                             str(part.get("microversionId") or ""), cfg)
        if found is None:
            return None
        try:
            result = checks.check(found[0], self.cfg, material["key"] if material and material["key"] else None, found[1])
        except Exception:  # noqa: BLE001 - a mark is a nicety; the part's own check says what's wrong
            log.exception("panel: checking %s for the list", part.get("name"))
            return None
        return "ok" if result["ok"] else "bad"

    def part(self, q: Dict[str, str]) -> Dict[str, Any]:
        did, wvm, wvmid, eid = self._studio(q)
        pid = self._part_id(q)
        cfg = self._cfg(q)
        if q.get("cached") == "1":                       # the page's own refresh: no Onshape call (see studio)
            part = next((p for p in self._solids(self.onshape.cached_parts(did, wvm, wvmid, eid, cfg) or [])
                         if p["partId"] == pid), None)
            if part is None:
                return {"stale": True}
        else:
            part = self._find(did, wvm, wvmid, eid, pid, cfg)
        pc = self.board.for_part(did, eid, pid, part.get("name") or "", self.board.part_cards(), cfg=cfg)
        return {"part": {"id": pid, "name": part.get("name") or "", "material": self._part_material(part),
                         "bent": self._flat(did, wvm, wvmid, eid, pid, cfg) is not None},
                "card": self.board.detail(pc) if pc is not None else None}

    def qty(self, q: Dict[str, str]) -> Dict[str, Any]:
        did, wvm, wvmid, eid = self._studio(q)
        pid = self._part_id(q)
        try:
            return self.onshape.assembly_counts(did, wvm, wvmid, eid, pid, fresh=q.get("fresh") == "1",
                                                cfg=self._cfg(q))
        except OutOfCalls as e:
            return {"assemblies": [], "counted": 0, "total": 0, "note": str(e)}

    def check(self, q: Dict[str, str]) -> Dict[str, Any]:
        """The design checks. Out of calls, or Onshape not answering, isn't an error for the page: it just says the
        check waits."""
        did, wvm, wvmid, eid = self._studio(q)
        pid = self._part_id(q)
        cfg = self._cfg(q)
        part = self._find(did, wvm, wvmid, eid, pid, cfg)
        have = self._part_material(part)
        key = q.get("m") or (have["key"] if have and have["key"] else None)
        if key is not None and key not in self.cfg.materials:
            raise Refused(400, "Bad request")
        flat = self._flat(did, wvm, wvmid, eid, pid, cfg)  # sheet metal: its flat pattern is what's cut
        state = str(part.get("microversionId") or "")
        bends = None
        try:
            geom, extra = self.onshape.geometry(did, wvm, wvmid, eid, (flat or part)["partId"], state,
                                                part.get("name") or pid, fresh=q.get("fresh") == "1", cfg=cfg)
            if flat is not None:
                bends = self.onshape.bends(did, wvm, wvmid, eid, pid, state, cfg)
        except OutOfCalls as e:
            return {"unavailable": str(e)}
        except NoAccess:
            raise
        except PanelOnshapeError as e:
            if flat is None or "bends" not in str(e):
                return {"unavailable": str(e)}
            bends = []
        out = checks.check(geom, self.cfg, key, extra)
        if flat is not None:
            out["bent"] = checks.bends_line(bends or [], self.cfg.materials.get(key) if key else None)
        return out

    def changed(self, q: Dict[str, str]) -> Dict[str, Any]:
        """{"changed": True/False/None, "what": [...], "run"}: None when there's nothing to compare with (not CAM'd
        from Onshape yet). Costs at most two calls the first time (the CAM'd state is kept for good)."""
        did, wvm, wvmid, eid = self._studio(q)
        pid = self._part_id(q)
        cfg = self._cfg(q)
        part = self._find(did, wvm, wvmid, eid, pid, cfg)
        name = part.get("name") or pid
        pc = self.board.for_part(did, eid, pid, name, self.board.part_cards(), card_id=str(q.get("card") or ""),
                                 cfg=cfg)
        ref = self.board.cammed(pc.card.id) if pc is not None else None
        if ref is None:
            return {"changed": None}
        if ref.get("microversion"):
            kind, ident = "m", ref["microversion"]
        elif "/v/" in (ref.get("url") or ""):
            kind, ident = "v", ref.get("vid") or ""
        else:
            return {"changed": None}
        try:
            then, then_extra = self.onshape.geometry(ref["did"], kind, ident, ref["eid"], ref.get("part_id") or pid,
                                                     ident, name, cfg=ref_configuration(ref))
            now, now_extra = self.onshape.geometry(did, wvm, wvmid, eid, pid, str(part.get("microversionId") or ""),
                                                   name, cfg=cfg)
        except (OutOfCalls, PanelOnshapeError) as e:
            return {"changed": None, "unavailable": str(e)}
        what = differences(then, now, float(then_extra.get("outline_area_in2") or 0),
                           float(now_extra.get("outline_area_in2") or 0))
        if what and pc.status == "sheet":
            try:
                self.board.flag_changed(pc, name, what, shape_key(now))
            except Exception as e:  # noqa: BLE001 - the panel still says it
                log.warning("panel: telling %s's sheets it changed: %s", name, e)
        return {"changed": bool(what), "what": what, "run": ref.get("run") or ""}

    def renest(self, d: Dict[str, Any]) -> Dict[str, Any]:
        self._rate_limit()
        did, wvm, wvmid, eid = self._studio(d)
        pid = self._part_id(d)
        cfg = self._cfg(d)
        part = self._find(did, wvm, wvmid, eid, pid, cfg)
        pc = self.board.for_part(did, eid, pid, part.get("name") or "", self.board.part_cards(fresh=True),
                                 card_id=str(d.get("card") or ""), cfg=cfg)
        if pc is None or pc.status != "sheet":
            raise Refused(409, "That card has moved on since this panel looked. Press Refresh.")
        card = self.board.renest(pc)
        log.info("panel: %s sent %s back to Ready for CAM (changed since it was CAM'd): %s",
                 clean_text(d.get("name"), 40) or "someone", pc.card.name, card.url)
        fresh = read_part_card(card, "queued")
        return {"card": self.board.detail(fresh) if fresh is not None else None}

    def hook(self, method: str, path: str, body: bytes) -> Response:
        """An event from Onshape's webhook (hooks.py). Anything that isn't one is a plain 404."""
        if method != "POST" or self.inbox is None or not secret_ok(path, self._hook_secret) or len(body) > 65536:
            return _json(404, {"error": "Not found"})
        try:
            event = json.loads(body.decode("utf-8")) if body else {}
        except ValueError:
            return _json(400, {"error": "Bad request"})
        if isinstance(event, dict) and self.inbox.note(event):
            log.debug("panel: Onshape says %s/%s changed", event.get("documentId"), event.get("elementId"))
        return _json(200, {"ok": True})

    def accept_change(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """Cut it as it was CAM'd: the same as ticking the CAD changed box on its card."""
        self._rate_limit()
        did, wvm, wvmid, eid = self._studio(d)
        pid = self._part_id(d)
        cfg = self._cfg(d)
        cards = self.board.part_cards(fresh=True)
        pc = self.board.for_part(did, eid, pid, "", cards, card_id=str(d.get("card") or ""), cfg=cfg)   # by Part ID
        if pc is None:                                   # a card made in Trello: by the part's name (one call)
            part = self._find(did, wvm, wvmid, eid, pid, cfg)
            pc = self.board.for_part(did, eid, pid, part.get("name") or "", cards, card_id=str(d.get("card") or ""),
                                     cfg=cfg)
        if pc is None or pc.status != "sheet" or self.board.open_change(pc.card.id) is None:
            raise Refused(409, "That card has moved on since this panel looked. Press Refresh.")
        by = clean_text(d.get("name"), 40)
        self.board.accept_change(pc, by)
        log.info("panel: %s: cut %s as it was CAM'd: %s", by or "someone", pc.card.name, pc.card.url)
        fresh = read_part_card(self.board.t.get_card(pc.card.id), "sheet")
        return {"card": self.board.detail(fresh) if fresh is not None else None}

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

    def which_part(self, q: Dict[str, str]) -> Dict[str, Any]:
        """A face, edge or corner clicked in the CAD -> the part it's on. Onshape's SELECTION names only the entity's id
        ("selectionType": "ENTITY", "entityType": "FACE", seen 2026-11-01; a whole part is "BODY" with its part id).
        Answered from the shapes AutoCAM already has for this state of the Part Studio (parts opened in the panel),
        never with an Onshape call: an unknown one is {"part": ""} and the page says to pick the part instead."""
        did, wvm, wvmid, eid = self._studio(q)
        wanted = {i for i in str(q.get("ids") or "").split(",")[:20] if ENTITY_RE.match(i)}
        if not wanted:
            return {"part": ""}
        cfg = self._cfg(q)
        for part in self._solids(self.onshape.cached_parts(did, wvm, wvmid, eid, cfg) or []):
            flat = self._flat(did, wvm, wvmid, eid, part["partId"], cfg)
            found = self.onshape.cached_geometry(did, wvm, wvmid, eid, (flat or part)["partId"],
                                                 str(part.get("microversionId") or ""), cfg)
            if found is None:
                continue
            extra = found[1]
            known = set(extra.get("face_ids") or ()) | set(extra.get("vertex_ids") or ())
            for edges in (extra.get("face_edges") or {}).values():
                known.update(edges)
            if wanted & known:
                return {"part": part["partId"]}
        return {"part": ""}

    def which_row(self, q: Dict[str, str]) -> Dict[str, Any]:
        """A part clicked in an assembly -> its row in the panel's list ({"key": ""}: not found). Onshape's SELECTION
        gives the occurrence path; its last id is the part's instance (2026-11-02). The assembly's instances are one
        call kept assembly_cache_s, read again once if the instance is new (a part just added)."""
        did, wvm, wvmid, eid = self._studio(q)
        inst_id = str(q.get("inst") or "")
        rows = self.onshape.cached_bom(did, wvm, wvmid, eid)
        if not INSTANCE_RE.match(inst_id) or rows is None:
            return {"key": ""}
        inst = self.onshape.assembly_instances(did, wvm, wvmid, eid).get(inst_id)
        if inst is None:
            inst = self.onshape.assembly_instances(did, wvm, wvmid, eid, fresh=True).get(inst_id)
        row = row_for_instance(inst, rows) if inst else None
        return {"key": _row_key(row) if row else ""}

    def occurrences(self, q: Dict[str, str]) -> Dict[str, Any]:
        """A row of the assembly's list -> {"paths": [occurrence paths], "pid": its part id}: every copy of that part
        in the assembly, for Onshape's requestSelectionHighlight (documented only for Part Studios; tried 2026-11-02).
        Same assembly read as which-row (one call, kept 30 min)."""
        did, wvm, wvmid, eid = self._studio(q)
        key = str(q.get("key") or "")
        rows = self.onshape.cached_bom(did, wvm, wvmid, eid) or []
        row = next((r for r in rows if _row_key(r) == key), None)
        if row is None:
            return {"paths": [], "pid": ""}
        instances, paths = self.onshape.assembly_occurrences(did, wvm, wvmid, eid)
        found = [p for iid, inst in instances.items() if row_for_instance(inst, rows) is row for p in paths.get(iid, [])]
        return {"paths": found[:60], "pid": row["pid"]}

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
        scribe = str(d.get("scribe") or "")
        if scribe not in ("", "endmill", "chamfer"):
            raise Refused(400, "Bad request")
        cfg = self._cfg(d)
        part = self._find(did, wvm, wvmid, eid, pid, cfg)
        name = part.get("name") or ""

        cards = self.board.part_cards(fresh=True)
        existing = None
        if d.get("card"):
            existing = self.board.for_part(did, eid, pid, name, cards, card_id=str(d["card"]), cfg=cfg)
            if existing is None or existing.status not in ("back", "drafts"):
                raise Refused(409, "That card has moved on since this panel looked. Press Refresh.")
        else:
            going = self.board.for_part(did, eid, pid, name, cards, cfg=cfg)
            if going is not None and going.status in ACTIVE:
                raise Refused(409, "This part already has a card. Change its quantity instead of sending it again.",
                              kind="duplicate", card=self.board.detail(going))

        materials = self._materials()
        have = self._part_material(part)
        smoked = bool(d.get("smoked"))
        line, set_in_onshape, why, missed = "", False, "", False
        if have and have["key"]:
            if have["family"] != "polycarbonate":
                smoked = False
            sheet = materials.get(str(d.get("sheet") or ""))       # which sheet, for Onshape's polycarbonate
            if sheet is not None and sheet["onshape"].lower() == have["onshape"].lower():
                smoked = sheet["smoked"]
                if sheet["line"]:
                    line = material_line(self.cfg.materials[sheet["value"]])
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
            if not set_in_onshape or chosen["line"]:      # SRPP: Onshape's Polycarbonate can't say which
                line = material_line(self.cfg.materials[chosen["value"]])
            if not set_in_onshape:
                missed = True
                log.info("panel: %s's material not set in Onshape (%s); the card says Material: %s", name, why, line)

        face = bool(d.get("face")) and self.cfg.face is not None
        if scribe and (self.cfg.scribe is None or self._flat(did, wvm, wvmid, eid, pid, cfg) is None):
            scribe = ""                                  # only a bent part has bend lines
        title = self._titled(did, wvm, wvmid, eid, name, cfg)
        s = Send(did=did, wvm=wvm, wvmid=wvmid, eid=eid, pid=pid, name=title, host=self._host(d.get("server")),
                 qty=qty, nest=nest, tabs=tabs, face=face, rush=bool(d.get("rush")), smoked=smoked,
                 tool_eighth=bool(d.get("tool_eighth")), drafts=bool(d.get("drafts")), material_line=line,
                 fit_line=fit_line, scribe=scribe, sender=sender, notes=clean_text(d.get("notes"), 300),
                 configuration=cfg)
        card = self.board.write(s, existing)
        where = "inbox" if s.drafts else "ready_for_cam"
        log.info("panel: %s sent %s x%d to %s: %s", sender, name, qty, LIST_NAMES[where], card.url)
        return {"card": self._detail(card, where), "list": LIST_NAMES[where],
                "material": {"setInOnshape": set_in_onshape, "why": why, "line": line, "missed": missed}}

    def send_program(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """Someone CAM'd the part themselves: their .tap goes on a card linked to the part (Material, Thickness and
        an "Own program:" line), and AutoCAM picks it a spot (own_programs.py). The guard checks it here first for
        what doesn't depend on where it goes (codes, units, nothing below Z0, the park); the placed copy gets the full
        check (clamps, reach) before it's ever on a sheet card. Never into Ready to cut, like every card."""
        self._rate_limit()
        did, wvm, wvmid, eid = self._studio(d)
        pid = self._part_id(d)
        sender = self._sender(d)
        configuration = self._cfg(d)
        part = self._find(did, wvm, wvmid, eid, pid, configuration)
        name = self._titled(did, wvm, wvmid, eid, part.get("name") or pid, configuration)
        prog = d.get("program") if isinstance(d.get("program"), dict) else {}
        filename = re.sub(r"[^A-Za-z0-9_. -]+", "_", Path(str(prog.get("filename") or "")).name).strip() or "program.tap"
        if not filename.lower().endswith(PROGRAM_SUFFIXES):
            raise Refused(400, "Pick your program: a .tap (or .nc) file.")
        data = str(prog.get("text") or "").encode("utf-8")
        if not data.strip():
            raise Refused(400, "That program is empty.")
        if len(data) > self.cfg.trello.attachment_limit_mb * 1024 * 1024:
            raise Refused(400, f"That program is bigger than Trello takes ({self.cfg.trello.attachment_limit_mb:g} MB).")

        have = self._part_material(part)
        key = have["key"] if have and have["key"] else str(d.get("material") or "")
        if key not in self.cfg.materials:
            raise Refused(400, "Pick the part's material.")
        m = self.cfg.materials[key]
        try:
            thickness = float(d.get("thickness"))
        except (TypeError, ValueError):
            raise Refused(400, "Pick the plate's thickness.") from None
        stock = next((s for s in m.thicknesses_in if abs(s - thickness) < 0.0005), None)
        if stock is None:
            raise Refused(400, f"{thickness:g} in isn't a stock thickness of {material_label(m)}.")
        cutter_said = clean_text(d.get("cutter"), 20)
        cutter_in, why = parse_cutter(f"Cutter: {cutter_said}") if cutter_said else (None, [])
        if why:
            raise Refused(400, why[0])

        cfg = self.cfg
        spec = GuardSpec(z_floor_in=cfg.machine.z_floor_in, allowed_g=frozenset(cfg.tapguard.allowed_g),
                         allowed_m=frozenset(cfg.tapguard.allowed_m), park=cfg.machine.park)
        report = check_program(data, spec)
        if not report.passed:
            raise Refused(400, "AutoCAM's safety check stopped your program: " + report.summary()[:400])
        box, problems, _ = footprint(data, stock, cutter_in or cfg.hand_cuts.cutter_in)
        if problems or box is None:
            raise Refused(400, (problems or ["AutoCAM couldn't tell where your program cuts."])[0])

        cards = self.board.part_cards(fresh=True)
        going = self.board.for_part(did, eid, pid, part.get("name") or pid, cards, cfg=configuration)
        if going is not None and going.status in ACTIVE:
            raise Refused(409, "This part already has a card.", kind="duplicate", card=self.board.detail(going))
        from .checks import inches                       # 3/16 in for the Thickness: line
        from ..onshape.urls import configuration_query
        lines = [f"https://{self._host(d.get('server'))}/documents/{did}/{wvm}/{wvmid}/e/{eid}"
                 f"{configuration_query(configuration)}", f"Part ID: {pid}",
                 f"Material: {material_line(m)}", f"Thickness: {inches(stock).replace(' in', '')}"]
        if cutter_in:
            lines.append(f"Cutter: {cutter_said}")
        if d.get("place_myself"):
            lines += ["Own program: the attached program, CAM'd by hand",
                      "Place it myself: the operator zeros it on a piece they pick (AutoCAM checks it as it is)"]
        else:
            lines.append("Own program: the attached program, CAM'd by hand (AutoCAM places it and checks the placed copy)")
        lines.append(f"Sent from the Onshape panel by {sender}")
        notes = clean_text(d.get("notes"), 300)
        if notes:
            lines.append(f"Note: {notes}")
        t = self.board.t
        card = t.create_card("inbox", name, "\n".join(lines))       # in Drafts until the program is on it
        t.attach_program(card.id, filename, data, report)
        t.move(card.id, "ready_for_cam")
        self.board.invalidate()
        log.info("panel: %s sent their own program for %s (%s): %s", sender, name, filename, card.url)
        return {"card": self._detail(t.get_card(card.id), "ready_for_cam"), "list": "Ready for CAM", "own": True,
                "material": {"setInOnshape": False, "why": "", "line": ""}}

    def _detail(self, card: Card, list_key: str) -> Dict[str, Any]:
        pc = read_part_card(card, {"inbox": "drafts", "ready_for_cam": "queued"}.get(list_key, "queued"))
        return self.board.detail(pc) if pc is not None else {"url": card.url, "card": card.id}

    def change_qty(self, d: Dict[str, Any]) -> Dict[str, Any]:
        self._rate_limit()
        did, wvm, wvmid, eid = self._studio(d)
        pid = self._part_id(d)
        qty = self._qty(d)
        cfg = self._cfg(d)
        part = self._find(did, wvm, wvmid, eid, pid, cfg)
        pc = self.board.for_part(did, eid, pid, part.get("name") or "", self.board.part_cards(fresh=True),
                                 card_id=str(d.get("card") or ""), cfg=cfg)
        if pc is None or pc.status not in ("drafts", "queued", "sheet"):
            raise Refused(409, "That card has moved on since this panel looked. Press Refresh.")
        card = self.board.change_qty(pc, qty)
        log.info("panel: %s changed %s to x%d: %s", clean_text(d.get("name"), 40) or "someone", pc.card.name, qty,
                 card.url)
        status = "queued" if pc.status == "sheet" else pc.status
        fresh = read_part_card(card, status)
        return {"card": self.board.detail(fresh) if fresh is not None else None, "renest": pc.status == "sheet"}


def _why_not_cut(r: Dict[str, Any], material: Optional[Dict[str, Any]]) -> str:
    """Why an assembly part isn't sendable ("" it is), past bought and library parts by document (the user,
    2026-11-02): rubber (the Shooter's Viton and Neoprene belts) and McMaster numbers are "Bought part", prints are
    "Printed part", any other material AutoCAM doesn't cut (brass, steel, 7075) is "AutoCAM doesn't cut". A part
    with no material at all stays "No material": it may be a plate nobody set one on."""
    if MCMASTER_RE.match(r.get("name") or ""):
        return "Bought part"
    if material is None:
        return "No material"
    if material["key"]:
        return ""
    name = material.get("onshape") or ""
    if PRINTED_RE.search(name):
        return "Printed part"
    return "Bought part" if RUBBER_RE.search(name) else "AutoCAM doesn't cut"


def _vendor(r: Dict[str, Any]) -> str:
    """Who sells a bought part: the BOM's Vendor column if the part has one, McMaster from its number, else ""."""
    v = str(r.get("vendor") or "").strip()
    if v:
        return v[:40]
    return "McMaster" if MCMASTER_RE.match(r.get("name") or "") else ""


def _row_key(r: Dict[str, Any]) -> str:
    """An assembly row's key: the part, and its configuration (each configuration is its own part)."""
    return f"{r['did']}/{r['eid']}/{r['pid']}" + configuration_tag(r.get("configuration") or "")


def ref_configuration(ref: Dict[str, Any]) -> str:
    """The configuration a job CAM'd (it's in the card link the job kept)."""
    return configuration_of(str(ref.get("url") or ""))


def _natural(name: str):
    """P-2 before P-10."""
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", name)]
