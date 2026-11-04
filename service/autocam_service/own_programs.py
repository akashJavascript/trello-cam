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

**Quantity** (2026-11-03: part_509.tap was sent with qty 2 and one was placed): a "Qty: 2" line (the panel writes
one; none means 1). As many copies as fit go side by side on one piece, in a grid part_spacing apart, as one program
that cuts the moved program once per copy (repeat_program: everything up to the first move once, the moves once per
copy with a G53 Z retract between, the end once), on one card. The most copies that fit an offcut go there first,
the rest on more offcuts or new sheets, each piece its own card (H1, H2...). A program whose moves lean on a mode set
before them (its first move without G0/G1, or its first feed move without an F) is never repeated: one copy per
card. All the copies are planned and checked before any card is made.

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

from .cards import MAX_QTY, QTY_LINE_RES, TITLE_QTY_RE, _plain
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


def own_qty(name: str, desc: str) -> Tuple[int, Optional[str]]:
    """How many to cut: a Qty line (or "x2" in the title), the same way as a part card's; none means 1."""
    found = [v.strip() for rx in QTY_LINE_RES for v in rx.findall(_plain(desc))]
    m = TITLE_QTY_RE.match(" ".join((name or "").split()))
    if m and m["name"]:
        found.append(m["qty"])
    if not found:
        return 1, None
    if len(set(found)) > 1:
        return 0, f"Two different quantities ({', '.join(sorted(set(found)))}). Keep one."
    if not found[0].isdigit() or not 1 <= int(found[0]) <= MAX_QTY:
        return 0, f"Quantity {found[0]} isn't a whole number from 1 to {MAX_QTY}."
    return int(found[0]), None


def _moves(line: str) -> Optional[List[Tuple[str, Optional[float]]]]:
    """The words of a line that moves the cutter (X/Y/Z, not G53 or a dwell), else None."""
    try:
        words = parse_code(line)
    except ValueError:
        return None
    gs = {v for letter, v in words if letter == "G"}
    if 53.0 in gs or 4.0 in gs or not any(letter in "XYZ" for letter, _ in words):
        return None
    return words


def repeat_program(text: str, offsets: Sequence[Tuple[float, float]], note: str = "") -> Optional[str]:
    """The program cut once at each (dx, dy): everything before its first move once, its moves once per copy (moved
    there, with "[COPY n OF k]" and a G53 Z retract before each copy but the first), everything after its last move
    once. None if the moves can't be repeated as they are: the first move has no G0/G1 of its own, the first feed
    move has no F, or a drilling cycle is still on after the last move."""
    lines = [(m.group(1), m.group(2)) for m in _LINE.finditer(text) if m.group(1) or m.group(2)]
    moving = [i for i, (line, _) in enumerate(lines) if _moves(line) is not None]
    if not moving:
        return None
    first, last = moving[0], moving[-1]
    mode: Optional[float] = None
    fed = False
    for i in range(first, last + 1):
        try:
            words = parse_code(lines[i][0])
        except ValueError:
            return None
        for letter, v in words:
            if letter == "G" and v in (0.0, 1.0, 2.0, 3.0, 73.0, 81.0, 82.0, 83.0):
                mode = v
            elif letter == "G" and v == 80.0:
                mode = None if mode in (73.0, 81.0, 82.0, 83.0) else mode
        if i == first and mode not in (0.0, 1.0):
            return None
        if not fed and mode not in (None, 0.0) and _moves(lines[i][0]) is not None:
            if not any(letter == "F" for letter, _ in words):
                return None
            fed = True
    if mode in (73.0, 81.0, 82.0, 83.0):
        return None
    end = lines[0][1] or "\r\n"
    out: List[str] = []
    dx0, dy0 = offsets[0]
    for i, (line, nl) in enumerate(lines[:first]):
        out.append(_shift_line(line, dx0, dy0) + nl)
        if i == 0 and note:
            out.append(f"[{note}]{nl or end}")
    for n, (dx, dy) in enumerate(offsets, 1):
        if n > 1:
            out += [f"[COPY {n} OF {len(offsets)}]{end}", f"G53 Z{end}"]
        out += [_shift_line(line, dx, dy) + (nl or end) for line, nl in lines[first:last + 1]]
    out += [line + nl for line, nl in lines[last + 1:]]
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


@dataclass(frozen=True)
class Group:
    """Copies of a program side by side on one piece."""
    spot: Spot                                   # the block of copies (its dx/dy: the first copy's)
    offsets: Tuple[Tuple[float, float], ...]     # each copy's (dx, dy)
    cuts_loaded: Tuple[Rect, ...]                # what each copy's cutter covers, the piece as loaded
    cuts_own: Tuple[Rect, ...]                   # ...in the piece's own coordinates


def pack_copies(box: Rect, n: int, offcuts: Mapping[str, Mapping], region: Rect, width: float, length: float,
                gap: float, spacing: float, repeat: bool = True) -> Optional[Group]:
    """As many of n copies of a program (footprint `box`) as fit in one spot, in a grid `spacing` apart: the most
    that fit an offcut first (the grid that uses the least length), else a new sheet. One copy only if `repeat` is
    False. None if one copy doesn't fit a sheet."""
    w, h = box[2] - box[0], box[3] - box[1]
    for pool, offcut_only in ((offcuts, True), ({}, False)):
        if offcut_only and not pool:
            continue
        for k in range(n if repeat else 1, 0, -1):
            best: Optional[Tuple[Tuple[float, float], int, Spot]] = None
            for cols in range(1, k + 1):
                rows = -(-k // cols)
                if (rows - 1) * cols >= k:
                    continue                                     # an empty row: the same block as fewer columns
                block = (box[0], box[1], box[0] + cols * w + (cols - 1) * spacing,
                         box[1] + rows * h + (rows - 1) * spacing)
                spot = find_spot(block, pool, region, width, length, gap)
                if spot is None or (offcut_only and spot.offcut_id is None):
                    continue
                rank = (block[3] - block[1], block[2] - block[0])
                if best is None or rank < best[0]:
                    best = (rank, cols, spot)
            if best is not None:
                _, cols, spot = best
                offsets, loaded, own = [], [], []
                for c in range(k):
                    ox, oy = (c % cols) * (w + spacing), (c // cols) * (h + spacing)
                    offsets.append((round(spot.dx + ox, 4), round(spot.dy + oy, 4)))
                    r = tuple(round(v, 3) for v in (spot.cut_loaded[0] + ox, spot.cut_loaded[1] + oy,
                                                    spot.cut_loaded[0] + ox + w, spot.cut_loaded[1] + oy + h))
                    loaded.append(r)
                    own.append(tuple(turn_rect(r, width, length)) if spot.turned else r)
                return Group(spot, tuple(offsets), tuple(loaded), tuple(own))
    return None


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
