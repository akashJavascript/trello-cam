"""What the service writes on Trello (pure text; no API calls here). Plain text, as short as it can be.

Sheet card title: what to load first, e.g. `6061 1/8in - 4 mm O-flute ALU - 5 parts - 23 min - r017 S1`.
Description: LOAD (stock, cutter, clamps), RUN (program, stops, resume key), CUT ORDER, then details.
"""

from fractions import Fraction
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple

from autocam_core.schema_job import Job, MaterialSpec
from autocam_core.schema_result import PartResult

from .cards import problem_comment
from .results import IngestedJob, VerifiedSheet


def material_label(m: MaterialSpec) -> str:
    return f"{m.name} {m.color}".strip() if m.color else m.name


def thickness_label(t: float) -> str:
    """0.125 -> 1/8in, 0.25 -> 1/4in; anything that isn't a 1/64ths fraction stays decimal."""
    f = Fraction(t).limit_denominator(64)
    if abs(float(f) - t) < 1e-6:
        return f"{f.numerator}/{f.denominator}in" if f.denominator > 1 else f"{f.numerator}in"
    return f"{t:g}in"


def _minutes(seconds: Optional[float]) -> Optional[str]:
    return f"{max(1, round(seconds / 60))} min" if seconds else None


def _clamp_edges(job: Job) -> str:
    width = job.sheet.width_in
    edges = []
    for x0, y0, x1, y1 in job.fixture.clamp_zones_in:
        if y0 <= 0 and y1 < width:
            edges.append("front")
        elif y1 >= width and y0 > 0:
            edges.append("back")
        else:
            edges.append("end")
    return " and ".join(dict.fromkeys(edges)) + " edges only" if edges else "none"


def sheet_title(job: Job, vs: VerifiedSheet) -> str:
    s = vs.sheet
    n = sum(p.count for p in s.parts)
    bits = [f"{material_label(job.material)} {thickness_label(s.thickness_in)}", s.cutter_label,
            f"{n} part{'s' if n != 1 else ''}", _minutes(s.machining_time_s), f"{job.run_id} S{s.index}"]
    title = " - ".join(b for b in bits if b)
    return title if vs.cuttable else f"NOT CUTTABLE - {title}"


def sheet_description(job: Job, ing: IngestedJob, vs: VerifiedSheet, *, resume_key: str,
                      part_cards: Mapping[str, Tuple[str, str]]) -> str:
    """part_cards: part_key -> (part name, card url)."""
    s = vs.sheet
    tool = job.tooling.tools[s.tool]
    m = job.material
    lines: List[str] = []
    if not vs.cuttable:
        lines += ["NOT CUTTABLE. This sheet failed the checks:"] + [f"- {p}" for p in vs.problems] + [""]

    lines += ["LOAD",
              f"Stock: {material_label(m)} {thickness_label(s.thickness_in)} ({s.thickness_in:g}), "
              f"{job.sheet.width_in:g} x {job.sheet.length_in:g}",
              f"Cutter: {s.cutter_label} (T{tool.number}). Check the cutter itself: tool numbers are shared.",
              f"Clamps: {_clamp_edges(job)}. Mist: {'on' if m.use_mist else 'off'}."]
    for forced in s.tool_forced_by:
        name = part_cards.get(forced.part_key, (forced.part_key, ""))[0]
        lines.append(f"This sheet uses the {s.cutter_label} because of {name}: {forced.reason}.")

    if vs.cuttable:
        n = vs.pause_count
        key = resume_key or "the continue key"
        run = f"Program: {s.tap}" + (f" (about {_minutes(s.machining_time_s)})" if s.machining_time_s else "")
        lines += ["", "RUN", run]
        if n:
            lines.append(f"It stops after each part but the last ({n} stops), spindle off. Take the part out, "
                         f"then press {key}. Never press Esc at a stop: it ends the program.")

    lines += ["", "CUT ORDER"]
    counts = {}
    totals = {p.part_key: p.count for p in s.parts}
    for i, instance in enumerate(s.outer_order, 1):
        part_key = instance.rsplit("-", 1)[0]
        counts[part_key] = counts.get(part_key, 0) + 1
        name = part_cards.get(part_key, (part_key, ""))[0]
        of = f" ({counts[part_key]} of {totals[part_key]})" if totals.get(part_key, 1) > 1 else ""
        lines.append(f"{i}. {name}{of}")

    if s.warnings:
        lines += ["", "CHECK"] + [f"- {w.msg}" for w in s.warnings]

    team = ing.result.fusion_team if ing.result else None
    details = []
    if team and team.url:
        details.append(f"Fusion file: {team.url}")
    elif team and team.name:
        details.append(f"Fusion file: {team.name} in {job.fusion_team.project or 'Fusion Team'}")
    if vs.cuttable and vs.check is not None:
        details.append(f"Lowest Z {vs.check.guard.min_z_in:.4f}in (Z0 = spoilboard). Run {job.run_id}.")
    return "\n".join(lines + ([""] + details if details else []))


def part_nested_comment(run_id: str, part: PartResult, sheet_links: Sequence[Tuple[int, str]]) -> str:
    sheets = [f"S{i}" for i, _ in sheet_links]
    where = sheets[0] if len(sheets) == 1 else ", ".join(sheets[:-1]) + " and " + sheets[-1]
    text = f"On sheet{'s' if len(sheets) > 1 else ''} {where} (run {run_id})."
    if part.warnings:
        text += "\nCheck: " + " ".join(w.msg.rstrip(".") + "." for w in part.warnings)
    return text


def part_problem_comment(run_id: str, problems: Iterable[str]) -> str:
    return problem_comment(f"Couldn't CAM this part (run {run_id})", [p[:1].upper() + p[1:] for p in problems])


def part_deferred_comment(run_id: str, part: PartResult) -> str:
    return (f"Didn't fit this run (all {part.qty} have to go on the same run). "
            "It stays in Ready for CAM for the next run.")


def part_bad_sheet_comment(run_id: str, sheet_links: Sequence[Tuple[int, str]]) -> str:
    return "Its sheet failed the safety checks, so it won't be cut. A mentor will look at the sheet card."


def job_failed_comment(job_id: str, failure: str, n_parts: int) -> str:
    return f"{job_id} failed in Fusion: {failure}. Its {n_parts} card(s) stay in Ready for CAM."


def run_started_comment(run_id: str, jobs: Mapping[str, int], rejected: int, untouched: int,
                        stop_reason: Optional[str]) -> str:
    n = sum(jobs.values())
    lines = [f"Run {run_id} started: {n} part{'s' if n != 1 else ''}."]
    if rejected:
        lines.append(f"{rejected} went to Needs fixing.")
    if untouched:
        lines.append(f"{untouched} left in Ready for CAM for the next run.")
    if stop_reason:
        lines.append(f"Stopped early: {stop_reason}")
    return "\n".join(lines)


def run_summary_comment(run_id: str, ingested: Sequence[IngestedJob]) -> str:
    sheets, rejected, deferred, failed = [], 0, 0, []
    for ing in ingested:
        if ing.failure:
            failed.append(f"{ing.job_id} failed: {ing.failure}")
            continue
        sheets += [vs for vs in ing.sheets]
        if ing.result:
            rejected += sum(1 for p in ing.result.parts if p.errors)
            deferred += sum(1 for p in ing.result.parts if p.deferred)
    good = sum(1 for vs in sheets if vs.cuttable)
    lines = [f"Run {run_id} done: {good} sheet{'s' if good != 1 else ''} in Sheet review."]
    bad = len(sheets) - good
    if bad:
        lines.append(f"{bad} sheet{'s' if bad != 1 else ''} NOT CUTTABLE (see the card).")
    if rejected:
        lines.append(f"{rejected} part{'s' if rejected != 1 else ''} went to Needs fixing.")
    if deferred:
        lines.append(f"{deferred} part{'s' if deferred != 1 else ''} didn't fit and stay in Ready for CAM.")
    return "\n".join(lines + failed)
