"""result.json: what the Fusion worker made from one job. Written last; its presence means done."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .errors import Issue
from .schema import dumps, from_dict

RESULT_SCHEMA = "autocam.result/1"
OK, NEEDS_REVIEW, FAILED = "ok", "needs_review", "failed"


@dataclass(frozen=True)
class WorkerInfo:
    fusion_version: str
    python_version: str
    attempt: int
    started_utc: str
    finished_utc: str
    untested_steps: Tuple[str, ...]


@dataclass(frozen=True)
class PostResult:
    method: str                    # "path" (pinned .cps) or "library" (verified against the pin)
    sha256_verified: bool
    properties: Dict[str, Any]


@dataclass(frozen=True)
class FusionTeamResult:
    url: Optional[str]
    name: Optional[str]


@dataclass(frozen=True)
class GuardSummary:
    passed: bool
    sha256: str
    floor_in: float
    min_z_in: Optional[float]
    units: str
    offenders: Tuple[str, ...]
    clamp_violations: Tuple[str, ...]
    problems: Tuple[str, ...]


@dataclass(frozen=True)
class PauseEntryResult:
    after: str
    line: int


@dataclass(frozen=True)
class PauseSummary:
    mode: str
    expected: int
    found: int
    entries: Tuple[PauseEntryResult, ...]
    problems: Tuple[str, ...]


@dataclass(frozen=True)
class SheetPart:
    part_key: str
    count: int


@dataclass(frozen=True)
class ToolForce:
    part_key: str
    reason: str


@dataclass(frozen=True)
class SheetResult:
    index: int
    name: str                      # program name, e.g. 6061_0p125_r017_S1
    stock_type: str                # e.g. al6061-0.125
    thickness_in: float
    tool: str
    tool_guid: str
    cutter_label: str
    template: str
    tap: Optional[str]             # file name in the job's done folder; None if not offered for cutting
    tap_rejected: Optional[str]    # *.REJECTED.tap kept for inspection
    tap_bytes: Optional[int]
    tap_sha256: Optional[str]
    guard: Optional[GuardSummary]
    pauses: Optional[PauseSummary]
    machining_time_s: Optional[float]
    preview_png: Optional[str]
    parts: Tuple[SheetPart, ...]
    outer_order: Tuple[str, ...]
    tool_forced_by: Tuple[ToolForce, ...]
    errors: Tuple[Issue, ...]
    warnings: Tuple[Issue, ...]
    notes: Tuple[str, ...]


@dataclass(frozen=True)
class HoleCounts:
    drill: int
    bore: int
    bearing: int
    inner: int
    contour_warn: int
    pockets: int


@dataclass(frozen=True)
class PartResult:
    part_key: str
    card_id: str
    qty: int
    placed: int
    deferred: bool
    sheets: Tuple[int, ...]
    measured_thickness_in: Optional[float]
    stock_thickness_in: Optional[float]
    tool_need: Optional[str]
    holes: Optional[HoleCounts]
    errors: Tuple[Issue, ...]
    warnings: Tuple[Issue, ...]


@dataclass(frozen=True)
class Result:
    schema: str
    core_version: str
    job_id: str
    run_id: str
    status: str
    worker: WorkerInfo
    post: Optional[PostResult]
    fusion_team: Optional[FusionTeamResult]
    f3d: Optional[str]
    f3d_bytes: Optional[int]
    sheets: Tuple[SheetResult, ...]
    parts: Tuple[PartResult, ...]
    errors: Tuple[Issue, ...]       # job-level problems
    notes: Tuple[str, ...]

    def validate(self) -> List[str]:
        e: List[str] = []
        if self.schema != RESULT_SCHEMA:
            e.append(f"schema: expected {RESULT_SCHEMA}, got {self.schema!r}")
        if self.status not in (OK, NEEDS_REVIEW, FAILED):
            e.append(f"status: {self.status!r}")
        indexes = [s.index for s in self.sheets]
        if len(set(indexes)) != len(indexes):
            e.append("sheets: duplicate index")
        for s in self.sheets:
            if s.tap is not None and (s.guard is None or not s.guard.passed):
                e.append(f"sheets.{s.index}: a .tap offered for cutting must have a passing guard report")
            if s.tap is not None and s.guard is not None and s.tap_sha256 != s.guard.sha256:
                e.append(f"sheets.{s.index}: tap_sha256 doesn't match the guard report")
        return e


def overall_status(sheets: Tuple[SheetResult, ...], parts: Tuple[PartResult, ...],
                   errors: Tuple[Issue, ...]) -> str:
    """failed: nothing usable came out. ok: every sheet posted clean and every part placed clean.
    Anything else needs review (and every sheet is reviewed by a human anyway)."""
    if errors and not any(s.tap for s in sheets):
        return FAILED
    if not sheets:
        return FAILED
    clean_sheets = all(s.tap and not s.errors and not s.warnings for s in sheets)
    clean_parts = all(not p.errors and not p.warnings and not p.deferred for p in parts)
    return OK if clean_sheets and clean_parts and not errors else NEEDS_REVIEW


def load_result(data: Dict[str, Any]) -> Result:
    return from_dict(Result, data)


def read_result(path: Path) -> Result:
    return load_result(json.loads(Path(path).read_text(encoding="utf-8")))


def result_json(result: Result) -> str:
    return dumps(result)
