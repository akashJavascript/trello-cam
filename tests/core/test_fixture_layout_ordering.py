import pytest

from autocam_core import fixture
from autocam_core.layout import Placed, plan_layout
from autocam_core.ordering import order_outlines

DEFAULTS = dict(sheet_length_in=48.0, sheet_width_in=24.0, reach_in=40.0, edge_margin_in=0.5,
                reach_margin_in=0.5, clamp_edges=("left", "right"), clamp_reach_in=1.0,
                clamp_clearance_in=0.25, clamp_height_in=1.5)


def test_default_fixture_matches_the_machine():
    # X across the 24 in width (clamp strips on the left and right long edges), Y along the length to the 40 in reach.
    fx = fixture.build(**DEFAULTS)
    assert fx.nest_region_in == (1.25, 0.5, 22.75, 39.5)
    assert fx.clamp_zones_in == ((0.0, 0.0, 1.25, 48.0), (22.75, 0.0, 24.0, 48.0))
    assert fx.nest_size_in == (21.5, 39.0)


def test_clamped_front_end_gets_a_strip():
    fx = fixture.build(**{**DEFAULTS, "clamp_edges": ("left", "right", "front")})
    assert fx.nest_region_in[1] == 1.25
    assert (0.0, 0.0, 24.0, 1.25) in fx.clamp_zones_in


def test_no_room_or_unknown_edge_is_an_error():
    with pytest.raises(ValueError, match="no room"):
        fixture.build(**{**DEFAULTS, "clamp_reach_in": 12.0})
    with pytest.raises(ValueError, match="unknown clamp edges"):
        fixture.build(**{**DEFAULTS, "clamp_edges": ("far_end",)})


def test_heights():
    assert fixture.clamp_clear_z(0.125, 1.5, 0.25) == pytest.approx(1.875)
    assert fixture.min_clearance_height(0.25, 2.0) == pytest.approx(2.25)


def test_sheet_origin_from_envelope():
    region = (0.5, 1.25, 39.5, 22.75)
    assert fixture.sheet_origin((100.5, 1.25, 139.5, 22.75), region) == (100.0, 0.0)


# ---- layout

ENV1 = (100.0, 0.0, 139.0, 21.5)
ENV2 = (145.0, 0.0, 184.0, 21.5)


def body(i, key, x, y, w=2.0, h=1.0):
    return Placed(f"b{i}", key, (x, y, x + w, y + h))


def test_bodies_are_binned_by_position_and_numbered():
    bodies = [body(1, "p01", 105, 5), body(2, "p01", 101, 5), body(3, "p02", 150, 2), body(4, "p01", 160, 8)]
    lay = plan_layout(bodies, [ENV1, ENV2], {"p01": 3, "p02": 1})
    assert [s.index for s in lay.sheets] == [1, 2]
    assert [(i, b.body_id) for i, b in lay.sheets[0].instances] == [("p01-1", "b2"), ("p01-2", "b1")]
    assert [(i, b.body_id) for i, b in lay.sheets[1].instances] == [("p01-1", "b4"), ("p02-1", "b3")]
    assert lay.placed == {"p01": 3, "p02": 1}
    assert not lay.deferred and not lay.problems


def test_short_quantity_defers_the_whole_card():
    bodies = [body(1, "p01", 105, 5), body(2, "p02", 110, 5), body(3, "p02", 300, 5)]  # one p02 didn't fit
    lay = plan_layout(bodies, [ENV1], {"p01": 1, "p02": 2})
    assert lay.deferred == ("p02",)
    assert lay.removed_bodies == ("b2",)
    assert lay.unplaced_bodies == ("b3",)
    assert [i for i, _ in lay.sheets[0].instances] == ["p01-1"]
    assert lay.placed == {"p01": 1}


def test_sheet_left_empty_by_deferral_is_dropped():
    lay = plan_layout([body(1, "p01", 105, 5), body(2, "p02", 150, 5)], [ENV1, ENV2], {"p01": 2, "p02": 1})
    assert lay.deferred == ("p01",)
    assert [(s.index, s.envelope_in) for s in lay.sheets] == [(1, ENV2)]


def test_layout_problems_are_reported():
    bodies = [body(1, "p01", 137.5, 5), body(2, "p01", 110, 5), body(3, "p09", 120, 5)]  # b1 sticks out past x=139
    lay = plan_layout(bodies, [ENV1], {"p01": 1})
    assert any("crosses the edge" in p for p in lay.problems)
    assert any("2 copies placed but only 1 ordered" in p for p in lay.problems)
    assert any("p09: on a sheet but not in the job" in p for p in lay.problems)


# ---- ordering

def test_nearest_neighbour_from_the_zero_corner():
    pts = [("p03-1", (30.0, 5.0)), ("p01-1", (2.0, 2.0)), ("p02-1", (5.0, 2.0)), ("p04-1", (6.0, 20.0))]
    assert order_outlines(pts) == ["p01-1", "p02-1", "p04-1", "p03-1"]


def test_ties_go_to_the_lower_id_and_duplicates_fail():
    assert order_outlines([("b", (1.0, 0.0)), ("a", (0.0, 1.0))]) == ["a", "b"]
    with pytest.raises(ValueError):
        order_outlines([("a", (0.0, 0.0)), ("a", (1.0, 1.0))])
