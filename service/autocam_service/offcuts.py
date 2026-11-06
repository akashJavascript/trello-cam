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
- A part someone cut out of an offcut by hand is a "Cut by hand: 12 to 18 along, 5 to 10 across" line on its
  card (parse_hand_cuts; across left out = the whole width). The service takes that rectangle out once
  (core cut_away) and writes the line back as it read it; a scrap card can carry such lines from the start.
  Optionally ([hand_cuts] programs) the card can carry the program that cut it instead, with where it was zeroed
  ("Zeroed at: 10 along, 5 across"; handprograms.py): the cut is then worked out from the program.
- Besides the used stretches, an offcut keeps the room beside the parts cut from them ("beside": rectangles
  in its own coordinates, core 0.5.0), which the next nest fills first. A sheet that used some of that room
  replaces it with what's left of it. An offcut is used up when it has neither a free stretch worth loading
  nor any room beside its cuts.

    state/offcuts.json   {offcut card id: {"material", "thickness_in", "used": [[a, b], ...],
                          "beside": [[x0, y0, x1, y1], ...] (absent before core 0.5.0),
                          "last": {"label": "r006 S1", "stretch": [a, b], "turned": false},
                          "reserved_by": sheet card id or null, "number": 4, "missing": false,
                          "scrap": false, "length_in": 48.0,
                          "hand_cuts": [[x0, y0, x1, y1], ...] (across, along; recorded once each)}}
    state/offcut_numbers.json   {"last": 4}         the last number given
    state/offcut_replies.json   {card id: what the card said when the service last answered it}
"""

import json
import re
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, Union

from autocam_core.hotfolder import write_atomic
from autocam_core.offcuts import Rect, Stretch

SCRAP_HELP = ("To add a piece of stock as an offcut, its description needs these lines (inches):\n"
              "Material: 6061\nThickness: 3/16\nLength: 20\n"
              "Length is end to end. It must be the full sheet width (the clamps hold the long edges). If parts "
              "were cut out of it already, add a line for each, like Cut by hand: 12 to 18 along, 5 to 10 across "
              "(along from the end that goes against the front stop; mark that end), or attach the program that "
              "cut it with a line like Zeroed at: 10 along, 5 across (where you zeroed X/Y, measured the same way).")


def hand_cut_help(first_end: str, programs: bool = True) -> str:
    """The offcut card's line on recording a part cut out of it by hand. first_end: where `along` is measured from.
    programs: the card may carry the program that cut it instead ([hand_cuts] programs)."""
    return ("Cut a part out of it by hand? Add a line like: Cut by hand: 12 to 18 along, 5 to 10 across. Inches: "
            f"along from {first_end}, across from the left edge with that end toward you. Leave out \"across\" "
            "if the cut went all the way across."
            + (" Or, if you cut it with your own program, attach the .tap you ran and add a line like Zeroed at: 10 "
               "along, 5 across (where you zeroed X/Y, measured the same way); AutoCAM works out what it cut "
               "(add Cutter: 4 mm if the cutter was bigger than 1/4 in)." if programs else "")
            + " Runs then keep clear of it.")


_SPAN = r"(\d+(?:\.\d*)?|\.\d+)\s*(?:in\b|\"|”)?\s*(?:to|-|–)\s*(\d+(?:\.\d*)?|\.\d+)\s*(?:in\b|\"|”)?\s*"
_HAND_CUT = re.compile(r"^[ \t>\-*]*cut by hand[ \t*]*[:=][ \t]*(.+?)[ \t*]*$", re.IGNORECASE | re.MULTILINE)


def parse_hand_cuts(text: str, width: float, length: float) -> Tuple[List[Rect], List[str]]:
    """The "Cut by hand:" lines in a card's text: (x0, y0, x1, y1) in the piece's own coordinates (across, along;
    no "across" = the whole width), and what's wrong with each line that can't be read."""
    cuts, problems = [], []
    for m in _HAND_CUT.finditer(text):
        said = m.group(1).strip("`* ")
        along = re.search(_SPAN + r"along", said, re.IGNORECASE)
        across = re.search(_SPAN + r"across", said, re.IGNORECASE)
        if not along:
            problems.append(f'"Cut by hand: {said}" has no "... along" part (inches along its length, like '
                            "12 to 18 along).")
            continue
        y0, y1 = sorted((float(along[1]), float(along[2])))
        x0, x1 = sorted((float(across[1]), float(across[2]))) if across else (0.0, width)
        if y1 - y0 <= 0 or x1 - x0 <= 0 or y1 > length + 1e-6 or x1 > width + 1e-6:
            problems.append(f'"Cut by hand: {said}" isn\'t within the {width:g} x {length:g} in piece.')
            continue
        cuts.append((x0, y0, x1, y1))
    return cuts, problems


def hand_cut_line(cut: Sequence[float], width: float) -> str:
    """A recorded hand cut as its card line (parse_hand_cuts reads it back the same)."""
    x0, y0, x1, y1 = cut
    across = "" if x0 <= 1e-6 and x1 >= width - 1e-6 else f", {x0:g} to {x1:g} across"
    return f"Cut by hand: {y0:g} to {y1:g} along{across}"


def same_cut(a: Sequence[float], b: Sequence[float]) -> bool:
    return all(abs(x - y) < 1e-3 for x, y in zip(a, b))


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
              length_in: Optional[float] = None, hand_cuts: Sequence[Sequence[float]] = (),
              width: float = 24.0, programs: bool = True, gap_in: float = 0.0) -> Tuple[str, str]:
    """stock: e.g. "6061 3/16in". The offcut card's title and description. length_in: a scrap's length (the
    rest of the sheet's length is missing, not cut). hand_cuts: parts cut out of it by hand, as recorded.
    gap_in: how far past the earlier cuts later parts start (part spacing + offcut slack), said so it doesn't
    look like a mistake (the user, 2026-11-04)."""
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
    if gap_in:
        lines.append(f"Parts put on it later start about {gap_in:g} in past the earlier cuts, on purpose: the usual "
                     "space between parts, plus a little in case it goes back on slightly differently.")
    if number:
        lines.append(f"Write #{number} on the piece, so whoever cuts next can find it.")
    if beside:
        sizes = ", ".join(f"{x1 - x0:.1f} x {y1 - y0:.1f} in" for x0, y0, x1, y1 in beside)
        lines.append(f"Room beside the parts already cut, filled first with parts that fit: {sizes}.")
    lines += [hand_cut_line(c, width) for c in hand_cuts]
    first_end = ("the end that goes against the front stop (mark it)" if length_in is not None and not last_label
                 else "the end AutoCAM cut first")
    lines.append(hand_cut_help(first_end, programs))
    lines.append("Archive this card if the sheet is gone.")
    return title, "\n".join(lines)


CUT_END_CLEAR_IN = 1.0      # one end's cuts must come this much nearer its edge than the other's to call it the cut end


def cut_end_at_front(used: Sequence[Stretch], last_stretch: Stretch, turned: bool, sheet_length: float) -> bool:
    """Is the offcut's cut end at the front as it's loaded (turned: end B at the front)? The cut end is the one its
    cuts come nearest, from every cut on it: what a person sees on the piece. Not where the last cut sat: r101's cut
    on offcut #4 ran 12.6 to 38.8 in, mostly in the back half, though the earlier cuts reach the front edge and the
    back 9 in are clean (r107's card said "at the back", 2026-11-05). When both ends are cut about as close, the last
    cut's middle decides, as before."""
    if used:
        from_a = min(s[0] for s in used)
        from_b = sheet_length - max(s[1] for s in used)
        if abs(from_a - from_b) >= CUT_END_CLEAR_IN:
            return (from_a < from_b) != turned
    middle = (last_stretch[0] + last_stretch[1]) / 2
    if turned:
        middle = sheet_length - middle
    return middle < sheet_length / 2


def load_line(stock: str, url: str, last_label: str, last_stretch: Stretch, turned: bool, sheet_length: float,
              last_turned: bool = False, number: Optional[int] = None, scrap_length: Optional[float] = None,
              hand_cut: bool = False, gap_in: float = 0.0, used: Sequence[Stretch] = (), still: bool = False) -> str:
    """The sheet card's Stock line for a nest on an offcut: the same way round as its last cut, or spun round.
    scrap_length: a piece shorter than a sheet (always at the front, never turned). hand_cut: it has parts cut
    out by hand (a scrap AutoCAM hasn't cut yet then goes in the end they were measured from first). used: every
    cut stretch on it, in its own coordinates (cut_end_at_front). still: nested as still clamped from its last cut,
    with almost no slack for loading it back ([nest] offcut_bed_gap_in, core 0.31.0)."""
    front = cut_end_at_front(used, last_stretch, turned, sheet_length)
    end = "at the front (by you)" if front else "at the back (hanging off the bed)"
    head = (f"Stock: offcut #{number} ({stock}), not a new sheet ({url})." if number else
            f"Stock: the {stock} offcut, not a new sheet ({url}).")
    if scrap_length is not None:
        how = (f"It's a {scrap_length:.0f} in piece: push it against the front stop, "
               + (f"the end where {last_label}'s parts were cut toward you." if last_label else
                  "the end its hand cuts were measured from (marked) toward you." if hand_cut else "either end first."))
    elif turned == last_turned:
        how = f"Put it in the same way round as for {last_label}: the end where its parts were cut {end}."
    else:
        how = (f"Spin it round from how it was for {last_label} (flat, same side up, don't flip it over): the end "
               f"where its parts were cut goes {end}.")
    if still and gap_in:
        gap = (f" The parts start about {round(gap_in, 2):g} in past the earlier cuts on purpose. It was nested as "
               f"still clamped from {last_label}, with almost no slack for loading it back: if it was taken off since, "
               "line it up exactly as it was.")
    else:
        gap = (f" The parts start about {gap_in:g} in past the earlier cuts on purpose (part spacing, plus a little "
               "in case it sits slightly differently)." if gap_in else "")
    return f"{head} {how} Clamps and zero as usual.{gap}"


def _number(text: str) -> Optional[float]:
    m = re.fullmatch(r"(\d+(?:\.\d*)?|\.\d+)\s*(?:/\s*(\d+))?\s*(?:in|inch|inches|\"|”)?", text.strip().lower())
    if not m:
        return None
    if m.group(2):
        return float(Fraction(int(float(m.group(1))), int(m.group(2))))
    return float(m.group(1))


def parse_scrap(text: str, materials: Mapping, sheet_length,
                resolve) -> Union[Tuple[str, float, float], str]:
    """A hand-made card in Offcuts: (material key, thickness, length) or what's wrong. resolve: hint ->
    (material key or None, problem). sheet_length: a new sheet's length, or material key -> its length (a material
    can have its own sheet size)."""
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
    if callable(sheet_length):
        sheet_length = sheet_length(key)
    n = _number(length)
    if n is None or not 0 < n <= sheet_length + 1e-6:
        return f"Length {length} should be the piece's length in inches, up to {sheet_length:g}."
    return key, stock[0], min(n, sheet_length)
