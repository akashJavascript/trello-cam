"""Ingest a finished job from the hot folder and re-check every program before it can reach Trello.

The service doesn't trust result.json's word that a .tap is safe: it re-runs the same guard, pause
and cut-plan checks (autocam_core.sheetcheck) on the bytes it is about to upload, against its own
copy of the job. It also checks that each part's numbers add up before a card is moved to Nested.
A sheet's FACE program is checked the same way (check_face_program), and a sheet with parts that ask for
facing is only cuttable with a FACE program that faces every one of their copies. A SCRIBE program (bend lines,
check_scribe_program) only for parts that ask for it; one that's missing just means marking them by hand.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from autocam_core.hotfolder import Queue
from autocam_core.schema import SchemaError
from autocam_core.schema_job import Job
from autocam_core.schema_result import ProgramResult, Result, SheetResult, read_result
from autocam_core.sheetcheck import (SheetCheck, check_face_program, check_fit_program, check_scribe_program,
                                     check_sheet_program, check_small_program)


@dataclass(frozen=True)
class VerifiedProgram:
    """One of a sheet's other programs (the FACE program), re-checked like the sheet's own."""
    program: ProgramResult
    tap_bytes: Optional[bytes]
    check: Optional[SheetCheck]
    problems: Tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.tap_bytes is not None and self.check is not None and self.check.passed and not self.problems


@dataclass(frozen=True)
class VerifiedSheet:
    sheet: SheetResult
    tap_bytes: Optional[bytes]
    check: Optional[SheetCheck]
    problems: Tuple[str, ...]           # why it can't be offered for cutting (empty if it can)
    programs: Tuple[VerifiedProgram, ...] = ()   # run besides the main one (the FACE program, first)

    @property
    def cuttable(self) -> bool:
        return (self.tap_bytes is not None and self.check is not None and self.check.passed and not self.problems
                and all(p.ok for p in self.programs))

    @property
    def face(self) -> Optional[VerifiedProgram]:
        return next((p for p in self.programs if p.program.kind == "face"), None)

    @property
    def scribe(self) -> Optional[VerifiedProgram]:
        return next((p for p in self.programs if p.program.kind == "scribe"), None)

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


def verify_program(folder: Path, job: Job, sheet: SheetResult, program: ProgramResult) -> VerifiedProgram:
    if program.kind not in ("face", "scribe", "small", "fit", "outlines"):
        return VerifiedProgram(program, None, None, (f"unknown kind of program {program.kind!r}",))
    if not program.tap:
        return VerifiedProgram(program, None, None, (f"the {program.kind} program {program.name} wasn't offered for "
                                                     "cutting",))
    path = folder / program.tap
    if not path.is_file():
        return VerifiedProgram(program, None, None, (f"{program.tap} is missing from the job folder",))
    data = path.read_bytes()
    problems = []
    if hashlib.sha256(data).hexdigest() != program.tap_sha256:
        problems.append(f"{program.tap} changed after Fusion checked it")
    if program.kind == "fit":
        return _verify_fit(job, sheet, program, data, problems)
    if program.kind == "outlines":
        return _verify_outlines(job, sheet, program, data, problems)
    if program.kind == "scribe":
        return _verify_scribe(job, sheet, program, data, problems)
    if program.kind == "small":
        key = job.tooling.small_features or ""
        check = check_small_program(data, job, sheet.thickness_in, key, {p.part_key: p.count for p in program.parts},
                                    sheet.by_hand)
        problems += [f"small-tool program: {p}" for p in check.problems()]
        tool = job.tooling.tools.get(key)
        if tool is None or program.tool_guid != tool.guid:
            problems.append(f"the small-tool program used tool GUID {program.tool_guid}, not the job's small tool")
        return VerifiedProgram(program, data, check, tuple(problems))
    check = check_face_program(data, job, sheet.thickness_in, {p.part_key: p.count for p in program.parts},
                               sheet.by_hand)
    problems += [f"face program: {p}" for p in check.problems()]
    if job.face is None or program.tool_guid != job.face.tool.guid:
        problems.append(f"the face program used tool GUID {program.tool_guid}, not the job's surfacing bit")
    elif abs(program.depth_in - job.face.depth_in) > 1e-9:
        problems.append(f"the face program skims {program.depth_in:g} in, not the job's {job.face.depth_in:g} in")
    return VerifiedProgram(program, data, check, tuple(problems))


def _verify_fit(job: Job, sheet: SheetResult, program: ProgramResult, data: bytes, problems) -> VerifiedProgram:
    counts = {p.part_key: p.count for p in program.parts}
    spec = next((p for p in job.parts if p.part_key in counts), None)
    outer: Tuple[str, ...] = ()
    if spec is not None and spec.fit is not None and spec.fit.kind == "outline":
        outer = tuple(i for i in sheet.outer_order if i.rsplit("-", 1)[0] == spec.part_key)
    check = check_fit_program(data, job, sheet.thickness_in, sheet.tool, outer, counts, sheet.by_hand)
    problems += [f"{program.name}: {p}" for p in check.problems()]
    tool = job.tooling.tools.get(sheet.tool)
    if tool is None or program.tool_guid != tool.guid:
        problems.append(f"{program.name} used tool GUID {program.tool_guid}, not the sheet's")
    return VerifiedProgram(program, data, check, tuple(problems))


def _verify_outlines(job: Job, sheet: SheetResult, program: ProgramResult, data: bytes, problems) -> VerifiedProgram:
    """The OUTLINES program (core 0.26.0): checked like an outline lap, against the outlines the sheet left for last."""
    counts = {p.part_key: p.count for p in program.parts}
    check = check_fit_program(data, job, sheet.thickness_in, sheet.tool, sheet.outlines_last, counts, sheet.by_hand,
                              what="outlines program")
    problems += [f"{program.name}: {p}" for p in check.problems()]
    tool = job.tooling.tools.get(sheet.tool)
    if tool is None or program.tool_guid != tool.guid:
        problems.append(f"{program.name} used tool GUID {program.tool_guid}, not the sheet's")
    return VerifiedProgram(program, data, check, tuple(problems))


def _outlines_problems(job: Job, sheet: SheetResult, counts: Dict[str, int]) -> List[str]:
    """Every copy of a part whose hole fit is cut out last is left for the OUTLINES program, and only those; it
    cuts them all (core 0.26.0)."""
    late = {p.part_key for p in job.parts if p.fit is not None and p.fit.outline_last and p.part_key in counts}
    want = sorted(i for i in sheet.outer_order if i.rsplit("-", 1)[0] in late)
    out = []
    if sorted(sheet.outlines_last) != want:
        out.append(f"the outlines left for last {sorted(sheet.outlines_last)} aren't the hole-fit parts' {want}")
    programs = [p for p in sheet.programs if p.kind == "outlines"]
    if len(programs) > 1:
        out.append(f"the sheet has {len(programs)} outlines programs")
    if want and not programs:
        out.append("the hole-fit parts' outlines are left for last but the sheet has no outlines program")
    elif programs and not want:
        out.append("the sheet has an outlines program but nothing is left for last")
    elif programs and {q.part_key: q.count for q in programs[0].parts} != {k: counts[k] for k in late}:
        out.append("the outlines program doesn't cut out every copy of the hole-fit parts")
    return out


def _verify_scribe(job: Job, sheet: SheetResult, program: ProgramResult, data: bytes, problems) -> VerifiedProgram:
    check = check_scribe_program(data, job, sheet.thickness_in, {p.part_key: p.count for p in program.parts},
                                 sheet.by_hand)
    problems += [f"scribe program: {p}" for p in check.problems()]
    chamfer = job.scribe.chamfer if job.scribe is not None else None
    if chamfer is None or program.tool_guid != chamfer.guid:
        problems.append(f"the scribe program used tool GUID {program.tool_guid}, not the job's chamfer mill")
    elif abs(program.depth_in - job.scribe.chamfer_depth_in) > 1e-9:
        problems.append(f"the scribe program scribes {program.depth_in:g} in deep, not the job's "
                        f"{job.scribe.chamfer_depth_in:g} in")
    return VerifiedProgram(program, data, check, tuple(problems))


def _fit_problems(job: Job, sheet: SheetResult, counts: Dict[str, int]) -> List[str]:
    """Every fitted part on the sheet has its whole series of laps, in order, with the stocks the job asked for."""
    out = []
    for spec in job.parts:
        if spec.fit is None or spec.part_key not in counts:
            continue
        laps = sorted((p for p in sheet.programs if p.kind == "fit" and any(q.part_key == spec.part_key for q in p.parts)),
                      key=lambda p: p.lap)
        want = spec.fit.stocks_in[1:]
        got = tuple(p.stock_in for p in laps)
        if len(got) != len(want) or any(g is None or abs(g - w) > 1e-6 for g, w in zip(got, want)):
            out.append(f"{spec.name}'s fit laps leave {list(got)}, not the {list(want)} asked for")
        elif any(p.parts[0].count != counts[spec.part_key] for p in laps):
            out.append(f"{spec.name}'s fit laps don't cover every copy")
    return out


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
    check = check_sheet_program(data, job, sheet.thickness_in, sheet.tool, sheet.outer_order, counts, sheet.by_hand,
                                sheet.outlines_last)
    problems += check.problems()
    tool = job.tooling.tools.get(sheet.tool)
    if tool is not None and sheet.tool_guid != tool.guid:
        problems.append(f"sheet used tool GUID {sheet.tool_guid}, expected {tool.guid}")
    programs = tuple(verify_program(folder, job, sheet, p) for p in sheet.programs)
    faces = [p.program for p in programs if p.program.kind == "face"]
    faced = {p.part_key: counts[p.part_key] for p in job.parts if p.face and p.part_key in counts}
    if len(faces) > 1:
        problems.append(f"the sheet has {len(faces)} face programs")
    if faced and not faces:
        problems.append("parts on it ask for facing but the sheet has no face program")
    elif faces and {p.part_key: p.count for p in faces[0].parts} != faced:
        problems.append("the face program doesn't face exactly the copies that ask for it")
    smalls = [p.program for p in programs if p.program.kind == "small"]
    if len(smalls) > 1:
        problems.append(f"the sheet has {len(smalls)} small-tool programs")
    if smalls and sheet.tool != job.tooling.default:
        problems.append("the sheet has a small-tool program but isn't on the default tool")
    for p in smalls:
        if any(counts.get(q.part_key) != q.count for q in p.parts):
            problems.append("the small-tool program doesn't cut every copy of its parts")
    scribes = [p.program for p in programs if p.program.kind == "scribe"]
    if len(scribes) > 1:
        problems.append(f"the sheet has {len(scribes)} scribe programs")
    for p in scribes:
        if any(counts.get(q.part_key) != q.count for q in p.parts):
            problems.append("the scribe program doesn't scribe every copy of its parts")
    endmill = {p.part_key for p in job.parts if p.scribe == "endmill"}
    if any(q.part_key not in endmill or counts.get(q.part_key) != q.count for q in sheet.scribed):
        problems.append("the sheet's program scribes parts that don't ask for it with its cutter")
    problems += _fit_problems(job, sheet, counts)
    problems += _outlines_problems(job, sheet, counts)
    return VerifiedSheet(sheet, data, check, tuple(problems), programs)


def part_consistency(job: Job, result: Result) -> Dict[str, Tuple[str, ...]]:
    on_sheets: Dict[str, Dict[int, int]] = {}
    for s in result.sheets:
        for p in s.parts:
            on_sheets.setdefault(p.part_key, {})[s.index] = (on_sheets.get(p.part_key, {}).get(s.index, 0)
                                                            + p.count * s.repeat)    # by hand: run repeat times
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
