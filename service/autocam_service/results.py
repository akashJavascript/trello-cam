"""Ingest a finished job from the hot folder and re-check every program before it can reach Trello.

The service doesn't trust result.json's word that a .tap is safe: it re-runs the same guard and
pause verification (autocam_core.sheetcheck) on the bytes it is about to upload.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

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


@dataclass(frozen=True)
class IngestedJob:
    job_id: str
    folder: Path
    job: Optional[Job]
    result: Optional[Result]
    failure: Optional[str]              # error.txt from failed/, or why result.json couldn't be read
    sheets: Tuple[VerifiedSheet, ...] = ()

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
    if sheet.tool not in job.tooling.tools:
        return VerifiedSheet(sheet, None, None, (f"sheet tool {sheet.tool} isn't in the job",))
    check = check_sheet_program(data, job, sheet.thickness_in, sheet.tool, sheet.outer_order)
    problems += check.problems()
    if sheet.tool_guid != job.tooling.tools[sheet.tool].guid:
        problems.append(f"sheet used tool GUID {sheet.tool_guid}, expected {job.tooling.tools[sheet.tool].guid}")
    return VerifiedSheet(sheet, data, check, tuple(problems))


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
        return IngestedJob(job_id, done, job, result, None, sheets)
    failed = queue.failed / job_id
    error_file = failed / "error.txt"
    error = error_file.read_text(encoding="utf-8").strip() if error_file.exists() else "the job failed"
    return IngestedJob(job_id, failed, job, None, error)
