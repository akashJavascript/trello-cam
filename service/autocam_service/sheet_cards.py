"""What the service writes on Trello (pure text; no API calls here). Plain text, as short as it can be.

Sheet card title: what to load first, e.g. `6061 1/8in - 4 mm O-flute ALU - 5 parts - 23 min - r017 S1`.
Description: LOAD (stock, cutter, clamps), RUN (program, stops, resume key), CUT ORDER, then details.
A sheet with faced parts runs its FACE program first, with its own cutter: RUN lists both, in order.
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


def _about(seconds: Optional[float]) -> str:
    return f" · about {_minutes(seconds)}" if seconds else ""


def _clamp_edges(job: Job) -> str:
    return clamp_edges(job.fixture.clamp_zones_in, job.sheet.width_in)


def clamp_edges(zones, width: float) -> str:
    """Which edges the clamp strips are on, from the clamp zones (X across the sheet, Y along it)."""
    edges = []
    for x0, y0, x1, y1 in zones:
        if x0 <= 0 and x1 < width:
            edges.append("left")
        elif x1 >= width and x0 > 0:
            edges.append("right")
        else:
            edges.append("front")
    return " and ".join(dict.fromkeys(edges)) + " edges only" if edges else "none"


def _face(s):
    return next((p for p in s.programs if p.kind == "face"), None)


def _scribe(s):
    return next((p for p in s.programs if p.kind == "scribe"), None)


def _small(s):
    return next((p for p in s.programs if p.kind == "small"), None)


def _outlines(s):
    """The OUTLINES program (core 0.26.0): the hole-fit parts cut out last, after their laps."""
    return next((p for p in s.programs if p.kind == "outlines"), None)


def scribed_keys(s) -> set:
    """Parts whose bend lines this sheet scribes, in its own program or its SCRIBE program."""
    scribe = _scribe(s)
    return {p.part_key for p in s.scribed} | ({p.part_key for p in scribe.parts} if scribe else set())


def fit_series(job: Job, s) -> List[dict]:
    """Each fit series on a sheet: the part, its feature, the stocks and the lap programs in order."""
    out = []
    for n in sorted({p.series for p in s.programs if p.kind == "fit"}):
        laps = sorted((p for p in s.programs if p.kind == "fit" and p.series == n), key=lambda p: p.lap)
        spec = next((p for p in job.parts if p.part_key == laps[0].parts[0].part_key), None)
        if spec is None or spec.fit is None:
            continue
        out.append({"series": n, "part_key": spec.part_key, "name": spec.name, "fit": spec.fit,
                    "feature": laps[0].feature, "copies": laps[0].parts[0].count, "laps": laps,
                    "checklist": f"Fit: {spec.name} {laps[0].feature}"[:60]})
    return out


def fit_items(series: dict) -> List[Tuple[str, Optional[float]]]:
    """The Fit checklist on a sheet card: which run made it fit (the stock it left), or still tight after the last."""
    tight = "too big" if series["fit"].kind == "outline" else "tight"
    items = [(f"Fits after the sheet's program ({series['fit'].stocks_in[0]:+.4g})", series["fit"].stocks_in[0])]
    items += [(f"Fits after {p.name.rsplit('_', 2)[-2]}_{p.lap} ({p.stock_in:+.4g})", p.stock_in) for p in series["laps"]]
    items.append((f"Still {tight} after the last one", None))
    return items


def sheet_title(job: Job, vs: VerifiedSheet, offcut_number: Optional[int] = None) -> str:
    s = vs.sheet
    n = sum(p.count for p in s.parts)
    stock = f"{material_label(job.material)} {thickness_label(s.thickness_in)}" + (
        (f" offcut #{offcut_number}" if offcut_number else " offcut") if s.offcut_id else "")
    if s.by_hand:
        stock += " - placed by hand"
    face = _face(s)
    faced = sum(p.count for p in face.parts) if face else 0
    count = (f"{n} part{'s' if n != 1 else ''}" + (f", {faced} faced" if faced and not s.by_hand else
                                                   (", faced" if faced else ""))
             + (f", run {s.repeat}x" if s.repeat > 1 else ""))
    seconds = (s.machining_time_s or 0) + sum(p.machining_time_s or 0
                                              for p in (face, _scribe(s), _small(s), _outlines(s)) if p)
    cutters = s.cutter_label + (f" + {_small(s).cutter_label}" if _small(s) else "")
    bits = [stock, cutters, count, _minutes(seconds), f"{job.run_id} S{s.index}"]
    title = " - ".join(b for b in bits if b)
    return title if vs.cuttable else f"NOT CUTTABLE - {title}"


def _by_hand_load(job: Job, vs: VerifiedSheet, part_cards: Mapping[str, Tuple[str, str]]) -> List[str]:
    """Where a part placed by hand goes: any piece, anywhere; X/Y zero at the part's box corner."""
    s = vs.sheet
    name = part_cards.get(s.parts[0].part_key, (s.parts[0].part_key, ""))[0] if s.parts else "the part"
    stock = f"{material_label(job.material)} {thickness_label(s.thickness_in)}"
    box = vs.check.guard.cut_box_in if vs.check is not None else None
    lines = [f"Stock: any piece of {stock}, anywhere on the bed ({name} is placed by hand, not nested)."]
    if box is not None:
        x0, y0, x1, y1 = box
        lines += [f"Zero X and Y at the front-left corner of {name}'s box (the part's own corner, not the "
                  f"piece's). The cutter reaches X {x0:.2f} to {x1:.2f} and Y {y0:.2f} to {y1:.2f} in from "
                  f"there ({x1 - x0:.1f} x {y1 - y0:.1f} in), so the piece must cover that.",
                  "Z from the fixed tool setter (the spoilboard). Clamps: anywhere at least 1/2 in clear of that area."]
    else:
        lines += [f"Zero X and Y at the front-left corner of {name}'s box; Z from the fixed tool setter (the spoilboard)."]
    return lines


def sheet_description(job: Job, ing: IngestedJob, vs: VerifiedSheet, *, resume_key: str,
                      part_cards: Mapping[str, Tuple[str, str]], program: Optional[str] = None,
                      stops: bool = True, stock: Optional[str] = None) -> str:
    """part_cards: part_key -> (part name, card url). program/stops: the version on the card when the
    "without stopping" option is on. stock: what to load instead of a new sheet (an offcut)."""
    s = vs.sheet
    tool = job.tooling.tools[s.tool]
    m = job.material
    face = _face(s)
    scribe = _scribe(s)
    small = _small(s)
    lines: List[str] = []
    if not vs.cuttable:
        problems = list(vs.problems) + [p for v in vs.programs for p in v.problems]
        lines += ["NOT CUTTABLE. This sheet failed the checks:"] + [f"- {p}" for p in problems] + [""]
    first = next((p for p in (face, scribe, small) if p), None)
    cutter = (f"Cutter: {first.cutter_label} (T{first.tool_number}) first" if first else
              f"Cutter: {s.cutter_label} (T{tool.number})") + " (check the cutter itself: tool numbers are shared)"

    if s.by_hand:
        lines += ["LOAD"] + _by_hand_load(job, vs, part_cards) + [f"{cutter}. Mist: {'on' if m.use_mist else 'off'}."]
    else:
        lines += ["LOAD",
                  stock or f"Stock: {material_label(m)} {thickness_label(s.thickness_in)} ({s.thickness_in:g}), "
                           f"{job.sheet.width_in:g} x {job.sheet.length_in:g}",
                  f"{cutter}. Clamps: {_clamp_edges(job)}. Mist: {'on' if m.use_mist else 'off'}."]
    for forced in s.tool_forced_by:
        name = part_cards.get(forced.part_key, (forced.part_key, ""))[0]
        lines.append(f"This sheet uses the {s.cutter_label} because of {name}: {forced.reason}.")

    if vs.cuttable:
        # one line per program, in the order they're run (2026-11-05, the user: shorter; the machine screen walks
        # through the same steps)
        n = vs.pause_count
        key = resume_key or "the continue key"
        steps = []
        doing = {"face": "skims the faced tops", "scribe": "scribes the bend lines", "small": "the small features"}
        for p in (face, scribe, small):
            if p:
                what = (p.feature or doing["small"]) if p.kind == "small" else doing[p.kind]
                steps.append(f"{p.tap} · {p.cutter_label} (T{p.tool_number})" + _about(p.machining_time_s)
                             + f" · {what}")
        main = f"{program or s.tap}" + (f" · {s.cutter_label} (T{tool.number})" if steps else "") + \
            _about(s.machining_time_s)
        if s.by_hand and s.repeat > 1:
            main += f" · run it {s.repeat} times, zeroed at a fresh spot each time"
        if n and stops:
            main += f" · stops after each part: take it out, then press {key} (never Esc: it ends the program)"
        elif n:
            main += " · no stops: hands off until the spindle stops"
        steps.append(main)
        for f in fit_series(job, s):
            steps += fit_lines(f, s)
        late = _outlines(s)
        if late:
            names = ", ".join(dict.fromkeys(part_cards.get(p.part_key, (p.part_key, ""))[0] for p in late.parts))
            steps.append(f"{late.tap}" + _about(late.machining_time_s) + f" · once it fits: cuts out {names}")
        lines += ["", "RUN"] + ([f"{i}. {t}" for i, t in enumerate(steps, 1)] if len(steps) > 1 else steps)
    on_sheet = {p.part_key for p in s.parts}
    faced = {p.part_key for p in face.parts} if face else set()
    scribed = scribed_keys(s)
    fitted = {f["part_key"] for f in fit_series(job, s)}
    tabbed = {p.part_key for p in job.parts if p.tabs} & on_sheet
    if vs.cuttable and tabbed == on_sheet:
        lines.append("Every part (and slug) is held by small tabs: break them out at the end.")
        tabbed = set()                                # nothing to mark
    elif vs.cuttable and tabbed:
        lines.append("Parts marked tabs (and their slugs) are held by small tabs: break them out at the end.")
    bent_keys = {p.part_key for p in job.parts if p.bends}
    spare_keys = {p.part_key for p in job.parts if p.spare} & on_sheet
    if spare_keys:
        lines.append("Parts marked spare go in the spares box.")
    lines += ["", "CUT ORDER"]
    counts = {}
    totals = {p.part_key: p.count for p in s.parts}
    for i, instance in enumerate(s.outer_order, 1):
        part_key = instance.rsplit("-", 1)[0]
        counts[part_key] = counts.get(part_key, 0) + 1
        name = part_cards.get(part_key, (part_key, ""))[0]
        of = f" ({counts[part_key]} of {totals[part_key]})" if totals.get(part_key, 1) > 1 else ""
        marks = [m for m, on in (("faced", part_key in faced), ("fit", part_key in fitted),
                                 ("cut out last", instance in s.outlines_last),
                                 ("tabs", part_key in tabbed), ("bent", part_key in bent_keys),
                                 ("scribed", part_key in scribed), ("spare", part_key in spare_keys)) if on]
        lines.append(f"{i}. {name}{of}" + "".join(f" - {m}" for m in marks))

    bent = [p for p in job.parts if p.bends and p.part_key in on_sheet]
    if bent:
        lines += ["", "BEND AFTER CUTTING (these are cut flat)"]
        for p in bent:
            lines.append(f"- {part_cards.get(p.part_key, (p.name, ''))[0]}: {bend_text(p.bends)}"
                         + (f" ({m.name} can crack at a tight bend: check it)" if m.name == "6061" else ""))
        if any(p.part_key in scribed for p in bent):
            lines.append("Scribed parts have a line down the middle of each bend on the side that was up, with a U or "
                         "D beside it: U, bend the flange up toward the scribed side (it ends up inside the bend); D, "
                         "bend it away. Line the scribed line up with the brake.")

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
        if vs.face is not None and vs.face.check is not None:
            details.append(f"Face program: lowest Z {vs.face.check.guard.min_z_in:.4f}in, "
                           f"{s.thickness_in - face.depth_in:.4f} asked.")
    return "\n".join(lines + ([""] + details if details else []))


def own_program_description(*, program: str, placed: str, part: str, part_url: str, stock: str, cutter_in: float,
                            clamps: str, spot, lowest_z: Optional[float], offsets=(), of_qty: int = 1) -> str:
    """The card for someone's own program that AutoCAM placed (own_programs.py). offsets: each copy's (dx, dy)
    when the card cuts it more than once; of_qty: how many the part card asked for in all."""
    x0, y0, x1, y1 = spot.cut_loaded
    k = max(len(offsets), 1)
    if k > 1:
        run = [f"Program: {placed}. It cuts {program} {k} times side by side" + (f" ({k} of the {of_qty})" if of_qty > k
                                                                                    else "")
               + ", with its zero at the piece's front-left corner: zero X/Y there, as for AutoCAM's sheets, and Z "
               "from the fixed tool setter (Z0 = spoilboard).",
               f"It cuts {x0:.1f} to {x1:.1f} in across and {y0:.1f} to {y1:.1f} in along the piece as loaded (from "
               "its left edge and the front).",
               f"Or run {program} itself {k} times: zero X/Y at the piece's front-left corner, move to "
               + ", then ".join(f"X {dx:.4f} Y {dy:.4f}" for dx, dy in offsets)
               + ", zeroing X/Y again at each before running it."]
    else:
        run = [f"Program: {placed}. It's {program} moved so its zero is the piece's front-left corner: zero X/Y there, "
               "as for AutoCAM's sheets, and Z from the fixed tool setter (Z0 = spoilboard)."
               + (f" It's 1 of the {of_qty}." if of_qty > 1 else ""),
               f"It cuts {x0:.1f} to {x1:.1f} in across and {y0:.1f} to {y1:.1f} in along the piece as loaded (from "
               "its left edge and the front).",
               f"Or run {program} itself: zero X/Y at the piece's front-left corner, move to X {spot.dx:.4f} Y "
               f"{spot.dy:.4f}, and zero X/Y again there."]
    return "\n".join([
        "LOAD",
        stock,
        f"Cutter: the one {program} was made for (it's checked as up to {cutter_in:g} in across). Clamps: {clamps}.",
        "",
        "RUN",
        *run,
        "",
        f"{part} ({part_url}) was CAM'd by hand: AutoCAM only placed it and checked the moved program"
        + (f" (lowest Z {lowest_z:.4f}, clear of the clamps)" if lowest_z is not None else "")
        + ". Check the toolpaths yourself before ticking Review.",
        f"Stock not there? Move {part} back to Ready for CAM: it gets another spot and this card is replaced.",
    ])


def own_by_hand_description(*, program: str, part: str, part_url: str, stock: str, cutter_in: float, box,
                             margin_in: float, lowest_z: Optional[float], qty: int = 1) -> str:
    """The card for someone's own program that they place by hand ("Place it myself"): the program as it is."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    return "\n".join([
        "LOAD",
        f"Stock: a piece of {stock} at least {w + 2 * margin_in:.1f} x {h + 2 * margin_in:.1f} in (the cutter covers "
        f"{w:.1f} x {h:.1f} in), from the offcut rack or the scrap pile. Hold it down clear of the cut.",
        f"Cutter: the one {program} was made for (it's checked as up to {cutter_in:g} in across).",
        "",
        "RUN",
        f"Program: {program}, as it was made. Zero X/Y on the piece where its zero goes: it cuts X {x0:+.2f} to "
        f"{x1:+.2f} in and Y {y0:+.2f} to {y1:+.2f} in from there. Z from the fixed tool setter (Z0 = spoilboard).",
        *([f"Cut {qty}: run it {qty} times, zeroing X/Y at a fresh spot (or piece) each time, at least "
           f"{margin_in:g} in from the last cut."] if qty > 1 else []),
        "",
        f"{part} ({part_url}) was CAM'd by hand and is placed by hand: AutoCAM only checked the program"
        + (f" (lowest Z {lowest_z:.4f})" if lowest_z is not None else "")
        + ". Check the toolpaths yourself before ticking Review.",
        "Cut it from one of AutoCAM's numbered offcuts? Add a line to that offcut's card like Cut by hand: 12 to 18 "
        "along, 5 to 10 across, so later runs keep clear of it.",
    ])


def bend_text(bends) -> str:
    """Bend A 90 up, then Bend B 45 down (R 0.09 in)."""
    known = [b for b in bends if b.direction]
    if not known:
        return "its bends weren't read: check the part in Onshape"
    parts = [f"{b.name} {b.angle_deg:g}\u00b0 {b.direction}" for b in known]
    radii = sorted({round(b.radius_in, 3) for b in known if b.radius_in})
    return ", then ".join(parts) + (f" (R {', '.join(f'{r:g}' for r in radii)} in)" if radii else "")


def fit_lines(f: dict, s) -> List[str]:
    """What the sheet card says about one fit series: one line (2026-11-05: shorter)."""
    hole = f["fit"].kind == "hole"
    copies = f" ({f['copies']} copies)" if f["copies"] > 1 else ""
    what = f"{f['name']}'s {f['feature']}" + ("s" if hole and f["copies"] > 1 else "") + copies
    tight = "tight" if hole else "big"
    taps = [p.tap for p in f["laps"]]
    laps = taps[0] if len(taps) == 1 else f"{taps[0]} to _{'_'.join(taps[-1].rsplit('_', 2)[-2:])}"
    late = hole and any(i.rsplit("-", 1)[0] == f["part_key"] for i in s.outlines_last)
    stay = "" if late else f" Leave {f['name']} in at its stop."
    first = "Break the slug out by hand, then try it." if f["laps"][0].cutout else "Try it."
    return [f"Fit {what}: cut {f['fit'].stocks_in[0]:.4g} in {tight} on purpose.{stay} {first} Still too {tight}? "
            f"Run {laps}, one at a time, until it fits. Tick the one that did under \"{f['checklist']}\"."]


def fit_recorded_comment(name: str, feature: str, stock: float, next_start: float) -> str:
    return (f"Recorded: {name}'s {feature} fit at {stock:+.4g} in of radial stock. The next sheet with it starts at "
            f"{next_start:+.4g}.")


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


def air_test_comment(name: str, lift_in: float, gap_in: float, feed_ipm: float, why: str = "") -> str:
    """why: the sheet is a risky one and AutoCAM ticked the box itself (runner._risky)."""
    return (f"Air test added: {name}. It traces each part's outline once, at {feed_ipm:g} in/min, raised {lift_in:g} in "
            f"so the cutter stays {gap_in:g} in above the sheet and cuts nothing. Run it before the real program."
            + (f" AutoCAM ticked it because {why}: untick it under Options if you don't need it." if why else ""))


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
