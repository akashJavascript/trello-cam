"""The Fusion pipeline's job flow against a fake Fusion (fakeadapter.py), checked with the service's own checks."""

from dataclasses import replace
from pathlib import Path

import pytest

from autocam_core import errors as E
from autocam_core.schema_result import read_result
from autocam_worker.pipeline import JobFailed
from geombuilder import PlateBuilder
from rig import Rig, plate


def test_happy_path_one_sheet_programs_pass_the_service_checks(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("gusset", 2, plate(holes=(0.159, 0.25, 2.0)), (6.0, 4.0)),
                   ("spacer", 1, plate(), (3.0, 3.0))])
    result = rig.run(job)

    assert result.status == "ok", (result.sheets[0].errors, [p.errors for p in result.parts])
    assert (rig.out / "result.json").is_file() and (rig.out / "worker.log").is_file()
    assert read_result(rig.out / "result.json") == result
    (sheet,) = result.sheets
    assert sheet.name == "6061_0p125_r001_S1" and sheet.tap == "6061_0p125_r001_S1.tap"
    assert dict((p.part_key, p.count) for p in sheet.parts) == {"p01": 2, "p02": 1}
    assert sorted(sheet.outer_order) == ["p01-1", "p01-2", "p02-1"]
    assert sheet.pauses.expected == sheet.pauses.found == 2
    assert sheet.guard.passed and sheet.guard.min_z_in == 0.0

    fills = rig.fake.sheets[sheet.name]["fills"]
    assert len(fills["[drill] holes"].holes) == 2 and len(fills["[bore] holes"].holes) == 2
    assert len(fills["[inner] cutouts"].loops) == 2
    assert rig.fake.sheets[sheet.name]["deleted"] == ["[bearing] holes"]
    assert [p.placed for p in result.parts] == [2, 1]
    assert result.parts[0].holes.drill == 1 and result.parts[0].holes.bore == 1

    view = rig.service_view(job, result)
    assert view.failure is None and view.part_problems == {}
    assert [vs.cuttable for vs in view.sheets] == [True]


def test_envelope_keeps_outline_tool_paths_out_of_the_clamp_strips(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("plate", 1, plate(), (5.0, 5.0))])
    rig.run(job)
    region = job.fixture.nest_region_in
    env = rig.fake.arrange_envelopes[0]
    origin_x = env[0] - region[0] - job.nest.part_spacing_in
    assert env == pytest.approx((origin_x + region[0] + 0.25, region[1] + 0.25,
                                 origin_x + region[2] - 0.25, region[3] - 0.25))


def test_each_stock_thickness_gets_its_own_sheets_and_bad_parts_are_reported(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("thin", 1, plate(t=0.125), (4.0, 4.0)),
                   ("thick", 1, plate(t=0.25), (4.0, 4.0)),
                   ("odd", 1, plate(t=0.2), (4.0, 4.0))])
    result = rig.run(job)
    assert [s.name for s in result.sheets] == ["6061_0p125_r001_S1", "6061_0p25_r001_S2"]
    origins = [rig.fake.sheets[s.name]["origin"][0] for s in result.sheets]
    assert origins[1] - origins[0] == pytest.approx(job.sheet.length_in + job.nest.envelope_spacing_in)
    odd = result.parts[2]
    assert odd.errors[0].code == E.THICKNESS_NOT_STOCK and odd.placed == 0 and odd.sheets == ()
    assert "discard p03.1" in rig.fake.calls
    assert result.status == "needs_review"
    assert rig.service_view(job, result).part_problems == {}


def test_parts_that_dont_all_fit_are_deferred_whole(tmp_path):
    rig = Rig(tmp_path, max_sheets_per_group=1)
    # The envelope is 38.5 x 21 in: four 18 x 10 in parts fit, the fifth doesn't.
    job = rig.job([("big", 5, plate(), (18.0, 10.0)), ("small", 1, plate(), (2.0, 2.0))])
    result = rig.run(job)
    big, small = result.parts
    assert big.deferred and big.placed == 0 and not big.errors
    assert small.placed == 1 and small.sheets == (1,)
    assert [p.part_key for p in result.sheets[0].parts] == ["p02"]
    assert {f"discard p01.{n}" for n in range(1, 6)} <= set(rig.fake.calls)
    assert rig.service_view(job, result).part_problems == {}


def test_leftovers_go_to_the_next_sheet(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("big", 6, plate(), (18.0, 10.0))])
    result = rig.run(job)
    assert len(result.sheets) == 2
    assert sum(p.count for s in result.sheets for p in s.parts) == 6
    assert result.parts[0].sheets == (1, 2) and result.parts[0].placed == 6
    view = rig.service_view(job, result)
    assert view.part_problems == {} and all(vs.cuttable for vs in view.sheets)


def test_a_hole_too_small_for_4mm_puts_the_sheet_on_the_eighth(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("bracket", 1, plate(holes=(0.136,)), (4.0, 4.0)), ("spacer", 1, plate(), (3.0, 3.0))])
    result = rig.run(job)
    (sheet,) = result.sheets
    assert sheet.tool == "t12_eighth_alu" and sheet.cutter_label == job.tooling.tools["t12_eighth_alu"].cutter_label
    assert [f.part_key for f in sheet.tool_forced_by] == ["p01"]
    assert sheet.tap is not None
    assert len(rig.fake.sheets[sheet.name]["fills"]["[drill] holes"].holes) == 1   # 0.136 is a 1/8 drill size


def test_poly_part_needing_the_eighth_without_its_template_gets_the_decision_13_comment(tmp_path):
    rig = Rig(tmp_path)
    (tmp_path / "templates" / "poly_eighth.f3dhsm-template").unlink()
    job = rig.job([("window", 1, plate(holes=(0.136,)), (4.0, 4.0))], material="pc_clear")
    result = rig.run(job)
    assert result.parts[0].errors == (E.Issue(E.NEEDS_MANUAL_CAM, E.POLY_EIGHTH_MISSING_MSG),)
    assert result.sheets == () and result.status == "failed"


def test_pockets_need_manual_cam_until_the_pocket_selection_is_known(tmp_path):
    rig = Rig(tmp_path)
    b = PlateBuilder("pocketed")
    b.pocket(0.06)
    job = rig.job([("pocketed", 1, b.build(), (10.0, 10.0)), ("spacer", 1, plate(), (3.0, 3.0))])
    result = rig.run(job)
    pocketed = result.parts[0]
    assert pocketed.errors[0].code == E.NEEDS_MANUAL_CAM and "pocket" in pocketed.errors[0].msg
    assert [p.part_key for p in result.sheets[0].parts] == ["p02"]
    assert rig.service_view(job, result).part_problems == {}


def test_part_arranged_upside_down_is_rejected(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.upside_down.add("p01")
    job = rig.job([("flipped", 1, plate(), (4.0, 4.0)), ("spacer", 1, plate(), (3.0, 3.0))])
    result = rig.run(job)
    assert result.parts[0].errors[0].code == E.ARRANGE_FAILED
    assert [p.part_key for p in result.sheets[0].parts] == ["p02"]


def test_template_tool_guid_mismatch_stops_the_sheet(tmp_path):
    rig = Rig(tmp_path, guid_for={"alu_4mm": "00000000-0000-0000-0000-000000000000"})
    job = rig.job([("gusset", 1, plate(), (4.0, 4.0))])
    result = rig.run(job)
    (sheet,) = result.sheets
    assert sheet.tap is None and sheet.errors[0].code == E.TOOL_GUID_MISMATCH
    assert result.status == "needs_review"
    view = rig.service_view(job, result)
    assert [vs.cuttable for vs in view.sheets] == [False]


def test_template_without_outer_op_or_with_untagged_ops(tmp_path):
    rig = Rig(tmp_path, ops=("[inner] cutouts", "chamfer edges"))
    job = rig.job([("gusset", 1, plate(), (4.0, 4.0))])
    result = rig.run(job)
    msgs = [e.msg for e in result.sheets[0].errors]
    assert any("'chamfer edges' needs exactly one tag" in m for m in msgs)
    assert result.sheets[0].tap is None


def test_template_missing_an_op_the_sheet_needs(tmp_path):
    rig = Rig(tmp_path, ops=("[inner] cutouts", "[outer] outline"))
    job = rig.job([("gusset", 1, plate(holes=(0.159,)), (4.0, 4.0))])
    result = rig.run(job)
    assert result.sheets[0].errors[0].code == E.TEMPLATE_PROBLEM
    assert "no [drill] op" in result.sheets[0].errors[0].msg


def test_bearing_holes_fall_back_to_the_bore_op_with_a_warning(tmp_path):
    rig = Rig(tmp_path, ops=("[bore] holes", "[inner] cutouts", "[outer] outline"))
    job = rig.job([("plate", 1, plate(holes=(1.125,)), (4.0, 4.0))])
    result = rig.run(job)
    sheet = result.sheets[0]
    assert sheet.tap is not None and sheet.warnings[0].code == E.OP_WARNING
    assert len(rig.fake.sheets[sheet.name]["fills"]["[bore] holes"].holes) == 1


def test_program_below_the_floor_is_kept_as_rejected_and_not_offered(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.below_floor.add("6061_0p125_r001_S1")
    job = rig.job([("gusset", 1, plate(), (4.0, 4.0))])
    result = rig.run(job)
    (sheet,) = result.sheets
    assert sheet.tap is None and sheet.tap_rejected == "6061_0p125_r001_S1.REJECTED.tap"
    assert (rig.out / sheet.tap_rejected).is_file() and not (rig.out / "6061_0p125_r001_S1.tap").exists()
    assert any(e.code == E.TAP_REJECTED and "below Z" in e.msg for e in sheet.errors)


def test_missing_and_unimportable_step_files(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("gone", 1, plate(), (4.0, 4.0)), ("broken", 1, plate(), (4.0, 4.0)),
                   ("ok", 1, plate(), (4.0, 4.0))])
    Path(job.parts[0].step).unlink()
    rig.fake.fail_import.add(job.parts[1].step)
    result = rig.run(job)
    assert [p.errors[0].code if p.errors else None for p in result.parts] == [E.STEP_MISSING, E.STEP_IMPORT, None]
    assert result.parts[2].placed == 1


def test_step_file_changed_since_the_job_was_made(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("gusset", 1, plate(), (4.0, 4.0))])
    Path(job.parts[0].step).write_bytes(b"something else")
    result = rig.run(job)
    assert "changed after the job was made" in result.parts[0].errors[0].msg


def test_nothing_nestable_still_writes_a_result(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("odd", 1, plate(t=0.2), (4.0, 4.0))])
    result = rig.run(job)
    assert result.status == "failed" and result.errors[0].code == E.NOTHING_TO_NEST
    assert (rig.out / "result.json").is_file() and rig.fake.finished is False


@pytest.mark.parametrize("change, message", [
    (lambda job: replace(job, core_version="0.0.1"), "CORE_VERSION_MISMATCH"),
    (lambda job: replace(job, post=replace(job.post, sha256="0" * 64)), "must never be edited"),
    (lambda job: replace(job, pauses=replace(job.pauses, mode="manual_nc")), "bare M0"),
])
def test_job_level_failures_raise_before_touching_fusion(tmp_path, change, message):
    rig = Rig(tmp_path)
    job = change(rig.job([("gusset", 1, plate(), (4.0, 4.0))]))
    with pytest.raises(JobFailed, match=message):
        rig.run(job)
    assert rig.fake.calls == [] and not (rig.out / "result.json").exists()


def test_missing_template_fails_the_job(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("gusset", 1, plate(), (4.0, 4.0))])
    Path(job.tooling.tools[job.tooling.default].template_path).unlink()
    with pytest.raises(JobFailed, match="TEMPLATE_PROBLEM"):
        rig.run(job)


# ---- failures the review asked for (one part or one sheet fails; the rest of the job carries on)

def test_part_arrange_refuses_is_rejected_and_the_rest_nest(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.refuse.add("p01")
    job = rig.job([("standing", 2, plate(), (4.0, 4.0)), ("spacer", 1, plate(), (3.0, 3.0))])
    result = rig.run(job)
    standing, spacer = result.parts
    assert standing.errors[0].code == E.ARRANGE_FAILED and "lay it flat" in standing.errors[0].msg
    assert not standing.deferred and spacer.placed == 1 and result.sheets[0].tap
    assert rig.service_view(job, result).part_problems == {}


def test_arrange_failing_is_an_error_not_a_deferral(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.arrange_error = "arrangeFeatures.add: RuntimeError: 3 : Compute Failed"
    job = rig.job([("gusset", 1, plate(), (4.0, 4.0))])
    result = rig.run(job)
    part = result.parts[0]
    assert not part.deferred and part.errors[0].code == E.ARRANGE_FAILED and "Compute Failed" in part.errors[0].msg


def test_part_too_big_for_the_sheet_is_an_error_not_a_deferral(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("long", 1, plate(), (45.0, 10.0)), ("spacer", 1, plate(), (3.0, 3.0))])
    result = rig.run(job)
    long_part = result.parts[0]
    assert not long_part.deferred and "even alone on a sheet" in long_part.errors[0].msg
    assert result.parts[1].placed == 1


def test_part_that_fits_alone_but_ran_out_of_sheets_is_deferred(tmp_path):
    rig = Rig(tmp_path, max_sheets_per_group=1)
    job = rig.job([("big", 5, plate(), (18.0, 10.0))])
    part = rig.run(job).parts[0]
    assert part.deferred and not part.errors


def test_one_copy_left_off_the_sheet_plane_rejects_its_part(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.bad_z.add("p01.2")
    job = rig.job([("gusset", 2, plate(), (4.0, 4.0)), ("spacer", 1, plate(), (3.0, 3.0))])
    result = rig.run(job)
    assert "not on the sheet" in result.parts[0].errors[0].msg and result.parts[1].placed == 1
    assert rig.service_view(job, result).part_problems == {}


def test_parts_moved_after_nesting_stop_the_sheet(tmp_path):
    # e.g. Fusion re-solving an Arrange when one of its inputs is taken out
    rig = Rig(tmp_path, max_sheets_per_group=1)
    rig.fake.move_on_discard["p02.1"] = (-12.0, 0.0)
    job = rig.job([("big", 5, plate(), (18.0, 10.0)), ("small", 1, plate(), (2.0, 2.0))])
    result = rig.run(job)
    (sheet,) = result.sheets
    assert sheet.tap is None and "moved after nesting" in sheet.errors[0].msg
    assert [vs.cuttable for vs in rig.service_view(job, result).sheets] == [False]


def test_cutting_off_the_sheet_is_caught_by_the_guard(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.move_on_generate["p01.1"] = (-30.0, 0.0)
    job = rig.job([("gusset", 1, plate(), (4.0, 4.0))])
    result = rig.run(job)
    (sheet,) = result.sheets
    assert sheet.tap is None and any("off the sheet" in e.msg for e in sheet.errors)


def test_op_errors_warnings_and_missing_toolpaths(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.op_states["[inner] cutouts"] = (False, "no feasible toolpath", None)
    job = rig.job([("gusset", 1, plate(holes=(2.0,)), (4.0, 4.0))])
    sheet = rig.run(job).sheets[0]
    assert sheet.tap is None and sheet.errors[0].msg == "[inner] cutouts: no feasible toolpath"

    (tmp_path / "w").mkdir()
    rig = Rig(tmp_path / "w")
    rig.fake.op_states["[outer] p01-1"] = (True, None, "lead-in shortened")
    job = rig.job([("gusset", 1, plate(), (4.0, 4.0))])
    sheet = rig.run(job).sheets[0]
    assert sheet.tap and sheet.warnings[0].msg == "[outer] p01-1: lead-in shortened"


def test_one_sheet_failing_to_build_or_post_leaves_the_others(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.fail_make_sheet.add("6061_0p125_r001_S1")
    rig.fake.fail_post.add("6061_0p25_r001_S3")      # sheets are numbered in thickness order
    job = rig.job([("thin", 1, plate(t=0.125), (4.0, 4.0)), ("thick", 1, plate(t=0.25), (4.0, 4.0)),
                   ("thicker", 1, plate(t=0.1875), (4.0, 4.0))])
    result = rig.run(job)
    by_name = {s.name: s for s in result.sheets}
    assert by_name["6061_0p125_r001_S1"].errors[0].code == E.OP_ERROR
    assert by_name["6061_0p25_r001_S3"].errors[0].code == E.POST_FAILED
    assert by_name["6061_0p1875_r001_S2"].tap is not None
    assert rig.service_view(job, result).part_problems == {}


def test_split_top_face_needs_manual_cam(tmp_path):
    rig = Rig(tmp_path)
    b = PlateBuilder("split")
    b._face(kind="plane", z0=0.125, z1=0.125, normal_dot=1.0, area=10.0)
    job = rig.job([("split", 1, b.build(), (4.0, 4.0))])
    assert "top is split into 2 faces" in rig.run(job).parts[0].errors[0].msg
