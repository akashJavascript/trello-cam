"""A part cut out of an offcut with someone's own program, recorded from that program (optional; the other way is
a typed "Cut by hand:" rectangle, offcuts.py). Decided 2026-11-01.

People zero X/Y on the piece wherever they want to cut, so a program says what it cut but not where. The card says
where: the .tap they ran is attached, and a line gives the spot they zeroed, measured like a "Cut by hand:" line
(inches: along from the end the card measures from, across from the left edge with that end toward you):

    Zeroed at: 10 along, 5 across
    Zeroed at: 10 along, 5 across for P-3001.tap      (when the card has more than one new program)
    Cutter: 4 mm                                      (optional: the cutter's diameter; [hand_cuts] cutter_in if not)

The cut recorded is the box the cutter covered below the stock top (its center's path grown by its radius), moved
to that spot and cut down to the piece. From there it's the same as a typed line (core offcuts.cut_away).
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from autocam_core.offcuts import Rect
from autocam_core.tapguard import parse_code
from autocam_core.toolpoints import cutting_points

PROGRAM_SUFFIXES = (".tap", ".nc")
MM_PER_IN = 25.4

_ZEROED = re.compile(r"^[ \t>\-*]*zeroed at[ \t*]*[:=][ \t]*(.+?)[ \t*]*$", re.IGNORECASE | re.MULTILINE)
_CUTTER = re.compile(r"^[ \t>\-*]*cutter[ \t*]*[:=][ \t]*(.+?)[ \t*]*$", re.IGNORECASE | re.MULTILINE)
_NUM = r"(\d+(?:\.\d*)?|\.\d+)"
_ALONG = re.compile(_NUM + r"\s*(?:in\b|\"|”)?\s*along", re.IGNORECASE)
_ACROSS = re.compile(_NUM + r"\s*(?:in\b|\"|”)?\s*across", re.IGNORECASE)
_FOR = re.compile(r"\bfor\s+(\S+)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Zero:
    along: float
    across: float
    program: Optional[str]           # the program it's for, if the line names one
    said: str


@dataclass(frozen=True)
class ProgramCut:
    program: str                     # the attachment's file name
    cut: Rect                        # (across, along) x 2 in the piece's own coordinates
    note: str = ""                   # anything worth saying about the program (not a problem)


def is_program(name: str) -> bool:
    return name.lower().endswith(PROGRAM_SUFFIXES)


def parse_zeroes(text: str) -> Tuple[List[Zero], List[str]]:
    zeroes, problems = [], []
    for m in _ZEROED.finditer(text):
        said = m.group(1).strip("`* ")
        name = _FOR.search(said)
        spot = said[:name.start()] if name else said
        along, across = _ALONG.search(spot), _ACROSS.search(spot)
        if not along or not across:
            problems.append(f'"Zeroed at: {said}" needs both parts, like Zeroed at: 10 along, 5 across (inches, '
                            "where you zeroed X/Y).")
            continue
        zeroes.append(Zero(float(along[1]), float(across[1]), name[1].strip("`*") if name else None, said))
    return zeroes, problems


def parse_cutter(text: str) -> Tuple[Optional[float], List[str]]:
    """The Cutter: line's diameter in inches (4 mm, 1/8, 0.25 in), or None if there's no line."""
    m = _CUTTER.search(text)
    if not m:
        return None, []
    said = m.group(1).strip("`* ").lower()
    mm = re.fullmatch(_NUM + r"\s*mm", said)
    inch = re.fullmatch(r"(\d+)\s*/\s*(\d+)\s*(?:in|inch|\"|”)?|" + _NUM + r"\s*(?:in|inch|\"|”)?", said)
    if mm:
        d = float(mm[1]) / MM_PER_IN
    elif inch and inch[1]:
        d = int(inch[1]) / int(inch[2])
    elif inch:
        d = float(inch[3])
    else:
        return None, [f'"Cutter: {m.group(1)}" isn\'t a size AutoCAM can read (like Cutter: 4 mm or Cutter: 1/8).']
    if not 0 < d <= 1.0:
        return None, [f'"Cutter: {m.group(1)}" isn\'t a cutter size.']
    return d, []


def footprint(data: bytes, thickness_in: float, cutter_in: float) -> Tuple[Optional[Rect], List[str], str]:
    """The box the cutter covers below the stock top, in the program's own X/Y: (box or None, problems, a note).
    Programs with Z0 on the spoilboard (the tool setter's, as AutoCAM's) cut below the stock thickness; one with
    Z0 on top of the stock cuts below 0, and is read that way."""
    text = data.decode("ascii", errors="replace")
    units = set()
    for line in text.splitlines():
        try:
            words = parse_code(line)
        except ValueError:
            continue
        units |= {int(v) for letter, v in words if letter == "G" and v in (20.0, 21.0)}
    if 21 in units:
        return None, ["it's in millimeters (G21); AutoCAM reads inch programs (G20) only"], ""
    moves = cutting_points(text, float("inf"))          # every feed move
    if not moves:
        return None, ["it has no cutting moves"], ""
    lowest = min(z for _, _, _, z in moves)
    on_top = lowest < -1e-4
    below = 0.0 if on_top else thickness_in
    pts = [(x, y) for _, x, y, z in cutting_points(text, below - 1e-4)]
    if not pts:
        return None, [f"it never goes below the top of a {thickness_in:g} in piece"], ""
    r = cutter_in / 2
    box = (min(x for x, _ in pts) - r, min(y for _, y in pts) - r, max(x for x, _ in pts) + r,
           max(y for _, y in pts) + r)
    note = (f"its Z0 was on top of the stock (lowest Z {lowest:.4f}); AutoCAM's programs zero Z on the spoilboard"
            if on_top else "")
    return box, [], note


def placed(box: Rect, zero: Zero, width: float, length: float) -> Optional[Rect]:
    """The program's box moved to where it was zeroed (X across, Y along), cut down to the piece; None if that
    misses the piece."""
    x0, y0, x1, y1 = (box[0] + zero.across, box[1] + zero.along, box[2] + zero.across, box[3] + zero.along)
    x0, y0, x1, y1 = max(x0, 0.0), max(y0, 0.0), min(x1, width), min(y1, length)
    if x1 - x0 <= 1e-6 or y1 - y0 <= 1e-6:
        return None
    return tuple(round(v, 2) for v in (x0, y0, x1, y1))


def program_cuts(text: str, programs: Sequence[Tuple[str, bytes]], thickness_in: float, width: float,
                 length: float, default_cutter_in: float) -> Tuple[List[ProgramCut], List[str]]:
    """programs: (file name, bytes) of the card's programs not recorded yet. Each needs a Zeroed at: line; with
    one program a line without a name is its, with more each line names its program."""
    zeroes, problems = parse_zeroes(text)
    cutter, cutter_problems = parse_cutter(text)
    problems += cutter_problems
    cutter = cutter or default_cutter_in
    named = {z.program.lower(): z for z in zeroes if z.program}
    plain = [z for z in zeroes if not z.program]
    out: List[ProgramCut] = []
    for name, data in programs:
        zero = named.get(name.lower())
        if zero is None and len(programs) == 1 and len(plain) == 1:
            zero = plain[0]
        if zero is None:
            if len(programs) > 1 and plain:
                problems.append(f"{name}: with {len(programs)} programs on the card, name each one's Zeroed at "
                                f"line, like Zeroed at: 10 along, 5 across for {name}.")
            elif not zeroes:
                problems.append(f"{name}: add a line like Zeroed at: 10 along, 5 across (inches: where you zeroed "
                                "X/Y, measured like a Cut by hand line) so AutoCAM knows where it cut.")
            continue
        box, why, note = footprint(data, thickness_in, cutter)
        if box is None:
            problems.append(f"{name}: " + "; ".join(why) + ".")
            continue
        cut = placed(box, zero, width, length)
        if cut is None:
            problems.append(f"{name}: zeroed at {zero.along:g} along, {zero.across:g} across, it would have cut "
                            "off the piece; check the numbers.")
            continue
        out.append(ProgramCut(name, cut, note))
    return out, problems
