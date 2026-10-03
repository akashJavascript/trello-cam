"""Offcuts: a sheet put back on the machine after some of it was cut, for the next nest to use.

The sheet is kept whole. The cut parts leave a skeleton, but the clamped long edges are never cut, so it
clamps and zeros like a new sheet. What's recorded is which stretches along its length are used, in the
sheet's own coordinates: end A is the end that was at the zero corner (front left) when it was first cut.

The next nest goes in the longest free stretch the machine can reach. The sheet can be loaded as before
or turned end for end (end B at the zero corner). Turning it brings the end that hung off the bed onto it,
so a sheet with a short strip used at one end is nearly a whole new sheet the other way round. But spinning
it is a chore, so the nest keeps it the way it was last cut unless spinning fits more parts or saves a new
sheet (the pipeline decides; placement_for gives each way round).
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

Stretch = Tuple[float, float]         # along the sheet's length, inches


@dataclass(frozen=True)
class Placement:
    turned: bool                      # end B at the zero corner
    x0: float                         # the free stretch, in machine X (the sheet as loaded)
    x1: float

    @property
    def length(self) -> float:
        return self.x1 - self.x0


def as_loaded(used: Sequence[Stretch], sheet_length: float, turned: bool) -> List[Stretch]:
    """The used stretches in machine X for a sheet loaded as before (turned=False) or end for end."""
    if not turned:
        return sorted((a, b) for a, b in used)
    return sorted((sheet_length - b, sheet_length - a) for a, b in used)


def free_stretch(used: Sequence[Stretch], sheet_length: float, turned: bool, lo: float, hi: float,
                 gap: float) -> Optional[Stretch]:
    """The longest stretch of [lo, hi] (machine X) at least `gap` from anything used."""
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
