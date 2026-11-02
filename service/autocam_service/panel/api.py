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
from .onshape_reads import NoAccess, OutOfCalls, PanelOnshape, PanelOnshapeError

log = logging.getLogger("autocam.panel")

STATIC = Path(__file__).with_name("static")
STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".svg": "image/svg+xml"}
ID_RE = re.compile(r"^[0-9a-f]{24}$")
PART_RE = re.compile(r"^[A-Za-z0-9_.+/=-]{1,64}$")
USER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ENTITY_RE = re.compile(r"^[A-Za-z0-9]{1,24}$")       # a face or edge id from Onshape's SELECTION
MAX_BODY = 16 * 1024
MAX_PROGRAM_BODY = 4 * 1024 * 1024        # /api/send-program carries the .tap
PROGRAM_PATH = "/api/send-program"
SELECTION_LOGS_PER_DAY = 30
MAX_ASSEMBLY_SEND = 60
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors https://*.onshape.com https://onshape.com; base-uri 'none'; form-action 'none'")
LIST_NAMES = {"inbox": "Drafts", "ready_for_cam": "Ready for CAM"}


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


def versioned_page() -> bytes:
    """index.html with each /static/ link stamped with its file's hash. Cloudflare tells browsers to keep static files
    for 4 hours whatever this server says (seen 2026-11-01: an old panel.js kept running after a deploy); the page
    itself is no-store, so a changed file gets a new address and is fetched at once."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    def stamp(m: "re.Match[str]") -> str:
        f = STATIC / m.group(1)
        digest = hashlib.sha256(f.read_bytes()).hexdigest()[:12] if f.is_file() else "0"
        return f'"/static/{m.group(1)}?v={digest}"'

    return re.sub(r'"/static/([A-Za-z0-9_.-]+)"', stamp, html).encode("utf-8")


class PanelApp:
    def __init__(self, cfg: Config, key: str, onshape: PanelOnshape, board: PanelBoard, names: Names,
                 clock: Callable[[], float] = time.monotonic,
                 today: Callable[[], str] = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                 inbox: Optional[ChangeInbox] = None, hook_secret: str = ""):
        if not key:
            raise ValueError("the panel needs AUTOCAM_PANEL_KEY")
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
            if path.startswith("/static/") and method == "GET":
                return self._static(path[len("/static/"):])
            if not path.startswith("/api/"):
                return Response(404, b"Not found", "text/plain; charset=utf-8")
            if path.startswith(HOOK_PREFIX):                 # Onshape's webhook: its address is its password
                return self.hook(method, path, body)
            given = query.get("key", "") if path == "/api/preview" else headers.get("x-autocam-key", "")
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
                     ("GET", "/api/which-part"): self.which_part}.get((method, path))
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
        return Response(200, versioned_page(), "text/html; charset=utf-8", headers)

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
                            "smoked": m.color == "smoked", "thicknesses": list(m.thicknesses_in)}
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

    def _flat(self, did: str, wvm: str, wvmid: str, eid: str, pid: str) -> Optional[Dict[str, Any]]:
        """A sheet metal part's flat pattern (what's cut), from the parts list read with includeFlatParts."""
        from ..onshape.export import flat_of
        return flat_of(self.onshape.cached_parts(did, wvm, wvmid, eid) or [], pid)

    def _find(self, did: str, wvm: str, wvmid: str, eid: str, pid: str) -> Dict[str, Any]:
        part = next((p for p in self._solids(self.onshape.parts(did, wvm, wvmid, eid)) if p["partId"] == pid), None)
        if part is None:
            raise Refused(404, "That part isn't in this Part Studio any more. Press Refresh.")
        return part

    def studio(self, q: Dict[str, str]) -> Dict[str, Any]:
        """cached=1 is the page's own refresh every few seconds: statuses from Trello, parts only from the last read
        (no Onshape call ever; {"stale": true} when there's none)."""
        did, wvm, wvmid, eid = self._studio(q)
        if q.get("cached") == "1":
            raw = self.onshape.cached_parts(did, wvm, wvmid, eid)
            if raw is None:
                return {"stale": True}
        else:
            raw = self.onshape.parts(did, wvm, wvmid, eid, fresh=q.get("fresh") == "1")
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
                self.board.chip(self.board.for_part(did, eid, p["partId"], p.get("name") or "", cards))
            material = self._part_material(p)
            listed.append({"id": p["partId"], "name": p.get("name") or "", "material": material, "chip": chip,
                           "mark": self._mark(did, wvm, wvmid, eid, p, material),
                           "bent": self._flat(did, wvm, wvmid, eid, p["partId"]) is not None})
        uid = q.get("u", "")
        return {"parts": listed, "boardError": board_error, "writable": wvm == "w", "queue": self._queue(),
                "user": self.names.get(uid) if USER_RE.match(uid or "") else "",
                "materials": list(self._materials().values()), "fit": self.cfg.fit.enabled,
                "face": self.cfg.face is not None, "scribe": self.cfg.scribe is not None,
                "tabsDefault": self.cfg.tabs.default, "autoRefreshS": self.cfg.panel.auto_refresh_s,
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
        items = []
        for r in rows:
            if r["standard"]:
                continue                                 # Onshape's standard content: screws, nuts
            material = self._material_of(r["material"])
            pc = None if cards is None else self.board.for_part(r["did"], r["eid"], r["pid"], r["name"], cards)
            chip = {"status": "unknown", "label": ""} if cards is None else self.board.chip(pc)
            why = ("No material" if material is None else "" if material["key"] else "Not cut here") or \
                ("Configured" if r["configured"] else "")
            items.append({"key": f"{r['did']}/{r['eid']}/{r['pid']}", "name": r["name"] or r["pid"], "qty": r["qty"],
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
        return {"name": name, "items": items, "boardError": board_error, "queue": self._queue(), "face": self.cfg.face is not None,
                "user": self.names.get(uid) if USER_RE.match(uid or "") else "", "tabsDefault": self.cfg.tabs.default,
                "maxQty": MAX_QTY, "callsLeft": self.onshape.calls_left(), "autoRefreshS": self.cfg.panel.auto_refresh_s}

    def send_many(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """Cards for the ticked parts of an assembly, each in the format a typed card has (its own Part Studio's
        link). Skipped, with the reason: no material AutoCAM cuts, configured parts, parts that already have a card."""
        self._rate_limit()
        did, wvm, wvmid, eid = self._studio(d)
        sender = self._sender(d)
        nest, tabs = bool(d.get("nest", True)), bool(d.get("tabs", self.cfg.tabs.default))
        drafts, rush = bool(d.get("drafts")), bool(d.get("rush"))
        face = bool(d.get("face")) and self.cfg.face is not None
        wanted = d.get("items") if isinstance(d.get("items"), list) else []
        rows = {f"{r['did']}/{r['eid']}/{r['pid']}": r for r in self.onshape.bom(did, wvm, wvmid, eid)}
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
                qty = self._qty(item)
                material = self._material_of(r["material"])
                if material is None or not material["key"]:
                    raise Refused(400, "has no material AutoCAM cuts: open its Part Studio's panel")
                if r["configured"]:
                    raise Refused(400, "is a configured part: send it from its Part Studio")
                going = self.board.for_part(r["did"], r["eid"], r["pid"], r["name"], cards)
                if going is not None and going.status in ACTIVE:
                    raise Refused(409, "already has a card")
                s = Send(did=r["did"], wvm=r["wvm"], wvmid=r["wvmid"], eid=r["eid"], pid=r["pid"], name=r["name"],
                         host=host, qty=qty, nest=nest, tabs=tabs, face=face, rush=rush, drafts=drafts, sender=sender)
                card = self.board.write(s)
                results.append({"key": key, "name": name, "sent": True, "url": card.url})
            except Refused as e:
                results.append({"key": key, "name": name, "sent": False, "why": str(e)})
        sent = sum(1 for x in results if x["sent"])
        log.info("panel: %s sent %d of %d parts of an assembly to %s", sender, sent, len(results),
                 "Drafts" if drafts else "Ready for CAM")
        return {"results": results, "list": "Drafts" if drafts else "Ready for CAM"}

    def _mark(self, did: str, wvm: str, wvmid: str, eid: str, part: Dict[str, Any],
              material: Optional[Dict[str, Any]]) -> Optional[str]:
        """"ok" or "bad" for a part already checked in this state of the Part Studio (no Onshape call); None
        otherwise."""
        flat = self._flat(did, wvm, wvmid, eid, part["partId"])
        found = self.onshape.cached_geometry(did, wvm, wvmid, eid, (flat or part)["partId"],
                                             str(part.get("microversionId") or ""))
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
        if q.get("cached") == "1":                       # the page's own refresh: no Onshape call (see studio)
            part = next((p for p in self._solids(self.onshape.cached_parts(did, wvm, wvmid, eid) or [])
                         if p["partId"] == pid), None)
            if part is None:
                return {"stale": True}
        else:
            part = self._find(did, wvm, wvmid, eid, pid)
        pc = self.board.for_part(did, eid, pid, part.get("name") or "", self.board.part_cards())
        return {"part": {"id": pid, "name": part.get("name") or "", "material": self._part_material(part),
                         "bent": self._flat(did, wvm, wvmid, eid, pid) is not None},
                "card": self.board.detail(pc) if pc is not None else None}

    def qty(self, q: Dict[str, str]) -> Dict[str, Any]:
        did, wvm, wvmid, eid = self._studio(q)
        pid = self._part_id(q)
        try:
            return self.onshape.assembly_counts(did, wvm, wvmid, eid, pid, fresh=q.get("fresh") == "1")
        except OutOfCalls as e:
            return {"assemblies": [], "counted": 0, "total": 0, "note": str(e)}

    def check(self, q: Dict[str, str]) -> Dict[str, Any]:
        """The design checks. Out of calls, or Onshape not answering, isn't an error for the page: it just says the
        check waits."""
        did, wvm, wvmid, eid = self._studio(q)
        pid = self._part_id(q)
        part = self._find(did, wvm, wvmid, eid, pid)
        have = self._part_material(part)
        key = q.get("m") or (have["key"] if have and have["key"] else None)
        if key is not None and key not in self.cfg.materials:
            raise Refused(400, "Bad request")
        flat = self._flat(did, wvm, wvmid, eid, pid)       # sheet metal: its flat pattern is what's cut
        state = str(part.get("microversionId") or "")
        bends = None
        try:
            geom, extra = self.onshape.geometry(did, wvm, wvmid, eid, (flat or part)["partId"], state,
                                                part.get("name") or pid, fresh=q.get("fresh") == "1")
            if flat is not None:
                bends = self.onshape.bends(did, wvm, wvmid, eid, pid, state)
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
        part = self._find(did, wvm, wvmid, eid, pid)
        name = part.get("name") or pid
        pc = self.board.for_part(did, eid, pid, name, self.board.part_cards(), card_id=str(q.get("card") or ""))
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
                                                     ident, name)
            now, now_extra = self.onshape.geometry(did, wvm, wvmid, eid, pid, str(part.get("microversionId") or ""),
                                                   name)
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
        part = self._find(did, wvm, wvmid, eid, pid)
        pc = self.board.for_part(did, eid, pid, part.get("name") or "", self.board.part_cards(fresh=True),
                                 card_id=str(d.get("card") or ""))
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
        cards = self.board.part_cards(fresh=True)
        pc = self.board.for_part(did, eid, pid, "", cards, card_id=str(d.get("card") or ""))   # by its Part ID line
        if pc is None:                                   # a card made in Trello: by the part's name (one call)
            part = self._find(did, wvm, wvmid, eid, pid)
            pc = self.board.for_part(did, eid, pid, part.get("name") or "", cards, card_id=str(d.get("card") or ""))
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
        """A face or edge clicked in the CAD -> the part it's on. Onshape's SELECTION names only the entity's id
        ("selectionType": "ENTITY", "entityType": "FACE", seen 2026-11-01; a whole part is "BODY" with its part id).
        Answered from the shapes AutoCAM already has for this state of the Part Studio (parts opened in the panel),
        never with an Onshape call: an unknown one is {"part": ""} and the page says to pick the part instead."""
        did, wvm, wvmid, eid = self._studio(q)
        wanted = {i for i in str(q.get("ids") or "").split(",")[:20] if ENTITY_RE.match(i)}
        if not wanted:
            return {"part": ""}
        for part in self._solids(self.onshape.cached_parts(did, wvm, wvmid, eid) or []):
            flat = self._flat(did, wvm, wvmid, eid, part["partId"])
            found = self.onshape.cached_geometry(did, wvm, wvmid, eid, (flat or part)["partId"],
                                                 str(part.get("microversionId") or ""))
            if found is None:
                continue
            extra = found[1]
            known = set(extra.get("face_ids") or ())
            for edges in (extra.get("face_edges") or {}).values():
                known.update(edges)
            if wanted & known:
                return {"part": part["partId"]}
        return {"part": ""}

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
        if scribe and (self.cfg.scribe is None or self._flat(did, wvm, wvmid, eid, pid) is None):
            scribe = ""                                  # only a bent part has bend lines
        s = Send(did=did, wvm=wvm, wvmid=wvmid, eid=eid, pid=pid, name=name, host=self._host(d.get("server")),
                 qty=qty, nest=nest, tabs=tabs, face=face, rush=bool(d.get("rush")), smoked=smoked,
                 tool_eighth=bool(d.get("tool_eighth")), drafts=bool(d.get("drafts")), material_line=line,
                 fit_line=fit_line, scribe=scribe, sender=sender, notes=clean_text(d.get("notes"), 300))
        card = self.board.write(s, existing)
        where = "inbox" if s.drafts else "ready_for_cam"
        log.info("panel: %s sent %s x%d to %s: %s", sender, name, qty, LIST_NAMES[where], card.url)
        return {"card": self._detail(card, where), "list": LIST_NAMES[where],
                "material": {"setInOnshape": set_in_onshape, "why": why, "line": line}}

    def send_program(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """Someone CAM'd the part themselves: their .tap goes on a card linked to the part (Material, Thickness and
        an "Own program:" line), and AutoCAM picks it a spot (own_programs.py). The guard checks it here first for
        what doesn't depend on where it goes (codes, units, nothing below Z0, the park); the placed copy gets the full
        check (clamps, reach) before it's ever on a sheet card. Never into Ready to cut, like every card."""
        self._rate_limit()
        did, wvm, wvmid, eid = self._studio(d)
        pid = self._part_id(d)
        sender = self._sender(d)
        part = self._find(did, wvm, wvmid, eid, pid)
        name = part.get("name") or pid
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
        going = self.board.for_part(did, eid, pid, name, cards)
        if going is not None and going.status in ACTIVE:
            raise Refused(409, "This part already has a card.", kind="duplicate", card=self.board.detail(going))
        from .checks import inches                       # 3/16 in for the Thickness: line
        lines = [f"https://{self._host(d.get('server'))}/documents/{did}/{wvm}/{wvmid}/e/{eid}", f"Part ID: {pid}",
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
