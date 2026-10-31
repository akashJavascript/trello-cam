"""Offcuts: a sheet put back on the machine after some of it was cut, for the next nest to use.

The sheet is kept whole. The cut parts leave a skeleton, but the clamped long edges are never cut, so it
clamps and zeros like a new sheet. What's recorded is which stretches along its length are used, in the
sheet's own coordinates: end A is the end that was at the front (by the operator) when it was first cut.
Positions along the length are machine Y.

The next nest goes in the longest free stretch the machine can reach. The sheet can be loaded as before
or turned end for end (end B at the front). Turning it brings the end that hung off the bed onto it,
so a sheet with a short strip used at one end is nearly a whole new sheet the other way round. But spinning
it is a chore, so the nest keeps it the way it was last cut unless spinning fits more parts or saves a new
sheet (the pipeline decides; placement_for gives each way round).

A used stretch is the whole width, but its parts rarely are: Arrange packs from the left edge, so a single
part leaves most of its band empty. That **room beside** the parts (room_beside: from their right edge to
the edge of the nest region, as deep as the band) is kept too, as rectangles in the sheet's own coordinates,
and the next nest fills it first with whatever fits. What a nest leaves of a rectangle it used (to the right
of its new parts) is kept the same way.

A part someone cuts out by hand is recorded the same way (cut_away): its stretch along the length is used, and
the room to its left and right is room beside.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

Stretch = Tuple[float, float]         # along the sheet's length, inches
Rect = Tuple[float, float, float, float]   # x0, y0, x1, y1: X across the sheet, Y along its length, inches


@dataclass(frozen=True)
class Placement:
    turned: bool                      # end B at the front
    x0: float                         # the free stretch, in machine Y (the sheet as loaded)
    x1: float

    @property
    def length(self) -> float:
        return self.x1 - self.x0


def as_loaded(used: Sequence[Stretch], sheet_length: float, turned: bool) -> List[Stretch]:
    """The used stretches in machine Y for a sheet loaded as before (turned=False) or end for end."""
    if not turned:
        return sorted((a, b) for a, b in used)
    return sorted((sheet_length - b, sheet_length - a) for a, b in used)


def free_stretch(used: Sequence[Stretch], sheet_length: float, turned: bool, lo: float, hi: float,
                 gap: float) -> Optional[Stretch]:
    """The longest stretch of [lo, hi] (machine Y) at least `gap` from anything used."""
    blocked = [(a - gap, b + gap) for a, b in as_loaded(used, sheet_length, turned)]
    best: Optional[Stretch] = None
    start = lo
    for a, b in sorted(blocked) + [(hi, hi)]:
        end = min(a, hi)
        if end > start and (best is None or end - start > best[1] - best[0]):
            best = (start, end)
        start = max(start, b)
        if start >= hi:
            break
    return best


def placement_for(used: Sequence[Stretch], sheet_length: float, lo: float, hi: float, gap: float,
                  min_length: float, turned: bool) -> Optional[Placement]:
    """The free stretch with the sheet loaded one particular way round, or None if it's under min_length."""
    s = free_stretch(used, sheet_length, turned, lo, hi, gap)
    return Placement(turned, s[0], s[1]) if s is not None and s[1] - s[0] >= min_length else None


def best_placement(used: Sequence[Stretch], sheet_length: float, lo: float, hi: float, gap: float,
                   min_length: float) -> Optional[Placement]:
    """How to load the sheet for the longest free stretch, or None if neither way leaves min_length.
    Ties keep it the way it was."""
    options = []
    for turned in (False, True):
        s = free_stretch(used, sheet_length, turned, lo, hi, gap)
        if s is not None and s[1] - s[0] >= min_length:
            options.append(Placement(turned, s[0], s[1]))
    return max(options, key=lambda p: p.length) if options else None


def add_used(used: Sequence[Stretch], machine: Stretch, sheet_length: float, turned: bool) -> Tuple[Stretch, ...]:
    """Record a stretch cut with the sheet loaded `turned`, in the sheet's own coordinates; overlaps merged."""
    a, b = machine
    new = (sheet_length - b, sheet_length - a) if turned else (a, b)
    out: List[Stretch] = []
    for s in sorted([(float(x), float(y)) for x, y in used] + [new]):
        if out and s[0] <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], s[1]))
        else:
            out.append(s)
    return tuple((round(x, 3), round(y, 3)) for x, y in out)


def free_length(used: Sequence[Stretch], sheet_length: float, lo: float, hi: float, gap: float) -> float:
    """The longest reachable free stretch, either way round (0 if none)."""
    p = best_placement(used, sheet_length, lo, hi, gap, 0.0)
    return p.length if p else 0.0


def turn_rect(r: Rect, width: float, length: float) -> Rect:
    """A rectangle on the sheet, with the sheet turned end for end (spun round flat, not flipped over)."""
    x0, y0, x1, y1 = r
    return (round(width - x1, 3), round(length - y1, 3), round(width - x0, 3), round(length - y0, 3))


def beside_as_loaded(beside: Sequence[Rect], width: float, length: float, turned: bool, region: Rect,
                     min_side: float) -> List[Tuple[int, Rect]]:
    """The room beside earlier cuts the machine can use with the sheet loaded as before (turned=False) or end for
    end: (index in `beside`, the rectangle in machine X/Y cut down to the nest region), at least min_side both
    ways."""
    out = []
    for i, r in enumerate(beside):
        x0, y0, x1, y1 = turn_rect(r, width, length) if turned else tuple(r)
        x0, y0, x1, y1 = max(x0, region[0]), max(y0, region[1]), min(x1, region[2]), min(y1, region[3])
        if x1 - x0 >= min_side and y1 - y0 >= min_side:
            out.append((i, (x0, y0, x1, y1)))
    return out


def room_beside(boxes: Sequence[Rect], band: Stretch, right: float, margin: float, gap: float,
                min_side: float) -> Optional[Rect]:
    """The room to the right of parts (boxes in sheet coordinates) in a band along the length: from their right
    edge, plus `margin` for the cutter's path and `gap` for loading the sheet back a little off, to `right`
    (the edge of the nest region or of the room they were nested in). None if it's under min_side either way."""
    if not boxes:
        return None
    x0 = max(b[2] for b in boxes) + margin + gap
    if right - x0 < min_side or band[1] - band[0] < min_side:
        return None
    return (round(x0, 3), round(band[0], 3), round(right, 3), round(band[1], 3))


def to_own(r: Rect, width: float, length: float, turned: bool) -> Rect:
    """A rectangle in machine X/Y back in the sheet's own coordinates (end A at the front)."""
    return turn_rect(r, width, length) if turned else tuple(round(v, 3) for v in r)


def rect_minus(r: Rect, cut: Rect) -> List[Rect]:
    """What's left of rectangle r with `cut` taken out: up to four rectangles that don't overlap (the full height
    left and right of the cut, then below and above it)."""
    x0, y0, x1, y1 = r
    c0, d0, c1, d1 = cut
    if c0 >= x1 or c1 <= x0 or d0 >= y1 or d1 <= y0:
        return [tuple(r)]
    out: List[Rect] = []
    if c0 > x0:
        out.append((x0, y0, c0, y1))
    if c1 < x1:
        out.append((c1, y0, x1, y1))
    m0, m1 = max(x0, c0), min(x1, c1)
    if d0 > y0:
        out.append((m0, y0, m1, d0))
    if d1 < y1:
        out.append((m0, d1, m1, y1))
    return out


def cut_away(used: Sequence[Stretch], beside: Sequence[Rect], cut: Rect, sheet_length: float,
             across: Tuple[float, float], margin: float, gap: float,
             min_side: float) -> Tuple[Tuple[Stretch, ...], List[Rect]]:
    """A rectangle cut out of an offcut by hand (x across, y along, in its own coordinates). Its stretch along the
    length, `margin` either side for the next cutter's path, becomes used. The room to its left and right, in
    what was free of that stretch, is kept as room beside: from the edges of the nest region (`across`) to
    `margin` + `gap` clear of the cut, like the room beside AutoCAM's own cuts. Room beside earlier cuts loses
    whatever the cut takes. Rooms under min_side either way go. Each earlier room keeps its place in the list
    (empty if the cut took all of it), so a sheet nested in a room by its index still finds it; the pieces split
    off and the new rooms come after (compact drops the empty ones). Returns (used, beside)."""
    x0, y0, x1, y1 = cut
    a, b = max(0.0, y0 - margin), min(sheet_length, y1 + margin)
    clear = margin + gap
    hole = (x0 - clear, y0 - clear, x1 + clear, y1 + clear)

    def big(r: Rect) -> bool:
        return r[2] - r[0] >= min_side and r[3] - r[1] >= min_side

    keep: List[Rect] = []
    extra: List[Rect] = []
    for r in beside:
        pieces = [piece for piece in rect_minus(tuple(r), hole) if big(piece)]
        keep.append(pieces[0] if pieces else (r[0], r[1], r[0], r[1]))
        extra += pieces[1:]
    free, at = [], a                                # what of a..b no earlier cut used
    for u0, u1 in sorted((float(s[0]), float(s[1])) for s in used):
        if u1 <= at:
            continue
        if u0 >= b:
            break
        if u0 > at:
            free.append((at, u0))
        at = max(at, u1)
    if at < b:
        free.append((at, b))
    for f0, f1 in free:
        extra += [r for r in ((across[0], f0, hole[0], f1), (hole[2], f0, across[1], f1)) if big(r)]
    return add_used(used, (a, b), sheet_length, False), [tuple(round(v, 3) for v in r) for r in keep + extra]


def compact(beside: Sequence[Rect]) -> List[Rect]:
    """The rooms beside without the empty places cut_away keeps."""
    return [tuple(r) for r in beside if r[2] > r[0] and r[3] > r[1]]
