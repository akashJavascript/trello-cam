"""The clamp strips as fixtures in each sheet's Fusion setup (2026-11-04), so its simulation shows them."""

import dataclasses

from rig import Rig, plate
from test_face import faced_job


def test_a_sheet_gets_its_clamp_strips_at_clamp_height(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("gusset", 2, plate(), (6.0, 4.0))])
    assert job.fixture.clamps_in_fusion                                   # on in the shop's config
    result = rig.run(job)
    (sheet,) = result.sheets
    assert result.status == "ok" and not sheet.warnings
    zones, height = rig.fake.sheets[sheet.name]["clamps"]
    assert zones == job.fixture.clamp_zones_in == ((0.0, 0.0, 1.25, 48.0), (22.75, 0.0, 24.0, 48.0))
    assert height == job.fixture.clamp_height_in == 1.5


def test_its_face_program_has_the_same_clamps(tmp_path):
    rig = Rig(tmp_path)
    job = faced_job(rig)
    result = rig.run(job)
    (sheet,) = result.sheets
    (face,) = sheet.programs
    assert rig.fake.sheets[face.name]["clamps"] == rig.fake.sheets[sheet.name]["clamps"]


def test_no_clamps_on_a_part_placed_by_hand(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("gusset", 2, plate(name="gusset"), (6.0, 3.0)), ("bracket", 1, plate(name="bracket"), (5.0, 2.0))])
    job = dataclasses.replace(job, parts=(job.parts[0], dataclasses.replace(job.parts[1], by_hand=True)))
    nested, by_hand = rig.run(job).sheets
    assert "clamps" in rig.fake.sheets[nested.name]
    assert "clamps" not in rig.fake.sheets[by_hand.name]                  # zeroed on its own box, clamped anywhere


def test_switched_off_or_an_old_job_adds_none(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("gusset", 1, plate(), (6.0, 4.0))])
    job = dataclasses.replace(job, fixture=dataclasses.replace(job.fixture, clamps_in_fusion=False))
    (sheet,) = rig.run(job).sheets
    assert "clamps" not in rig.fake.sheets[sheet.name]


def test_strips_past_the_sheet_are_cut_to_it(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("gusset", 1, plate(), (6.0, 4.0))])
    job = dataclasses.replace(job, fixture=dataclasses.replace(job.fixture, clamp_zones_in=(
        (22.75, -1.0, 30.0, 60.0), (30.0, 0.0, 31.0, 48.0))))             # the second is off the sheet altogether
    (sheet,) = rig.run(job).sheets
    zones, _ = rig.fake.sheets[sheet.name]["clamps"]
    assert zones == ((22.75, 0.0, 24.0, 48.0),)


def test_fusion_refusing_the_clamps_only_warns(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.fail_clamps = True
    job = rig.job([("gusset", 1, plate(), (6.0, 4.0))])
    result = rig.run(job)
    (sheet,) = result.sheets
    assert result.status == "needs_review" and sheet.tap and not sheet.errors and sheet.guard.passed
    [warning] = sheet.warnings
    assert "clamps aren't shown in Fusion's simulation" in warning.msg and "InternalValidationError" in warning.msg
    view = rig.service_view(job, result)
    assert [vs.cuttable for vs in view.sheets] == [True]
