import math
from pathlib import Path

import pytest

from autocam_core.toolpoints import _arc_mid, cutting_points

TAPS = Path(__file__).resolve().parents[1] / "fixtures" / "taps"


def test_real_outline_stays_a_tool_radius_outside_the_plate():
    # fusion_one_outline.tap: the outline of a 4 x 3 in plate at X1-5, Y1-4, cut to Z0 with the 4 mm tool.
    points = cutting_points((TAPS / "fusion_one_outline.tap").read_text(), below_z=0.125)
    assert len(points) > 40
    r = 0.0787 - 1e-3
    for _, x, y, z in points:
        assert z < 0.125
        dx, dy = max(1 - x, 0, x - 5), max(1 - y, 0, y - 4)
        assert math.hypot(dx, dy) >= r, (x, y)      # round the corners too, at the tool radius


def test_drill_cycles_and_retracts():
    text = "\r\n".join(["G90", "G20", "G53 Z", "G0 X5. Y5.", "G0 Z0.325", "G81 X5. Y5. Z0. R0.325 F20.",
                        "X6. Y5.", "G80", "G0 Z2.125", "G1 X7. F60."])
    points = cutting_points(text, below_z=0.125)
    assert [(n, x, y) for n, x, y, _ in points] == [(6, 5.0, 5.0), (7, 6.0, 5.0)]


def test_arc_midpoints_follow_the_direction():
    assert _arc_mid(10, 10, 12, 10, 11, 10, clockwise=False) == pytest.approx((11, 9))
    assert _arc_mid(10, 10, 12, 10, 11, 10, clockwise=True) == pytest.approx((11, 11))
    assert _arc_mid(12, 10, 12, 10, 11, 10, clockwise=False) == pytest.approx((10, 10))


def test_moves_above_the_stock_are_ignored():
    text = "\r\n".join(["G90", "G20", "G53 Z", "G0 X1. Y1.", "G0 Z0.5", "G1 X2. F60.", "G1 Z0.1 F20.", "G1 X3."])
    assert [n for n, *_ in cutting_points(text, below_z=0.125)] == [7, 7, 8, 8]
