"""Someone's own program, placed by AutoCAM (option 3, the user's choice 2026-11-01; [hand_cuts] place_programs).

A card in Ready for CAM with a .tap attached instead of an Onshape link or a STEP file, plus Material and
Thickness lines (and Cutter: if it's over 1/4 in), is a part someone CAM'd themselves. So is one with a link to
the part and an "Own program:" line (the Onshape panel sends them that way, so the card follows the part). AutoCAM doesn't CAM it: it
finds the program a spot (find_spot: room beside earlier cuts on an offcut, an offcut's free stretch, or a new
sheet; the smallest that fits, the way round the offcut was last cut if it can), makes a copy of the program moved
there (shift_program: its zero becomes the piece's front-left corner, like AutoCAM's own sheets), checks that copy
like any sheet program (clamps, reach, Z floor, rapids), and puts it on a card in Sheet review. When that card
goes to Cut, the cut is recorded on the piece (like a "Cut by hand:" line). The program itself can't be turned, so
its footprint (the box its cutter covers) is placed as it is.

With a "Place it myself" line, AutoCAM doesn't pick a spot: the program goes on a card in Sheet review as it is (the
operator's list, like every sheet), checked for what doesn't depend on where it's cut (codes, units, nothing below
Z0, the park, no sideways rapid in the stock), with the area its cutter covers so the operator can pick a big enough
piece. Nothing is recorded on an offcut (AutoCAM doesn't know which piece it was).
"""

import re
from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence, Tuple

from autocam_core.offcuts import Rect, beside_as_loaded, placement_for, turn_rect
from autocam_core.tapguard import parse_code

from .handprograms import PROGRAM_SUFFIXES

SUFFIX = "_PLACED"
OWN_PROGRAM_RE = re.compile(r"^[ \t>\-*]*own[ \t]*program[ \t*]*[:=]", re.IGNORECASE | re.MULTILINE)
# "Place it myself": no spot picked; the program as it is goes in the operator's list (Sheet review), zeroed on whatever
# piece they choose (the user's ask, 2026-11-01)
PLACE_MYSELF_RE = re.compile(r"^[ \t>\-*]*place[ \t]+it[ \t]+myself\b", re.IGNORECASE | re.MULTILINE)
_THICKNESS = re.compile(r"^[ \t>\-*]*thickness[ \t*]*[:=][ \t]*(.+?)[ \t*]*$", re.IGNORECASE | re.MULTILINE)
_XY = re.compile(r"(?<![A-Z])([XY])( *)([+-]?(?:\d+\.?\d*|\.\d+))")
_LINE = re.compile(r"([^\r\n]*)(\r\n|\n|$)")


def program_attachments(card) -> list:
    return [a for a in card.attachments if a.name.lower().endswith(PROGRAM_SUFFIXES)]


def placed_name(program: str, label: str) -> str:
    """P-3001.tap, H4 -> P-3001_H4_PLACED.tap (letters, digits, - and _ only)."""
    stem = program.rsplit(".", 1)[0]
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "program"
    return f"{stem}_{label}{SUFFIX}.tap"


def _number(value: float) -> str:
    text = f"{value:.4f}".rstrip("0")
    return "0." if text in ("-0.", "0.") else text


def _shift_code(code: str, dx: float, dy: float) -> str:
    return _XY.sub(lambda m: f"{m.group(1)}{m.group(2)}{_number(float(m.group(3)) + (dx if m.group(1) == 'X' else dy))}",
                   code)


def _shift_line(line: str, dx: float, dy: float) -> str:
    if not line.strip():
        return line
    try:
        words = parse_code(line)
    except ValueError:
        return line                                  # the check rejects it either way
    gs = {v for letter, v in words if letter == "G"}
    if 53.0 in gs or 4.0 in gs:
        return line                                  # machine coordinates, or a dwell's X (seconds)
    parts = re.split(r"(\[[^\]]*\])", line)          # leave comments as they are
    return "".join(p if p.startswith("[") else _shift_code(p, dx, dy) for p in parts)


def shift_program(text: str, dx: float, dy: float, note: str = "") -> str:
    """Every X and Y moved by (dx, dy), line endings kept (arc centers I/J are relative: unchanged; G53 lines and
    a G4 dwell's X are left alone). `note` goes in as a comment after line 1."""
    out = []
    for i, m in enumerate(_LINE.finditer(text)):
        line, end = m.group(1), m.group(2)
        if not line and not end:
            break
        out.append(_shift_line(line, dx, dy) + end)
        if i == 0 and note:
            out.append(f"[{note}]{end or chr(13) + chr(10)}")
    return "".join(out)


def parse_thickness(text: str, stock: Sequence[float], number) -> Tuple[Optional[float], Optional[str]]:
    m = _THICKNESS.search(text)
    if not m:
        return None, "No Thickness line. Add one like Thickness: 3/16 (the plate it's cut from)."
    t = number(m.group(1).strip("`* "))
    found = [x for x in stock if t is not None and abs(x - t) < 0.005]
    if not found:
        have = ", ".join(f"{x:g}" for x in stock)
        return None, f"Thickness {m.group(1)} isn't one of the stock thicknesses (in inches: {have})."
    return found[0], None


@dataclass(frozen=True)
class Spot:
    offcut_id: Optional[str]         # None: a new sheet
    turned: bool                     # the offcut loaded end for end from its own coordinates
    dx: float                        # added to the program's X/Y: its zero goes here on the piece (as loaded)
    dy: float
    cut_loaded: Rect                 # what the cutter covers, the piece as loaded (X across, Y along from the front)
    cut_own: Rect                    # ...in the piece's own coordinates (across, along from its end A)
    room: Optional[int] = None       # which room beside earlier cuts it went in (None: the free stretch)


def find_spot(box: Rect, offcuts: Mapping[str, Mapping], region: Rect, width: float, length: float,
              gap: float) -> Optional[Spot]:
    """Where a program's footprint `box` (its own X/Y) goes: the smallest room beside earlier cuts or free stretch
    on one of `offcuts` (pieces of the right stock, free to use: id -> record) that it fits, the way round the
    offcut was last cut first, else a new sheet's front-left corner; None if it doesn't fit a sheet."""
    w, h = box[2] - box[0], box[3] - box[1]
    best: Optional[Tuple[Tuple, Spot]] = None
    for offcut_id, piece in offcuts.items():
        last = bool((piece.get("last") or {}).get("turned", False))
        used = [tuple(s) for s in piece.get("used", [])]
        for turned in ((last,) if piece.get("scrap") else (last, not last)):
            rects: List[Tuple[Optional[int], Rect]] = list(beside_as_loaded(
                [tuple(r) for r in piece.get("beside", [])], width, length, turned, region, 0.0))
            stretch = placement_for(used, length, region[1], region[3], gap, 0.0, turned)
            if stretch is not None:
                rects.append((None, (region[0], stretch.x0, region[2], stretch.x1)))
            for room, r in rects:
                if r[2] - r[0] + 1e-9 < w or r[3] - r[1] + 1e-9 < h:
                    continue
                rank = (turned != last, (r[2] - r[0]) * (r[3] - r[1]))
                if best is None or rank < best[0]:
                    best = (rank, _spot(offcut_id, turned, room, r, box, width, length))
    if best is not None:
        return best[1]
    if region[2] - region[0] + 1e-9 < w or region[3] - region[1] + 1e-9 < h:
        return None
    return _spot(None, False, None, region, box, width, length)


def _spot(offcut_id, turned, room, r: Rect, box: Rect, width: float, length: float) -> Spot:
    dx, dy = round(r[0] - box[0], 4), round(r[1] - box[1], 4)
    loaded = tuple(round(v, 3) for v in (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy))
    own = turn_rect(loaded, width, length) if turned else loaded
    return Spot(offcut_id, turned, dx, dy, loaded, tuple(own), room)
