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
    """Which edges the clamp strips are on, from the clamp zones (X across the sheet, Y along it)."""
    width = job.sheet.width_in
    edges = []
    for x0, y0, x1, y1 in job.fixture.clamp_zones_in:
        if x0 <= 0 and x1 < width:
            edges.append("left")
        elif x1 >= width and x0 > 0:
            edges.append("right")
        else:
            edges.append("front")
    return " and ".join(dict.fromkeys(edges)) + " edges only" if edges else "none"


def sheet_title(job: Job, vs: VerifiedSheet) -> str:
    s = vs.sheet
    n = sum(p.count for p in s.parts)
    stock = f"{material_label(job.material)} {thickness_label(s.thickness_in)}" + (" offcut" if s.offcut_id else "")
    bits = [stock, s.cutter_label,
            f"{n} part{'s' if n != 1 else ''}", _minutes(s.machining_time_s), f"{job.run_id} S{s.index}"]
    title = " - ".join(b for b in bits if b)
    return title if vs.cuttable else f"NOT CUTTABLE - {title}"


def sheet_description(job: Job, ing: IngestedJob, vs: VerifiedSheet, *, resume_key: str,
                      part_cards: Mapping[str, Tuple[str, str]], program: Optional[str] = None,
                      stops: bool = True, stock: Optional[str] = None) -> str:
    """part_cards: part_key -> (part name, card url). program/stops: the version on the card when the
    "without stopping" option is on. stock: what to load instead of a new sheet (an offcut)."""
    s = vs.sheet
    tool = job.tooling.tools[s.tool]
    m = job.material
    lines: List[str] = []
    if not vs.cuttable:
        lines += ["NOT CUTTABLE. This sheet failed the checks:"] + [f"- {p}" for p in vs.problems] + [""]

    lines += ["LOAD",
              stock or f"Stock: {material_label(m)} {thickness_label(s.thickness_in)} ({s.thickness_in:g}), "
                       f"{job.sheet.width_in:g} x {job.sheet.length_in:g}",
              f"Cutter: {s.cutter_label} (T{tool.number}). Check the cutter itself: tool numbers are shared.",
              f"Clamps: {_clamp_edges(job)}. Mist: {'on' if m.use_mist else 'off'}."]
    for forced in s.tool_forced_by:
        name = part_cards.get(forced.part_key, (forced.part_key, ""))[0]
        lines.append(f"This sheet uses the {s.cutter_label} because of {name}: {forced.reason}.")

    if vs.cuttable:
        n = vs.pause_count
        key = resume_key or "the continue key"
        run = f"Program: {program or s.tap}" + (f" (about {_minutes(s.machining_time_s)})" if s.machining_time_s else "")
        lines += ["", "RUN", run]
        if n and stops:
            lines.append(f"It stops after each part but the last ({n} stops), spindle off. Take the part out, "
                         f"then press {key}. Never press Esc at a stop: it ends the program.")
        elif n:
            lines.append("It cuts the whole sheet without stopping: cut parts stay loose in the sheet until it's "
                         "done. Keep hands off until the spindle stops at the end.")

    lines += ["", "CUT ORDER"]
    counts = {}
    totals = {p.part_key: p.count for p in s.parts}
    for i, instance in enumerate(s.outer_order, 1):
        part_key = instance.rsplit("-", 1)[0]
        counts[part_key] = counts.get(part_key, 0) + 1
        name = part_cards.get(part_key, (part_key, ""))[0]
        of = f" ({counts[part_key]} of {totals[part_key]})" if totals.get(part_key, 1) > 1 else ""
        lines.append(f"{i}. {name}{of}")

    use = sheet_use(s)
    if use:
        lines += ["", use]

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


def sheet_use(s) -> Optional[str]:
    """How full the sheet is, from the worker's numbers (None from workers older than 2026-10-02)."""
    if not s.parts_area_in2 or not s.usable_area_in2:
        return None
    pct = round(100 * s.parts_area_in2 / s.usable_area_in2)
    text = f"Sheet use: {pct}% of the cutting area is parts"
    if s.free_length_in is not None and s.free_length_in >= 1:
        text += f", and the back {s.free_length_in:.0f} in are empty"
    return text + "."


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


def part_bumped_comment(run_id: str) -> str:
    """A part that was on an open sheet and no longer fits once new parts joined it."""
    return f"Didn't fit with the new parts (run {run_id}), so it's back in Ready for CAM. The next run nests it again."


def part_waiting_comment(run_id: str) -> str:
    return "Waiting for the next run: someone started reviewing the sheet it was going on."


def joined_sheet_failed(problems: Sequence[str]) -> str:
    why = "; ".join(dict.fromkeys(problems)) or "no reason given"
    return (f"With this part added, the sheet failed the safety checks ({why}). The sheet was left as it was. "
            "A mentor should look at it")


def air_test_comment(name: str, lift_in: float, gap_in: float, feed_ipm: float) -> str:
    return (f"Air test added: {name}. It traces each part's outline once, at {feed_ipm:g} in/min, raised {lift_in:g} in "
            f"so the cutter stays {gap_in:g} in above the sheet and cuts nothing. Run it before the real program.")


def air_test_failed_comment(why: str) -> str:
    return f"Couldn't make the air test: {why}."


def old_layout_comment() -> str:
    return ("Archived: this sheet was laid out with its length along the machine's X, but X runs across the bed, so "
            "its program was turned 90 degrees. Don't cut it. Its parts went back to Ready for CAM to be nested again.")


def part_old_layout_comment() -> str:
    return "Back in Ready for CAM: its sheet was laid out the wrong way round for the machine and was archived."


def fusion_down_comment(jobs: int) -> str:
    return (f"Fusion isn't running, and {jobs} job{'s are' if jobs != 1 else ' is'} waiting for it. Start Fusion "
            "with the auto-CAM add-in on the shop PC.")


def fusion_back_comment() -> str:
    return "Fusion is running again. The waiting jobs carry on."


def options_failed_comment(why: str) -> str:
    return f"Couldn't apply the options: {why}."


def no_stop_comment(name: str, on: bool) -> str:
    return (f"Now cuts the whole sheet without stopping: {name}." if on
            else f"Back to stopping after each part: {name}.")


def sheet_rebuilt_comment(run_id: str) -> str:
    return f"Rebuilt with new parts (run {run_id}). The checklist was reset: check it again."


def sheet_retired_comment(run_id: str) -> str:
    return f"Not needed any more: its parts were nested again in run {run_id}."


def part_bad_sheet_comment(run_id: str, sheet_links: Sequence[Tuple[int, str]]) -> str:
    return "Its sheet failed the safety checks, so it won't be cut. A mentor will look at the sheet card."


def job_failed_comment(job_id: str, failure: str, n_parts: int) -> str:
    return f"{job_id} failed in Fusion: {failure}. Its {n_parts} card(s) stay in Ready for CAM."


def part_job_failed_comment(failure: str) -> str:
    return f"CAM failed this run ({failure}). The card stays in Ready for CAM: move it out and back to try again."


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'s' if count != 1 else ''}"


def run_started_comment(run_id: str, jobs: Mapping[str, int], rejected: int, untouched: int,
                        stop_reason: Optional[str], carried: int = 0, later: int = 0) -> str:
    n = sum(jobs.values())
    head = f"Run {run_id} started: {_n(n, 'part')}"
    lines = [head + (f", added to open sheets with {carried} already there." if carried else ".")]
    if rejected:
        lines.append(f"{rejected} went to Needs fixing.")
    if later:
        lines.append(f"{later} wait for the next run (Onshape calls per run are limited).")
    if untouched:
        lines.append(f"{untouched} left in Ready for CAM.")
    if stop_reason:
        lines.append(f"Stopped early: {stop_reason}")
    return "\n".join(lines)


def run_summary_comment(run_id: str, ingested: Sequence[IngestedJob],
                        sheet_counts: Sequence[Mapping[str, int]] = ()) -> str:
    """sheet_counts: per job, how many sheet cards publishing made ("new"), rebuilt ("updated") and how many
    of those can't be cut ("bad")."""
    rejected, deferred, failed = 0, 0, []
    for ing in ingested:
        if ing.failure:
            failed.append(f"{ing.job_id} failed: {ing.failure}")
            continue
        if ing.result:
            rejected += sum(1 for p in ing.result.parts if p.errors)
            deferred += sum(1 for p in ing.result.parts if p.deferred)
    new, updated, bad = (sum(c.get(k, 0) for c in sheet_counts) for k in ("new", "updated", "bad"))
    made = [x for x in (_n(new, "new sheet") if new else "", f"{_n(updated, 'sheet')} updated" if updated else "") if x]
    lines = [f"Run {run_id} done: {' and '.join(made)} in Sheet review." if made else
             f"Run {run_id} done: no sheet changed."]
    if bad:
        lines.append(f"{_n(bad, 'sheet')} NOT CUTTABLE (see the card).")
    if rejected:
        lines.append(f"{rejected} part{'s' if rejected != 1 else ''} went to Needs fixing.")
    if deferred:
        lines.append(f"{deferred} part{'s' if deferred != 1 else ''} didn't fit and stay in Ready for CAM.")
    return "\n".join(lines + failed)
