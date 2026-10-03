"""The season's stock tally, one line on the System card: what's been cut since status.season_start.

Counts sheet cards that went to Cut: new sheets (by stock, for ordering more) and sheets cut from offcuts,
and how much of the new sheets' area the parts on every cut sheet took. That share only grows as the
offcuts on the shelf get used, so the line says how many there are.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence


@dataclass(frozen=True)
class CutSheet:
    stock: str                          # e.g. "6061 1/8in"
    from_offcut: bool
    parts_area_in2: Optional[float]     # None: not on record (a sheet card from before 2026-10-02)


def season_text(cut: Sequence[CutSheet], since: datetime, sheet_area_in2: float, on_shelf: int) -> str:
    head = f"Stock since {since:%b} {since.day}: "
    if not cut:
        return head + "nothing cut yet."
    new = [c for c in cut if not c.from_offcut]
    reloads = len(cut) - len(new)
    stocks = Counter(c.stock for c in new)
    text = head + f"{len(new)} new sheet{'s' if len(new) != 1 else ''}"
    if stocks:
        text += " (" + ", ".join(f"{stock} x{n}" for stock, n in sorted(stocks.items())) + ")"
    text += f", {reloads} cut from offcuts."
    known = [c.parts_area_in2 for c in cut if c.parts_area_in2 is not None]
    if new and known:
        parts = sum(known)
        text += f" Parts: {parts:.0f} sq in, {100 * parts / (len(new) * sheet_area_in2):.0f}% of the new sheets"
        text += f" ({on_shelf} offcut{'s' if on_shelf != 1 else ''} still on the shelf)." if on_shelf else "."
    missing = len(cut) - len(known)
    if missing:
        text += f" ({missing} cut sheet{'s' if missing != 1 else ''} with no parts area on record.)"
    return text
