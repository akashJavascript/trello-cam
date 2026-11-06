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

Since 2026-11-03 (the user: keep any empty corner, not just the room to the right) the rooms are every empty
rectangle among the parts (free_rooms): in front of a short part, behind it, between two parts, as well as to the
right. They never overlap, so each gets its own Arrange and parts in two rooms can't meet. A part's bounding box
counts as used, so the room inside a part's own notches isn't kept.

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


def reach_clean(rooms: Sequence[Tuple[int, Rect]], used: Sequence[Stretch], stretch: Optional[Placement],
                region: Rect, gap: float, sheet_length: float) -> List[Tuple[int, Rect]]:
    """Rooms beside earlier cuts (as loaded, from beside_as_loaded) that run to the end of the used stretch they're
    in reach on into the clean sheet past it: up to the next used stretch (less the gap) or the nest region's end.
    Not where that clean strip is this way round's free stretch (it's offered whole, full width, in its own
    Arrange). The room was cut off at the end of the cut it's beside, though nothing was cut past it (offcut #4,
    2026-11-05: a room 26.1 in long beside a 25.12 in p0209 had 0.75 in more clean sheet behind it, and a second
    p0209 didn't fit until it was counted). used: the used stretches as loaded (as_loaded)."""
    def blocked(a: float, b: float) -> bool:
        return stretch is not None and stretch.x0 < b - 1e-9 and stretch.x1 > a + 1e-9
    out: List[Tuple[int, Rect]] = []
    for i, (x0, y0, x1, y1) in rooms:
        band = next((u for u in used if u[0] - 1e-6 <= y0 and y1 <= u[1] + 1e-6), None)
        if band is not None:
            if abs(y1 - band[1]) < 0.01:
                after = [u[0] for u in used if u[0] > band[1] + 1e-9]
                end = min(min(after) - gap if after else sheet_length, region[3])
                if end > y1 + 1e-6 and not blocked(band[1], end):
                    y1 = round(end, 3)
            if abs(y0 - band[0]) < 0.01:
                before = [u[1] for u in used if u[1] < band[0] - 1e-9]
                start = max(max(before) + gap if before else 0.0, region[1])
                if start < y0 - 1e-6 and not blocked(start, band[0]):
                    y0 = round(start, 3)
        out.append((i, (x0, y0, x1, y1)))
    return out


EDGE_TOL_IN = 0.01                    # an area's edge this close to the nest region's is on it


def inset_area(area: Rect, region: Rect, spacing: float, beside_cut: float) -> Rect:
    """Where Arrange may put parts in an area (machine X/Y: a free stretch, a room beside earlier cuts, a new sheet's
    nest region): an edge on the nest region's edge comes in by the part spacing, as on a new sheet; any other edge is
    next to earlier cuts and comes in only by beside_cut (2026-11-06: next to a hole already cut, the new part needs
    room for its cutter, not the strip between two parts that holds their tabs). beside_cut is at least half the part
    spacing, so parts in two areas that meet still end up a part spacing apart."""
    x0, y0, x1, y1 = area
    return (x0 + (spacing if x0 <= region[0] + EDGE_TOL_IN else beside_cut),
            y0 + (spacing if y0 <= region[1] + EDGE_TOL_IN else beside_cut),
            x1 - (spacing if x1 >= region[2] - EDGE_TOL_IN else beside_cut),
            y1 - (spacing if y1 >= region[3] - EDGE_TOL_IN else beside_cut))


def grow_rooms(rooms: Sequence[Tuple[int, Rect]], by: float, region: Rect,
               used: Sequence[Stretch]) -> List[Tuple[int, Rect]]:
    """Rooms beside earlier cuts (as loaded) for an offcut still clamped the way it was cut: the rooms were kept the
    loading slack (`by`, the offcut gap) clear of the parts, in case the piece went back on a little differently; it
    didn't, so each edge next to the parts moves that much closer to them (2026-11-06). Not an edge on the nest
    region's edge, nor one on the end of a used stretch (past it is clean sheet, the free stretch or another band, which
    keep their own clearance), nor one that would then overlap another room. used: the used stretches as loaded."""
    out = [(i, tuple(r)) for i, r in rooms]
    ends = [y for u in used for y in u]

    def on_end(y: float) -> bool:
        return any(abs(y - e) < EDGE_TOL_IN for e in ends)

    def overlaps(a: Rect, b: Rect) -> bool:
        return a[0] < b[2] - 1e-9 and b[0] < a[2] - 1e-9 and a[1] < b[3] - 1e-9 and b[1] < a[3] - 1e-9

    for n, (i, r) in enumerate(out):
        x0, y0, x1, y1 = r
        tries = [(0, x0 - by, x0 > region[0] + EDGE_TOL_IN), (2, x1 + by, x1 < region[2] - EDGE_TOL_IN),
                 (1, y0 - by, y0 > region[1] + EDGE_TOL_IN and not on_end(y0)),
                 (3, y1 + by, y1 < region[3] - EDGE_TOL_IN and not on_end(y1))]
        grown = [x0, y0, x1, y1]
        for side, value, ok in tries:
            if not ok:
                continue
            edge = min(value, region[side]) if side >= 2 else max(value, region[side])
            trial = list(grown)
            trial[side] = edge
            if not any(overlaps(tuple(trial), other) for m, (_, other) in enumerate(out) if m != n):
                grown = trial
        out[n] = (i, tuple(round(v, 3) for v in grown))
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


MOST_ROOMS = 6                        # rooms kept per band (each is one more Arrange in Fusion on the next nest)


def free_rooms(boxes: Sequence[Rect], area: Rect, clear: float, min_side: float, most: int = MOST_ROOMS) -> List[Rect]:
    """The empty rectangles in `area` (sheet coordinates: a band the parts were nested in, or a room they went in),
    at least `clear` from every part's box (the cutter's path and the loading slack, like room_beside), biggest
    first, never overlapping, each at least min_side both ways; at most `most`. Greedy: the biggest empty rectangle,
    then the biggest one left beside it, and so on."""
    if not boxes:
        return []
    ax0, ay0, ax1, ay1 = area
    blocked = [(max(ax0, b[0] - clear), max(ay0, b[1] - clear), min(ax1, b[2] + clear), min(ay1, b[3] + clear))
               for b in boxes]
    blocked = [b for b in blocked if b[2] > b[0] and b[3] > b[1]]
    rooms: List[Rect] = []
    while len(rooms) < most:
        best = _biggest_empty(area, blocked + rooms, min_side)
        if best is None:
            break
        rooms.append(best)
    return [tuple(round(v, 3) for v in r) for r in rooms]


def _biggest_empty(area: Rect, blocked: Sequence[Rect], min_side: float) -> Optional[Rect]:
    """The biggest rectangle in `area` overlapping nothing in `blocked`, at least min_side both ways. Its top and
    bottom are at the area's or a blocked rectangle's edges, so trying every such pair of Ys, with the widest gap
    across between what blocks that band, finds it."""
    ax0, ay0, ax1, ay1 = area
    ys = sorted({ay0, ay1} | {y for b in blocked for y in (b[1], b[3]) if ay0 < y < ay1})
    best: Optional[Rect] = None
    best_area = 0.0
    for i, y0 in enumerate(ys):
        for y1 in ys[i + 1:]:
            if y1 - y0 < min_side - 1e-9:
                continue
            cuts = sorted((b[0], b[2]) for b in blocked if b[1] < y1 - 1e-9 and b[3] > y0 + 1e-9)
            x = ax0
            for c0, c1 in cuts + [(ax1, ax1)]:
                g0, g1 = x, min(c0, ax1)
                if g1 - g0 >= min_side - 1e-9 and (g1 - g0) * (y1 - y0) > best_area + 1e-9:
                    best, best_area = (g0, y0, g1, y1), (g1 - g0) * (y1 - y0)
                x = max(x, c1)
    return best


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
