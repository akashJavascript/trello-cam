import pytest

from autocam_core import errors as E
from autocam_core.geometry import Face, PartGeometry
from autocam_core.holes import (
    BEARING, BORE, CONTOUR_WARN, DRILL, INNER, TOO_SMALL, HoleRules, ToolProfile, classify, group_cylinders,
)
from autocam_core.plate import analyze, snap_thickness
from autocam_core.tooling import part_tool, plan_features, sheet_tool
from geombuilder import PlateBuilder

RULES = HoleRules(drill_tol_in=0.002, bore_min_in=0.197, bore_min_tol_in=0.002, bore_max_in=1.5,
                  bearing_sizes_in=(1.125, 0.875), bearing_tol_in=0.010)
MM4 = ToolProfile("t1_4mm_alu", 4 / 25.4, 0.0787, (0.156, 0.159), "4 mm O-flute ALU")
EIGHTH = ToolProfile("t12_eighth_alu", 0.125, 0.0625, (0.125, 0.136), "1/8 in ALU")
STOCK = (0.0625, 0.125, 0.1875, 0.25)


def analysis(builder: PlateBuilder):
    return analyze(builder.build(), STOCK, 0.005)


# ---- hole classification (decision 7)

@pytest.mark.parametrize("d, tool, kind", [
    (0.156, MM4, DRILL), (0.159, MM4, DRILL), (0.158, MM4, DRILL), (0.154, MM4, DRILL),
    (0.1535, MM4, TOO_SMALL), (0.136, MM4, TOO_SMALL),
    (0.195, MM4, BORE), (0.197, MM4, BORE), (0.1949, MM4, CONTOUR_WARN), (0.17, MM4, CONTOUR_WARN),
    (0.25, MM4, BORE), (1.5, MM4, BORE), (1.5001, MM4, INNER), (2.0, MM4, INNER),
    (0.875, MM4, BEARING), (0.885, MM4, BEARING), (0.8851, MM4, BORE),
    (1.125, MM4, BEARING), (1.115, MM4, BEARING), (1.114, MM4, BORE),
    (0.136, EIGHTH, DRILL), (0.125, EIGHTH, DRILL), (0.159, EIGHTH, CONTOUR_WARN),
    (0.1, EIGHTH, TOO_SMALL), (1.125, EIGHTH, BEARING), (0.25, EIGHTH, BORE),
])
def test_classify(d, tool, kind):
    assert classify(d, tool, RULES) == kind


def test_split_half_cylinders_make_one_full_hole():
    b = PlateBuilder()
    b.hole(0.25, (1, 1), split=True)
    b.slot(0.25, (3, 3))
    b.outside_corner(0.5)
    b.edge_fillet()
    holes = group_cylinders(b.build().faces)
    full = [h for h in holes if h.full]
    assert len(full) == 1 and full[0].diameter_in == pytest.approx(0.25) and len(full[0].face_ids) == 2
    assert sorted(h.sweep_deg for h in holes if not h.full) == [180.0, 180.0]  # slot ends stay separate


# ---- plate rules

def test_plain_plate():
    b = PlateBuilder()
    b.hole(0.159, (1, 1))
    b.hole(0.25, (2, 1))
    b.slot()
    a = analysis(b)
    assert a.ok, a.errors
    assert a.stock_thickness_in == 0.125 and not a.flipped
    assert len(a.through_holes) == 2
    assert sorted(l.hole for l in a.through_loops if l.hole is not None) == [0, 1]
    assert sum(1 for l in a.through_loops if l.hole is None) == 1   # the slot
    assert a.up_face_id == b.top


@pytest.mark.parametrize("t, stock", [(0.128, 0.125), (0.1201, 0.125), (0.25, 0.25)])
def test_thickness_snaps_within_tolerance(t, stock):
    assert snap_thickness(t, STOCK, 0.005) == stock


def test_off_stock_thickness_is_rejected():
    a = analysis(PlateBuilder(thickness=0.135))
    assert [e.code for e in a.errors] == [E.THICKNESS_NOT_STOCK]
    assert '0.1350"' in a.errors[0].msg


def test_two_bodies_rejected():
    b = PlateBuilder()
    b.bodies = 2
    assert [e.code for e in analysis(b).errors] == [E.BODY_COUNT]


@pytest.mark.parametrize("add, code", [
    (PlateBuilder.chamfer, E.CHAMFER), (PlateBuilder.edge_fillet, E.EDGE_FILLET), (PlateBuilder.cone, E.UNSUPPORTED_FACE),
])
def test_unsupported_shapes_rejected(add, code):
    b = PlateBuilder()
    add(b)
    assert code in [e.code for e in analysis(b).errors]


def test_pocket_from_the_top_is_accepted():
    b = PlateBuilder(thickness=0.25)
    floor = b.pocket(depth=0.1, corner_r=0.1)
    b.hole(0.159, (1, 1))
    a = analysis(b)
    assert a.ok and not a.flipped
    assert a.pocket_floor_ids == (floor,)
    assert a.inside_radii_in == (0.1,) * 4
    assert all(l.hole is not None for l in a.through_loops)  # the pocket opening is not a through loop


def test_pocket_seen_from_below_is_flipped_pocket_side_up():
    b = PlateBuilder(thickness=0.25, axis_up=False)
    floor = b.pocket(depth=0.1)
    a = analysis(b)
    assert a.ok and a.flipped
    assert a.pocket_floor_ids == (floor,)
    assert a.up_face_id == b.top
    assert a.geometry.face(floor).normal_dot == 1.0


def test_two_sided_part_rejected():
    b = PlateBuilder(thickness=0.25)
    b.pocket(depth=0.1)
    b.bottom_floor(depth=0.05)
    assert [e.code for e in analysis(b).errors] == [E.TWO_SIDED]


def test_hole_in_a_pocket_floor_goes_through():
    b = PlateBuilder(thickness=0.25)
    floor = b.pocket(depth=0.1)
    b.hole(0.25, (8.5, 8.5), on=floor)
    a = analysis(b)
    assert a.ok
    assert [l.face_id for l in a.through_loops] == [floor]
    assert a.through_holes[0].diameter_in == pytest.approx(0.25)


def test_counterbore():
    b = PlateBuilder(thickness=0.25)
    b.counterbore(d_big=0.4, depth=0.1, d_small=0.2)
    a = analysis(b)
    assert a.ok
    assert [round(h.diameter_in, 4) for h in a.through_holes] == [0.2]
    assert 0.2 in a.inside_radii_in       # the counterbore itself must suit the tool


def test_sharp_inside_corners_warn():
    b = PlateBuilder()
    b.sharp = 3
    a = analysis(b)
    assert a.ok and [w.code for w in a.warnings] == [E.SHARP_INSIDE_CORNERS]


def test_geometry_round_trips_through_dict():
    b = PlateBuilder()
    b.hole(0.25)
    g = b.build()
    assert PartGeometry.from_dict(g.to_dict()) == g


# ---- tool choice (decision 13)

def need(builder, family="aluminum", small=EIGHTH, force=False, strict=True):
    return part_tool(analysis(builder), family=family, default=MM4, small=small, rules=RULES,
                     force_small=force, strict_inside_radius=strict)


def test_4mm_is_the_default():
    b = PlateBuilder()
    b.hole(0.159)
    b.hole(0.197, (2, 2))
    b.inside_corner(0.0787)
    assert need(b).tool == "t1_4mm_alu"


def test_8_32_hole_moves_aluminum_part_to_eighth():
    b = PlateBuilder()
    b.hole(0.136)
    n = need(b)
    assert n.tool == "t12_eighth_alu"
    assert n.reasons == ('0.1360" hole is too small for the 4 mm O-flute ALU',)


def test_tight_inside_corner_needs_eighth():
    b = PlateBuilder()
    b.inside_corner(0.07)
    assert need(b).tool == "t12_eighth_alu"


def test_label_forces_eighth():
    n = need(PlateBuilder(), force=True)
    assert n.tool == "t12_eighth_alu" and n.reasons == ('"Tool 1/8" label on the card',)


@pytest.mark.parametrize("force", [False, True])
def test_poly_part_without_poly_eighth_gets_the_exact_comment(force):
    b = PlateBuilder()
    if not force:
        b.hole(0.136)
    n = need(b, family="polycarbonate", small=None, force=force)
    assert n.tool is None
    assert [(e.code, e.msg) for e in n.errors] == [
        (E.NEEDS_MANUAL_CAM, "needs manual CAM: no poly feeds for the 1/8 in endmill")]


def test_feature_too_small_for_any_tool():
    b = PlateBuilder()
    b.hole(0.1)
    n = need(b)
    assert n.tool is None and [e.code for e in n.errors] == [E.FEATURE_TOO_SMALL]


@pytest.mark.parametrize("strict, tool, codes", [
    (True, None, [E.INSIDE_RADIUS_TOO_SMALL]), (False, "t12_eighth_alu", [E.SMALL_INSIDE_RADIUS]),
])
def test_corner_too_tight_for_any_tool(strict, tool, codes):
    b = PlateBuilder()
    b.inside_corner(0.05)
    n = need(b, strict=strict)
    assert n.tool == tool
    assert [i.code for i in n.errors + n.warnings] == codes


def test_rejected_part_has_no_tool():
    assert need(PlateBuilder(thickness=0.3)).tool is None


def test_one_part_needing_eighth_moves_the_sheet():
    b1, b2 = PlateBuilder(), PlateBuilder()
    b2.hole(0.136)
    needs = {"p01": need(b1), "p02": need(b2)}
    tool, forced = sheet_tool(needs, "t1_4mm_alu", "t12_eighth_alu")
    assert tool == "t12_eighth_alu"
    assert forced == (("p02", '0.1360" hole is too small for the 4 mm O-flute ALU'),)
    assert sheet_tool({"p01": needs["p01"]}, "t1_4mm_alu", "t12_eighth_alu") == ("t1_4mm_alu", ())


# ---- per-sheet feature plan

def plate_with_everything():
    b = PlateBuilder(thickness=0.25)
    b.hole(0.159, (1, 1))       # drill (4 mm)
    b.hole(0.25, (2, 1))        # bore
    b.hole(1.125, (4, 4))       # bearing
    b.hole(2.0, (10, 4))        # inner (big round)
    b.hole(0.18, (6, 1))        # contour with a warning
    b.slot()                    # inner
    floor = b.pocket(depth=0.1)
    return b, floor


def test_feature_plan_with_4mm():
    b, floor = plate_with_everything()
    plan = plan_features(analysis(b), MM4, RULES)
    assert plan.counts() == {"drill": 1, "bore": 1, "bearing": 1, "inner": 3, "contour_warn": 1, "pockets": 1}
    assert plan.pocket_floor_ids == (floor,)
    assert [w.code for w in plan.warnings] == [E.HOLE_CONTOURED]
    assert not plan.errors


def test_feature_plan_on_an_eighth_sheet_contours_4mm_drill_sizes():
    b, _ = plate_with_everything()
    plan = plan_features(analysis(b), EIGHTH, RULES)
    assert plan.counts()["drill"] == 0
    assert plan.counts()["contour_warn"] == 2       # 0.159 and 0.18 are both contoured by the 1/8 in
    assert len([w for w in plan.warnings if w.code == E.HOLE_CONTOURED]) == 2
