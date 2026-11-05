"""The pictures drawn from the programs (drawings.py) and where they go (2026-11-04, the user's picks):

- A sheet card gets <sheet>_toolpath.png beside its preview (the runner's options pass, for any cuttable sheet
  card without one: new, rebuilt, or published before this): what its programs cut, on the stock as loaded, with
  what was cut from that offcut before (sheet_png).
- An offcut card's cover is what the piece should look like (refresh_offcut): every sheet AutoCAM cut on it (own
  programs AutoCAM placed too: their placed program from the sheet card, downloaded once and kept in
  state/pictures/), in the piece's own coordinates (end A at the bottom), parts cut out by hand, and the room left. Redrawn whenever the
  offcut's record changes (the runner looks each pass; a signature of the record says when), the old picture
  deleted. A piece made before 2026-11-04 doesn't say which sheet it came from (made_from): that's the sheet of its
  material that went to Cut when its card was made (within MADE_WITHIN_S; Trello card ids carry the time), and
  never one cut on it. A used stretch no program on record explains shows hatched.

The programs come from the job folders (queue/done/<job>: the main program, the small-tool and scribe programs
from result.json) and each cutter's size from the job. Nothing here may stop a card: every caller catches.
"""

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from autocam_core.hotfolder import write_atomic
from autocam_core.offcuts import Rect, Stretch, turn_rect
from autocam_core.schema_job import Job, load_job
from autocam_core.toolpath import Move, ToolpathError, read_program, turn_moves

from . import drawings
from .run_state import RunStore
from .tracker.base import Tracker

log = logging.getLogger("autocam.pictures")

Cuts = List[Tuple[List[Move], float]]           # each program's moves and its cutter's diameter
KINDS = ("small", "scribe", "outlines")         # programs drawn besides the main one (face skims, fits lap)
SAID = {"small": "the small-tool program, {cutter} (run it first)", "scribe": "the scribe program, {cutter} (run it first)",
        "outlines": "the outlines program (run it last)"}
MADE_WITHIN_S = 300                             # an offcut's card is made in the pass its sheet goes to Cut


def _kerf(job: Optional[Job], key: str = "", guid: str = "") -> float:
    """A cutter's diameter from the job (by tool key or GUID); 1/8 in if it can't be found."""
    if job is not None:
        tools = list(job.tooling.tools.values()) + ([job.scribe.chamfer] if job.scribe and job.scribe.chamfer else [])
        for t in tools:
            if (key and t.key == key) or (guid and t.guid == guid):
                return float(t.diameter_in)
    return 0.125


class CardPictures:
    def __init__(self, tracker: Tracker, store: RunStore, done: Path, offcuts: Any,
                 stock_label: Callable[[str, float], str], size: Callable[[Optional[str]], Tuple[float, float]],
                 clamps: Callable[[Optional[str]], Sequence[Rect]], reach: float):
        self.t = tracker
        self.store = store
        self.done = Path(done)                  # queue/done: each job's folder
        self.offcuts = offcuts                  # offcuts.OffcutStore
        self.stock_label = stock_label          # (material key, thickness) -> "6061 1/8in"
        self.size = size                        # material key -> (length, width) of its sheets
        self.clamps = clamps                    # material key -> the clamp strips (fixture), sheet coordinates
        self.reach = reach

    # ---- a sheet's programs, from its job folder
    def _job(self, job_id: str) -> Optional[Job]:
        try:
            text = self.store.job_text(job_id)
            return load_job(json.loads(text)) if text else None
        except Exception:  # noqa: BLE001 - a job this version can't read: cutters default
            return None

    def _own_cuts(self, sheet_id: str, info: Dict[str, Any]) -> Cuts:
        """An own program AutoCAM placed: the placed copy attached to its sheet card (kept once read)."""
        own = info.get("own") or {}
        name = own.get("placed")
        if own.get("by_hand") or not name:
            return []                           # placed by hand: where it went isn't known
        keep = Path(self.store.sheets_file).with_name("pictures") / f"{sheet_id}.tap"
        if keep.is_file():
            data = keep.read_bytes()
        else:
            card = self.t.get_card(sheet_id)
            att = next((a for a in card.attachments if a.name == name), None)
            if att is None:
                return []
            data = self.t.download(att)
            keep.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(keep, data)
        try:
            program = read_program(data.decode("ascii", errors="replace"), float(info.get("thickness_in") or 0.0))
        except ToolpathError:
            return []
        return [(list(program.moves), float(own.get("cutter_in") or 0.157))]

    def sheet_cuts(self, sheet_id: str, info: Dict[str, Any]) -> Cuts:
        """Every program of a sheet card (its record in sheet_cards.json), as loaded on the machine."""
        if info.get("own"):
            return self._own_cuts(sheet_id, info)
        job_id = str(info.get("job") or "")
        folder = self.done / job_id
        try:
            result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
            sheet = next(s for s in result.get("sheets", []) if s.get("index") == info.get("index"))
        except (OSError, ValueError, StopIteration):
            return []
        job = self._job(job_id)
        thickness = float(sheet.get("thickness_in") or info.get("thickness_in") or 0.0)
        cuts: Cuts = []
        files = [(sheet.get("tap"), _kerf(job, key=str(sheet.get("tool") or "")))]
        files += [(p.get("tap"), _kerf(job, guid=str(p.get("tool_guid") or "")))
                  for p in sheet.get("programs") or [] if p.get("kind") in KINDS]
        for name, kerf in files:
            if not name:
                continue
            try:
                program = read_program((folder / name).read_text(encoding="ascii", errors="replace"), thickness)
            except (OSError, ToolpathError):
                continue
            cuts.append((list(program.moves), kerf))
        return cuts

    # ---- what's been cut from an offcut, in its own coordinates
    def _made_from(self, offcut_id: str, piece: Dict[str, Any], reg: Dict[str, Dict], cut_on: List[str]) -> str:
        found = str(piece.get("made_from") or "")
        if found or piece.get("scrap"):
            return found
        try:
            made = int(offcut_id[:8], 16)       # when Trello made the offcut's card
        except ValueError:
            return ""
        near = []
        for sid, info in reg.items():
            if not info.get("offcut_done") or info.get("offcut_id") == offcut_id or sid in cut_on \
                    or info.get("material") != piece.get("material"):
                continue
            try:
                when = datetime.fromisoformat(str(info.get("cut_utc") or "").replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if abs(when - made) <= MADE_WITHIN_S:
                near.append((abs(when - made), sid))
        return min(near)[1] if near else ""

    def piece_cuts(self, offcut_id: str, piece: Dict[str, Any]) -> Tuple[Cuts, List[str]]:
        """(every program cut on the piece, in its own coordinates; the sheet cards they're from)."""
        reg = self.store.sheet_cards()
        length, width = self.size(piece.get("material"))
        cut_on = sorted((sid for sid, info in reg.items() if info.get("offcut_id") == offcut_id and info.get("cut")),
                        key=lambda sid: str(reg[sid].get("cut_utc") or ""))
        made = self._made_from(offcut_id, piece, reg, cut_on)
        cuts: Cuts = []
        for sid, turned in ([(made, False)] if made in reg else []) + [(s, bool(reg[s].get("turned"))) for s in cut_on]:
            for moves, kerf in self.sheet_cuts(sid, reg[sid]):
                cuts.append((turn_moves(moves, width, length) if turned else moves, kerf))
        return cuts, ([made] if made in reg else []) + cut_on

    # ---- the sheet card's picture
    def sheet_png(self, info: Dict[str, Any]) -> Optional[Tuple[str, bytes]]:
        """(file name, picture) for a sheet card (its record in sheet_cards.json); None if its result is gone."""
        job_id = str(info.get("job") or "")
        folder = self.done / job_id
        try:
            result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
            sheet = next(s for s in result.get("sheets", []) if s.get("index") == info.get("index"))
        except (OSError, ValueError, StopIteration):
            return None
        if not sheet.get("tap"):
            return None
        job = self._job(job_id)
        material = str(info.get("material") or "")
        thickness = float(sheet.get("thickness_in") or info.get("thickness_in") or 0.0)
        label = str(info.get("label") or f"{info.get('run')} S{info.get('index')}")
        png = self._sheet_png(label=label, material=material, thickness=thickness, job=job, folder=folder,
                              tap=sheet["tap"], tool=str(sheet.get("tool") or ""), programs=sheet.get("programs") or [],
                              offcut_id=info.get("offcut_id"), turned=bool(info.get("turned")))
        return f"{sheet.get('name') or sheet['tap'][:-4]}_toolpath.png", png

    def _sheet_png(self, *, label: str, material: str, thickness: float, job: Optional[Job], folder: Path,
                   tap: str, tool: str, programs: Sequence[Dict[str, Any]], offcut_id: Optional[str],
                   turned: bool) -> bytes:
        """programs: the sheet's other programs, as result.json has them (kind, tap, tool_guid, cutter_label)."""
        length, width = self.size(material)
        main = read_program((folder / tap).read_text(encoding="ascii", errors="replace"), thickness)
        others = []
        for p in programs:
            if p.get("kind") in KINDS and p.get("tap"):
                prog = read_program((folder / p["tap"]).read_text(encoding="ascii", errors="replace"), thickness)
                others.append((SAID[p["kind"]].format(cutter=p.get("cutter_label", "")), prog,
                               _kerf(job, guid=str(p.get("tool_guid") or ""))))
        earlier: Cuts = []
        hand: List[Rect] = []
        missing = None
        if offcut_id:
            piece = self.offcuts.get(offcut_id) or {}
            own, _ = self.piece_cuts(offcut_id, piece)
            earlier = [(turn_moves(m, width, length) if turned else m, k) for m, k in own]
            hand = [turn_rect(tuple(r), width, length) if turned else tuple(r) for r in piece.get("hand_cuts") or []]
            if piece.get("scrap") and piece.get("length_in"):
                missing = float(piece["length_in"])       # a scrap always goes in at the front
        stock = self.stock_label(material, thickness)
        where = f"on offcut #{(self.offcuts.get(offcut_id) or {}).get('number', '?')}" if offcut_id else "a new sheet"
        return drawings.toolpath_png(
            title=f"{label}: what the program cuts", sub=f"{stock}, {where} · {tap}", width=width, length=length,
            main=main, kerf_in=_kerf(job, key=tool), others=others, clamps=self.clamps(material), reach=self.reach,
            earlier=earlier, hand_cuts=hand, missing_from=missing)

    # ---- the offcut card's picture
    def _signature(self, piece: Dict[str, Any], sheets: Sequence[str]) -> str:
        keep = [piece.get(k) for k in ("used", "beside", "hand_cuts", "number", "length_in", "scrap", "material",
                                       "thickness_in")]
        return hashlib.sha1(json.dumps([keep, list(sheets)], sort_keys=True, default=str).encode()).hexdigest()[:16]

    def offcut_png(self, offcut_id: str, piece: Dict[str, Any], cuts: Cuts) -> bytes:
        length, width = self.size(piece.get("material"))
        end = float(piece["length_in"]) if piece.get("scrap") and piece.get("length_in") else length
        used: List[Stretch] = sorted((float(a), float(b)) for a, b in piece.get("used") or [])
        free, at = [], 0.0
        for a, b in used + [(end, end)]:
            if a > at + 1.0:                    # less is the edge margin, not room
                free.append((at, min(a, end)))
            at = max(at, b)
        hand = [tuple(float(v) for v in r) for r in piece.get("hand_cuts") or []]
        # what's used but no program or hand cut on record explains (a sheet before 2026-11-04, a program elsewhere)
        known = [(min(min(m.y0, m.y1) for m in moves) - 1.0, max(max(m.y0, m.y1) for m in moves) + 1.0)
                 for moves, _ in cuts if moves] + [(r[1] - 1.0, r[3] + 1.0) for r in hand]
        unknown: List[Stretch] = []
        for a, b in used:
            parts = [(a, min(b, end))]
            for lo, hi in known:
                parts = [q for p in parts for q in ((p[0], min(p[1], lo)), (max(p[0], hi), p[1])) if q[1] - q[0] > 0.05]
            unknown += [p for p in parts if p[1] - p[0] >= 1.0]
        stock = self.stock_label(piece.get("material", ""), float(piece.get("thickness_in") or 0.0))
        last = str((piece.get("last") or {}).get("label") or "")
        sub = (f"last cut: {last}" if last else
               f"added by hand, {end:g} in long" if piece.get("scrap") else "")
        return drawings.offcut_png(
            title=f"Offcut #{piece.get('number', '?')} · {stock}: what it should look like", sub=sub, width=width,
            length=length, cuts=cuts, hand_cuts=hand, free=free, rooms=[tuple(r) for r in piece.get("beside") or []],
            missing_from=end if end < length - 0.05 else None, unknown=unknown)

    def refresh_offcut(self, offcut_id: str) -> bool:
        """The offcut card's cover, redrawn if its record changed since the last one. True if it was."""
        piece = self.offcuts.get(offcut_id)
        if piece is None:
            return False
        cuts, sheets = self.piece_cuts(offcut_id, piece)
        sig = self._signature(piece, sheets)
        if piece.get("picture_sig") == sig:
            return False
        try:
            png = self.offcut_png(offcut_id, piece, cuts)
        except Exception as e:  # noqa: BLE001 - the card is fine without it; not tried again until it changes
            log.warning("offcut %s: couldn't draw its picture: %s", offcut_id, e)
            piece["picture_sig"] = sig
            self.offcuts.put(offcut_id, piece)
            return False
        att = self.t.attach_file(offcut_id, f"offcut_{piece.get('number', 'x')}.png", png, "image/png")
        self.t.set_cover(offcut_id, att)
        old = piece.get("picture")
        if old and old != att:
            try:
                self.t.delete_attachment(offcut_id, old)
            except Exception as e:  # noqa: BLE001 - an old picture left on the card is harmless
                log.info("offcut %s: couldn't delete its old picture: %s", offcut_id, e)
        piece = self.offcuts.get(offcut_id) or piece          # re-read: keep anything written meanwhile
        piece.update(picture=att, picture_sig=sig)
        if len(sheets) and not piece.get("made_from") and sheets[0] not in [
                sid for sid, info in self.store.sheet_cards().items() if info.get("offcut_id") == offcut_id]:
            piece["made_from"] = sheets[0]                     # found by its last cut: kept
        self.offcuts.put(offcut_id, piece)
        return True
