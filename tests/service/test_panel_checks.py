"""The panel's design checks: Onshape's description of a part -> AutoCAM's part model -> its own plate rules, said as
something a student can fix. (geometry.py's conventions were checked on P-2011's real response against Fusion's
analysis of the same version: 3/16 in, 4 mm, 2 drilled, 20 bored, 2 bearing, 25 cut out.)"""

import copy
import json

from autocam_service.config import parse_config
from autocam_service.panel.checks import check
from autocam_service.panel.geometry import body_geometry, solid_body
from fakeonshape import FakeTransport, resp
from onshape_body import plate_body, rectangle
from test_panel import PARTS, Q, Panel
from test_runner_e2e import RAW, REPO_ROOT

CFG = parse_config(copy.deepcopy(RAW), root=REPO_ROOT)


def geom(outline, thickness, holes=()):
    data = plate_body(outline, thickness, holes)
    return body_geometry(solid_body(data, "JHD"), "P-2014")


def every_text(result):
    return " ".join([p["title"] + " " + p["fix"] for p in result["problems"]] + result["notes"]
                    + [result["cutterWhy"]])


def test_the_model_is_read_like_fusions():
    g = geom(rectangle(4, 2), 0.125, [(1, 1, 0.25)])
    assert abs(g.thickness_in - 0.125) < 1e-9 and g.sharp_inside_corners == 0
    holes = [f for f in g.faces if f.kind == "cylinder"]
    assert len(holes) == 2 and all(f.concave and abs(f.sweep_deg - 180) < 1e-6 for f in holes)
    top = next(f for f in g.faces if f.kind == "plane" and f.normal_dot > 0.99 and f.z_min_in > 0.1)
    assert [sorted(loop) for loop in top.inner_loops] == [sorted(f.id for f in holes)]


def test_a_plate_auto_cam_can_cut():
    r = check(geom(rectangle(4, 2), 0.125, [(1, 1, 0.25), (3, 1, 0.156)]), CFG, "al6061")
    assert r["ok"] and r["problems"] == [] and r["notes"] == []
    assert (r["thickness"], r["cutter"], r["cutterWhy"], r["features"]) == ("1/8 in", "4 mm endmill", "",
                                                                             "1 drilled, 1 bored")


def test_a_hole_that_needs_the_eighth_is_a_fact_not_a_problem():
    r = check(geom(rectangle(4, 2), 0.1875, [(1, 1, 0.13)]), CFG, "al6061")
    assert r["ok"] and r["problems"] == []
    assert (r["cutter"], r["cutterWhy"]) == ("1/8 in endmill", "a 0.130 in hole needs it")
    assert "Make" not in every_text(r) and "change" not in every_text(r).lower()   # never asked to change it


def test_a_thickness_that_isnt_stock():
    r = check(geom(rectangle(4, 2), 0.21), CFG, "al6061")
    assert not r["ok"] and r["problems"] == [{"title": "It's 0.210 in thick, which isn't a stock size.",
                                              "fix": "6061 aluminum comes in 1/16, 1/8, 3/16, 1/4 in. Make it 3/16 in."}]
    r = check(geom(rectangle(4, 2), 0.1875), CFG, "al5052")             # 5052 only comes thin
    assert "Make it 1/8 in" in r["problems"][0]["fix"]


def test_a_hole_too_small_for_any_cutter():
    r = check(geom(rectangle(4, 2), 0.125, [(1, 1, 0.1)]), CFG, "al6061")
    assert not r["ok"]
    assert r["problems"] == [{"title": "A 0.100 in hole is too small for any cutter.",
                              "fix": "Make it at least 0.127 in, or drill it by hand after."}]


def test_sharp_inside_corners_are_a_note():
    ell = [(0, 0), (4, 0), (4, 1), (2, 1), (2, 2), (0, 2)]
    g = geom(ell, 0.125)
    assert g.sharp_inside_corners == 1                                   # the L's one inside corner, not its five outside
    r = check(g, CFG, "al6061")
    assert r["ok"] and r["notes"] == ["1 sharp inside corner: the cutter leaves a small radius there. Add dogbones if "
                                      "something fits into them."]


def test_without_a_material_the_shape_is_still_checked():
    r = check(geom(rectangle(4, 2), 0.125), CFG, None)
    assert r["ok"] and r["needMaterial"] and r["cutter"] == ""
    assert not check(geom(rectangle(4, 2), 0.21), CFG, None)["ok"]


def test_the_panel_checks_a_part_with_one_call_per_state_of_the_part_studio(tmp_path):
    parts = [dict(p, microversionId="m1") for p in PARTS]
    body = plate_body(rectangle(4, 2), 0.1875, [(1, 1, 0.13)])
    transport = FakeTransport([("GET", "/bodydetails", [resp(body=body)] * 2), ("GET", "/parts/", [resp(body=parts)] * 5)])
    p = Panel(tmp_path, transport=transport)
    status, r = p.call("GET", "/api/check", {**Q, "p": "JHD"})
    assert status == 200 and r["ok"] and r["cutter"] == "1/8 in endmill"
    status, r = p.call("GET", "/api/check", {**Q, "p": "JHD", "m": "al5052"})     # another material: no call
    assert not r["ok"] and "isn't a stock size" in r["problems"][0]["title"]
    assert [u for _, u, _, _ in transport.sent if "bodydetails" in u] == [
        f"https://cad.onshape.com/api/v10/parts/d/{Q['d']}/w/{Q['w']}/e/{Q['e']}/partid/JHD/bodydetails"]
    cached = list((p.h.cfg.paths.cache / "onshape").glob("geometry_*.json"))
    assert len(cached) == 1 and json.loads(cached[0].read_text())["thickness_in"] > 0.18
    (tmp_path / "again").mkdir()
    p2 = Panel(tmp_path / "again", transport=transport)                 # a restarted service: from disk, no call
    p2.onshape.cache_root = p.onshape.cache_root
    assert p2.call("GET", "/api/check", {**Q, "p": "JHD"})[1]["ok"]
    assert len([u for _, u, _, _ in transport.sent if "bodydetails" in u]) == 1


def test_out_of_calls_the_check_just_waits(tmp_path):
    p = Panel(tmp_path, per_day=1)
    p.call("GET", "/api/studio", Q)                                     # the day's one call
    status, r = p.call("GET", "/api/check", {**Q, "p": "JHD"})
    assert status == 200 and "used its Onshape calls" in r["unavailable"]
