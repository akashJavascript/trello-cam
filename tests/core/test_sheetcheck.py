from pathlib import Path

from autocam_core.sheetcheck import outer_depth_problems

SHEET = (Path(__file__).resolve().parents[1] / "fixtures" / "taps" / "sheet_mist.tap").read_bytes().decode("ascii")


def test_sample_sheet_cuts_every_outline_through():
    assert outer_depth_problems(SHEET, 0.0) == []


def test_outline_stopping_at_the_stock_top_is_named():
    # What pipeline_probe posted: the template's bottom followed the selected top face.
    shallow = SHEET.replace("[outer p02-1]\r\nG0 X12. Y4.\r\nG0 Z0.325\r\nG1 Z0. F20.",
                            "[outer p02-1]\r\nG0 X12. Y4.\r\nG0 Z0.325\r\nG1 Z0.125 F20.")
    assert shallow != SHEET
    problems = outer_depth_problems(shallow, 0.0)
    assert len(problems) == 1
    assert "[outer p02-1] never reaches the stock bottom (lowest Z 0.1250 in)" in problems[0]
    assert "Stock bottom, offset 0" in problems[0]


def test_whole_program_at_stock_top_flags_every_outline():
    problems = outer_depth_problems(SHEET.replace("G1 Z0. F20.", "G1 Z0.125 F20."), 0.0)
    assert [p.split("]")[0] for p in problems] == ["outline op [outer p01-1", "outline op [outer p02-1",
                                                  "outline op [outer p02-2"]


def test_depth_reached_by_a_later_op_does_not_count():
    text = "\r\n".join(["G20", "[outer p01-1]", "G0 X1. Y1.", "G0 Z0.3", "G1 Z0.1 F20.", "G0 Z2.",
                        "[PART 1 DONE - PAUSE]", "G53 Z", "M5", "M0",
                        "[outer p02-1]", "G0 X5. Y1.", "G0 Z0.3", "G1 Z0. F20.", "G0 Z2."])
    problems = outer_depth_problems(text, 0.0)
    assert len(problems) == 1 and "[outer p01-1]" in problems[0]


def test_machine_retracts_are_not_depths():
    text = "\r\n".join(["[outer p01-1]", "G53 Z", "G0 X1. Y1.", "G0 Z2."])
    assert "never reaches the stock bottom (lowest Z 2.0000 in)" in outer_depth_problems(text, 0.0)[0]
    assert outer_depth_problems("[outer p01-1]\r\nG53 Z\r\nG0 X1. Y1.", 0.0) == ["outline op [outer p01-1] has no Z move"]


def test_tolerance_is_posting_precision_only():
    at = "[outer p01-1]\r\nG1 Z{} F20."
    assert outer_depth_problems(at.format("0.0004"), 0.0) == []
    assert outer_depth_problems(at.format("0.001"), 0.0) != []
