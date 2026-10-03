"""Offcuts on the board: partly used sheets kept for the next run (the geometry is autocam_core/offcuts.py).

- A sheet card with room left gets an "Offcut" box, ticked: "Keep the rest of the sheet for the next run".
  When the card goes to Cut with the box ticked, the service adds an offcut card to the Offcuts list. A sheet
  cut from an offcut updates that offcut's card instead, or archives it once what's left isn't worth loading.
- Each run offers a material's offcuts to its job before new sheets. A sheet card nested onto one reserves
  it and says how to load it. The reservation ends when that sheet is cut, rebuilt onto something else, or
  retired. An open sheet that's rebuilt can keep its offcut.
- An offcut card someone archives or moves out of the Offcuts list is forgotten: the sheet is gone.

    state/offcuts.json   {offcut card id: {"material", "thickness_in", "used": [[a, b], ...],
                          "last": {"label": "r006 S1", "stretch": [a, b]}, "reserved_by": sheet card id or null}}
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


def card_text(stock: str, free_in: float, used: Sequence[Stretch], last_label: str) -> Tuple[str, str]:
    """stock: e.g. "6061 3/16in". The offcut card's title and description."""
    title = f"{stock} offcut - {free_in:.0f} in free"
    used_text = ", ".join(f"{a:.1f} to {b:.1f} in" for a, b in used)
    desc = "\n".join([
        f"A partly used {stock} sheet. Runs put {stock} parts on it before starting a new sheet.",
        f"Last cut: {last_label}. Used along its length: {used_text}.",
        "Archive this card if the sheet is gone.",
    ])
    return title, desc


def load_line(stock: str, url: str, last_label: str, last_stretch: Stretch, turned: bool, sheet_length: float) -> str:
    """The sheet card's Stock line for a nest on an offcut: which end goes where."""
    middle = (last_stretch[0] + last_stretch[1]) / 2
    if turned:
        middle = sheet_length - middle
    end = "at the zero corner (front left, by you)" if middle < sheet_length / 2 else \
        "at the far end (hanging off the bed)"
    return (f"Stock: the {stock} offcut, not a new sheet ({url}). Put it in with the end where {last_label}'s "
            f"parts were cut {end}. Same side up: spin it round flat, don't flip it over. Clamps and zero as usual.")
