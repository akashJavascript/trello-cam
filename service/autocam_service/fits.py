"""Fit series (core 0.16.0; the user's choice 2026-11-01): a part card's "Fit:" line, the stocks each run leaves,
and what was learned from the sheet cards' Fit checklists.

    Fit: 1.125 hole       the part's bored holes of that size (within holes.bearing_tol_in)
    Fit: outline          its outline (+ stock: the part bigger)

The sheet's program leaves `start` of radial stock on that feature, and each lap program after it leaves step less
([fit] laps of them). Once someone ticks which run fit on a sheet card, that stock is kept (state/fits.json, per
material and feature: a hole size, or the outline of that part), and the next series is centred on it: the
program two steps tight, the laps from one step tight to two steps loose.
"""

import json
import re
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from autocam_core.hotfolder import write_atomic

_FIT = re.compile(r"^[ \t>\-*]*fit[ \t*]*[:=][ \t]*(.+?)[ \t*]*$", re.IGNORECASE | re.MULTILINE)
_SIZE = re.compile(r"(\d+(?:\.\d*)?|\.\d+)")

Fit = Tuple[str, float]              # ("hole", diameter) or ("outline", 0.0)


def parse_fit(text: str) -> Union[None, Fit, str]:
    """The card's Fit: line as (kind, diameter), None if it has none, or what's wrong with it."""
    found = [m.group(1).strip("`* ") for m in _FIT.finditer(text or "")]
    if not found:
        return None
    if len(found) > 1:
        return "More than one Fit: line. Keep one (one feature per part)."
    said = found[0].lower()
    if "outline" in said:
        return ("outline", 0.0)
    m = _SIZE.search(said)
    if m and float(m.group(1)) > 0:
        return ("hole", float(m.group(1)))
    return f'"Fit: {found[0]}" isn\'t a fit AutoCAM knows: write Fit: 1.125 hole (that hole size) or Fit: outline.'


def key(material: str, fit: Fit, part_name: str) -> str:
    kind, d = fit
    return f"{material}|hole|{d:.4f}" if kind == "hole" else f"{material}|outline|{' '.join(part_name.split()).lower()}"


def feature(fit: Fit) -> str:
    return f"{fit[1]:g} hole" if fit[0] == "hole" else "outline"


def stocks(start_in: float, step_in: float, laps: int, learned: Optional[float] = None) -> Tuple[float, ...]:
    """The radial stock each run leaves: the sheet's program first, then each lap."""
    first = learned + 2 * step_in if learned is not None else start_in
    return tuple(round(first - k * step_in, 4) for k in range(laps + 1))


class FitStore:
    """state/fits.json: {key: {"stock_in": the stock that fit, "from": the sheet card it came from}}."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def all(self) -> Dict[str, Dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def learned(self, k: str) -> Optional[float]:
        entry = self.all().get(k)
        return float(entry["stock_in"]) if entry else None

    def record(self, k: str, stock_in: float, source: str) -> None:
        data = self.all()
        data[k] = {"stock_in": stock_in, "from": source}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, (json.dumps(data, indent=1) + "\n").encode("utf-8"))
