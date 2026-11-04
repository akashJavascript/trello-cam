"""Pictures drawn from the programs themselves (2026-11-04, the user's picks):

- toolpath_png, on a sheet card: what the program the machine runs really cuts, on the stock as it's loaded (front
  at the bottom): through cuts dark, anything that stops above the spoilboard orange (tabs, ramps, pockets), the
  small-tool program teal, drilled holes as dots, each part's outline numbered in cut order (the card's CUT
  ORDER), the clamp strips and how far the machine reaches; on an offcut, what was cut from it before in grey.
  A missing hole, an outline that doesn't go through, or a part on top of an earlier cut shows before the cut.
- offcut_png, on an offcut card: what the piece should look like, in its own coordinates (end A at the bottom):
  every program cut on it, parts cut out by hand, and the room that's left (the free stretch and the room beside
  the cuts, with their sizes), so it can be matched to the piece on the rack.

Pillow (a service dependency, as for labels.py); the callers never let a picture stop a card.
"""

import io
import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from autocam_core.toolpath import Move, Program

from .labels import _font

Rect = Tuple[float, float, float, float]

PAPER = (255, 255, 255)
STOCK = (236, 233, 228)
STOCK_EDGE = (110, 104, 98)
HATCH = (190, 182, 172)
INK = (28, 27, 26)
MUTED = (84, 67, 59)
THROUGH = (23, 50, 77)
PARTIAL = (224, 123, 31)
SMALL = (15, 139, 141)
EARLIER = (150, 144, 137)
HAND = (196, 85, 59)
FREE = (47, 125, 90)
FREE_FILL = (214, 238, 221)
BADGE = (255, 212, 0)
THROUGH_Z = 0.01                 # a cut this close to the spoilboard (Z0) goes through


@dataclass(frozen=True)
class _Frame:
    left: float
    top: float
    scale: float                 # pixels per inch
    width: float                 # the stock, inches: across (X)
    length: float                # along (Y)

    def pt(self, x: float, y: float) -> Tuple[float, float]:
        return self.left + x * self.scale, self.top + (self.length - y) * self.scale

    def rect(self, r: Rect) -> Tuple[float, float, float, float]:
        (ax, ay), (bx, by) = self.pt(r[0], r[3]), self.pt(r[2], r[1])
        return ax, ay, bx, by


def _canvas(width: float, length: float, header: int = 120, legend: int = 0):
    from PIL import Image, ImageDraw
    scale = min(760.0 / width, 1400.0 / length)
    w = 1000
    h = int(header + length * scale + 70 + legend)
    image = Image.new("RGB", (w, h), PAPER)
    frame = _Frame((w - width * scale) / 2, header, scale, width, length)
    return image, ImageDraw.Draw(image), frame


def _header(d, w: int, title: str, sub: str) -> None:
    d.text((w / 2, 22), title, font=_font(30), fill=INK, anchor="mt")
    if sub:
        d.text((w / 2, 64), sub, font=_font(20), fill=MUTED, anchor="mt")


def _hatch(d, box, color, step: int = 12, width: int = 1) -> None:
    """Diagonal lines inside a pixel box."""
    x0, y0, x1, y1 = (int(v) for v in box)
    for k in range(x0 - (y1 - y0), x1, step):
        a = (max(k, x0), y0 + max(0, x0 - k))
        b = (min(k + (y1 - y0), x1), y0 + min(y1 - y0, x1 - k))
        if a[0] < b[0]:
            d.line([a, b], fill=color, width=width)


def _stock(d, f: _Frame, missing_from: Optional[float]) -> None:
    d.rectangle(f.rect((0, 0, f.width, f.length)), fill=STOCK)
    if missing_from is not None and missing_from < f.length:      # a piece shorter than a sheet: the rest isn't there
        box = f.rect((0, missing_from, f.width, f.length))
        d.rectangle(box, fill=PAPER)
        _hatch(d, box, HATCH)
        d.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), "not there", font=_font(22), fill=MUTED, anchor="mm")
        d.rectangle(f.rect((0, 0, f.width, missing_from)), outline=STOCK_EDGE, width=2)
    else:
        d.rectangle(f.rect((0, 0, f.width, f.length)), outline=STOCK_EDGE, width=2)


def _moves(d, f: _Frame, moves: Iterable[Move], kerf_in: float, color=None, least_px: float = 2.0) -> None:
    """The cuts at the cutter's width (kerf_in, its diameter), so a hole or a slot shows at its real size: joined
    moves drawn as one line with round joints and ends. color: one for all, else by depth: ramps and lead-outs
    first (they cross the full-depth path), then what goes through, then what stays at one height above the
    spoilboard on top (tabs, pocket floors)."""
    if color is None:
        moves = list(moves)
        _draw(d, f, [m for m in moves if m.z > THROUGH_Z and not m.flat], kerf_in, PARTIAL, least_px)
        _draw(d, f, [m for m in moves if m.z <= THROUGH_Z], kerf_in, THROUGH, least_px)
        _draw(d, f, [m for m in moves if m.z > THROUGH_Z and m.flat], kerf_in, PARTIAL, least_px)
    else:
        _draw(d, f, moves, kerf_in, color, least_px)


def _draw(d, f: _Frame, moves: Iterable[Move], kerf_in: float, color, least_px: float) -> None:
    width = max(least_px, kerf_in * f.scale)
    r = width / 2

    def flush(points, c) -> None:
        if len(points) > 1:
            d.line(points, fill=c, width=int(round(width)), joint="curve")
        for p in (points[0], points[-1]) if points else ():
            d.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=c)

    run: List[Tuple[float, float]] = []
    run_c = None
    last = None
    for m in moves:
        c = color
        a, b = f.pt(m.x0, m.y0), f.pt(m.x1, m.y1)
        if m.drill:
            d.ellipse((a[0] - r, a[1] - r, a[0] + r, a[1] + r), fill=c)
            continue
        if run and c == run_c and last is not None and abs(m.x0 - last[0]) < 1e-6 and abs(m.y0 - last[1]) < 1e-6:
            run.append(b)
        else:
            flush(run, run_c)
            run, run_c = [a, b], c
        last = (m.x1, m.y1)
    flush(run, run_c)


def _distance(px: float, py: float, m: Move) -> float:
    dx, dy = m.x1 - m.x0, m.y1 - m.y0
    n2 = dx * dx + dy * dy
    t = 0.0 if n2 < 1e-12 else max(0.0, min(1.0, ((px - m.x0) * dx + (py - m.y0) * dy) / n2))
    return math.hypot(px - (m.x0 + t * dx), py - (m.y0 + t * dy))


def badge_spot(outline: Sequence[Move], moves: Sequence[Move], grid: int = 9) -> Tuple[float, float]:
    """Where an outline's number goes: inside its box, as far as can be from every cut (not over a hole)."""
    pts = [p for m in outline for p in ((m.x0, m.y0), (m.x1, m.y1))]
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    near = [m for m in moves if max(m.x0, m.x1) >= x0 and min(m.x0, m.x1) <= x1
            and max(m.y0, m.y1) >= y0 and min(m.y0, m.y1) <= y1]
    best, spot = -1.0, ((x0 + x1) / 2, (y0 + y1) / 2)
    for i in range(1, grid):
        for j in range(1, grid):
            x, y = x0 + (x1 - x0) * i / grid, y0 + (y1 - y0) * j / grid
            gap = min((_distance(x, y, m) for m in near), default=math.inf)
            centre = math.hypot(x - (x0 + x1) / 2, y - (y0 + y1) / 2) * 1e-3       # ties: nearer the middle
            if gap - centre > best:
                best, spot = gap - centre, (x, y)
    return spot


def _badge(d, x: float, y: float, n: int, r: int = 15) -> None:
    d.ellipse((x - r, y - r, x + r, y + r), fill=BADGE, outline=INK, width=2)
    d.text((x, y), str(n), font=_font(int(r * 1.2)), fill=INK, anchor="mm")


def _legend(d, x: float, y: float, rows: Sequence[Tuple[Tuple[int, int, int], str, str]]) -> None:
    """rows: (color, "line" | "dot" | "fill" | "hatch" | "badge", text)."""
    for i, (color, kind, text) in enumerate(rows):
        cy = y + i * 30
        if kind == "line":
            d.line([(x, cy), (x + 34, cy)], fill=color, width=4)
        elif kind == "dot":
            d.ellipse((x + 12, cy - 5, x + 22, cy + 5), fill=color)
        elif kind == "fill":
            d.rectangle((x + 4, cy - 9, x + 30, cy + 9), fill=FREE_FILL, outline=color, width=2)
        elif kind == "hatch":
            d.rectangle((x + 4, cy - 9, x + 30, cy + 9), outline=color, width=2)
            _hatch(d, (x + 4, cy - 9, x + 30, cy + 9), color, step=6)
        elif kind == "badge":
            _badge(d, x + 17, cy, 1, 11)
        d.text((x + 48, cy), text, font=_font(19), fill=INK, anchor="lm")


def _png(image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _size(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def toolpath_png(*, title: str, sub: str, width: float, length: float, main: Program, kerf_in: float,
                 others: Sequence[Tuple[str, Program, float]] = (), clamps: Sequence[Rect] = (),
                 reach: Optional[float] = None, earlier: Sequence[Tuple[Sequence[Move], float]] = (),
                 hand_cuts: Sequence[Rect] = (), missing_from: Optional[float] = None) -> bytes:
    """The sheet's programs on the stock as loaded, each at its cutter's width (kerf_in). others: (what it's called
    on the card, program, kerf), e.g. the small-tool program; earlier (moves, kerf), hand_cuts: what was cut from
    this offcut before, as loaded."""
    rows: List[Tuple[Tuple[int, int, int], str, str]] = [
        (THROUGH, "line", "cuts through"), (PARTIAL, "line", "stops above the spoilboard: tabs, ramps, pockets"),
        (THROUGH, "dot", "drilled hole"), (BADGE, "badge", "outline cut order (the card's CUT ORDER)")]
    rows += [(SMALL, "line", label) for label, _, _ in others]
    if earlier or hand_cuts:
        rows.append((EARLIER, "line", "cut before (this offcut's earlier sheets)"))
    if clamps:
        rows.append((HATCH, "hatch", "clamps"))
    image, d, f = _canvas(width, length, legend=30 * len(rows) + 30)
    w = image.size[0]
    _header(d, w, title, sub)
    _stock(d, f, missing_from)
    for r in clamps:
        box = f.rect(r)
        _hatch(d, box, HATCH)
        d.rectangle(box, outline=HATCH, width=1)
    if reach is not None and reach < length:
        y = f.pt(0, reach)[1]
        for x in range(int(f.left) - 30, int(f.left + width * f.scale) + 30, 18):
            d.line([(x, y), (x + 10, y)], fill=MUTED, width=2)
        d.text((f.left + width * f.scale + 34, y), "machine\nreach", font=_font(17), fill=MUTED, anchor="lm")
    for r in hand_cuts:
        box = f.rect(r)
        _hatch(d, box, EARLIER, step=8)
        d.rectangle(box, outline=EARLIER, width=1)
    for moves, kerf in earlier:
        _moves(d, f, moves, kerf, EARLIER)
    for _, program, kerf in others:
        _moves(d, f, program.moves, kerf, SMALL)
    _moves(d, f, main.moves, kerf_in)
    every = list(main.moves) + [m for _, p, _ in others for m in p.moves]
    for n, (_, moves) in enumerate(main.outlines(), 1):
        if moves:
            _badge(d, *f.pt(*badge_spot(moves, every)), n)
    bottom = f.top + length * f.scale
    d.text((w / 2, bottom + 14), "FRONT (toward you)", font=_font(20), fill=MUTED, anchor="mt")
    _legend(d, f.left, bottom + 62, rows)
    return _png(image)


def offcut_png(*, title: str, sub: str, width: float, length: float, cuts: Sequence[Tuple[Sequence[Move], float]] = (),
               hand_cuts: Sequence[Rect] = (), free: Sequence[Tuple[float, float]] = (), rooms: Sequence[Rect] = (),
               missing_from: Optional[float] = None, unknown: Sequence[Tuple[float, float]] = ()) -> bytes:
    """What the piece should look like, in its own coordinates (end A at the bottom). cuts: (moves, the cutter's
    diameter) for each program cut on it. free: stretches along its
    length with nothing cut; rooms: room beside the cuts; unknown: used stretches no program on record covers
    (drawn shaded: cut by a program AutoCAM doesn't have)."""
    rows: List[Tuple[Tuple[int, int, int], str, str]] = [(THROUGH, "line", "cut by AutoCAM's sheets")]
    if hand_cuts:
        rows.append((HAND, "hatch", "cut out by hand"))
    if unknown:
        rows.append((EARLIER, "hatch", "used (no program on record)"))
    rows.append((FREE, "fill", "uncut: room for the next sheets"))
    image, d, f = _canvas(width, length, legend=30 * len(rows) + 30)
    w = image.size[0]
    _header(d, w, title, sub)
    _stock(d, f, missing_from)
    for a, b in unknown:
        box = f.rect((0, a, width, b))
        _hatch(d, box, EARLIER, step=10)
    for a, b in free:
        box = f.rect((0, a, width, b))
        d.rectangle(box, fill=FREE_FILL, outline=FREE, width=2)
        if b - a >= 2:
            d.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), f"{_size(b - a)} in uncut", font=_font(30),
                   fill=FREE, anchor="mm")
    for r in rooms:
        box = f.rect(r)
        d.rectangle(box, fill=FREE_FILL, outline=FREE, width=2)
        if box[2] - box[0] > 70 and box[3] - box[1] > 26:
            d.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), f"{_size(r[2] - r[0])} x {_size(r[3] - r[1])}",
                   font=_font(18), fill=FREE, anchor="mm")
    for r in hand_cuts:
        box = f.rect(r)
        _hatch(d, box, HAND, step=8, width=2)
        d.rectangle(box, outline=HAND, width=2)
    for moves, kerf in cuts:
        _moves(d, f, moves, kerf, THROUGH)
    top, bottom = f.top, f.top + length * f.scale
    d.text((w / 2, top - 8), "END B", font=_font(22), fill=MUTED, anchor="mb")
    d.text((w / 2, bottom + 12), "END A (at the front when it was first cut)", font=_font(22), fill=MUTED, anchor="mt")
    _legend(d, f.left, bottom + 62, rows)
    return _png(image)
