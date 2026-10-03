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
