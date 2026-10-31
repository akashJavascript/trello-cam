"""Pockets (decisions, 2026-10-31): [pocket] clears each floor, [pocketfinish] takes its walls, both before the holes."""

from autocam_core import errors as E
from autocam_core.airtest import op_tag
from geombuilder import PlateBuilder
from rig import OPS, Rig, plate

POCKET_OPS = ("[pocket] pockets", "[pocketfinish] pocket walls") + OPS


def retainer():
    """A bearing-retainer-like plate: a 1.6 in counterbore 0.1 deep with a 0.25 in hole through its floor."""
    b = PlateBuilder("retainer", thickness=0.125)
    floor = b.counterbore(d_big=1.6, depth=0.06, d_small=0.25, center=(2.0, 2.0))
    return b.build(), floor


def test_pockets_are_cleared_and_finished_before_the_holes(tmp_path):
    rig = Rig(tmp_path, ops=POCKET_OPS)
    geom, floor = retainer()
    job = rig.job([("retainer", 2, geom, (4.0, 4.0)), ("spacer", 1, plate(), (3.0, 3.0))])
    result = rig.run(job)

    assert result.status == "ok", (result.sheets[0].errors, [p.errors for p in result.parts])
    (sheet,) = result.sheets
    s = rig.fake.sheets[sheet.name]
    floors = (("p01.1", floor), ("p01.2", floor))
    assert s["fills"]["[pocket] pockets"].floors == floors
    assert s["fills"]["[pocketfinish] pocket walls"].floors == floors
    assert len(s["fills"]["[bore] holes"].holes) == 2            # the hole in each floor, cut after the pocket
    assert s["ops"][:3] == ["[pocket] pockets", "[pocketfinish] pocket walls", "[bore] holes"]
    assert result.parts[0].holes.pockets == 1
    view = rig.service_view(job, result)
    assert view.failure is None and view.part_problems == {}


def test_a_template_without_the_finish_pass_still_takes_pockets(tmp_path):
    rig = Rig(tmp_path, ops=("[pocket] pockets",) + OPS)
    geom, floor = retainer()
    result = rig.run(rig.job([("retainer", 1, geom, (4.0, 4.0))]))
    assert result.status == "ok", (result.sheets[0].errors, [p.errors for p in result.parts])
    assert rig.fake.sheets[result.sheets[0].name]["fills"]["[pocket] pockets"].floors == (("p01.1", floor),)


def test_a_template_without_a_pocket_op_stops_a_sheet_with_pockets(tmp_path):
    rig = Rig(tmp_path)
    geom, _ = retainer()
    result = rig.run(rig.job([("retainer", 1, geom, (4.0, 4.0))]))
    (sheet,) = result.sheets
    assert any(e.code == E.TEMPLATE_PROBLEM and "no [pocket] op" in e.msg for e in sheet.errors)


def test_pocket_ops_go_from_a_sheet_without_pockets(tmp_path):
    rig = Rig(tmp_path, ops=POCKET_OPS)
    result = rig.run(rig.job([("gusset", 1, plate(holes=(0.25,)), (4.0, 4.0))]))
    assert result.status == "ok"
    deleted = rig.fake.sheets[result.sheets[0].name]["deleted"]
    assert {"[pocket] pockets", "[pocketfinish] pocket walls"} <= set(deleted)


def test_an_adapter_that_cant_fill_pockets_sends_the_part_to_manual_cam(tmp_path):
    rig = Rig(tmp_path, ops=POCKET_OPS)
    rig.fake.capabilities = rig.fake.capabilities - {"pocket"}
    geom, _ = retainer()
    job = rig.job([("retainer", 1, geom, (4.0, 4.0)), ("spacer", 1, plate(), (3.0, 3.0))])
    result = rig.run(job)
    retainer_part = result.parts[0]
    assert retainer_part.errors[0].code == E.NEEDS_MANUAL_CAM and "pocket" in retainer_part.errors[0].msg
    assert [p.part_key for p in result.sheets[0].parts] == ["p02"]


def test_a_raised_island_sends_the_part_to_manual_cam(tmp_path):
    rig = Rig(tmp_path, ops=POCKET_OPS)
    b = PlateBuilder("islanded")
    b.island(b.pocket(0.06), height=0.03)      # one up to the top splits the top face, which is caught already
    job = rig.job([("islanded", 1, b.build(), (10.0, 10.0)), ("spacer", 1, plate(), (3.0, 3.0))])
    result = rig.run(job)
    assert result.parts[0].errors[0].code == E.NEEDS_MANUAL_CAM and "island" in result.parts[0].errors[0].msg
    assert [p.part_key for p in result.sheets[0].parts] == ["p02"]


def test_the_air_test_knows_the_finish_pass_is_its_own_op():
    assert op_tag("[pocketfinish pocket walls]") == "pocketfinish"
    assert op_tag("[pocket pockets]") == "pocket"
