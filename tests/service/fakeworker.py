"""A fake Fusion worker: consumes hot-folder jobs and writes results the way the real add-in will.

Programs are post-shaped text (see tests/fixtures/taps/README.md) with one `[outer <instance>]` op per
part copy; pauses are inserted with the production code and checked with autocam_core.sheetcheck.
"""

from typing import List, Optional

from autocam_core import CORE_VERSION
from autocam_core.errors import Issue
from autocam_core.hotfolder import Queue
from autocam_core.names import instance_id, program_name
from autocam_core.offcuts import beside_as_loaded, placement_for
from autocam_core.pauses import insert
from autocam_core.schema_job import read_job
from autocam_core.schema_result import (
    RESULT_SCHEMA, GuardSummary, HoleCounts, PartResult, PauseEntryResult, PauseSummary, PostResult, Result,
    SheetPart, SheetResult, WorkerInfo, overall_status, result_json,
)
from autocam_core.sheetcheck import check_sheet_program, pause_spec


def make_program(name: str, instances: List[str], thickness: float, mist: bool) -> str:
    clear, retract = thickness + 2.0, thickness + 0.2
    lines = [f"[{name}]"] + (["M11 C8"] if mist else []) + ["G90", "G20", "G53 Z", "[inner]", "S18000", "M3", "G4 X4."]
    for k, inst in enumerate(instances):          # one after another along the sheet's length (Y)
        y = 2 + 4 * k
        lines += [f"[outer {inst}]", f"G0 X4. Y{y}.", f"G0 Z{retract:g}", "G1 Z0. F20.", "G1 X7. F60.",
                  f"G1 Y{y + 3}.", "G1 X4.", f"G1 Y{y}.", f"G0 Z{clear:g}"]
    lines += (["M12 C8"] if mist else []) + ["G53 Z", "M5", "G53 P10"]
    return "\r\n".join(lines) + "\r\n"


def run_fake_worker(queue: Queue, *, reject_sheet: bool = False, fail_job: Optional[str] = None,
                    defer_part: Optional[str] = None, part_error: Optional[str] = None,
                    shallow_outlines: bool = False, beside: bool = False) -> List[str]:
    """beside: report room beside the parts cut and fill an offcut's room beside earlier cuts first, like the
    real worker (off by default so the older tests' offcut cards keep their plain titles)."""
    processed = []
    for job_id in queue.pending():
        claim = queue.claim(job_id)
        job = read_job(claim.job_path)
        processed.append(job_id)
        if job_id == fail_job:
            queue.fail(job_id, "Fusion error: something broke")
            continue
        thickness = job.material.thicknesses_in[1] if len(job.material.thicknesses_in) > 1 else job.material.thicknesses_in[0]
        tool = job.tooling.default
        placed = [p for p in job.parts if p.part_key not in (defer_part, part_error)]
        instances = [instance_id(p.part_key, n) for p in placed for n in range(1, p.qty + 1)]
        name = program_name(job.material.program_prefix, thickness, job.run_id, 1)
        program = make_program(name, instances, thickness, job.material.use_mist)
        if job.pauses.enabled:
            program = insert(program, instances, pause_spec(job), job.pauses.after_last_part)
        if reject_sheet:
            program = program.replace("G1 Z0. F20.", "G1 Z-0.02 F20.", 1)
        if shallow_outlines:  # template bottom height left at the selected (top) face
            program = program.replace("G1 Z0. F20.", f"G1 Z{thickness:g} F20.")
        data = program.encode("ascii")
        check = check_sheet_program(data, job, thickness, tool, instances, {p.part_key: p.qty for p in placed})
        tap_name = f"{name}.tap" if check.passed else f"{name}.REJECTED.tap"
        (claim.out_dir / tap_name).write_bytes(data)
        (claim.out_dir / f"{name}.png").write_bytes(b"\x89PNG fake preview")
        g = check.guard
        # Like the pipeline: the first offcut of this thickness with room goes before a new sheet, the same way
        # round as its last cut if that has room. Each part takes about 4 in along X from the start of the stretch.
        # With beside: the parts (3 x 3 in, at X 4 to 7) all go in an offcut's first room beside earlier cuts if
        # it has one, else the free stretch, which leaves the room right of them (7.75 in on) as deep as its band.
        region = job.fixture.nest_region_in
        offcut, place, room = None, None, None
        for o in job.offcuts:
            if abs(o.thickness_in - thickness) > 1e-6:
                continue
            rooms = beside_as_loaded(o.beside_in, job.sheet.width_in, job.sheet.length_in, o.last_turned, region,
                                     job.nest.offcut_beside_min_in) if beside else []
            if rooms:
                offcut, room = o, rooms[0]
                break
            ways = [placement_for(o.used_in, job.sheet.length_in, region[1], region[3], job.nest.offcut_gap_in,
                                  job.nest.offcut_min_in, t) for t in (o.last_turned, not o.last_turned)]
            p = ways[0] or ways[1]
            if p is not None:
                offcut, place = o, p
                break
        start = place.x0 if place else region[1]
        used_x = (round(start, 3), round(start + 4.0 * len(instances), 3)) if instances and not room else None
        beside_used = (room[0],) if room else ()
        beside_left = ((7.75, used_x[0], region[2], used_x[1]),) if beside and used_x else ()
        turned = offcut.last_turned if room else bool(place and place.turned)
        sheet = SheetResult(
            index=1, name=name, stock_type=f"{job.material.key}-{thickness:g}", thickness_in=thickness, tool=tool,
            tool_guid=job.tooling.tools[tool].guid, cutter_label=job.tooling.tools[tool].cutter_label,
            template=job.tooling.tools[tool].template_key,
            tap=tap_name if check.passed else None, tap_rejected=None if check.passed else tap_name,
            tap_bytes=len(data), tap_sha256=g.sha256 if check.passed else None,
            guard=GuardSummary(g.passed, g.sha256, g.floor_in, g.min_z_in, g.units, g.offenders,
                               g.clamp_violations, g.problems),
            pauses=PauseSummary("tap_text", check.pauses.expected, check.pauses.found,
                                tuple(PauseEntryResult(e.after, e.line) for e in check.pauses.entries),
                                check.pauses.problems) if check.pauses else None,
            machining_time_s=900.0, preview_png=f"{name}.png",
            parts=tuple(SheetPart(p.part_key, p.qty) for p in placed), outer_order=tuple(instances),
            tool_forced_by=(),
            errors=tuple(Issue("TAP_REJECTED", msg) for msg in check.problems()),
            warnings=(), notes=(), offcut_id=offcut.id if offcut else None,
            offcut_turned=turned, used_y_in=used_x, beside_used=beside_used, beside_left_in=beside_left)
        parts = []
        for p in job.parts:
            if p.part_key == part_error:
                parts.append(PartResult(p.part_key, p.card_id, p.qty, 0, False, (), 0.13, None, None, None,
                                        (Issue("THICKNESS_NOT_STOCK", 'thickness 0.1300" is not a stock thickness'),), ()))
            elif p.part_key == defer_part:
                parts.append(PartResult(p.part_key, p.card_id, p.qty, 0, True, (), thickness, thickness, tool,
                                        None, (), ()))
            else:
                parts.append(PartResult(p.part_key, p.card_id, p.qty, p.qty, False, (1,), thickness, thickness, tool,
                                        HoleCounts(2, 1, 0, 1, 0, 0), (), ()))
        sheets, part_rows = (sheet,), tuple(parts)
        result = Result(
            schema=RESULT_SCHEMA, core_version=CORE_VERSION, job_id=job.job_id, run_id=job.run_id,
            status=overall_status(sheets, part_rows, ()),
            worker=WorkerInfo("fake", "3.12", claim.attempt, "t0", "t1", ("fake worker",)),
            post=PostResult("path", True, dict(job.post.properties)), fusion_team=None, f3d=None, f3d_bytes=None,
            sheets=sheets, parts=part_rows, errors=(), notes=())
        (claim.out_dir / "result.json").write_text(result_json(result), encoding="utf-8")
        queue.complete(claim)
    return processed
