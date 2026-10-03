"""Offcuts on the board: partly used sheets kept for the next run (the geometry is autocam_core/offcuts.py).

- A sheet card with room left gets an "Offcut" box, ticked: "Keep the rest of the sheet for the next run".
  When the card goes to Cut with the box ticked, the service adds an offcut card to the Offcuts list. A sheet
  cut from an offcut updates that offcut's card instead, or archives it once what's left isn't worth loading.
- Each run offers a material's offcuts to its job before new sheets. A sheet card nested onto one reserves
  it and says how to load it. The reservation ends when that sheet is cut, rebuilt onto something else, or
  retired. An open sheet that's rebuilt can keep its offcut.
- An offcut card someone archives or moves out of the Offcuts list is forgotten: the sheet is gone.
- Every offcut has a number (#4) to write on the piece, so the operator can find the one a sheet card names.
  If it isn't on the rack, the sheet card's Stock checklist says so (runner: the same program on another
  offcut it fits, a new sheet, or a re-nest), and the missing one is archived but kept on record ("missing")
  in case its card is sent back to Offcuts.
- A piece the service didn't make (a "scrap") is added by making a card in Offcuts with Material, Thickness
  and Length lines (parse_scrap). It must be the full width; the missing length counts as used, and it's
  never turned round (it always goes in at the front).
- Besides the used stretches, an offcut keeps the room beside the parts cut from them ("beside": rectangles
  in its own coordinates, core 0.5.0), which the next nest fills first. A sheet that used some of that room
  replaces it with what's left of it. An offcut is used up when it has neither a free stretch worth loading
  nor any room beside its cuts.

    state/offcuts.json   {offcut card id: {"material", "thickness_in", "used": [[a, b], ...],
                          "beside": [[x0, y0, x1, y1], ...] (absent before core 0.5.0),
                          "last": {"label": "r006 S1", "stretch": [a, b], "turned": false},
                          "reserved_by": sheet card id or null, "number": 4, "missing": false,
                          "scrap": false, "length_in": 48.0}}
    state/offcut_numbers.json   {"last": 4}         the last number given
    state/offcut_replies.json   {card id: what the card said when the service last answered it}
"""

import json
import re
from fractions import Fraction
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple, Union

from autocam_core.hotfolder import write_atomic
from autocam_core.offcuts import Stretch

SCRAP_HELP = ("To add a piece of stock as an offcut, its description needs these lines (inches):\n"
              "Material: 6061\nThickness: 3/16\nLength: 20\n"
              "Length is end to end. It must be the full sheet width (the clamps hold the long edges), "
              "with nothing cut out of it yet.")


class OffcutStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def all(self) -> Dict[str, Dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, pieces: Dict[str, Dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, (json.dumps(pieces, indent=1) + "\n").encode("utf-8"))

    def get(self, offcut_id: str) -> Optional[Dict]:
        return self.all().get(offcut_id)

    def put(self, offcut_id: str, piece: Dict) -> None:
        pieces = self.all()
        pieces[offcut_id] = piece
        self._write(pieces)

    def remove(self, offcut_id: str) -> None:
        pieces = self.all()
        if pieces.pop(offcut_id, None) is not None:
            self._write(pieces)

    def reserve(self, offcut_id: str, sheet_card: Optional[str]) -> None:
        pieces = self.all()
        if offcut_id in pieces:
            pieces[offcut_id]["reserved_by"] = sheet_card
            self._write(pieces)

    def next_number(self) -> int:
        """A new offcut number, never given before (counting on from the highest on record)."""
        f = self.path.with_name("offcut_numbers.json")
        try:
            last = int(json.loads(f.read_text(encoding="utf-8"))["last"])
        except (OSError, ValueError, KeyError, TypeError):
            last = 0
        last = max([last] + [int(p.get("number") or 0) for p in self.all().values()]) + 1
        write_atomic(f, (json.dumps({"last": last}) + "\n").encode("utf-8"))
        return last

    def replies(self) -> Dict[str, str]:
        try:
            return dict(json.loads(self.path.with_name("offcut_replies.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}

    def set_reply(self, card_id: str, said_to: str) -> None:
        replies = self.replies()
        replies[card_id] = said_to
        write_atomic(self.path.with_name("offcut_replies.json"), (json.dumps(replies, indent=1) + "\n").encode("utf-8"))

    def release_all(self, sheet_cards: Sequence[str]) -> None:
        pieces = self.all()
        changed = False
        for piece in pieces.values():
            if piece.get("reserved_by") in sheet_cards:
                piece["reserved_by"] = None
                changed = True
        if changed:
            self._write(pieces)


def card_text(stock: str, free_in: float, used: Sequence[Stretch], last_label: str,
              beside: Sequence[Sequence[float]] = (), min_free_in: float = 0.0, number: Optional[int] = None,
              length_in: Optional[float] = None) -> Tuple[str, str]:
    """stock: e.g. "6061 3/16in". The offcut card's title and description. length_in: a scrap's length (the
    rest of the sheet's length is missing, not cut)."""
    title = (f"#{number} - " if number else "") + f"{stock} offcut - " + \
        (f"{free_in:.0f} in free" if free_in >= min_free_in else "small parts only")
    if beside and free_in >= min_free_in:
        title += " + room for small parts"
    if length_in is not None:
        cuts = [(a, min(b, length_in)) for a, b in used if a < length_in - 1e-6]
        lines = [f"A {length_in:.0f} in long piece of {stock}, added by hand. It always goes in pushed to the "
                 "front, never turned round. Runs put parts on it before starting a new sheet.",
                 f"Last cut: {last_label or 'none yet'}."
                 + (" Cut along its length: " + ", ".join(f"{a:.1f} to {b:.1f} in" for a, b in cuts) + "."
                    if cuts else "")]
    else:
        used_text = ", ".join(f"{a:.1f} to {b:.1f} in" for a, b in used)
        lines = [
            f"A partly used {stock} sheet. Runs put {stock} parts on it before starting a new sheet.",
            f"Last cut: {last_label}. Used along its length: {used_text}.",
        ]
    if number:
        lines.append(f"Write #{number} on the piece, so whoever cuts next can find it.")
    if beside:
        sizes = ", ".join(f"{x1 - x0:.1f} x {y1 - y0:.1f} in" for x0, y0, x1, y1 in beside)
        lines.append(f"Room beside the parts already cut, filled first with parts that fit: {sizes}.")
    lines.append("Archive this card if the sheet is gone.")
    return title, "\n".join(lines)


def load_line(stock: str, url: str, last_label: str, last_stretch: Stretch, turned: bool, sheet_length: float,
              last_turned: bool = False, number: Optional[int] = None, scrap_length: Optional[float] = None) -> str:
    """The sheet card's Stock line for a nest on an offcut: the same way round as its last cut, or spun round.
    scrap_length: a piece shorter than a sheet (always at the front, never turned)."""
    middle = (last_stretch[0] + last_stretch[1]) / 2
    if turned:
        middle = sheet_length - middle
    end = "at the front (by you)" if middle < sheet_length / 2 else "at the back (hanging off the bed)"
    head = (f"Stock: offcut #{number} ({stock}), not a new sheet ({url})." if number else
            f"Stock: the {stock} offcut, not a new sheet ({url}).")
    if scrap_length is not None:
        how = (f"It's a {scrap_length:.0f} in piece: push it against the front stop, "
               + (f"the end where {last_label}'s parts were cut toward you." if last_label else "either end first."))
    elif turned == last_turned:
        how = f"Put it in the same way round as for {last_label}: the end where its parts were cut {end}."
    else:
        how = (f"Spin it round from how it was for {last_label} (flat, same side up, don't flip it over): the end "
               f"where its parts were cut goes {end}.")
    return f"{head} {how} Clamps and zero as usual."


def _number(text: str) -> Optional[float]:
    m = re.fullmatch(r"(\d+(?:\.\d*)?|\.\d+)\s*(?:/\s*(\d+))?\s*(?:in|inch|inches|\"|”)?", text.strip().lower())
    if not m:
        return None
    if m.group(2):
        return float(Fraction(int(float(m.group(1))), int(m.group(2))))
    return float(m.group(1))


def parse_scrap(text: str, materials: Mapping, sheet_length: float,
                resolve) -> Union[Tuple[str, float, float], str]:
    """A hand-made card in Offcuts: (material key, thickness, length) or what's wrong. resolve: hint ->
    (material key or None, problem)."""
    def line(name: str) -> Optional[str]:
        m = re.search(rf"^[ \t>\-*]*{name}[ \t*]*[:=][ \t]*(.+?)[ \t*]*$", text, re.IGNORECASE | re.MULTILINE)
        return m.group(1).strip("`* ") if m else None
    material, thickness, length = line("material"), line("thickness"), line("length")
    if not material or not thickness or not length:
        missing = [n for n, v in (("Material", material), ("Thickness", thickness), ("Length", length)) if not v]
        return f"No {' or '.join(missing)} line."
    key, problem = resolve(material)
    if key is None:
        return problem[:1].upper() + problem[1:] + "."
    t = _number(thickness)
    stock = [x for x in materials[key].thicknesses_in if t is not None and abs(x - t) < 0.005]
    if not stock:
        have = ", ".join(f"{x:g}" for x in materials[key].thicknesses_in)
        return f"Thickness {thickness} isn't a stock thickness of {material} (in inches: {have})."
    n = _number(length)
    if n is None or not 0 < n <= sheet_length + 1e-6:
        return f"Length {length} should be the piece's length in inches, up to {sheet_length:g}."
    return key, stock[0], min(n, sheet_length)
