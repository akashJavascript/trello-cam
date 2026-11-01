"""A fake Fusion worker: consumes hot-folder jobs and writes results the way the real add-in will.

Programs are post-shaped text (see tests/fixtures/taps/README.md) with one `[outer <instance>]` op per
part copy; pauses are inserted with the production code and checked with autocam_core.sheetcheck.
"""

from dataclasses import replace
from typing import List, Optional

from autocam_core import CORE_VERSION
from autocam_core.errors import Issue
from autocam_core.hotfolder import Queue
from autocam_core.names import face_program_name, fit_program_name, instance_id, program_name
from autocam_core.offcuts import beside_as_loaded, placement_for
from autocam_core.pauses import insert
from autocam_core.schema_job import read_job
from autocam_core.schema_result import (
    RESULT_SCHEMA, GuardSummary, HoleCounts, PartResult, PauseEntryResult, PauseSummary, PostResult, ProgramResult,
    Result, SheetPart, SheetResult, WorkerInfo, overall_status, result_json,
)
from autocam_core.sheetcheck import check_face_program, check_fit_program, check_sheet_program, pause_spec


def make_program(name: str, instances: List[str], thickness: float, mist: bool) -> str:
    clear, retract = thickness + 2.0, thickness + 0.2
    lines = [f"[{name}]"] + (["M11 C8"] if mist else []) + ["G90", "G20", "G53 Z", "[inner]", "S18000", "M3", "G4 X4."]
    for k, inst in enumerate(instances):          # one after another along the sheet's length (Y)
        y = 2 + 4 * k
        lines += [f"[outer {inst}]", f"G0 X4. Y{y}.", f"G0 Z{retract:g}", "G1 Z0. F20.", "G1 X7. F60.",
                  f"G1 Y{y + 3}.", "G1 X4.", f"G1 Y{y}.", f"G0 Z{clear:g}"]
    lines += (["M12 C8"] if mist else []) + ["G53 Z", "M5", "G53 P10"]
    return "\r\n".join(lines) + "\r\n"


def make_face_program(name: str, slots: List[int], thickness: float, depth: float, mist: bool) -> str:
    """A skim over the 3 x 3 in squares make_program cuts in these slots (k: the k-th copy along Y), the face
    depth down."""
    clear, retract = thickness + 2.0, thickness + 0.2
    lines = [f"[{name}]"] + (["M11 C8"] if mist else []) + ["G90", "G20", "G53 Z", "[face tops]", "S18000", "M3",
                                                            "G4 X4."]
    for k in slots:
        y = 2 + 4 * k
        lines += [f"G0 X4.2 Y{y + 0.2:g}", f"G0 Z{retract:g}", f"G1 Z{thickness - depth:.4f} F20.", "G1 X6.8 F90.",
                  f"G1 Y{y + 2.8:g}", "G1 X4.2", f"G0 Z{clear:g}"]
    lines += (["M12 C8"] if mist else []) + ["G53 Z", "M5", "G53 P10"]
    return "\r\n".join(lines) + "\r\n"


def make_fit_lap(name: str, slots: List[int], instances: List[str], thickness: float, mist: bool, outline: bool) -> str:
    """A fit lap over the copies in these slots: their outline again (outline fits) or a bore in each (holes)."""
    clear, retract = thickness + 2.0, thickness + 0.2
    lines = [f"[{name}]"] + (["M11 C8"] if mist else []) + ["G90", "G20", "G53 Z"]
    if not outline:
        lines += ["[bore holes]"]
    lines += ["S18000", "M3", "G4 X4."]
    for k in slots:
        y = 2 + 4 * k
        if outline:
            lines += [f"[outer {instances[k]}]", f"G0 X4. Y{y}.", f"G0 Z{retract:g}", "G1 Z0. F20.", "G1 X7. F60.",
                      f"G1 Y{y + 3}.", "G1 X4.", f"G1 Y{y}.", f"G0 Z{clear:g}"]
        else:
            lines += [f"G0 X5.5 Y{y + 1.5:g}", f"G0 Z{retract:g}", "G1 Z0. F20.", "G1 X5.6 F60.", f"G0 Z{clear:g}"]
    lines += (["M12 C8"] if mist else []) + ["G53 Z", "M5", "G53 P10"]
    return "\r\n".join(lines) + "\r\n"


def run_fake_worker(queue: Queue, *, reject_sheet: bool = False, fail_job: Optional[str] = None,
                    defer_part: Optional[str] = None, part_error: Optional[str] = None,
                    shallow_outlines: bool = False, beside: bool = False, face_too_deep: bool = False) -> List[str]:
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
        programs = ()
        faced = [p for p in placed if p.face]
        if faced and job.face is not None:            # like the pipeline: a FACE program over the faced copies
            face_name = face_program_name(name)
            keys = {p.part_key for p in faced}
            slots = [k for k, i in enumerate(instances) if i.rsplit("-", 1)[0] in keys]
            face = make_face_program(face_name, slots, thickness, job.face.depth_in, job.material.use_mist)
            if face_too_deep:
                face = face.replace(f"Z{thickness - job.face.depth_in:.4f}", f"Z{thickness - 0.02:.4f}")
            face_data = face.encode("ascii")
            fc = check_face_program(face_data, job, thickness, {p.part_key: p.qty for p in faced})
            face_tap = f"{face_name}.tap" if fc.passed else f"{face_name}.REJECTED.tap"
            (claim.out_dir / face_tap).write_bytes(face_data)
            fg = fc.guard
            programs = (ProgramResult(
                kind="face", name=face_name, runs="before", tool_guid=job.face.tool.guid,
                cutter_label=job.face.tool.cutter_label, tool_number=job.face.tool.number,
                tap=face_tap if fc.passed else None, tap_rejected=None if fc.passed else face_tap,
                tap_bytes=len(face_data), tap_sha256=fg.sha256 if fc.passed else None,
                guard=GuardSummary(fg.passed, fg.sha256, fg.floor_in, fg.min_z_in, fg.units, fg.offenders,
                                   fg.clamp_violations, fg.problems),
                machining_time_s=240.0, parts=tuple(SheetPart(p.part_key, p.qty) for p in faced),
                depth_in=job.face.depth_in),)
            if not fc.passed:                         # like the pipeline: the sheet isn't offered without it
                check = replace(check, plan_problems=check.plan_problems + tuple(fc.problems()))
        for series, p in enumerate([p for p in placed if p.fit is not None], 1):     # like the pipeline: the laps
            slots = [k for k, i in enumerate(instances) if i.rsplit("-", 1)[0] == p.part_key]
            outline = p.fit.kind == "outline"
            for lap, stock in enumerate(p.fit.stocks_in[1:], 1):
                lap_name = fit_program_name(name, series, lap)
                lap_data = make_fit_lap(lap_name, slots, instances, thickness, job.material.use_mist,
                                        outline).encode("ascii")
                lc = check_fit_program(lap_data, job, thickness, tool,
                                       tuple(instances[k] for k in slots) if outline else (), {p.part_key: p.qty})
                (claim.out_dir / f"{lap_name}.tap").write_bytes(lap_data)
                lg = lc.guard
                programs += (ProgramResult(
                    kind="fit", name=lap_name, runs="after", tool_guid=job.tooling.tools[tool].guid,
                    cutter_label=job.tooling.tools[tool].cutter_label, tool_number=job.tooling.tools[tool].number,
                    tap=f"{lap_name}.tap" if lc.passed else None, tap_rejected=None if lc.passed else f"{lap_name}.tap",
                    tap_bytes=len(lap_data), tap_sha256=lg.sha256 if lc.passed else None,
                    guard=GuardSummary(lg.passed, lg.sha256, lg.floor_in, lg.min_z_in, lg.units, lg.offenders,
                                       lg.clamp_violations, lg.problems),
                    machining_time_s=30.0, parts=(SheetPart(p.part_key, p.qty),), stock_in=stock, series=series,
                    lap=lap, feature=f"{p.fit.diameter_in:g} hole" if not outline else "outline"),)
                assert lc.passed, lc.problems()
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
            offcut_turned=turned, used_y_in=used_x, beside_used=beside_used, beside_left_in=beside_left,
            parts_area_in2=9.0 * len(instances), programs=programs)       # 3 x 3 in each
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
