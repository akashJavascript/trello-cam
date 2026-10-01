"""What the service writes on Trello (pure text; no API calls here).

Sheet card title, e.g. `6061 0.125 - 4 mm O-flute ALU - S1 (5 pauses) - r017` (brief, "Trello model").
The description says which physical cutter to load (decision 17), the resume key and pause count
(decision 19), the lowest Z (decision 22) and the cut order.
"""

from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from autocam_core.schema_job import Job, MaterialSpec
from autocam_core.schema_result import PartResult

from .results import IngestedJob, VerifiedSheet


def material_label(m: MaterialSpec) -> str:
    return f"{m.name} {m.color}".strip() if m.color else m.name


def pause_count(vs: VerifiedSheet) -> int:
    p = vs.sheet.pauses
    return len(p.entries) if p else 0


def sheet_title(job: Job, vs: VerifiedSheet) -> str:
    s = vs.sheet
    title = (f"{material_label(job.material)} {s.thickness_in:g} - {s.cutter_label} - S{s.index} "
             f"({pause_count(vs)} pauses) - {job.run_id}")
    return title if vs.cuttable else f"NOT CUTTABLE - {title}"


def sheet_description(job: Job, ing: IngestedJob, vs: VerifiedSheet, *, resume_key: str,
                      part_cards: Mapping[str, Tuple[str, str]]) -> str:
    """part_cards: part_key -> (part name, card url)."""
    s = vs.sheet
    tool = job.tooling.tools[s.tool]
    lines: List[str] = []
    if not vs.cuttable:
        lines += ["**This program is NOT offered for cutting.**"] + [f"- {p}" for p in vs.problems] + [""]
    lines.append(f"**LOAD: {s.cutter_label}** (T{tool.number}). Both 4 mm cutters are T1, so check the cutter itself.")
    if vs.cuttable and vs.check is not None:
        lines.append(f"Program: `{s.tap}`, lowest Z {vs.check.guard.min_z_in:.4f} in (Z0 = stock bottom).")
    n = pause_count(vs)
    key = resume_key or "the start/continue key (not confirmed yet; see docs/manual-tests.md)"
    lines.append(f"Pauses: {n}. The machine stops after each part with the spindle off. Remove the part, "
                 f"then resume with {key}. **Never press Esc at a pause**: it aborts the job.")
    if s.machining_time_s:
        lines.append(f"Estimated machining time: {s.machining_time_s / 60:.0f} min.")
    team = ing.result.fusion_team if ing.result else None
    if team and team.url:
        lines.append(f"Fusion file: {team.url}")
    elif team and team.name:
        lines.append(f"Fusion file: {team.name} (Fusion Team project {job.fusion_team.project or '?'})")
    for forced in s.tool_forced_by:
        name = part_cards.get(forced.part_key, (forced.part_key, ""))[0]
        lines.append(f"Uses the {s.cutter_label} because of {name}: {forced.reason}.")
    lines += ["", "**Cut order** (one pause after each part but the last):"]
    for i, instance in enumerate(s.outer_order, 1):
        part_key = instance.rsplit("-", 1)[0]
        name, url = part_cards.get(part_key, (part_key, ""))
        lines.append(f"{i}. {name} ({instance})" + (f" {url}" if url else ""))
    warnings = [w.msg for w in s.warnings]
    if warnings:
        lines += ["", "**Warnings**"] + [f"- {w}" for w in warnings]
    return "\n".join(lines)


def part_nested_comment(run_id: str, part: PartResult, sheet_links: Sequence[Tuple[int, str]]) -> str:
    where = ", ".join(f"S{i} {url}" for i, url in sheet_links)
    text = f"Nested in run {run_id}: {part.placed} of {part.qty} on {where}."
    if part.warnings:
        text += "\nCheck before cutting:\n" + "\n".join(f"- {w.msg}" for w in part.warnings)
    return text


def part_problem_comment(run_id: str, problems: Iterable[str]) -> str:
    return (f"This part couldn't be CAM'd in run {run_id}:\n" + "\n".join(f"- {p}" for p in problems)
            + "\n\nFix it and move the card back to Ready for CAM.")


def part_deferred_comment(run_id: str, part: PartResult) -> str:
    return (f"Only {part.placed} of {part.qty} fit on the sheets in run {run_id}, so none were cut this time. "
            "The card stays in Ready for CAM for the next run.")


def part_bad_sheet_comment(run_id: str, sheet_links: Sequence[Tuple[int, str]]) -> str:
    where = ", ".join(f"S{i} {url}" for i, url in sheet_links)
    return (f"Nested in run {run_id} on {where}, but that sheet's program was rejected, so it won't be cut. "
            "A mentor should check the sheet card; move this card back to Ready for CAM to try again.")


def job_failed_comment(job_id: str, failure: str, n_parts: int) -> str:
    return (f"Job {job_id} failed in Fusion: {failure}\n"
            f"Its {n_parts} part card(s) stay in Ready for CAM.")


def run_started_comment(run_id: str, jobs: Mapping[str, int], rejected: int, untouched: int,
                        stop_reason: Optional[str]) -> str:
    lines = [f"Run {run_id} started: {sum(jobs.values())} part card(s) in {len(jobs)} job(s)."]
    lines += [f"- {job_id}: {n} part card(s)" for job_id, n in jobs.items()]
    if rejected:
        lines.append(f"{rejected} card(s) went to Needs fixing (see their comments).")
    if untouched:
        lines.append(f"{untouched} card(s) were left in Ready for CAM for the next run.")
    if stop_reason:
        lines.append(f"Stopped early: {stop_reason}")
    return "\n".join(lines)


def run_summary_comment(run_id: str, ingested: Sequence[IngestedJob]) -> str:
    lines = [f"Run {run_id} finished."]
    for ing in ingested:
        if ing.failure:
            lines.append(f"- {ing.job_id}: FAILED ({ing.failure})")
            continue
        for vs in ing.sheets:
            z = f"lowest Z {vs.check.guard.min_z_in:.4f} in" if vs.cuttable and vs.check else "NOT CUTTABLE"
            lines.append(f"- {vs.sheet.name}: {sum(p.count for p in vs.sheet.parts)} part(s), {z}")
        rejected = [p.part_key for p in ing.result.parts if p.errors] if ing.result else []
        deferred = [p.part_key for p in ing.result.parts if p.deferred] if ing.result else []
        if rejected:
            lines.append(f"  {len(rejected)} part(s) rejected")
        if deferred:
            lines.append(f"  {len(deferred)} part(s) didn't fit and stay queued")
    lines.append("Every sheet needs a human review before Ready to cut.")
    return "\n".join(lines)
