import hashlib
from pathlib import Path

import pytest

from autocam_core.tapguard import GuardSpec, _arc_box, check_program, parse_code, rejected_path

REPO = Path(__file__).resolve().parents[2]
SAMPLE = (REPO / "tests/fixtures/taps/sheet_mist.tap").read_bytes()
AIR_TEST = (REPO / "fusion/tests/pause_air_test.tap").read_bytes()

THICKNESS = 0.125
SPEC = GuardSpec(
    clamp_zones_in=((0.0, 0.0, 48.0, 1.25), (0.0, 22.75, 48.0, 24.0)),
    clamp_clear_z_in=THICKNESS + 1.5 + 0.25,
    tool_radius_in=0.0787,
    reach_y_in=40.0,
    mist=True,
    stock_top_in=THICKNESS,
)


def edit(old: str, new: str, data: bytes = SAMPLE, count: int = 1) -> bytes:
    text = data.decode("ascii")
    assert old in text, old
    return text.replace(old, new, count).encode("ascii")


def check(data: bytes, spec: GuardSpec = SPEC):
    return check_program(data, spec)


def test_sample_program_passes():
    report = check(SAMPLE)
    assert report.passed, report.summary()
    assert report.min_z_in == 0.0
    assert report.m0_count == 0
    assert report.sha256 == hashlib.sha256(SAMPLE).hexdigest()
    assert report.summary() == "passed; lowest Z 0.0000 in"


def test_pause_air_test_passes():
    report = check(AIR_TEST, GuardSpec(mist=False))
    assert report.passed, report.summary()
    assert report.min_z_in == 3.0
    assert report.m0_count == 2


# ---- the Z floor

@pytest.mark.parametrize("new", ["G1 Z-0.0001 F20.", "G1 Z-.5 F20.", "G1 Z-0. F20.X"])
def test_below_floor_or_unreadable_fails(new):
    report = check(edit("G1 Z0. F20.", new))
    assert not report.passed


def test_smallest_negative_z_is_an_offender():
    report = check(edit("G1 Z0. F20.", "G1 Z-0.0001 F20."))
    assert report.offenders == ("line 19: G1 Z-0.0001 F20.",)
    assert report.min_z_in == pytest.approx(-0.0001)
    assert "goes below Z0.0000" in report.summary()


def test_negative_zero_and_plus_sign_pass():
    assert check(edit("G1 Z0. F20.", "G1 Z-0. F20.")).passed
    assert check(edit("G0 Z0.325", "G0 Z+0.325")).passed


def test_z_inside_a_comment_is_ignored():
    assert check(edit("[drill]", "[drill Z-5 is only a comment]")).passed


def test_unclosed_comment_fails():
    report = check(edit("[outer p01-1]", "[outer p01-1"))
    assert any("unclosed '['" in p for p in report.problems)


def test_floor_above_zero_is_respected():
    report = check(SAMPLE, GuardSpec(**{**SPEC.__dict__, "z_floor_in": 0.01}))
    assert not report.passed and report.offenders


def test_negative_floor_is_refused():
    with pytest.raises(ValueError):
        GuardSpec(z_floor_in=-0.01)


# ---- G53 and canned cycles

def test_only_the_two_g53_forms_are_allowed():
    report = check(edit("G53 Z\r\nM5", "G53 X5.\r\nG53 Z\r\nM5"))
    assert any("only 'G53 Z'" in p for p in report.problems)


@pytest.mark.parametrize("cycle", ["G81 X5. Y5. Z-0.01 R0.325 F20.", "G81 X5. Y5. Z0. R-0.1 F20."])
def test_drilling_cycle_z_and_r_are_checked(cycle):
    report = check(edit("G81 X5. Y5. Z0. R0.325 F20.", cycle))
    assert report.offenders and not report.passed


def test_peck_cycles_are_rejected_by_default():
    report = check(edit("G81 X5. Y5. Z0. R0.325 F20.", "G83 X5. Y5. Z0. R0.325 Q0.05 F20."))
    assert any("G83 is not allowed" in p for p in report.problems)


def test_drilling_cycle_needs_z_and_r():
    report = check(edit("G81 X5. Y5. Z0. R0.325 F20.", "G81 X5. Y5. Z0. F20."))
    assert any("without Z and R" in p for p in report.problems)


# ---- forbidden codes and units

@pytest.mark.parametrize("line, why", [
    ("G91", "G91 is not allowed"),
    ("G41 O0.0787", "unexpected word O"),
    ("G41", "G41 is not allowed"),
    ("G92 X0. Y0.", "G92 is not allowed"),
    ("G28", "G28 is not allowed"),
    ("G17", "G17 is not allowed"),
    ("T1", "unexpected word T"),
    ("M8", "M8 is not allowed"),
    ("g0 x1.", "can't read"),
])
def test_unexpected_codes_fail(line, why):
    report = check(edit("[outer p01-1]", f"[outer p01-1]\r\n{line}"))
    assert any(why in p for p in report.problems), report.problems


def test_missing_g20_fails():
    report = check(edit("G20\r\n", ""))
    assert "expected exactly one G20, found 0" in report.problems
    assert any("motion before G20" in p for p in report.problems)


def test_metric_fails():
    report = check(edit("G20", "G22"))
    assert any("G22 is not allowed" in p for p in report.problems)


def test_non_ascii_fails():
    report = check(SAMPLE.replace(b"[drill]", "[drill °]".encode("utf-8")))
    assert "program contains non-ASCII bytes" in report.problems


# ---- clamp zones and heights

def test_rapid_across_clamp_zone_at_low_z_fails():
    report = check(edit("G1 X8. F60.", "G1 X4. Y0.5 F60.\r\nG1 X8. Y4."))
    assert report.clamp_violations and not report.passed


def test_same_rapid_after_g53_z_passes():
    report = check(edit("G0 Z0.325\r\nG1 Z0. F20.\r\nG1 X8.",
                        "G53 Z\r\nG0 X4. Y0.5\r\nG0 X4. Y4.\r\nG0 Z0.325\r\nG1 Z0. F20.\r\nG1 X8."))
    assert report.passed, report.summary()


def test_rapid_over_zone_at_clearance_height_passes():
    assert check(edit("G0 X4. Y4.", "G0 X4. Y0.5\r\nG0 X4. Y4.")).passed


def test_arc_over_clamp_zone_fails():
    report = check(edit("G1 X8. F60.", "G2 X4. Y4. I0. J-2. F60.\r\nG1 X8. F60."))
    assert report.clamp_violations


def test_descending_at_unknown_position_fails():
    report = check(edit("[outer p01-1]", "G53 Z\r\nG53 P10\r\nG0 Z0.5\r\n[outer p01-1]"))
    assert any("descends at an unknown position" in v for v in report.clamp_violations)


def test_xy_move_before_retract_fails():
    report = check(edit("G53 Z\r\n[drill]", "[drill]"))
    assert any("XY move before the height is known" in p for p in report.problems)


def test_park_without_retract_fails():
    report = check(edit("[outer p01-1]", "G0 Z0.325\r\nG53 P10\r\n[outer p01-1]"))
    assert any("park without a G53 Z" in p for p in report.problems)


def test_y_beyond_reach_fails():
    report = check(edit("G1 Y8.", "G1 Y41."))
    assert any("beyond the 40.0 in reach" in p for p in report.problems)


# ---- spindle, mist and the program ending

def test_m0_with_spindle_running_fails():
    report = check(edit("G4 X4.\r\n", "G4 X4.\r\nM0\r\n"))
    assert any("M0 stop with the spindle running" in p for p in report.problems)


def test_cutting_with_spindle_stopped_fails():
    report = check(edit("[outer p01-1]", "M5\r\n[outer p01-1]"))
    assert any("spindle stopped" in p for p in report.problems)


def test_mist_codes_when_mist_is_off_fail():
    report = check(SAMPLE, GuardSpec(**{**SPEC.__dict__, "mist": False}))
    assert "mist codes present but mist is off for this material" in report.problems


def test_missing_mist_on_fails():
    report = check(edit("M11 C8\r\n", ""))
    assert any("M11 C8 is missing" in p for p in report.problems)


def test_missing_park_at_end_fails():
    report = check(edit("G53 P10\r\n", ""))
    assert "program doesn't end with 'G53 P10'" in report.problems


def test_missing_final_retract_fails():
    report = check(edit("M12 C8\r\nG53 Z\r\nM5", "M12 C8\r\nM5"))
    assert "no G53 Z retract after the last move" in report.problems


def test_repeated_axis_word_fails():
    report = check(edit("G1 X8. F60.", "G1 X8. X9. F60."))
    assert any("repeated X word" in p for p in report.problems)


# ---- helpers

def test_parse_code_reads_post_words():
    assert parse_code("G53 Z") == [("G", 53.0), ("Z", None)]
    assert parse_code("N10 G4 X4. [dwell]") == [("G", 4.0), ("X", 4.0)]
    assert parse_code("G0X-.5Y+1") == [("G", 0.0), ("X", -0.5), ("Y", 1.0)]
    with pytest.raises(ValueError):
        parse_code("G0 X1 % junk")


def test_rejected_path():
    assert rejected_path(Path("out/6061_0p125_r017_S1.tap")).name == "6061_0p125_r017_S1.REJECTED.tap"


# ---- review fixes: line splitting, safe code sets, sideways rapids

@pytest.mark.parametrize("raw, why", [
    (b"G53 Z\x0bG0 X5. Y5.", "control characters 0x0b"),
    (b"G53 Z\rG0 X5. Y5.", "lone CR line ending"),
    (b"G0\tX5.", "control characters 0x09"),
    (b"G0 X5. Y5.\nG0 Z1.", "mixed CRLF and LF"),
])
def test_bytes_that_could_split_differently_fail(raw, why):
    report = check(edit("[outer p01-1]", "[outer p01-1]\r\n" + raw.decode("ascii")))
    assert any(why in p for p in report.problems), report.problems


def test_cr_only_file_fails_but_lf_only_passes():
    assert any("CR line endings" in p for p in check(SAMPLE.replace(b"\r\n", b"\r")).problems)
    assert check(SAMPLE.replace(b"\r\n", b"\n")).passed


@pytest.mark.parametrize("code, line", [(18, "G18"), (55, "G55"), (91, "G91"), (17, "G17"), (43, "G43")])
def test_config_cannot_widen_the_guard(code, line):
    wide = GuardSpec(**{**SPEC.__dict__, "allowed_g": SPEC.allowed_g | {code}})
    report = check(edit("[outer p01-1]", f"[outer p01-1]\r\n{line}"), wide)
    assert any(f"G{code} is not allowed" in p for p in report.problems)


def test_sideways_rapid_below_stock_top_fails():
    report = check(edit("G1 Z0. F20.\r\nG1 X8. F60.", "G0 Z0.\r\nG0 X8."))
    assert any("rapid sideways below the stock top" in p for p in report.problems)


def test_vertical_rapid_below_stock_top_is_allowed():
    assert check(edit("G1 Z0. F20.\r\nG1 X8. F60.", "G0 Z0.05\r\nG1 Z0. F20.\r\nG1 X8. F60.")).passed


def test_drilling_travel_below_stock_top_fails():
    report = check(edit("G81 X5. Y5. Z0. R0.325 F20.", "G81 X5. Y5. Z0. R0.05 F20."))
    assert any("drilling travel below the stock top" in p for p in report.problems)


# ---- staying on the sheet

ON_SHEET = GuardSpec(**{**SPEC.__dict__, "sheet_in": (48.0, 24.0)})


def test_sample_program_stays_on_the_sheet():
    assert check(SAMPLE, ON_SHEET).passed


@pytest.mark.parametrize("old, new", [
    ("G1 X8. F60.", "G1 X-1. F60."),             # cutting past the zero end
    ("G1 Y8.", "G1 Y23.99"),                     # into the far edge (tool radius counts)
    ("G81 X5. Y5. Z0. R0.325 F20.", "G81 X5. Y-0.5 Z0. R0.325 F20."),   # drilling off the front edge
])
def test_cutting_off_the_sheet_fails(old, new):
    report = check(edit(old, new), ON_SHEET)
    assert not report.passed
    assert any("off the sheet" in p for p in report.problems)


def test_travel_above_the_stock_may_leave_the_sheet():
    # A rapid at clearance height off the sheet is travel, not cutting (the clamp check still applies).
    data = edit("G0 X12. Y4.\r\n", "G0 X-2. Y12.\r\nG0 X12. Y4.\r\n")
    assert not any("off the sheet" in p for p in check(data, ON_SHEET).problems)


def test_without_a_sheet_size_nothing_is_checked():
    assert not any("off the sheet" in p for p in check(edit("G1 X8. F60.", "G1 X-1. F60.")).problems)


# ---- arcs are bounded by what they sweep, not their whole circle

def test_arc_box_follows_the_direction():
    # Half circle from (10, 10) to (12, 10) around (11, 10).
    assert _arc_box(10, 10, 12, 10, 11, 10, clockwise=False) == pytest.approx((10, 9, 12, 10))   # G3: underneath
    assert _arc_box(10, 10, 12, 10, 11, 10, clockwise=True) == pytest.approx((10, 10, 12, 11))   # G2: over the top


def test_arc_box_full_circle_and_short_arc():
    assert _arc_box(12, 10, 12, 10, 11, 10, clockwise=False) == pytest.approx((10, 9, 12, 11))
    # A short stretch of a 28 in radius outline stays a small box.
    x0, y0 = 7.9564 + 0.2, 13.4923 - 0.2
    box = _arc_box(x0, y0, 7.9564, 13.4923, x0 - 27.8577, y0 + 0.139, clockwise=False)
    assert box[2] - box[0] < 0.5 and box[3] - box[1] < 0.5


def test_big_radius_outline_mid_sheet_passes():
    # Run t184402: a C-shaped plate's outline ramping down along a 28 in radius arc, mid-sheet.
    data = edit("G3 X9. Y5. I0. J1.", "G3 X3.9 Y4.05 Z0.0962 I-27.8577 J0.139", count=1)
    report = check(data, ON_SHEET)
    assert not report.clamp_violations and not any("off the sheet" in p for p in report.problems), report.summary()


def test_arc_that_really_dips_into_a_clamp_strip_is_still_caught():
    # From (9, 5) around (9, 3) to (11, 3): G3 goes the long way, down through Y1 (inside the front strip)
    # at Z0; G2 is the quarter turn on the right and stays clear.
    assert check(edit("G1 Y8.\r\n", "G3 X11. Y3. I0. J-2.\r\nG1 Y8.\r\n"), ON_SHEET).clamp_violations
    assert not check(edit("G1 Y8.\r\n", "G2 X11. Y3. I0. J-2.\r\nG1 Y8.\r\n"), ON_SHEET).clamp_violations


def test_the_report_says_what_the_cutter_covers_below_the_stock_top():
    program = b"G90\r\nG20\r\nG53 Z\r\nS18000\r\nM3\r\nG4 X4.\r\nG0 X1. Y1.\r\nG0 Z0.3\r\nG1 Z0. F20.\r\n" \
              b"G1 X3. F60.\r\n" \
              b"G3 X3. Y3. I0. J1.\r\nG1 X1.\r\nG0 Z2.\r\nG0 X9. Y9.\r\nG53 Z\r\nM5\r\nG53 P10\r\n"
    report = check_program(program, GuardSpec(stock_top_in=0.125, tool_radius_in=0.1))
    # the counter-clockwise arc bulges out to X4 (centre X3 Y2, radius 1); the rapid at Z2 doesn't count
    assert report.cut_box_in == (0.9, 0.9, 4.1, 3.1)
