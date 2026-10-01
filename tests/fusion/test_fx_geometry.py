"""fx_geometry.extract on a fake BRep plate, fed through the real plate rules (arithmetic and bookkeeping only)."""

import pytest

import fakebrep
from autocam_core.holes import HoleRules, ToolProfile, classify
from autocam_core.plate import analyze

RULES = HoleRules(0.002, 0.197, 0.002, 1.5, (1.125, 0.875), 0.010)
FOUR_MM = ToolProfile("t1", 0.15748, 0.0787, (0.156, 0.159))


@pytest.fixture
def fx_geometry():
    fakebrep.install()
    from autocam_worker import fx_geometry
    yield fx_geometry
    fakebrep.uninstall()


def test_plate_with_split_holes(fx_geometry):
    occ = fakebrep.plate(6.0, 4.0, 0.125, holes=((0.25, (1.0, 1.0)), (0.159, (3.0, 2.0))))
    geom = fx_geometry.extract("p01", occ)
    assert geom.solid_bodies == 1 and geom.thickness_in == pytest.approx(0.125)
    assert len(geom.faces) == 2 + 4 + 4
    assert geom.sharp_inside_corners == 0

    a = analyze(geom, (0.125, 0.25), 0.005)
    assert a.ok, a.errors
    assert a.stock_thickness_in == 0.125 and not a.pocket_floor_ids
    assert sorted(round(h.diameter_in, 4) for h in a.through_holes) == [0.159, 0.25]
    assert all(h.full for h in a.through_holes)
    assert [classify(h.diameter_in, FOUR_MM, RULES) for h in a.through_holes] == ["bore", "drill"]
    top = geom.face(a.up_face_id)
    assert top.normal_dot == pytest.approx(1.0) and top.z_min_in == pytest.approx(0.125)
    loops = [lp for lp in a.through_loops if lp.face_id == a.up_face_id]
    assert sorted(lp.index for lp in loops) == [0, 1]
    assert {lp.hole for lp in loops} == {0, 1}


def test_hole_centers_and_sweep(fx_geometry):
    geom = fx_geometry.extract("p01", fakebrep.plate(4.0, 3.0, 0.25, holes=((0.5, (2.0, 1.5)),)))
    halves = [f for f in geom.faces if f.kind == "cylinder"]
    assert [round(f.sweep_deg) for f in halves] == [180, 180]
    assert all(f.concave and f.axis_dot == pytest.approx(1.0) for f in halves)
    assert halves[0].center_in == pytest.approx(halves[1].center_in)
    assert halves[0].z_min_in == pytest.approx(0.0) and halves[0].z_max_in == pytest.approx(0.25)


def test_no_flat_face_or_two_bodies(fx_geometry):
    occ = fakebrep.plate(4.0, 3.0, 0.125)
    occ.bRepBodies.append(occ.bRepBodies[0])
    assert fx_geometry.extract("p01", occ).solid_bodies == 2
