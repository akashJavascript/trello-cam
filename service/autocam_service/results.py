"""Ingest a finished job from the hot folder and re-check every program before it can reach Trello.

The service doesn't trust result.json's word that a .tap is safe: it re-runs the same guard, pause
and cut-plan checks (autocam_core.sheetcheck) on the bytes it is about to upload, against its own
copy of the job. It also checks that each part's numbers add up before a card is moved to Nested.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from autocam_core.hotfolder import Queue
from autocam_core.schema import SchemaError
from autocam_core.schema_job import Job
from autocam_core.schema_result import Result, SheetResult, read_result
from autocam_core.sheetcheck import SheetCheck, check_sheet_program


@dataclass(frozen=True)
class VerifiedSheet:
    sheet: SheetResult
    tap_bytes: Optional[bytes]
    check: Optional[SheetCheck]
    problems: Tuple[str, ...]           # why it can't be offered for cutting (empty if it can)

    @property
    def cuttable(self) -> bool:
        return self.tap_bytes is not None and self.check is not None and self.check.passed and not self.problems

    @property
    def pause_count(self) -> int:
        """From the service's own check of the program, not from the worker's summary."""
        if self.check is None or self.check.pauses is None:
            return 0
        return len(self.check.pauses.entries)


@dataclass(frozen=True)
class IngestedJob:
    job_id: str
    folder: Path
    job: Optional[Job]
    result: Optional[Result]
    failure: Optional[str]              # error.txt from failed/, or why result.json couldn't be used
    sheets: Tuple[VerifiedSheet, ...] = ()
    part_problems: Dict[str, Tuple[str, ...]] = field(default_factory=dict)  # result numbers that don't add up

    def file(self, name: Optional[str]) -> Optional[Path]:
        if not name:
            return None
        path = self.folder / name
        return path if path.is_file() else None


def verify_sheet(folder: Path, job: Job, sheet: SheetResult) -> VerifiedSheet:
    if not sheet.tap:
        reasons = [e.msg for e in sheet.errors] or ["no program was posted for this sheet"]
        return VerifiedSheet(sheet, None, None, tuple(reasons))
    path = folder / sheet.tap
    if not path.is_file():
        return VerifiedSheet(sheet, None, None, (f"{sheet.tap} is missing from the job folder",))
    data = path.read_bytes()
    problems = []
    if hashlib.sha256(data).hexdigest() != sheet.tap_sha256:
        problems.append(f"{sheet.tap} changed after Fusion checked it")
    counts = {p.part_key: p.count for p in sheet.parts}
    check = check_sheet_program(data, job, sheet.thickness_in, sheet.tool, sheet.outer_order, counts)
    problems += check.problems()
    tool = job.tooling.tools.get(sheet.tool)
    if tool is not None and sheet.tool_guid != tool.guid:
        problems.append(f"sheet used tool GUID {sheet.tool_guid}, expected {tool.guid}")
    return VerifiedSheet(sheet, data, check, tuple(problems))


def part_consistency(job: Job, result: Result) -> Dict[str, Tuple[str, ...]]:
    on_sheets: Dict[str, Dict[int, int]] = {}
    for s in result.sheets:
        for p in s.parts:
            on_sheets.setdefault(p.part_key, {})[s.index] = on_sheets.get(p.part_key, {}).get(s.index, 0) + p.count
    problems: Dict[str, List[str]] = {}
    reported = {p.part_key for p in result.parts}
    for spec in job.parts:
        if spec.part_key not in reported:
            problems.setdefault(spec.part_key, []).append("Fusion reported nothing for this part")
    for part in result.parts:
        issues: List[str] = []
        spec = next((p for p in job.parts if p.part_key == part.part_key), None)
        if spec is None:
            continue
        if part.card_id != spec.card_id:
            issues.append("result card doesn't match the job")
        counts = on_sheets.get(part.part_key, {})
        if not part.errors and not part.deferred:
            if part.qty != spec.qty or part.placed != spec.qty:
                issues.append(f"{part.placed} of {spec.qty} placed")
            if sum(counts.values()) != part.placed:
                issues.append(f"sheets hold {sum(counts.values())} copies but {part.placed} were reported placed")
            if sorted(counts) != sorted(part.sheets) or not part.sheets:
                issues.append("the sheets listed for the part don't match the sheets it's on")
        elif counts:
            issues.append("the part is on a sheet but was reported as rejected or deferred")
        if issues:
            problems.setdefault(part.part_key, []).extend(issues)
    return {k: tuple(v) for k, v in problems.items()}


def ingest(queue: Queue, job_id: str, job: Optional[Job]) -> IngestedJob:
    """`job` is the service's own copy of what it submitted (state/jobs/), never Fusion's copy."""
    done = queue.done / job_id
    if done.is_dir():
        if job is None:
            return IngestedJob(job_id, done, None, None, "the service has no record of this job")
        try:
            result = read_result(done / "result.json")
        except (OSError, ValueError, SchemaError) as e:
            return IngestedJob(job_id, done, job, None, f"result.json can't be read: {e}")
        if result.job_id != job_id:
            return IngestedJob(job_id, done, job, result, f"result.json is for {result.job_id}")
        sheets = tuple(verify_sheet(done, job, s) for s in result.sheets)
        return IngestedJob(job_id, done, job, result, None, sheets, part_consistency(job, result))
    failed = queue.failed / job_id
    error_file = failed / "error.txt"
    error = error_file.read_text(encoding="utf-8").strip() if error_file.exists() else "the job failed"
    return IngestedJob(job_id, failed, job, None, error)
