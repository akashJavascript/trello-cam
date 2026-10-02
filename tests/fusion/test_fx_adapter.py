"""The parts of the Fusion adapter that can run offline (on fakebrep's stand-in BRep)."""

import types

import pytest

import fakebrep
from autocam_worker.adapter import AdapterError

V = fakebrep.Vector3D


@pytest.fixture
def fx():
    fakebrep.install()
    from autocam_worker import fx_adapter, fx_design, fx_geometry
    yield types.SimpleNamespace(adapter=fx_adapter, design=fx_design, geometry=fx_geometry)
    fakebrep.uninstall()


def test_flip_rule_matches_the_probe(fx):
    # pipeline_probe2 v3: plates modeled flat report upDirection (0, 0, -1) and land upside down unless flipped.
    assert fx.design.orientation(V(0, 0, -1), V(0, 0, 1)) is True
    assert fx.design.orientation(V(0, 0, 1), V(0, 0, 1)) is False
    assert fx.design.orientation(V(0, 1, 0), V(0, 0, 1)) is None     # would lie on its side


def test_any_fusion_exception_becomes_an_adapter_error(fx):
    def boom(*a):
        raise RuntimeError("3 : InternalValidationError")
    adapter = fx.adapter.FusionAdapter(types.SimpleNamespace(version="x"), job=None)
    adapter.design = types.SimpleNamespace(findEntityByToken=boom)
    adapter.tokens["p01.1"] = "token"
    with pytest.raises(AdapterError, match="^discard: RuntimeError: 3 : InternalValidationError"):
        adapter.discard("p01.1")
    with pytest.raises(AdapterError, match="^no occurrence for p02.1"):
        adapter.box("p02.1")


def test_inner_loops_whole_face_or_one_by_one(fx):
    occ = fakebrep.plate(6.0, 4.0, 0.125, holes=((0.25, (1.0, 1.0)), (2.0, (3.5, 2.0))))
    geom = fx.geometry.extract("p01", occ)
    top = next(f.id for f in geom.faces if f.kind == "plane" and f.normal_dot > 0.99)
    adapter = fx.adapter.FusionAdapter(types.SimpleNamespace(version="x"), job=None)
    adapter._occ = lambda cid: occ

    whole, single = adapter._inner_selection([("p01.1", top, 0), ("p01.1", top, 1)])
    assert [f.tempId for f in whole] == [occ.bRepBodies[0].faces[top - 1].tempId] and single == []

    whole, single = adapter._inner_selection([("p01.1", top, 1)])   # the 2 in cutout only; the 0.25 is bored
    assert whole == [] and len(single) == 1
    loop = single[0]
    assert loop is occ.bRepBodies[0].faces[top - 1].loops[2] and not loop.isOuter


def test_cuts_into_parts_tests_the_tool_center_against_the_bodies(fx):
    occ = fakebrep.plate(6.0, 4.0, 0.25, holes=((0.5, (2.0, 2.0)),))
    adapter = fx.adapter.FusionAdapter(types.SimpleNamespace(version="x"), job=None)
    adapter._occ = lambda cid: occ
    adapter.sheet_info["S1"] = ((-10.0, 0.0), 0.25, ["p01.1"])     # sheet origin at x=-10 in the design
    points = [(1, 12.0, 2.0, 0.0),       # the hole's center: air
              (2, 13.0, 2.0, 0.0),       # 1 in to its right: inside the plate
              (3, 17.0, 2.0, 0.0),       # past the plate's right edge: air
              (4, 13.0, 2.0, 0.0)]       # the same point again: checked once
    assert adapter.cuts_into_parts("S1", points) == [(2, 13.0, 2.0)]


def test_fusion_team_link_kind(fx):
    import base64
    # The link run 2026-10-02 got right after saveAs: a folder, not the file.
    folder = "https://dtechhs88.autodesk360.com/g/projects/202610021148992906/data/dXJuOmFkc2sud2lwcHJvZDpmcy5mb2xkZXI6Y28uLWNZenB"
    assert fx.design.link_kind(folder) == "folder"
    urn = base64.urlsafe_b64encode(b"urn:adsk.wipprod:dm.lineage:AbCdEf123").decode().rstrip("=")
    assert fx.design.link_kind(f"https://x.autodesk360.com/g/data/{urn}") == "file"
    assert fx.design.link_kind("https://x.autodesk360.com/g/data/@@@") == "unknown"
