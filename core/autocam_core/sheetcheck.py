"""One definition of "this sheet's program is safe to offer for cutting", shared by both sides.

The Fusion worker runs it right after posting (and renames failures *.REJECTED.tap); the service
runs it again on the downloaded bytes before anything is uploaded to Trello. Same job, same code,
same answer.

It doesn't take the worker's word for the sheet: the thickness must be one of the job's stock
thicknesses, the tool must be one of the job's tools, and the program's `[outer <part>]` ops must
be exactly the planned cut order and account for every placed copy (so no part loses its pause).
Every `[outer <part>]` op must also reach the stock bottom: a template whose bottom height follows
the selected (top) face posts a program that passes the Z floor but never cuts a part free.
"""

import re
from dataclasses import dataclass, replace
from typing import List, Mapping, Optional, Sequence, Tuple

from .fixture import clamp_clear_z
from .names import instance_id
from .pauses import PauseCheck, PauseSpec, verify
from .schema_job import Job
from .tapguard import GuardReport, GuardSpec, check_program, parse_code

_OUTER_LINE = re.compile(r"^\[outer (.*)\]$")
_COMMENT_ONLY = re.compile(r"^\[[^\[\]]*\]$")

# Z is posted to 4 decimals; a through cut ends at the floor itself (Z0 = spoilboard).
THROUGH_CUT_TOL_IN = 0.0005


@dataclass(frozen=True)
class SheetCheck:
    guard: GuardReport
    pauses: Optional[PauseCheck]      # None when pauses are off for the job
    plan_problems: Tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.guard.passed and (self.pauses is None or self.pauses.ok) and not self.plan_problems

    def problems(self) -> List[str]:
        out = list(self.plan_problems)
        if not self.guard.passed:
            out.append(self.guard.summary())
        if self.pauses is not None and not self.pauses.ok:
            out += list(self.pauses.problems)
        return out


def guard_spec(job: Job, thickness_in: float, tool_key: str) -> GuardSpec:
    tool = job.tooling.tools[tool_key]
    return GuardSpec(
        z_floor_in=job.guard.z_floor_in,
        allowed_g=frozenset(job.guard.allowed_g),
        allowed_m=frozenset(job.guard.allowed_m),
        park=job.pauses.park,
        clamp_zones_in=job.fixture.clamp_zones_in,
        clamp_clear_z_in=clamp_clear_z(thickness_in, job.fixture.clamp_height_in, job.guard.clamp_margin_in),
        tool_radius_in=tool.diameter_in / 2,
        reach_x_in=job.sheet.reach_x_in,
        mist=job.material.use_mist,
        stock_top_in=thickness_in,
    )


def pause_spec(job: Job) -> PauseSpec:
    return PauseSpec(mist=job.material.use_mist, park=job.pauses.park, spindle_rpm=job.pauses.spindle_rpm,
                     dwell_s=job.pauses.dwell_s)


def plan_problems(text: str, job: Job, thickness_in: float, tool_key: str, outer_order: Sequence[str],
                  part_counts: Mapping[str, int]) -> List[str]:
    out: List[str] = []
    if not any(abs(thickness_in - t) <= 1e-9 for t in job.material.thicknesses_in):
        out.append(f"sheet thickness {thickness_in:g} in is not a {job.material.name} stock thickness")
    allowed_tools = {job.tooling.default, job.tooling.small_features} - {None}
    if tool_key not in allowed_tools:
        out.append(f"sheet tool {tool_key} isn't one of this job's tools")
    unknown = sorted(set(part_counts) - {p.part_key for p in job.parts})
    if unknown:
        out.append(f"sheet lists parts that aren't in the job: {', '.join(unknown)}")
    expected = sorted(instance_id(k, n) for k, c in part_counts.items() for n in range(1, c + 1))
    if sorted(outer_order) != expected or len(set(outer_order)) != len(outer_order):
        out.append(f"cut order {list(outer_order)} doesn't match the parts on the sheet {expected}")
    in_program = [m.group(1) for m in (_OUTER_LINE.match(l.strip()) for l in text.splitlines()) if m]
    if in_program != list(outer_order):
        out.append(f"outline ops in the program {in_program} don't match the cut order {list(outer_order)}")
    if not part_counts:
        out.append("no parts on the sheet")
    out += outer_depth_problems(text, job.guard.z_floor_in)
    return out


def outer_depth_problems(text: str, floor_in: float) -> List[str]:
    """Each outline op runs from its `[outer ...]` line to the next comment-only line or the end."""
    out: List[str] = []
    lines = [l.strip() for l in text.splitlines()]
    for start, line in enumerate(lines):
        m = _OUTER_LINE.match(line)
        if not m:
            continue
        lowest: Optional[float] = None
        for code in lines[start + 1:]:
            if _COMMENT_ONLY.match(code):
                break
            try:
                words = parse_code(code)
            except ValueError:
                continue      # the guard rejects unreadable lines
            if ("G", 53.0) in words:
                continue
            for letter, value in words:
                if letter == "Z" and value is not None:
                    lowest = value if lowest is None else min(lowest, value)
        if lowest is None:
            out.append(f"outline op [outer {m.group(1)}] has no Z move")
        elif lowest > floor_in + THROUGH_CUT_TOL_IN:
            out.append(f"outline op [outer {m.group(1)}] never reaches the stock bottom (lowest Z {lowest:.4f} in); "
                       f"the template's bottom height must be Stock bottom, offset 0")
    return out


def check_sheet_program(data: bytes, job: Job, thickness_in: float, tool_key: str,
                        outer_order: Sequence[str], part_counts: Mapping[str, int]) -> SheetCheck:
    text = data.decode("ascii", errors="replace")
    problems = tuple(plan_problems(text, job, thickness_in, tool_key, outer_order, part_counts))
    if tool_key not in job.tooling.tools:
        return SheetCheck(_failed(check_program(data, GuardSpec()), "unknown tool"), None, problems)
    spec = guard_spec(job, thickness_in, tool_key)
    report = check_program(data, spec)
    if not job.pauses.enabled:
        if report.m0_count:
            report = _failed(report, f"pauses are off for this job but the program has {report.m0_count} M0")
        return SheetCheck(report, None, problems)
    pauses = verify(text, outer_order, pause_spec(job), after_last_part=job.pauses.after_last_part,
                    safe_z_in=spec.clamp_clear_z_in)
    return SheetCheck(report, pauses, problems)


def _failed(report: GuardReport, problem: str) -> GuardReport:
    return replace(report, passed=False, problems=report.problems + (problem,))
