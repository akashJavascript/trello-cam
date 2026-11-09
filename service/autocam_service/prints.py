"""The shop's 3D print queue (2026-11-09, the mockup the user approved: https://claude.ai/artifact/GS5EDfgP53ogQzmSjSxvqf).

Printed parts (a part whose Onshape material is a print material: PLA, PETG, TPU...) are sent from the Onshape panel,
one at a time or with an assembly, or added on the shop page from a STEP, STL or 3MF file. They show on the /shop page's
Prints tab: Waiting, then Printing (on which printer, done about when: both optional), then Done. A print that
failed goes back to Waiting to print again, counted. No slicing and nothing sent to the printers (yet: the user).

Each print carries its material (Onshape's, changeable in the panel even when it isn't what Onshape says), a color,
how many, its assembly, Rush, optional infill % and walls, and a note. Its model file: an upload is kept as it came;
a part from Onshape is exported as a STEP file (the user, 2026-11-09: "export step files not stl"; Bambu Studio
opens them) the first time someone downloads it, then kept, so prints nobody takes cost nothing. STEP is one of
Onshape's translations (start it, wait for it, download it: a few calls).

state/prints.json, like the other shop lists (shoplist.py); the files in state/print_files/<id>.<step|stl|3mf>.
"""

import base64
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from autocam_core.hotfolder import write_atomic

from .shoplist import ID_RE, ListError, ShopList

FILE_EXTS = (".step", ".stp", ".stl", ".3mf")
NAME_MAX = 80
NOTE_MAX = 300
COLOR_MAX = 30
MAX_MINUTES = 7 * 24 * 60      # a print time over a week is a typo
_WORD = re.compile(r"[^A-Za-z0-9 ._()+-]+")


def clean(value: Any, limit: int) -> str:
    """One line of plain text, at most `limit` characters."""
    return " ".join(str(value or "").split())[:limit]


def print_material(onshape_name: str, materials: Sequence[str]) -> str:
    """The queue's material for an Onshape material name ("3D Printed PLA" -> "PLA", "TPU 95A" -> "TPU 95A"): the
    longest of `materials` named in it, else the first ("" if there are none)."""
    name = (onshape_name or "").lower()
    hits = [m for m in materials if m and re.search(r"(?<![a-z0-9])" + re.escape(m.lower()) + r"(?![a-z0-9])", name)]
    if not hits:                                      # "TPU" for "TPU 95A": the material's first word
        hits = [m for m in materials if m and re.search(r"(?<![a-z0-9])" + re.escape(m.split()[0].lower())
                                                        + r"(?![a-z0-9])", name)]
    return max(hits, key=len) if hits else (materials[0] if materials else "")


class PrintList(ShopList):
    FILE_KEY = "prints"
    NOUN = "print"
    ACTIVE = ("waiting", "printing")
    FINISHED = "done"
    FINISHED_AT = "done_at"

    def __init__(self, path: Path, files: Path, printers: Sequence[str] = (), keep_days: int = 14,
                 now: Callable[[], datetime] = datetime.now):
        super().__init__(path, keep_days=keep_days, now=now)
        self.files = Path(files)
        self.printers = tuple(printers)

    # ---- in
    def add(self, entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """New prints, waiting. An entry from Onshape (a "source": its part, or its assembly row) is refused while
        the same one is still waiting or printing."""
        wanted = list(entries)

        def go(items):
            made = []
            for e in wanted:
                source = str(e.get("source") or "")
                if source and any(x.get("source") == source and x.get("status") in self.ACTIVE for x in items):
                    raise ListError("It's already in the print queue.", status=409, kind="duplicate")
                x = {"id": secrets.token_hex(6), "status": "waiting", "created": self._stamp(), "fails": 0,
                     "name": clean(e.get("name"), NAME_MAX) or "A print", "qty": _qty(e.get("qty")),
                     "material": clean(e.get("material"), 30), "color": clean(e.get("color"), COLOR_MAX) or "Any",
                     "infill": _optional_int(e.get("infill"), 0, 100, "Infill"),
                     "walls": _optional_int(e.get("walls"), 1, 30, "Walls"),
                     "rush": bool(e.get("rush")), "note": clean(e.get("note"), NOTE_MAX),
                     "sender": clean(e.get("sender"), 40), "source": source, "file": str(e.get("file") or "")}
                for k in ("assembly", "row", "url"):
                    if e.get(k):
                        x[k] = e[k]
                if e.get("who"):
                    x["who"] = clean(e["who"], 40)
                items.append(x)
                made.append(dict(x))
            return made
        return self._change(go)

    def add_file(self, data: bytes, filename: str, entry: Dict[str, Any]) -> Dict[str, Any]:
        """A print from a file someone added on the shop page (STEP, STL or 3MF)."""
        ext = Path(filename or "").suffix.lower()
        if ext not in FILE_EXTS:
            raise ListError("Add a STEP, STL or 3MF file.")
        if not data:
            raise ListError("That file is empty.")
        if not entry.get("name"):
            entry = {**entry, "name": Path(filename).stem}
        [x] = self.add([{**entry, "source": ""}])
        stored = f"{x['id']}{ext}"
        self.files.mkdir(parents=True, exist_ok=True)
        write_atomic(self.files / stored, data)
        self._set(x["id"], file=stored, filename=_WORD.sub("", Path(filename).name)[:NAME_MAX] or stored)
        return x

    # ---- the shop page
    def start(self, pid: Any, device: str, printer: Any = "", minutes: Any = 0) -> None:
        """Printing (on a printer, done about some time: both optional)."""
        printer = clean(printer, 40)
        if printer and printer not in self.printers:
            raise ListError("That printer isn't on the list.")
        try:
            minutes = int(minutes or 0)
        except (TypeError, ValueError):
            raise ListError("The print time should be hours and minutes.")
        if not 0 <= minutes <= MAX_MINUTES:
            raise ListError("The print time should be under a week.")

        def go(items):
            x = self._find(items, pid)
            if x.get("status") != "waiting":
                raise ListError("Someone already started it.", status=409, kind="started")
            x.update(status="printing", started_at=self._stamp(), started_by=device, printer=printer,
                     finish=(self.now() + timedelta(minutes=minutes)).isoformat(timespec="minutes") if minutes else "")
        self._change(go)

    def failed(self, pid: Any, device: str) -> None:
        """It failed: back to Waiting, to print again."""
        def go(items):
            x = self._find(items, pid)
            if x.get("status") != "printing":
                raise ListError("It isn't printing.", status=409)
            x["fails"] = int(x.get("fails") or 0) + 1
            x["status"] = "waiting"
            for k in ("started_at", "started_by", "printer", "finish"):
                x.pop(k, None)
        self._change(go)

    def done(self, pid: Any, device: str) -> None:
        def go(items):
            x = self._find(items, pid)
            if x.get("status") not in self.ACTIVE:
                raise ListError("It's already done.", status=409)
            x.update(status="done", done_at=self._stamp(), done_by=device)
        self._change(go)

    def undo(self, pid: Any, device: str) -> None:
        """Done by mistake: back to Printing (this phone's, today)."""
        def go(items):
            x = self._find(items, pid)
            if x.get("status") != "done" or x.get("done_by") != device or not self._today(x.get("done_at")):
                raise ListError("Only the phone that finished it can undo it, the same day.", status=409)
            x["status"] = "printing" if x.get("started_at") else "waiting"
            x.pop("done_at", None)
            x.pop("done_by", None)
        self._change(go)

    def remove(self, pid: Any) -> None:
        """Taken off the queue (from the panel, while it's waiting)."""
        def go(items):
            x = self._find(items, pid)
            if x.get("status") != "waiting":
                raise ListError("Someone already started it.", status=409, kind="started")
            items.remove(x)
        self._change(go)
        self._drop_files(pid)

    # ---- files
    def file_of(self, pid: Any) -> Optional[Path]:
        x = self.get(pid)
        if x is None or not x.get("file"):
            return None
        path = self.files / x["file"]
        return path if path.is_file() else None

    def keep_export(self, pid: Any, data: bytes) -> Path:
        """A STEP file exported from Onshape for this print, kept for the next download."""
        x = self.get(pid)
        if x is None:
            raise ListError("That print isn't on the list any more.", status=404, kind="gone")
        stored = f"{x['id']}.step"
        self.files.mkdir(parents=True, exist_ok=True)
        write_atomic(self.files / stored, data)
        self._set(x["id"], file=stored, translation="")
        return self.files / stored

    def note_translation(self, pid: str, tid: str) -> None:
        """Onshape's STEP translation started for this print: the next download picks it up."""
        self._set(pid, translation=tid)

    def download_name(self, x: Dict[str, Any]) -> str:
        name = x.get("filename") or (_WORD.sub("", x.get("name") or "print").strip() or "print") + Path(x.get("file") or ".step").suffix
        return name.replace('"', "")

    def get(self, pid: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(pid, str) or not ID_RE.match(pid):
            raise ListError("Bad request")
        return next((dict(x) for x in self.all() if x.get("id") == pid), None)

    def _set(self, pid: str, **fields: Any) -> None:
        def go(items):
            self._find(items, pid).update(fields)
        self._change(go)

    def _drop_files(self, pid: str) -> None:
        for ext in FILE_EXTS:
            try:
                (self.files / f"{pid}{ext}").unlink()
            except OSError:
                pass

    def _pruned(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        kept = super()._pruned(items)
        gone = {x.get("id") for x in items} - {x.get("id") for x in kept}
        for pid in gone:                             # done long ago: its file goes too
            if isinstance(pid, str) and ID_RE.match(pid):
                self._drop_files(pid)
        return kept

    # ---- the panel
    def for_source(self, source: str) -> Optional[Dict[str, Any]]:
        """The newest print of this part (or assembly row), with how it's going, for the panel."""
        return next((x for x in reversed(self.all()) if x.get("source") == source), None)

    # ---- what the page shows
    def view(self, x: Dict[str, Any], device: str) -> Dict[str, Any]:
        finish = x.get("finish") or ""
        return {"id": x["id"], "name": x.get("name", ""), "qty": x.get("qty", 1), "status": x.get("status"),
                "assembly": (x.get("assembly") or {}).get("name", "") if isinstance(x.get("assembly"), dict) else "",
                "sender": x.get("sender", ""), "material": x.get("material", ""), "color": x.get("color", "Any"),
                "infill": x.get("infill"), "walls": x.get("walls"), "rush": bool(x.get("rush")),
                "note": x.get("note", ""), "who": x.get("who", ""), "fails": int(x.get("fails") or 0),
                "printer": x.get("printer", ""), "finish": self._clock(finish) if finish else "",
                "mine": x.get("started_by") == device and x.get("status") == "printing",
                "file": bool(x.get("file") or x.get("row")), "upload": not x.get("source"),
                "url": x.get("url", ""), "when": self._clock(x["done_at"]) if x.get("done_at") else "",
                "undo": x.get("status") == "done" and x.get("done_by") == device and self._today(x.get("done_at"))}

    def shop_view(self, device: str) -> Dict[str, Any]:
        items = self.all()
        return {"prints": [self.view(x, device) for x in items if x.get("status") in self.ACTIVE],
                "printsDone": [self.view(x, device) for x in reversed(items)
                               if x.get("status") == "done" and self._today(x.get("done_at"))]}


def _qty(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ListError("Quantity should be a number.")
    if not 1 <= n <= 500:
        raise ListError("Quantity should be 1 to 500.")
    return n


def _optional_int(value: Any, low: int, high: int, what: str) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ListError(f"{what} should be a number.")
    if not low <= n <= high:
        raise ListError(f"{what} should be {low} to {high}.")
    return n


def decode_upload(data: Any, limit: int) -> bytes:
    """A file the shop page sent as base64."""
    if not isinstance(data, str) or not data:
        raise ListError("Add a file.")
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        raise ListError("That file didn't come through. Try again.")
    if len(raw) > limit:
        raise ListError(f"That file is over {limit // (1024 * 1024)} MB.", status=413)
    return raw
