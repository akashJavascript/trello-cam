"""Offcuts on the board: partly used sheets kept for the next run (the geometry is autocam_core/offcuts.py).

- A sheet card with room left gets an "Offcut" box, ticked: "Keep the rest of the sheet for the next run".
  When the card goes to Cut with the box ticked, the service adds an offcut card to the Offcuts list. A sheet
  cut from an offcut updates that offcut's card instead, or archives it once what's left isn't worth loading.
- Each run offers a material's offcuts to its job before new sheets. A sheet card nested onto one reserves
  it and says how to load it. The reservation ends when that sheet is cut, rebuilt onto something else, or
  retired. An open sheet that's rebuilt can keep its offcut.
- An offcut card someone archives or moves out of the Offcuts list is forgotten: the sheet is gone.
- Besides the used stretches, an offcut keeps the room beside the parts cut from them ("beside": rectangles
  in its own coordinates, core 0.5.0), which the next nest fills first. A sheet that used some of that room
  replaces it with what's left of it. An offcut is used up when it has neither a free stretch worth loading
  nor any room beside its cuts.

    state/offcuts.json   {offcut card id: {"material", "thickness_in", "used": [[a, b], ...],
                          "beside": [[x0, y0, x1, y1], ...] (absent before core 0.5.0),
                          "last": {"label": "r006 S1", "stretch": [a, b], "turned": false},
                          "reserved_by": sheet card id or null}}
"""

import json
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from autocam_core.hotfolder import write_atomic
from autocam_core.offcuts import Stretch


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
              beside: Sequence[Sequence[float]] = (), min_free_in: float = 0.0) -> Tuple[str, str]:
    """stock: e.g. "6061 3/16in". The offcut card's title and description."""
    title = f"{stock} offcut - " + (f"{free_in:.0f} in free" if free_in >= min_free_in else "small parts only")
    if beside and free_in >= min_free_in:
        title += " + room for small parts"
    used_text = ", ".join(f"{a:.1f} to {b:.1f} in" for a, b in used)
    lines = [
        f"A partly used {stock} sheet. Runs put {stock} parts on it before starting a new sheet.",
        f"Last cut: {last_label}. Used along its length: {used_text}.",
    ]
    if beside:
        sizes = ", ".join(f"{x1 - x0:.1f} x {y1 - y0:.1f} in" for x0, y0, x1, y1 in beside)
        lines.append(f"Room beside the parts already cut, filled first with parts that fit: {sizes}.")
    lines.append("Archive this card if the sheet is gone.")
    return title, "\n".join(lines)


def load_line(stock: str, url: str, last_label: str, last_stretch: Stretch, turned: bool, sheet_length: float,
              last_turned: bool = False) -> str:
    """The sheet card's Stock line for a nest on an offcut: the same way round as its last cut, or spun round."""
    middle = (last_stretch[0] + last_stretch[1]) / 2
    if turned:
        middle = sheet_length - middle
    end = "at the front (by you)" if middle < sheet_length / 2 else "at the back (hanging off the bed)"
    head = f"Stock: the {stock} offcut, not a new sheet ({url})."
    if turned == last_turned:
        how = f"Put it in the same way round as for {last_label}: the end where its parts were cut {end}."
    else:
        how = (f"Spin it round from how it was for {last_label} (flat, same side up, don't flip it over): the end "
               f"where its parts were cut goes {end}.")
    return f"{head} {how} Clamps and zero as usual."
