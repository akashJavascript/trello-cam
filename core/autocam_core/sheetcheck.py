"""One definition of "this sheet's program is safe to offer for cutting", shared by both sides.

The Fusion worker runs it right after posting (and renames failures *.REJECTED.tap); the service
runs it again on the downloaded bytes before anything is uploaded to Trello. Same job, same code,
same answer.
"""

from dataclasses import dataclass
from typing import Optional, Sequence

from .fixture import clamp_clear_z
from .pauses import PauseCheck, PauseSpec, verify
from .schema_job import Job
from .tapguard import GuardReport, GuardSpec, check_program


@dataclass(frozen=True)
class SheetCheck:
    guard: GuardReport
    pauses: Optional[PauseCheck]      # None when pauses are off for the job

    @property
    def passed(self) -> bool:
        return self.guard.passed and (self.pauses is None or self.pauses.ok)

    def problems(self):
        out = [] if self.guard.passed else [self.guard.summary()]
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
    )


def pause_spec(job: Job) -> PauseSpec:
    return PauseSpec(mist=job.material.use_mist, park=job.pauses.park, spindle_rpm=job.pauses.spindle_rpm,
                     dwell_s=job.pauses.dwell_s)


def check_sheet_program(data: bytes, job: Job, thickness_in: float, tool_key: str,
                        outer_order: Sequence[str]) -> SheetCheck:
    spec = guard_spec(job, thickness_in, tool_key)
    report = check_program(data, spec)
    if not job.pauses.enabled:
        if report.m0_count:
            report = _with_problem(report, f"pauses are off for this job but the program has {report.m0_count} M0")
        return SheetCheck(report, None)
    text = data.decode("ascii", errors="replace")
    pauses = verify(text, outer_order, pause_spec(job), after_last_part=job.pauses.after_last_part,
                    safe_z_in=spec.clamp_clear_z_in)
    return SheetCheck(report, pauses)


def _with_problem(report: GuardReport, problem: str) -> GuardReport:
    from dataclasses import replace
    return replace(report, passed=False, problems=report.problems + (problem,))
