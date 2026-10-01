"""Cut order for the per-part outline ops (decision 19): nearest neighbour from the zero corner.

Holes, pockets and inner cutouts stay sheet-wide; only the `[outer]` contours are split per part.
A part is finished the moment its outline finishes, so this is also the pause order.
"""

import math
from typing import List, Sequence, Tuple


def order_outlines(points: Sequence[Tuple[str, Tuple[float, float]]],
                   start: Tuple[float, float] = (0.0, 0.0)) -> List[str]:
    """`points` are (instance id, a point on the part in sheet coordinates). Ties go to the lower id."""
    remaining = list(points)
    if len({p[0] for p in remaining}) != len(remaining):
        raise ValueError("duplicate instance ids")
    order: List[str] = []
    here = start
    while remaining:
        best = min(remaining, key=lambda p: (round(math.dist(here, p[1]), 9), p[0]))
        order.append(best[0])
        here = best[1]
        remaining.remove(best)
    return order
