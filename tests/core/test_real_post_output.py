"""The guard, the depth check and the pause code on programs the pinned post wrote in Fusion (pipeline_probe2).

The plates sit near the front edge (y 0.9-4.1 in), inside the real clamp zones, so these use a spec
without clamp zones; the clamp check has its own tests.
"""

from pathlib import Path

from autocam_core.pauses import PauseSpec, insert, verify
from autocam_core.sheetcheck import outer_depth_problems
from autocam_core.tapguard import GuardSpec, check_program

TAPS = Path(__file__).resolve().parents[1] / "fixtures" / "taps"
SPEC = GuardSpec(stock_top_in=0.125, mist=True)


def load(name):
    return (TAPS / name).read_bytes()


def test_through_cut_outline_passes_everything():
    data = load("fusion_one_outline.tap")
    report = check_program(data, SPEC)
    assert report.passed, report.summary()
    assert report.min_z_in == 0.0
    assert outer_depth_problems(data.decode("ascii"), 0.0) == []


def test_outline_left_at_from_contour_passes_the_guard_but_not_the_depth_check():
    data = load("fusion_two_outlines_shallow.tap")
    report = check_program(data, SPEC)
    assert report.passed and report.min_z_in == 0.125
    problems = outer_depth_problems(data.decode("ascii"), 0.0)
    assert [p.split("]")[0] for p in problems] == ["outline op [outer p01-1", "outline op [outer p02-1"]


def test_pause_inserted_into_real_output_verifies_and_passes_the_guard():
    text = load("fusion_two_outlines_shallow.tap").decode("ascii")
    spec = PauseSpec(mist=True)
    paused = insert(text, ["p01-1", "p02-1"], spec)
    check = verify(paused, ["p01-1", "p02-1"], spec, safe_z_in=1.875)
    assert check.ok, check.problems
    assert check.found == check.expected == 1
    report = check_program(paused.encode("ascii"), SPEC)
    assert report.passed, report.summary()
    assert report.m0_count == 1


def test_manual_nc_stop_and_pass_through_are_not_safe_pauses():
    data = load("fusion_stop_passthrough.tap")
    # A pass-through writes its text as a line of its own: the guard can't read it.
    report = check_program(data, SPEC)
    assert not report.passed
    assert "PT_MESSAGE" in report.summary()
    # A Manual NC Stop is a bare M0: no retract, spindle stop, mist off or park before it.
    assert "M0 stop with the spindle running" in report.summary()
    check = verify(data.decode("ascii"), ["p01-1", "p02-1"], PauseSpec(mist=True), safe_z_in=1.875)
    assert not check.ok and "not the expected stop/restart block" in check.problems[0]
