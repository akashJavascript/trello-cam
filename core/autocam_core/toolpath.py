"""What a posted program cuts, for the pictures on sheet and offcut cards (2026-11-04, the user's pick: "a pic
... on what the sheet should look like", and the toolpath picture).

read_program: every move below the stock top, in the program's own coordinates (X across the sheet, Y along it,
from the front-left corner as loaded; Z0 the spoilboard), each with the operation it belongs to (the post's
comment line before it: "[bore holes]", "[outer p01-1]") and how deep it goes. A drilled hole is a point. Rapids
above the stock cut nothing and are left out. Reads the same programs the guard and cutmap.py read; anything it
can't (a G91 move, an arc given by R) raises ToolpathError.

turn_moves: the same moves with the sheet turned end for end (an offcut loaded end B first), for drawing every
program cut on a piece in the piece's own coordinates.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .cutmap import _arc_points
from .tapguard import parse_code

_MOTION = (0.0, 1.0, 2.0, 3.0)
_CYCLES = (73.0, 81.0, 82.0, 83.0)
_OP = re.compile(r"^\s*\[(.+)\]\s*$")
_STOP = re.compile(r"^\s*\[PART \d+ DONE", re.IGNORECASE)
_OUTER = re.compile(r"^outer\b", re.IGNORECASE)


class ToolpathError(ValueError):
    pass


@dataclass(frozen=True)
class Move:
    op: str                 # the operation it's in: "bore holes", "outer p01-1"
    x0: float
    y0: float
    x1: float
    y1: float
    z: float                # the lowest Z along it (inches above the spoilboard)
    drill: bool = False     # a drilled hole (a point)
    flat: bool = True       # at one height all along (a tab's top, a pocket floor), not a ramp up or down


@dataclass(frozen=True)
class Program:
    name: str               # its first comment line (the program's name), "" if it has none
    moves: Tuple[Move, ...]
    ops: Tuple[str, ...]    # the operations, in the order they run

    def outlines(self) -> List[Tuple[str, List[Move]]]:
        """Each part's outline ("[outer ...]" operations), in cut order: the CUT ORDER numbers on the card."""
        out: List[Tuple[str, List[Move]]] = []
        for op in self.ops:
            if _OUTER.match(op):
                out.append((op, [m for m in self.moves if m.op == op]))
        return out


def read_program(text: str, stock_top_in: float) -> Program:
    x = y = z = None
    motion: Optional[float] = None
    cycle: Optional[float] = None
    cycle_z: Optional[float] = None
    op, name = "", ""
    ops: List[str] = []
    moves: List[Move] = []
    for line in text.splitlines():
        m = _OP.match(line)
        if m and not _STOP.match(line):
            if not name and not ops and not moves:
                name = m.group(1)
            else:
                op = m.group(1)
                if op not in ops:
                    ops.append(op)
            continue
        try:
            words = parse_code(line)
        except ValueError as e:
            raise ToolpathError(f"unreadable line {line!r}: {e}") from None
        if not words:
            continue
        gs = [v for letter, v in words if letter == "G"]
        if 91.0 in gs:
            raise ToolpathError("relative moves (G91)")
        if 53.0 in gs:                       # machine coordinates: G53 Z goes to the top, the park leaves the sheet
            if any(letter == "Z" for letter, _ in words):
                z = float("inf")
            if any(letter == "P" for letter, _ in words):
                x = y = None
            continue
        if 4.0 in gs:
            continue                         # a dwell (its X is seconds) or a stop
        vals = {letter: v for letter, v in words if letter not in ("G", "M")}
        for g in gs:
            if g in _MOTION:
                motion, cycle = g, None
            elif g in _CYCLES:
                cycle = g
            elif g == 80.0:
                cycle = None
        if "R" in vals and cycle is None and motion in (2.0, 3.0):
            raise ToolpathError("an arc given by its radius (R)")
        if not any(k in vals for k in "XYZ"):
            continue
        nx, ny = vals.get("X", x), vals.get("Y", y)
        if cycle is not None:                # a drilling cycle: down to its Z at this point, back up to R
            if "Z" in vals:
                cycle_z = vals["Z"]
            if nx is not None and ny is not None and cycle_z is not None and cycle_z < stock_top_in:
                moves.append(Move(op, nx, ny, nx, ny, cycle_z, drill=True))
            x, y = nx, ny
            z = vals.get("R", z)
            continue
        nz = vals.get("Z", z)
        if nx is None or ny is None:
            x, y, z = nx, ny, nz
            continue
        known = [v for v in (z, nz) if v is not None]
        low = min(known) if known else None
        if low is not None and low < stock_top_in:
            flat = z is None or nz is None or abs(nz - z) < 1e-6
            if x is None or y is None:
                moves.append(Move(op, nx, ny, nx, ny, low, flat=flat))
            elif motion in (2.0, 3.0):
                cx, cy = x + vals.get("I", 0.0), y + vals.get("J", 0.0)
                px, py = x, y
                for qx, qy in _arc_points(x, y, nx, ny, cx, cy, motion == 2.0):
                    moves.append(Move(op, px, py, qx, qy, low, flat=flat))
                    px, py = qx, qy
            else:
                moves.append(Move(op, x, y, nx, ny, low, flat=flat))
        x, y, z = nx, ny, nz
    return Program(name, tuple(moves), tuple(ops))


def turn_moves(moves: Sequence[Move], width: float, length: float) -> List[Move]:
    """The moves with the sheet turned end for end (spun round flat): (x, y) -> (width - x, length - y)."""
    return [Move(m.op, width - m.x0, length - m.y0, width - m.x1, length - m.y1, m.z, m.drill, m.flat) for m in moves]


def shift_moves(moves: Sequence[Move], dx: float, dy: float) -> List[Move]:
    return [Move(m.op, m.x0 + dx, m.y0 + dy, m.x1 + dx, m.y1 + dy, m.z, m.drill, m.flat) for m in moves]
