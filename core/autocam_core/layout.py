"""After Arrange: which body is on which sheet, and what to do about parts that didn't all fit.

Binning is by position on purpose (carried over from Phase 1): the Arrange API preview has a
known issue reporting which occurrences landed in which envelope. A body belongs to the envelope
that contains the centre of its bounding box.

Short quantity (config nest.short_qty = "defer_card"): if not every copy of a card's part fit,
none of them are cut this run; the card stays queued for the next run.
"""

from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence, Tuple

from .fixture import Rect, contains
from .names import instance_id


@dataclass(frozen=True)
class Placed:
    body_id: str                 # opaque id from the adapter (e.g. an entity token)
    part_key: str
    bbox_in: Rect                # in the nesting document, inches

    @property
    def center(self) -> Tuple[float, float]:
        b = self.bbox_in
        return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


@dataclass(frozen=True)
class SheetLayout:
    index: int                                   # 1-based, after empty sheets are dropped
    envelope_in: Rect
    instances: Tuple[Tuple[str, Placed], ...]    # (instance id like p03-2, body)


@dataclass(frozen=True)
class Layout:
    sheets: Tuple[SheetLayout, ...]
    placed: Dict[str, int]                 # copies on sheets per part (after deferral)
    deferred: Tuple[str, ...]              # parts not cut this run (short quantity)
    removed_bodies: Tuple[str, ...]        # bodies to delete before CAM (copies of deferred parts)
    unplaced_bodies: Tuple[str, ...]       # bodies outside every envelope
    problems: Tuple[str, ...]


def plan_layout(bodies: Sequence[Placed], envelopes: Sequence[Rect], qty: Mapping[str, int],
                edge_tol_in: float = 1e-4) -> Layout:
    per_env: List[List[Placed]] = [[] for _ in envelopes]
    unplaced: List[str] = []
    problems: List[str] = []
    for body in bodies:
        cx, cy = body.center
        for i, env in enumerate(envelopes):
            if env[0] <= cx <= env[2] and env[1] <= cy <= env[3]:
                if not contains(env, body.bbox_in, tol=edge_tol_in):
                    problems.append(f"{body.part_key}: body {body.body_id} crosses the edge of nest envelope {i + 1}")
                per_env[i].append(body)
                break
        else:
            unplaced.append(body.body_id)

    counts: Dict[str, int] = {}
    for env_bodies in per_env:
        for body in env_bodies:
            counts[body.part_key] = counts.get(body.part_key, 0) + 1
    for key, n in counts.items():
        if key not in qty:
            problems.append(f"{key}: on a sheet but not in the job")
        elif n > qty[key]:
            problems.append(f"{key}: {n} copies placed but only {qty[key]} ordered")
    deferred = tuple(sorted(k for k, q in qty.items() if counts.get(k, 0) < q))

    removed: List[str] = []
    sheets: List[SheetLayout] = []
    for env, env_bodies in zip(envelopes, per_env):
        keep = [b for b in env_bodies if b.part_key not in deferred]
        removed += [b.body_id for b in env_bodies if b.part_key in deferred]
        if keep:
            sheets.append(SheetLayout(len(sheets) + 1, env, _number_instances(keep)))
    placed = {k: counts.get(k, 0) for k in qty if k not in deferred}
    return Layout(tuple(sheets), placed, deferred, tuple(removed), tuple(unplaced), tuple(problems))


def _number_instances(bodies: Sequence[Placed]) -> Tuple[Tuple[str, Placed], ...]:
    """Copies of a part on one sheet are numbered 1..n from the sheet's front-left corner."""
    out = []
    by_part: Dict[str, List[Placed]] = {}
    for b in bodies:
        by_part.setdefault(b.part_key, []).append(b)
    for key in sorted(by_part):
        for n, body in enumerate(sorted(by_part[key], key=lambda b: (b.center[0], b.center[1], b.body_id)), 1):
            out.append((instance_id(key, n), body))
    return tuple(out)
