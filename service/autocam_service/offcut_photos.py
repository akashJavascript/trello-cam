"""The shop page's Offcuts tab (2026-11-09; docs/offcut-photos.md): the rack, and offcuts added or updated from a phone
photo.

Nothing new is recorded here. A photo ends up as the same card a person would write by hand, and the service's own
offcut pass (runner.scan_offcuts) does the rest, as it always has:
- a new piece: a card in Offcuts with Material, Thickness and Length lines and a "Cut by hand" line per box (parse_scrap,
  parse_hand_cuts). The pass adds it, numbers it and says so on the card; the Offcuts tab shows the number.
- a re-photo of an offcut: its new boxes as "Cut by hand" lines added to its card (the pass records each once).
- Gone: its card archived (an offcut whose card leaves the list is forgotten).
The photo goes on the card too. Boxes are in the piece's own inches: across from the left edge and along from the
front end (the end at the bottom of the photo), the same measure the card lines use.

Photos are kept in state/offcut_photos/<id>.jpg for a day (the steps of one add); photo_find.py does the finding.
A photo that's saved is also kept in state/offcut_photos/kept/ with what was suggested and what people saved
([offcut_photos] keep, the newest 500), so the finding can be measured on the team's real photos
(photo_report.py, `offcut-photo-report`) and tuned or trained on them later. No names go in it.
"""

import base64
import binascii
import json
import logging
import math
import re
import secrets
import shutil
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from autocam_core.offcuts import rect_minus

from . import photo_find
from .offcuts import OffcutStore, hand_cut_line
from .shoplist import ListError

log = logging.getLogger("autocam.offcut_photos")

PHOTO_RE = re.compile(r"^[0-9a-f]{16}$")
CARD_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")     # only ever looked up in the offcut record
MAX_PHOTO_MB = 12
KEEP_S = 24 * 3600
MAX_BOXES = 80
BOARD_CACHE_S = 20
KEPT = "kept"
TAPS_PER_MINUTE = 40       # a tap on a hole is about half a second of the laptop's time
KINDS = ("found", "maybe", "tap", "drawn")       # where a saved box came from (for the report)


def _thickness_label(t: float) -> str:
    from fractions import Fraction
    f = Fraction(t).limit_denominator(64)
    if abs(float(f) - t) < 1e-6:
        return f"{f.numerator}/{f.denominator}" if f.denominator > 1 else f"{f.numerator}"
    return f"{t:g}"


class OffcutPhotos:
    def __init__(self, tracker: Any, cfg: Any, store: OffcutStore, folder: Path,
                 label: Callable[[Any], str], line: Callable[[Any], str], clock: Callable[[], float] = time.time,
                 keep: int = 500):
        self.t, self.cfg, self.store, self.folder = tracker, cfg, store, Path(folder)
        self.label, self.line, self.clock = label, line, clock
        self.keep = max(0, int(keep))
        self._taps: deque = deque()
        self._lock = threading.Lock()
        self._cards: Optional[tuple] = None          # (when, {card id: name}) of the Offcuts list

    # ------------------------------------------------------------------ the rack
    def size(self, material: str):
        sheet = self.cfg.sheet_for(material)
        return sheet.length_in, sheet.width_in

    def _on_board(self) -> Optional[Dict[str, str]]:
        with self._lock:
            if self._cards is not None and self.clock() - self._cards[0] < BOARD_CACHE_S:
                return self._cards[1]
        try:
            cards = {c.id: c.name for c in self.t.list_cards("offcuts")}
        except Exception as e:  # noqa: BLE001 - the tab still shows what's on record
            log.warning("offcut photos: reading the Offcuts list: %s", e)
            return None
        with self._lock:
            self._cards = (self.clock(), cards)
        return cards

    def forget_board(self) -> None:
        with self._lock:
            self._cards = None

    def used_areas(self, piece: Dict) -> List[List[float]]:
        """What's used of an offcut as rectangles (across0, along0, across1, along1, its own inches): its used stretches
        across the whole width, less the rooms beside its cuts that are still free."""
        length, width = self.size(piece["material"])
        rooms = [tuple(r) for r in piece.get("beside", []) if r[2] > r[0] and r[3] > r[1]]
        out: List[List[float]] = []
        for a, b in piece.get("used", []):
            pieces = [(0.0, float(a), float(width), float(b))]
            for r in rooms:
                pieces = [p for q in pieces for p in rect_minus(q, r)]
            out += [[round(v, 2) for v in p] for p in pieces if p[2] - p[0] > 0.05 and p[3] - p[1] > 0.05]
        return out

    def view(self) -> Dict[str, Any]:
        """The Offcuts tab: each offcut on the rack (its number, stock, size and what's used of it, for its picture),
        and the materials a new one can be."""
        on_board = self._on_board()
        out = []
        for card_id, p in self.store.all().items():
            if p.get("missing") or (on_board is not None and card_id not in on_board):
                continue
            m = self.cfg.materials.get(p.get("material"))
            if m is None:
                continue
            full, width = self.size(p["material"])
            length = float(p["length_in"]) if p.get("scrap") else full
            used = [[u[0], u[1], u[2], min(u[3], length)] for u in self.used_areas(p) if u[1] < length - 0.05]
            out.append({"id": card_id, "number": p.get("number"), "material": p["material"], "label": self.label(m),
                        "thickness": _thickness_label(float(p["thickness_in"])), "t": float(p["thickness_in"]),
                        "length": length, "sheet": full, "width": width, "used": used,   # a scrap's missing length isn't drawn
                        "inUse": bool(p.get("reserved_by")), "url": p.get("url") or ""})
        out.sort(key=lambda x: (x["label"], x["t"], x["number"] or 0))
        mats = [{"key": k, "label": self.label(m), "thicknesses": [_thickness_label(t) for t in m.thicknesses_in],
                 "length": self.size(k)[0], "width": self.size(k)[1]} for k, m in self.cfg.materials.items()]
        return {"offcuts": out, "offcutMaterials": mats, "offcutFinder": photo_find.AVAILABLE,
                "offcutBoard": on_board is not None}

    # ------------------------------------------------------------------ a photo, step by step
    def _prune(self) -> None:
        if not self.folder.is_dir():
            return
        now = self.clock()
        for f in [*self.folder.glob("*.jpg"), *self.folder.glob("*.json")]:   # not kept/
            try:
                if now - f.stat().st_mtime > KEEP_S:
                    f.unlink()
            except OSError:
                pass

    def _note(self, pid: str, **fields: Any) -> None:
        """What was suggested for a photo and what was done with it (<id>.json beside it), for keeping it once saved."""
        p = self.folder / f"{pid}.json"
        try:
            notes = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except (OSError, ValueError):
            notes = {}
        notes.update(fields)
        try:
            p.write_text(json.dumps(notes), encoding="utf-8")
        except OSError as e:
            log.warning("offcut photos: noting %s: %s", pid, e)

    def _keep(self, pid: str, photo: Path, fields: Dict[str, Any]) -> None:
        """A saved photo kept in kept/<when>-<id>/ (photo.jpg, record.json: the suggested corners and boxes, the ones
        saved, the stock), the newest `keep` of them. Never in the way of saving."""
        if self.keep <= 0:
            return
        try:
            notes = json.loads((self.folder / f"{pid}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return                                   # never straightened (no OpenCV): nothing to measure
        if "corners" not in notes:
            return
        try:
            when = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
            folder = self.folder / KEPT / f"{when}-{pid}"
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(photo, folder / "photo.jpg")
            (folder / "record.json").write_text(json.dumps({"when": when, **notes, **fields}, indent=1),
                                                encoding="utf-8")
            kept = sorted(p for p in (self.folder / KEPT).iterdir() if p.is_dir())
            for old in kept[:max(0, len(kept) - self.keep)]:
                shutil.rmtree(old, ignore_errors=True)
        except OSError as e:
            log.warning("offcut photos: keeping %s: %s", pid, e)

    def _photo_path(self, photo: Any) -> Path:
        if not isinstance(photo, str) or not PHOTO_RE.match(photo):
            raise ListError("Bad request")
        p = self.folder / f"{photo}.jpg"
        if not p.is_file():
            raise ListError("That photo is gone. Take it again.", status=404, kind="gone")
        return p

    def _target(self, target: Any) -> Optional[Dict]:
        """The offcut a re-photo is for ("" or None: a new piece)."""
        if not target:
            return None
        if not isinstance(target, str) or not CARD_RE.match(target):
            raise ListError("Bad request")
        piece = self.store.get(target)
        if piece is None:
            raise ListError("That offcut isn't on record any more.", status=404, kind="gone")
        return piece

    def _size_for(self, material: Any, length: Any, target: Optional[Dict]):
        """(material key, width, length) of the piece in the photo."""
        if target is not None:
            full, width = self.size(target["material"])
            return target["material"], width, float(target["length_in"]) if target.get("scrap") else full
        if material not in self.cfg.materials:
            raise ListError("Pick the material.")
        full, width = self.size(material)
        try:
            n = float(length)
        except (TypeError, ValueError):
            raise ListError("Type its length in inches.")
        if not 1 <= n <= full + 1e-6:
            raise ListError(f"Its length should be 1 to {full:g} in.")
        return material, width, n

    def photo(self, data: Any, material: Any = None, length: Any = None, target: Any = None,
              quality: Any = None) -> Dict[str, Any]:
        """A photo taken: kept, and its corners suggested if they can be found. `quality`: what the page's photo
        check measured (kept with it, to tune the check on real photos)."""
        piece = self._target(target)
        key, width, n = self._size_for(material, length, piece)
        if not isinstance(data, str) or len(data) > MAX_PHOTO_MB * 1024 * 1024 * 4 // 3 + 16:
            raise ListError("That photo is too big.", status=413)
        try:
            raw = base64.b64decode(data.split(",", 1)[-1], validate=True)
        except (binascii.Error, ValueError):
            raise ListError("That photo didn't come through. Take it again.")
        self.folder.mkdir(parents=True, exist_ok=True)
        self._prune()
        pid = secrets.token_hex(8)
        corners = None
        w = h = 0
        if photo_find.AVAILABLE:
            img = photo_find.decode(raw)
            if img is None:
                raise ListError("That isn't a photo AutoCAM can read. Take it again.")
            import cv2
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            (self.folder / f"{pid}.jpg").write_bytes(buf.tobytes())
            h, w = img.shape[:2]
            t0 = time.perf_counter()
            corners = photo_find.suggest_corners(img, width, n)
            log.info("offcut photo %s: %dx%d, corners %s (%.2f s)", pid, w, h, "found" if corners else "not found",
                     time.perf_counter() - t0)
            self._note(pid, w=w, h=h, suggested_corners=corners, quality=_quality(quality))
        else:
            if raw[:3] != b"\xff\xd8\xff":
                raise ListError("Take the photo as a JPEG.")
            (self.folder / f"{pid}.jpg").write_bytes(raw)
        return {"photo": pid, "corners": corners, "w": w, "h": h}

    @staticmethod
    def _corners(corners: Any) -> List[List[float]]:
        if not isinstance(corners, list) or len(corners) != 4:
            raise ListError("Bad request")
        out = []
        for p in corners:
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                raise ListError("Bad request")
            try:
                out.append([float(p[0]), float(p[1])])
            except (TypeError, ValueError):
                raise ListError("Bad request")
        return out

    def _straight(self, photo: Any, corners: Any, material: Any, length: Any, target: Any):
        """(the straightened piece, width, length, the offcut a re-photo is for, the corners), from the kept photo."""
        path = self._photo_path(photo)
        piece = self._target(target)
        key, width, n = self._size_for(material, length, piece)
        c = self._corners(corners)
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        return photo_find.straighten(img, c, width, n), width, n, piece, c

    def grow(self, photo: Any, corners: Any, at: Any, material: Any = None, length: Any = None,
             target: Any = None) -> Dict[str, Any]:
        """A tap on a hole the finding missed: the box over it (photo_find.grow_at), or none when no hole can be told
        there."""
        try:
            ax, ay = (float(v) for v in at)
        except (TypeError, ValueError):
            raise ListError("Bad request")
        if not (math.isfinite(ax) and math.isfinite(ay)):
            raise ListError("Bad request")
        with self._lock:
            now = self.clock()
            while self._taps and now - self._taps[0] > 60:
                self._taps.popleft()
            if len(self._taps) >= TAPS_PER_MINUTE:
                raise ListError("Lots of taps just now. Wait a moment, or drag a box.", status=429)
            self._taps.append(now)
        if not photo_find.AVAILABLE:
            return {"box": None}
        t0 = time.perf_counter()
        flat, width, n, piece, c = self._straight(photo, corners, material, length, target)
        boxes = photo_find.grow_at(flat, width, n, (ax, ay))
        log.info("offcut photo %s: tap at %.1f, %.1f in: %s (%.2f s)", photo, ax, ay,
                 f"{len(boxes)} box(es)" if boxes else "no hole", time.perf_counter() - t0)
        if not boxes:
            return {"box": None, "boxes": []}
        whole = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
        return {"box": whole, "boxes": [list(b) for b in boxes]}

    def flat(self, photo: Any, corners: Any, material: Any = None, length: Any = None,
             target: Any = None) -> Dict[str, Any]:
        """The piece straightened by its corners, the boxes found over its holes and the maybes (for a re-photo, only
        those not already on record)."""
        path = self._photo_path(photo)
        piece = self._target(target)
        key, width, n = self._size_for(material, length, piece)
        c = self._corners(corners)
        if not photo_find.AVAILABLE:
            return {"image": "", "boxes": [], "how": "", "width": width, "length": n}
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        t0 = time.perf_counter()
        flat = photo_find.straighten(img, c, width, n)
        found = photo_find.find(flat, width, n)
        boxes, groups, maybes, how = found["boxes"], found["groups"], found["maybes"], found["how"]
        known: List[List[float]] = []
        if piece is not None:
            known = self.used_areas(piece) + [list(x) for x in piece.get("hand_cuts", [])]
            kept = [(b, g) for b, g in zip(boxes, groups) if _inside_share(b, known) < 0.8]
            boxes, groups = [b for b, _ in kept], [g for _, g in kept]
            maybes = [b for b in maybes if _inside_share(b, known) < 0.8]
        r2 = lambda bs: [[round(float(v), 2) for v in b] for b in bs]  # noqa: E731
        self._note(photo, corners=c, suggested=r2(boxes), groups=groups, maybes=r2(maybes), how=how, known=r2(known))
        log.info("offcut photo %s: %d boxes, %d maybes by %s (%.2f s)", photo, len(boxes), len(maybes), how or "-",
                 time.perf_counter() - t0)
        image = base64.b64encode(photo_find.piece_jpeg(flat)).decode("ascii")
        return {"image": "data:image/jpeg;base64," + image, "boxes": [list(b) for b in boxes], "groups": groups,
                "maybes": [list(b) for b in maybes], "how": how, "width": width, "length": n}

    @staticmethod
    def _boxes(boxes: Any, width: float, length: float) -> List[List[float]]:
        if not isinstance(boxes, list) or len(boxes) > MAX_BOXES:
            raise ListError("Bad request")
        out = []
        for b in boxes:
            if not isinstance(b, (list, tuple)) or len(b) != 4:
                raise ListError("Bad request")
            try:
                x0, y0, x1, y1 = (float(v) for v in b)
            except (TypeError, ValueError):
                raise ListError("Bad request")
            x0, x1 = sorted((max(0.0, min(width, x0)), max(0.0, min(width, x1))))
            y0, y1 = sorted((max(0.0, min(length, y0)), max(0.0, min(length, y1))))
            if x1 - x0 < 0.1 or y1 - y0 < 0.1:
                continue
            out.append([round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)])
        return out

    def save(self, photo: Any, boxes: Any, sender: str, material: Any = None, thickness: Any = None,
             length: Any = None, target: Any = None, kinds: Any = None) -> Dict[str, Any]:
        """A new piece's card in Offcuts, or a re-photographed offcut's new boxes on its card (and the photo on it).
        `kinds`: where each box came from (KINDS), kept with the photo for the report."""
        path = self._photo_path(photo)
        piece = self._target(target)
        key, width, n = self._size_for(material, length, piece)
        cuts = self._boxes(boxes, width, n)
        tally = {k: 0 for k in KINDS}
        if isinstance(kinds, list) and isinstance(boxes, list) and len(kinds) == len(boxes):
            for kind in kinds:
                if kind in tally:
                    tally[kind] += 1
        tally = tally if any(tally.values()) else None
        lines = [hand_cut_line(tuple(c), width) for c in cuts]
        who = sender or "someone at the shop"
        image = path.read_bytes()
        if piece is not None:
            card = self.t.get_card(target)
            if cuts:
                desc = (card.desc.rstrip() + "\n\n" + "\n".join(lines)).strip()
                self.t.update_card(target, card.name, desc)
            self.t.attach_file(target, f"photo {time.strftime('%Y-%m-%d')}.jpg", image, "image/jpeg")
            self.t.comment(target, f"Photographed on the shop page by {who}: "
                           + (f"{len(cuts)} new hand cut{'s' if len(cuts) != 1 else ''}." if cuts else "nothing new cut."))
            self._keep(photo, path, {"kind": "re-photo", "material": key,
                                     "thickness": _thickness_label(float(piece["thickness_in"])), "width": width,
                                     "length": n, "saved": cuts, "number": piece.get("number"), "kinds": tally})
            log.info("offcut photos: %s re-photographed offcut #%s (%d cuts)", who, piece.get("number"), len(cuts))
            self.forget_board()
            return {"card": target, "number": piece.get("number"), "cuts": len(cuts)}
        m = self.cfg.materials[key]
        thicks = {_thickness_label(t): t for t in m.thicknesses_in}
        if thickness not in thicks:
            raise ListError("Pick the thickness.")
        desc = "\n".join([f"Material: {self.line(m)}", f"Thickness: {thickness}", f"Length: {n:g}", *lines,
                          "", f"Added from a photo on the shop page by {who}."])
        card = self.t.create_card("offcuts", "Offcut from a photo", desc)
        self.t.attach_file(card.id, "photo.jpg", image, "image/jpeg")
        self._keep(photo, path, {"kind": "new", "material": key, "thickness": thickness, "width": width, "length": n,
                                 "saved": cuts, "kinds": tally})
        log.info("offcut photos: %s added a %s %s piece, %g in long, %d cuts (card %s)", who, key, thickness, n,
                 len(cuts), card.id)
        self.forget_board()
        return {"card": card.id, "number": None, "cuts": len(cuts)}

    def gone(self, card_id: Any, sender: str) -> None:
        """An offcut used up or thrown away: its card archived (the service then forgets it)."""
        piece = self._target(card_id)
        if piece is None:
            raise ListError("Bad request")
        if piece.get("reserved_by"):
            raise ListError("A sheet waiting to be cut is on it. Move that sheet first.", status=409, kind="in use")
        self.t.comment(card_id, f"Gone (the shop page, {sender or 'someone at the shop'}).")
        self.t.archive(card_id)
        self.forget_board()
        log.info("offcut photos: offcut #%s gone (%s)", piece.get("number"), sender or "-")


def _quality(q: Any) -> Optional[Dict[str, Any]]:
    """The page's photo check (offcuts.js photoCheck): its numbers and a few known words, nothing else."""
    if not isinstance(q, dict):
        return None
    out: Dict[str, Any] = {}
    for key, lo, hi in (("sharp", 0.0, 5.0), ("bright", 0.0, 255.0), ("tilt", 0.0, 180.0)):
        v = q.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi:
            out[key] = round(float(v), 3)
    if q.get("problem") in ("blurry", "dark"):
        out["problem"] = q["problem"]
    if q.get("via") in ("camera", "app"):
        out["via"] = q["via"]
    if q.get("anyway") is True:
        out["anyway"] = True
    return out or None


def _inside_share(box: Sequence[float], known: Sequence[Sequence[float]]) -> float:
    """How much of a box lies in already-recorded areas (0-1; overlaps between those counted once at most)."""
    area = max((box[2] - box[0]) * (box[3] - box[1]), 1e-9)
    inside = 0.0
    for k in known:
        w = min(box[2], k[2]) - max(box[0], k[0])
        h = min(box[3], k[3]) - max(box[1], k[1])
        if w > 0 and h > 0:
            inside += w * h
    return min(1.0, inside / area)
