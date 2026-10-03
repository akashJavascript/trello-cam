"""Air test programs, on programs the pinned post wrote in Fusion and on the sample sheet with pauses."""

from pathlib import Path

import pytest

from autocam_core.airtest import air_test_name, lift_program, note_for
from autocam_core.pauses import PauseSpec, insert, verify
from autocam_core.tapguard import GuardSpec, check_program, parse_code

TAPS = Path(__file__).resolve().parents[1] / "fixtures" / "taps"
SPEC = GuardSpec(stock_top_in=0.125, mist=True)


def text(name):
    return (TAPS / name).read_bytes().decode("ascii")


def z_values(program):
    out = []
    for line in program.splitlines():
        if line.startswith("[") or not line.strip():
            continue
        words = parse_code(line)
        if ("G", 53.0) not in words:
            out += [v for letter, v in words if letter in "ZR" and v is not None]
    return out


def test_every_z_is_raised_and_nothing_else_changes():
    real = text("fusion_one_outline.tap")
    air = lift_program(real, 0.625)
    real_lines, air_lines = real.split("\r\n"), air.split("\r\n")
    assert len(air_lines) == len(real_lines)                     # CRLF kept, no line added or lost
    assert [round(z + 0.625, 4) for z in z_values(real)] == z_values(air)
    for a, b in zip(real_lines, air_lines):
        if "Z" not in a or a.startswith("G53"):
            assert a == b                                        # XY, arcs, feeds, spindle, G53 untouched
    assert "G1 Z0.625 F20." not in real and "X0.8898 Z0.625" in air   # the through cut, now in the air
    assert "G53 Z" in air.split("\r\n")


def test_it_passes_the_guard_and_never_comes_below_the_gap():
    air = lift_program(text("fusion_one_outline.tap"), 0.625, note_for(0.625, 0.5)).encode("ascii")
    report = check_program(air, SPEC)
    assert report.passed, report.summary()
    assert report.min_z_in == pytest.approx(0.625)
    assert air.split(b"\r\n")[1] == b"[AIR TEST - RAISED 0.625 IN - CUTTER STAYS 0.5 IN ABOVE THE SHEET]"


def test_pauses_are_kept_exactly():
    order = ["p01-1", "p02-1", "p02-2"]
    spec = PauseSpec(mist=True)
    sheet = insert(text("sheet_mist.tap"), order, spec)
    assert verify(sheet, order, spec).ok and sheet.count("M0") == 2
    air = lift_program(sheet, 0.625)
    check = verify(air, order, spec, safe_z_in=2.0)
    assert check.ok, check.problems
    assert air.count("M0") == sheet.count("M0") and air.count("M3") == sheet.count("M3")


def test_comments_and_drill_cycles():
    program = "\r\n".join(["[p Z1 R2]", "G53 Z", "G81 X1. Y1. Z0. R0.2 F10.", "G80", "Z.5 [keep Z3 as is]", ""])
    air = lift_program(program, 1.0)
    assert air.split("\r\n") == ["[p Z1 R2]", "G53 Z", "G81 X1. Y1. Z1. R1.2 F10.", "G80", "Z1.5 [keep Z3 as is]", ""]


def test_never_lowered_and_names():
    with pytest.raises(ValueError):
        lift_program("G1 Z0.", 0)
    assert air_test_name("6061_0p1875_r005_S1.tap") == "6061_0p1875_r005_S1_AIRTEST.tap"


def test_taking_the_pauses_out_gives_back_the_posted_program():
    from autocam_core.pauses import PauseError, remove
    order = ["p01-1", "p02-1", "p02-2"]
    spec = PauseSpec(mist=True)
    posted = text("sheet_mist.tap")
    for after_last in (False, True):
        paused = insert(posted, order, spec, after_last)
        assert remove(paused, order, spec, after_last) == posted
    paused = insert(posted, order, spec)
    with pytest.raises(PauseError):
        remove(paused.replace("G53 P10\r\n[REMOVE PART", "G53 P11\r\n[REMOVE PART", 1), order, spec)
    with pytest.raises(PauseError):
        remove(posted, order, spec)                        # nothing to take out


# ---- outlines only, one feed (air_test_program)

def test_only_the_outlines_keep_their_moves():
    from autocam_core.airtest import outlines_only
    sheet = text("sheet_mist.tap")
    air = outlines_only(sheet)
    lines = air.split("\r\n")
    assert "[drill]" not in lines and "[inner]" not in lines           # those ops' moves and names are gone
    assert not any(l.startswith(("G81", "G2 X10.")) for l in lines)
    assert lines[:9] == ["[6061_0p125_r017_S1]", "[r017-al6061]", "M11 C8", "G90", "G20", "G53 Z",
                         "S18000", "M3", "G4 X4."]                       # ...but the spindle still starts
    assert "G80" in lines
    outer = sheet[sheet.index("[outer p01-1]"):]
    assert air.endswith(outer)                                          # the outlines are byte for byte the same


def test_a_kept_move_that_would_change_is_refused():
    from autocam_core.airtest import AirTestError, outlines_only
    program = "\r\n".join(["[p]", "G90", "G20", "G53 Z", "[inner]", "S18000", "M3", "G0 X1. Y1.", "G0 Z0.3",
                           "G1 Z0. F20.", "[outer p01-1]", "X5. Y5.", "G0 Z2.", ""])   # X5 Y5 is still a G1 move
    with pytest.raises(AirTestError, match="X5. Y5."):
        outlines_only(program)
    with pytest.raises(AirTestError, match="no outline"):
        outlines_only("[p]\r\nG20\r\n[inner]\r\nG0 X1. Y1.\r\n")


def test_every_feed_move_at_one_speed():
    from autocam_core.airtest import set_feed
    out = set_feed(text("sheet_mist.tap"), 200)
    mode = None
    for line in out.split("\r\n"):
        if not line or line.startswith("["):
            continue
        words = parse_code(line)
        gs = [v for k, v in words if k == "G"]
        mode = next((g for g in gs if g in (0, 1, 2, 3, 81)), mode)
        moving = any(k in "XYZ" for k, _ in words) and 53 not in gs and 4 not in gs
        if moving and mode in (1, 2, 3, 81):
            assert "F200." in line and line.count("F") == 1, line
        elif moving:
            assert "F" not in line, line
    assert "G1 Y8. F200." in out and "G1 X8. F200." in out             # added where the post left F out


def test_the_whole_air_test_on_the_sample_sheet():
    from autocam_core.airtest import air_test_program
    order = ["p01-1", "p02-1", "p02-2"]
    spec = PauseSpec(mist=True)
    air = air_test_program(insert(text("sheet_mist.tap"), order, spec), 0.125, 0.5, 200)
    report = check_program(air.encode("ascii"), GuardSpec(stock_top_in=0.125, mist=True))
    assert report.passed, report.summary()
    assert report.min_z_in == pytest.approx(0.625)
    assert verify(air, order, spec, safe_z_in=2.0).ok and air.count("M0") == 2
    assert air.split("\r\n")[1] == ("[AIR TEST - RAISED 0.625 IN - CUTTER STAYS 0.5 IN ABOVE THE SHEET - OUTLINES "
                                    "ONLY, ONE LAP EACH, AT 200 IPM]")
    assert "G81" not in air and "[drill]" not in air


# ---- one lap per outline

RAMPED = "\r\n".join([
    "[p]", "G90", "G20", "G53 Z", "[outer p01-1]", "S18000", "M3", "G4 X4.",
    "G0 X1. Y1.", "Z2.", "Z0.3",
    "G1 Z0.25 F20.", "X2. Z0.2 F60.",                                  # ramp in
    "X5.", "Y5.", "X1.", "Y1.", "X2. Z0.1",                            # lap 1, then ramp down
    "X5.", "Y5.", "X1.", "Y1.", "X2. Z0.",                             # lap 2, ramp to the bottom
    "X5.", "G3 X6. Y2. I0. J1.", "G1 Y5.", "X1.", "Y1.", "X2.",        # the lap at the final depth
    "X2.5 Z0.05",                                                       # ramp out
    "G0 Z2.", "G53 Z", "M5", "G53 P10", ""])


def test_only_the_lap_at_the_final_depth_is_kept():
    from autocam_core.airtest import one_lap
    out = one_lap(RAMPED).split("\r\n")
    start = out.index("[outer p01-1]")
    assert out[start + 1:start + 4] == ["S18000", "M3", "G4 X4."]                  # the spindle start stays
    assert out[start + 4:] == ["G0 X2. Y1.", "G0 Z0.3", "G1 Z0.",                  # straight down where the lap starts
                               "G1 X5.", "G3 X6. Y2. I0. J1.", "G1 Y5.", "X1.", "Y1.", "X2.",
                               "G0 Z2.", "G53 Z", "M5", "G53 P10", ""]


def test_a_single_lap_outline_stays_the_same_path():
    from autocam_core.airtest import one_lap
    sheet = text("sheet_mist.tap")
    out = one_lap(sheet)
    outer = out[out.index("[outer p01-1]"):].split("\r\n")
    assert outer[1:9] == ["G0 X4. Y4.", "G0 Z0.325", "G1 Z0.", "G1 X8. F60.", "G3 X9. Y5. I0. J1.", "G1 Y8.",
                          "G1 X4.", "G1 Y4."]


def test_an_outline_with_anything_else_in_it_is_left_alone():
    from autocam_core.airtest import one_lap
    odd = RAMPED.replace("X5.\r\nY5.\r\nX1.\r\nY1.\r\nX2. Z0.1", "X5.\r\nM11 C8\r\nY5.\r\nX1.\r\nY1.\r\nX2. Z0.1")
    assert one_lap(odd) == odd


def test_the_whole_air_test_traces_each_outline_once():
    from autocam_core.airtest import air_test_program
    air = air_test_program(RAMPED, 0.25, 0.5, 200)
    assert "OUTLINES ONLY, ONE LAP EACH, AT 200 IPM" in air
    report = check_program(air.encode("ascii"), GuardSpec(stock_top_in=0.25, mist=False))
    assert report.passed, report.summary()
    assert report.min_z_in == pytest.approx(0.75)                       # the final depth, lifted: 0.5 in above the plate
    assert air.count("X5.") == 1                                        # one lap
