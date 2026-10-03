"""Checks a posted WinCNC program before anyone can cut it (decision 22: never cut the spoilboard).

Fail-closed: anything the guard doesn't understand is a problem, and a program with any
problem, any Z below the floor, or any move too low over a clamp zone does not pass.
It reads the exact bytes the machine will run, so it also catches template mistakes.

What the shop's post (fusion/posts/shopsabre_automatic_mist.cps) writes, and how it's read here:
- Comments are `[...]`. They are stripped; an unclosed `[` is a problem.
- `G53 Z` (no number) retracts to machine top; `G53 P10` parks. Any other G53 line is a problem.
- `G4 X4.` is a dwell in seconds, not an X move.
- `M11 C8` / `M12 C8` turn mist on/off; `C8` is an output number, not an axis.
- Drilling cycles (`G81 X Y Z R F`, ended by `G80`): both Z and R are checked against the floor.
- Arcs are XY-plane only (G17 is the only plane the guard accepts); a helix's lowest point is one of
  its end points.
- Bytes: printable ASCII plus one consistent line ending (CRLF or LF). A lone CR, a tab or any other
  control byte could make the controller split lines differently from the guard, so it fails.
- Rapids: a G0 that moves sideways below the stock top would plough through the plate, so it fails.
"""

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

TOP = math.inf  # Z right after `G53 Z`: machine top, above everything

Rect = Tuple[float, float, float, float]  # x0, y0, x1, y1 (inches, work coordinates)

# The only codes the guard knows how to reason about. Config allowlists are intersected with these, so
# adding e.g. G55 (another work offset), G18 (another arc plane) or G91 to config can never make it pass.
SAFE_G = frozenset({0, 1, 2, 3, 4, 20, 53, 73, 80, 81, 82, 83, 90})
SAFE_M = frozenset({0, 3, 5, 11, 12})
UNIT_CODES = frozenset({20, 21, 22})
MOTION_G = frozenset({0, 1, 2, 3})
CYCLE_G = frozenset({73, 81, 82, 83})
ALLOWED_LETTERS = frozenset("GMXYZIJRFSPCNQ")
Z_TOL = 1e-9  # Z-0.0001 (the smallest negative the post can write) must fail

_TOKEN_RE = re.compile(r" *([A-Z]) *([+-]?(?:\d+\.?\d*|\.\d+))?")


@dataclass(frozen=True)
class GuardSpec:
    z_floor_in: float = 0.0
    unit_code: int = 20                          # G20 = inch programs
    allowed_g: FrozenSet[int] = frozenset({0, 1, 2, 3, 4, 20, 53, 80, 81, 90})
    allowed_m: FrozenSet[int] = frozenset({0, 3, 5, 11, 12})
    park: str = "G53 P10"
    clamp_zones_in: Tuple[Rect, ...] = ()        # in work coordinates (WCS = sheet front-left bottom)
    clamp_clear_z_in: float = math.inf           # every move over a clamp zone must stay at or above this
    tool_radius_in: float = 0.0                  # clamp zones are grown by this much
    reach_y_in: Optional[float] = None           # no Y beyond the machine's reach (the sheet's length runs along Y)
    mist: Optional[bool] = None                  # expected useMist; None = don't check
    stock_top_in: Optional[float] = None         # no sideways rapid below this; None = don't check
    sheet_in: Optional[Tuple[float, float]] = None   # (X size, Y size): below the stock top the tool stays on it

    def __post_init__(self):
        if self.z_floor_in < 0:
            raise ValueError("z_floor_in must be >= 0: nothing may go below Z0 (the stock bottom)")


@dataclass(frozen=True)
class GuardReport:
    sha256: str
    passed: bool
    floor_in: float
    min_z_in: Optional[float]
    units: str
    offenders: Tuple[str, ...]          # lines that go below the floor
    clamp_violations: Tuple[str, ...]   # moves too low over a clamp zone
    problems: Tuple[str, ...]           # anything else that makes the program unsafe or unreadable
    m0_count: int
    line_count: int

    def summary(self, limit: int = 3) -> str:
        if self.passed:
            return f"passed; lowest Z {self.min_z_in:.4f} in" if self.min_z_in is not None else "passed"
        parts = []
        if self.offenders:
            parts.append(f"goes below Z{self.floor_in:.4f} (lowest Z {self.min_z_in:.4f} in): "
                         + "; ".join(self.offenders[:limit]))
        if self.clamp_violations:
            parts.append("too low over a clamp zone: " + "; ".join(self.clamp_violations[:limit]))
        if self.problems:
            parts.append("; ".join(self.problems[:limit]))
        return " | ".join(parts)

    def to_dict(self) -> Dict[str, object]:
        return {
            "sha256": self.sha256, "passed": self.passed, "floor_in": self.floor_in,
            "min_z_in": self.min_z_in, "units": self.units, "offenders": list(self.offenders),
            "clamp_violations": list(self.clamp_violations), "problems": list(self.problems),
            "m0_count": self.m0_count, "line_count": self.line_count,
        }


class _ParseError(Exception):
    pass


def _strip_comments(line: str) -> str:
    out, depth = [], 0
    for c in line:
        if c == "[":
            if depth:
                raise _ParseError("nested '['")
            depth = 1
        elif c == "]":
            if not depth:
                raise _ParseError("']' without '['")
            depth = 0
        elif not depth:
            out.append(c)
    if depth:
        raise _ParseError("unclosed '['")
    return "".join(out)


def _tokenize(code: str) -> List[Tuple[str, Optional[float]]]:
    words, pos, end = [], 0, len(code.rstrip(" "))
    while pos < end:
        m = _TOKEN_RE.match(code, pos)
        if not m or m.end() == pos:
            raise _ParseError(f"can't read {code[pos:].strip()!r}")
        letter, number = m.group(1), m.group(2)
        words.append((letter, float(number) if number is not None else None))
        pos = m.end()
    return words


def parse_code(line: str) -> List[Tuple[str, Optional[float]]]:
    """The words of one program line, with comments and N sequence numbers removed.

    `G53 Z` -> [("G", 53.0), ("Z", None)]. Raises ValueError for anything unreadable.
    """
    try:
        return [w for w in _tokenize(_strip_comments(line)) if w[0] != "N"]
    except _ParseError as e:
        raise ValueError(str(e)) from None


def _code_values(words, letter: str) -> List[float]:
    return [v for l, v in words if l == letter]


def _as_code(value: Optional[float]) -> Optional[int]:
    if value is None or not float(value).is_integer():
        return None
    return int(value)


def _grow(rect: Rect, r: float) -> Rect:
    return (rect[0] - r, rect[1] - r, rect[2] + r, rect[3] + r)


def _point_in(x: float, y: float, rect: Rect) -> bool:
    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


def _segment_hits(x0: float, y0: float, x1: float, y1: float, rect: Rect) -> bool:
    """Liang-Barsky: does the segment touch the rectangle?"""
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - rect[0]), (dx, rect[2] - x0), (-dy, y0 - rect[1]), (dy, rect[3] - y0)):
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return False
    return True


def _arc_box(x0: float, y0: float, x1: float, y1: float, cx: float, cy: float, clockwise: bool) -> Rect:
    """XY bounding box of the part of the circle an arc actually sweeps (G2 clockwise, G3 counterclockwise).

    Not the whole circle: a short stretch of a 28 in radius outline would otherwise look like it reaches the
    clamp strips and leaves the sheet. Start == end is a full circle.
    """
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    if clockwise:
        a0, a1 = a1, a0                       # the same points, swept counterclockwise
    sweep = (a1 - a0) % (2 * math.pi)
    if math.hypot(x1 - x0, y1 - y0) < 1e-6:
        sweep = 2 * math.pi
    r = math.hypot(x0 - cx, y0 - cy)
    xs, ys = [x0, x1], [y0, y1]
    for k in range(4):
        angle = k * math.pi / 2
        if (angle - a0) % (2 * math.pi) <= sweep + 1e-12:
            xs.append(cx + r * math.cos(angle))
            ys.append(cy + r * math.sin(angle))
    return min(xs), min(ys), max(xs), max(ys)


def _rects_overlap(a: Rect, b: Rect) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _fmt(z: float) -> str:
    return "top" if z == TOP else f"{z:.4f}"


def _byte_problems(data: bytes) -> Tuple[List[str], bytes]:
    """Printable ASCII plus one consistent line ending; returns (problems, the line ending)."""
    problems: List[str] = []
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    if newline == b"\r\n":
        if data.replace(b"\r\n", b"").count(b"\r"):
            problems.append("lone CR line ending (the controller may split lines differently)")
        if data.replace(b"\r\n", b"").count(b"\n"):
            problems.append("mixed CRLF and LF line endings")
    elif b"\r" in data:
        problems.append("CR line endings (the controller may split lines differently)")
    if any(b > 0x7E for b in data):
        problems.append("program contains non-ASCII bytes")
    bad = sorted({b for b in data if b < 0x20 and b not in (0x0A, 0x0D)})
    if bad:
        problems.append("program contains control characters " + ", ".join(f"0x{b:02x}" for b in bad))
    return problems, newline


def check_program(data: bytes, spec: GuardSpec) -> GuardReport:
    sha = hashlib.sha256(data).hexdigest()
    offenders: List[str] = []
    clamp: List[str] = []
    problems, newline = _byte_problems(data)
    text = data.decode("ascii", errors="replace")
    lines = text.split(newline.decode("ascii"))
    if lines and lines[-1] == "":
        lines.pop()
    allowed_g = spec.allowed_g & SAFE_G
    allowed_m = spec.allowed_m & SAFE_M
    zones = tuple(_grow(r, spec.tool_radius_in) for r in spec.clamp_zones_in)
    park_words = parse_code(spec.park)
    unit_name = f"G{spec.unit_code}"

    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None          # None = unknown height
    motion: Optional[int] = None
    cycle: Optional[int] = None
    cycle_z = cycle_r = 0.0
    absolute = False
    units_seen = 0
    spindle_on = False
    m0_count = 0
    min_z: Optional[float] = None
    mist_on_before_motion = False
    moved = False
    last_motion_index = -1
    mist_codes_seen = set()
    tail: List[Tuple[int, List[Tuple[str, Optional[float]]]]] = []  # code lines after the last motion

    def where(n: int, raw: str) -> str:
        return f"line {n}: {raw.strip()}"

    def floor_check(value: float, n: int, raw: str) -> None:
        nonlocal min_z
        min_z = value if min_z is None else min(min_z, value)
        if value < spec.z_floor_in - Z_TOL:
            offenders.append(where(n, raw))

    def off_sheet(box: Rect) -> bool:
        """The tool (center grown by its radius) leaves the sheet somewhere in box."""
        size_x, size_y = spec.sheet_in
        r = spec.tool_radius_in
        return (box[0] < r - 1e-6 or box[1] < r - 1e-6 or box[2] > size_x - r + 1e-6
                or box[3] > size_y - r + 1e-6)

    def low_enough(height: float) -> bool:
        return spec.sheet_in is not None and spec.stock_top_in is not None and height < spec.stock_top_in - 1e-6

    def over_zone(xa, ya, xb, yb, arc_box: Optional[Rect] = None) -> bool:
        for zone in zones:
            if arc_box is not None:
                if _rects_overlap(arc_box, zone):
                    return True
            elif _segment_hits(xa, ya, xb, yb, zone):
                return True
        return False

    for n, raw in enumerate(lines, 1):
        try:
            words = parse_code(raw)
        except ValueError as e:
            problems.append(f"{where(n, raw)} ({e})")
            continue
        if not words:
            continue

        letters = [l for l, _ in words]
        bad_letters = sorted(set(letters) - ALLOWED_LETTERS)
        if bad_letters:
            problems.append(f"{where(n, raw)} (unexpected word {', '.join(bad_letters)})")
            continue
        for letter in set(letters) - {"G", "M"}:
            if letters.count(letter) > 1:
                problems.append(f"{where(n, raw)} (repeated {letter} word)")
        values: Dict[str, Optional[float]] = {l: v for l, v in words if l not in ("G", "M")}
        empty = [l for l, v in values.items() if v is None and not (l == "Z" and 53.0 in _code_values(words, "G"))]
        if empty or None in _code_values(words, "G") or None in _code_values(words, "M"):
            problems.append(f"{where(n, raw)} (word without a value)")
            continue

        gs, ms = [], []
        line_ok = True
        for raw_g in _code_values(words, "G"):
            g = _as_code(raw_g)
            if g is None or g not in allowed_g or (g in UNIT_CODES and g != spec.unit_code):
                problems.append(f"{where(n, raw)} (G{raw_g:g} is not allowed)")
                line_ok = False
            else:
                gs.append(g)
        for raw_m in _code_values(words, "M"):
            m = _as_code(raw_m)
            if m is None or m not in allowed_m:
                problems.append(f"{where(n, raw)} (M{raw_m:g} is not allowed)")
                line_ok = False
            else:
                ms.append(m)
        if not line_ok:
            continue

        # Machine-coordinate lines: only the retract and the park.
        if 53 in gs:
            rest = [(l, v) for l, v in words if not (l == "G" and v == 53.0)]
            if rest == [("Z", None)]:
                z = TOP
            elif words == park_words:
                if z != TOP:
                    problems.append(f"{where(n, raw)} (park without a G53 Z retract first)")
                x = y = None
            else:
                problems.append(f"{where(n, raw)} (only 'G53 Z' and '{spec.park}' are allowed)")
            tail.append((n, words))
            continue

        if 4 in gs:  # dwell: X is seconds here
            if set(letters) - {"G", "X", "P", "N"} or len(gs) != 1:
                problems.append(f"{where(n, raw)} (dwell line with other words)")
            tail.append((n, words))
            continue

        if "C" in values and not (set(ms) & {11, 12}):
            problems.append(f"{where(n, raw)} (C word outside M11/M12)")
        if "P" in values and 82 not in gs:
            problems.append(f"{where(n, raw)} (P word outside G53/G82)")
        if "Q" in values and not (set(gs) & {73, 83}):
            problems.append(f"{where(n, raw)} (Q word outside a peck cycle)")

        if spec.unit_code in gs:
            units_seen += 1
            if moved:
                problems.append(f"{where(n, raw)} ({unit_name} after motion started)")
        if 90 in gs:
            absolute = True
        if 80 in gs:
            cycle = None

        for m in ms:
            if m == 3:
                spindle_on = True
            elif m == 5:
                spindle_on = False
            elif m == 0:
                m0_count += 1
                if spindle_on:
                    problems.append(f"{where(n, raw)} (M0 stop with the spindle running)")
            elif m in (11, 12):
                if values.get("C") != 8.0:
                    problems.append(f"{where(n, raw)} (M{m} without C8)")
                mist_codes_seen.add(m)
                if m == 11 and not moved:
                    mist_on_before_motion = True

        motion_g = [g for g in gs if g in MOTION_G]
        cycle_g = [g for g in gs if g in CYCLE_G]
        if len(motion_g) + len(cycle_g) > 1:
            problems.append(f"{where(n, raw)} (two motion codes on one line)")
            continue
        if motion_g:
            motion = motion_g[0]
            cycle = None
        if cycle_g:
            cycle = cycle_g[0]
            if "Z" not in values or "R" not in values:
                problems.append(f"{where(n, raw)} (drilling cycle without Z and R)")
                continue
        if cycle is not None and "Z" in values and values["Z"] is not None:
            cycle_z = values["Z"]
        if cycle is not None and "R" in values and values["R"] is not None:
            cycle_r = values["R"]
        if "R" in values and cycle is None:
            problems.append(f"{where(n, raw)} (R word outside a drilling cycle)")

        has_xyz = any(l in values for l in ("X", "Y", "Z"))
        if not has_xyz:
            if ms or gs or any(l in values for l in ("S", "F", "N")):
                if moved:
                    tail.append((n, words))
                continue
            continue

        if not absolute:
            problems.append(f"{where(n, raw)} (motion before G90)")
        if units_seen == 0:
            problems.append(f"{where(n, raw)} (motion before {unit_name})")
        if spec.mist and not mist_on_before_motion and not moved:
            problems.append(f"{where(n, raw)} (mist is on for this material but M11 C8 is missing)")
        nx = values.get("X", x) if "X" in values else x
        ny = values.get("Y", y) if "Y" in values else y
        if spec.reach_y_in is not None and "Y" in values and ny is not None and ny > spec.reach_y_in + 1e-6:
            problems.append(f"{where(n, raw)} (Y beyond the {spec.reach_y_in} in reach)")

        if cycle is not None and not motion_g:
            # A drilling cycle point: travel at the retract plane, plunge to the cycle Z.
            floor_check(cycle_z, n, raw)
            floor_check(cycle_r, n, raw)
            if not spindle_on:
                problems.append(f"{where(n, raw)} (drilling with the spindle stopped)")
            if nx is None or ny is None:
                problems.append(f"{where(n, raw)} (drilling cycle at an unknown position)")
            else:
                if low_enough(cycle_z) and off_sheet((nx, ny, nx, ny)):
                    problems.append(f"{where(n, raw)} (drills off the sheet)")
                travel_z = cycle_r if z is None else min(z, cycle_r)
                moves_xy = x is not None and y is not None and (nx, ny) != (x, y)
                if spec.stock_top_in is not None and moves_xy and travel_z < spec.stock_top_in - 1e-6:
                    problems.append(f"{where(n, raw)} (drilling travel below the stock top)")
                if zones and moves_xy and travel_z < spec.clamp_clear_z_in and over_zone(x, y, nx, ny):
                    clamp.append(where(n, raw))
                elif zones and over_zone(nx, ny, nx, ny):
                    clamp.append(where(n, raw))
            x, y = nx, ny
            z = cycle_r if z is None else min(z, cycle_r)
            moved = True
            last_motion_index = n
            tail = []
            continue

        if motion is None:
            problems.append(f"{where(n, raw)} (coordinates without G0/G1/G2/G3)")
            continue
        nz = values["Z"] if "Z" in values and values["Z"] is not None else z
        if "Z" in values and values["Z"] is not None:
            floor_check(values["Z"], n, raw)
        if motion in (1, 2, 3) and not spindle_on:
            problems.append(f"{where(n, raw)} (cutting move with the spindle stopped)")

        xy_moves = ("X" in values and nx != x) or ("Y" in values and ny != y)
        low = min(z if z is not None else -math.inf, nz if nz is not None else -math.inf)
        arc_box: Optional[Rect] = None
        if motion in (2, 3):
            if x is None or y is None or "I" not in values or "J" not in values:
                problems.append(f"{where(n, raw)} (arc without a known start and I/J)")
                continue
            cx, cy = x + values["I"], y + values["J"]
            arc_box = _arc_box(x, y, nx, ny, cx, cy, clockwise=motion == 2)
            xy_moves = True

        if xy_moves and motion == 0 and z is not None and spec.stock_top_in is not None \
                and low < spec.stock_top_in - 1e-6:
            problems.append(f"{where(n, raw)} (rapid sideways below the stock top)")
        if xy_moves:
            if z is None:
                problems.append(f"{where(n, raw)} (XY move before the height is known; expected G53 Z first)")
            elif x is None or y is None:
                # From the park position or the program start: only safe at clamp-clearing height.
                if zones and low < spec.clamp_clear_z_in:
                    clamp.append(where(n, raw) + f" (from an unknown position at Z {_fmt(low)})")
            elif zones and low < spec.clamp_clear_z_in and over_zone(x, y, nx, ny, arc_box):
                clamp.append(where(n, raw))
        elif "Z" in values and zones and nz is not None and min(z if z is not None else nz, nz) < spec.clamp_clear_z_in:
            if x is None or y is None:
                clamp.append(where(n, raw) + " (descends at an unknown position)")
            elif over_zone(x, y, x, y):
                clamp.append(where(n, raw))
        if low_enough(low):
            if arc_box is not None:
                path = arc_box
            elif x is not None and y is not None and nx is not None and ny is not None:
                path = (min(x, nx), min(y, ny), max(x, nx), max(y, ny))
            elif nx is not None and ny is not None:
                path = (nx, ny, nx, ny)
            else:
                path = None
            if path is not None and off_sheet(path):
                problems.append(f"{where(n, raw)} (cuts off the sheet)")

        x, y, z = nx, ny, nz
        moved = True
        last_motion_index = n
        tail = []

    if units_seen != 1:
        problems.append(f"expected exactly one {unit_name}, found {units_seen}")
    if not moved:
        problems.append("no motion found")
    if spindle_on:
        problems.append("program ends with the spindle running")
    if spec.mist is False and mist_codes_seen:
        problems.append("mist codes present but mist is off for this material")
    if moved:
        tail_codes = [w for _, w in tail]
        if not tail_codes or tail_codes[-1] != park_words:
            problems.append(f"program doesn't end with '{spec.park}'")
        if [("G", 53.0), ("Z", None)] not in tail_codes:
            problems.append("no G53 Z retract after the last move")
        if not any(("M", 5.0) in w for w in tail_codes):
            problems.append("no M5 after the last move")
        if spec.mist and not any(("M", 12.0) in w for w in tail_codes):
            problems.append("no M12 C8 (mist off) after the last move")

    passed = not offenders and not clamp and not problems
    return GuardReport(
        sha256=sha, passed=passed, floor_in=spec.z_floor_in, min_z_in=min_z, units=unit_name,
        offenders=tuple(offenders), clamp_violations=tuple(clamp), problems=tuple(problems),
        m0_count=m0_count, line_count=len(lines),
    )


def check_file(path: Path, spec: GuardSpec) -> GuardReport:
    return check_program(Path(path).read_bytes(), spec)


def rejected_path(path: Path) -> Path:
    """name.tap -> name.REJECTED.tap"""
    path = Path(path)
    return path.with_name(f"{path.stem}.REJECTED{path.suffix}")
