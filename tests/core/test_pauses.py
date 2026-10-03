from pathlib import Path

import pytest

from autocam_core.pauses import (
    PauseError, PauseSpec, air_test_program, insert, pause_block, verify,
)
from autocam_core.tapguard import GuardSpec, check_program

REPO = Path(__file__).resolve().parents[2]
SAMPLE = (REPO / "tests/fixtures/taps/sheet_mist.tap").read_bytes().decode("ascii")  # keep CRLF
AIR_TEST = (REPO / "fusion/tests/pause_air_test.tap").read_bytes()
ORDER = ["p01-1", "p02-1", "p02-2"]
MIST = PauseSpec(mist=True)
DRY = PauseSpec(mist=False)
SAFE_Z = 0.125 + 1.5 + 0.25
GUARD = GuardSpec(
    clamp_zones_in=((0.0, 0.0, 48.0, 1.25), (0.0, 22.75, 48.0, 24.0)),
    clamp_clear_z_in=SAFE_Z, tool_radius_in=0.0787, reach_y_in=40.0, mist=True,
)


def test_air_test_comes_from_the_production_block():
    """The committed air test is exactly what the production builder writes (no mist)."""
    assert air_test_program(DRY).encode("ascii") == AIR_TEST


def test_mist_air_test_passes_the_guard_with_two_pauses():
    program = air_test_program(MIST).encode("ascii")
    report = check_program(program, GuardSpec(mist=True))
    assert report.passed, report.summary()
    assert report.m0_count == 2


def test_pause_block_contents():
    assert pause_block(1, MIST) == [
        "[PART 1 DONE - PAUSE]", "G53 Z", "M5", "M12 C8", "G53 P10", "[REMOVE PART THEN RESUME]", "M0",
        "M11 C8", "S18000", "M3", "G4 X4"]
    assert pause_block(2, DRY, restart=False) == [
        "[PART 2 DONE - PAUSE]", "G53 Z", "M5", "G53 P10", "[REMOVE PART THEN RESUME]", "M0"]


def test_insert_puts_a_pause_between_each_pair_of_parts():
    out = insert(SAMPLE, ORDER, MIST)
    lines = out.split("\r\n")
    for instance in ("p02-1", "p02-2"):
        i = lines.index(f"[outer {instance}]")
        assert lines[i - 11:i] == pause_block(ORDER.index(instance), MIST)
    assert out.count("\nM0\r") == 2
    assert "\n" not in out.replace("\r\n", "")  # CRLF kept, no bare LF
    assert out.endswith("G53 P10\r\n")


def test_inserted_program_verifies_and_passes_the_guard():
    out = insert(SAMPLE, ORDER, MIST)
    check = verify(out, ORDER, MIST, safe_z_in=SAFE_Z)
    assert check.ok, check.problems
    assert (check.expected, check.found) == (2, 2)
    assert [e.after for e in check.entries] == ["p01-1", "p02-1"]
    report = check_program(out.encode("ascii"), GUARD)
    assert report.passed, report.summary()
    assert report.m0_count == 2


def test_single_part_sheet_has_no_pause():
    one = SAMPLE.replace("[outer p02-1]", "[outer px-1]").replace("[outer p02-2]", "[outer px-2]")
    assert insert(one, ["p01-1"], MIST) == one
    assert verify(one, ["p01-1"], MIST).ok


def test_manual_nc_layout_also_verifies():
    """Manual NC: the post writes the stop block, then restarts the spindle after the op comment."""
    stop = "\r\n".join(pause_block(1, MIST, restart=False))
    manual = SAMPLE.replace(
        "[outer p02-1]\r\n", f"{stop}\r\nM11 C8\r\n[outer p02-1]\r\nS18000\r\nM3\r\nG4 X4.\r\n")
    manual = manual.replace(
        "[outer p02-2]\r\n", f"{stop}\r\nM11 C8\r\n[outer p02-2]\r\nS18000\r\nM3\r\nG4 X4.\r\n")
    check = verify(manual, ORDER, MIST, safe_z_in=SAFE_Z)
    assert check.ok, check.problems


def test_missing_pause_is_caught():
    out = insert(SAMPLE, ORDER[:2], MIST)  # forgot the last part
    check = verify(out, ORDER, MIST)
    assert not check.ok
    assert "expected 2 M0 stops, found 1" in check.problems


def test_incomplete_block_is_caught():
    out = insert(SAMPLE, ORDER, MIST).replace("M5\r\nM12 C8\r\nG53 P10", "M12 C8\r\nG53 P10", 1)
    check = verify(out, ORDER, MIST)
    assert any("not the expected stop/restart block" in p for p in check.problems)


def test_mist_restart_missing_is_caught():
    out = insert(SAMPLE, ORDER, DRY)  # built without mist lines for a mist material
    assert not verify(out, ORDER, MIST).ok


def test_first_move_after_resume_must_come_down_from_the_top():
    out = insert(SAMPLE, ORDER, MIST).replace(
        "[outer p02-1]\r\nG0 X12. Y4.", "[outer p02-1]\r\nG0 Z0.325\r\nG0 X12. Y4.")
    check = verify(out, ORDER, MIST, safe_z_in=SAFE_Z)
    assert any("drops to Z0.325 before moving sideways" in p for p in check.problems)


def test_stop_before_the_first_outline_is_caught():
    out = insert(SAMPLE, ORDER, MIST).replace("[inner]", "M5\r\nM0\r\nM3\r\n[inner]")
    check = verify(out, ORDER, MIST)
    assert "M0 before the first part outline" in check.problems


def test_after_last_part_adds_a_final_stop():
    out = insert(SAMPLE, ORDER, MIST, after_last_part=True)
    check = verify(out, ORDER, MIST, after_last_part=True, safe_z_in=SAFE_Z)
    assert check.ok, check.problems
    assert [e.after for e in check.entries] == ORDER
    assert check_program(out.encode("ascii"), GUARD).passed


@pytest.mark.parametrize("order, message", [
    (["p02-1", "p01-1", "p02-2"], "not in the planned cut order"),
    (["p01-1", "p09-1"], "expected one '[outer p09-1]' line, found 0"),
    (["p01-1", "p01-1"], "lists a part twice"),
])
def test_insert_refuses_bad_order(order, message):
    with pytest.raises(PauseError, match=message.replace("[", r"\[").replace("]", r"\]")):
        insert(SAMPLE, order, MIST)
