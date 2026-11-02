"""The panel's second batch (2026-11-01): changed since it was CAM'd (the manufacturing version), the machine queue,
marks in the parts list, faces to click for a fit, cost and sheet use, and sending a whole assembly."""

import copy
import json

from autocam_service.config import parse_config
from autocam_service.panel.checks import check
from autocam_service.panel.geometry import differences, read_body, shape_key, solid_body
from autocam_service.panel.onshape_reads import bom_rows
from autocam_service.tracker.base import Card
from fakeonshape import FakeTransport, resp
from onshape_body import plate_body, rectangle
from test_panel import AL6061, D, E, PARTS, Q, W, Panel, card_in
from test_runner_e2e import RAW, REPO_ROOT

CFG = parse_config(copy.deepcopy(RAW), root=REPO_ROOT)
STUDIO_W = f"https://cad.onshape.com/documents/{D}/w/{W}/e/{E}"
MID = "7" * 24
SHEET = "e" * 24


def read(body):
    return read_body(solid_body(body, "JHD"), "P-2014")


# ---------------------------------------------------------------- shapes, picks, cost

def test_the_same_shape_anywhere_and_what_changed():
    a, _ = read(plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25)]))
    moved, _ = read(plate_body([(x + 5, y + 3) for x, y in rectangle(4, 2)], 0.125, [(6, 4, 0.25)]))
    assert shape_key(a) == shape_key(moved) and differences(a, moved) == []
    more, _ = read(plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25), (3, 1, 0.25)]))
    assert differences(a, more) == ["1 hole added"]
    shifted, _ = read(plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25), (2, 1, 0.25)]))
    assert differences(more, shifted) == ["the shape changed"]                  # a hole moved
    thick, _ = read(plate_body(rectangle(4, 2), 0.1875, [(1, 1, 0.25)]))
    assert differences(a, thick)[0] == "thickness 1/8 in to 3/16 in"


def test_faces_to_click_for_a_fit_and_the_cost():
    g, extra = read(plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25), (3, 1, 0.156)]))
    assert extra["outline_area_in2"] == 8.0 and sorted(extra["box_in"]) == [2.0, 4.0]
    r = check(g, CFG, "al6061", extra)
    assert r["pick"]["H00"] == r["pick"]["H01"] == {"kind": "hole", "size": 0.25}
    assert "H10" not in r["pick"]                                                # the drilled one can't be fitted
    assert all(r["pick"][f"S{i}"] == {"kind": "outline"} for i in range(4))
    share = 8.0 / 0.8 / (48 * 24)
    assert r["use"] == {"area_in2": 8.0, "sheet_share": share, "each": round(85.0 * share, 2), "box_in": extra["box_in"]}
    assert check(g, CFG, "al5052", extra)["use"]["each"] == round(80.0 * share, 2)


# ---------------------------------------------------------------- changed since it was CAM'd

def cammed(p, card_id, run="r034"):
    """A job that CAM'd the card's part from a pinned workspace state (what jobs.py writes)."""
    jobs = p.h.store.jobs_dir
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / f"{run}-al6061.json").write_text(json.dumps({"run_id": run, "parts": [{
        "card_id": card_id, "onshape": {"did": D, "vid": W, "eid": E, "part_id": "JHD", "url": STUDIO_W,
                                        "microversion": MID}}]}))


def changed_panel(tmp_path, then, now):
    parts = [dict(x, microversionId="m2") for x in PARTS]
    transport = FakeTransport([("GET", f"/m/{MID}/e/{E}/partid/JHD/bodydetails", [resp(body=then)] * 3),
                               ("GET", f"/w/{W}/e/{E}/partid/JHD/bodydetails", [resp(body=now)] * 3),
                               ("GET", "/parts/", [resp(body=parts)] * 5)])
    on = Card("a" * 24, "P-2014 gusset", f"{STUDIO_W}\nQty: 2\nPart ID: JHD", "nested", "https://t/on")
    p = Panel(tmp_path, transport=transport, cards=[on, Card(SHEET, "6061 - 25 min - r034 S1", "", "sheet_review",
                                                             "https://t/s1")])
    p.h.store.register_sheet(SHEET, "r034", True, [on.id], job="r034-al6061", material="al6061", thickness_in=0.125,
                             index=1, url="https://t/s1", extra={"label": "r034 S1"})
    cammed(p, on.id)
    return p, on


def test_a_part_changed_after_it_was_cammed_is_said_on_its_sheet_once(tmp_path):
    then = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25)])
    now = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25), (3, 1, 0.25)])
    p, on = changed_panel(tmp_path, then, now)
    status, r = p.call("GET", "/api/changed", {**Q, "p": "JHD", "card": on.id})
    assert status == 200 and r == {"changed": True, "what": ["1 hole added"], "run": "r034"}
    [said] = p.t.comments_on(SHEET)
    assert said.startswith("P-2014 gusset was changed in Onshape after this sheet was CAM'd (1 hole added).")
    p.call("GET", "/api/changed", {**Q, "p": "JHD", "card": on.id})
    assert len(p.t.comments_on(SHEET)) == 1                                       # said once
    assert len([u for _, u, _, _ in p.transport.sent if "bodydetails" in u]) == 2  # then and now, each once
    status, r = p.call("POST", "/api/renest", body={**Q, "p": "JHD", "card": on.id})
    assert status == 200 and p.t.get_card(on.id).list_key == "ready_for_cam"


def test_an_unchanged_part_says_so_and_a_card_not_cammed_has_nothing_to_compare(tmp_path):
    body = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25)])
    p, on = changed_panel(tmp_path, body, body)
    assert p.call("GET", "/api/changed", {**Q, "p": "JHD", "card": on.id})[1] == {"changed": False, "what": [],
                                                                                    "run": "r034"}
    assert p.t.comments_on(SHEET) == []
    (p.h.store.jobs_dir / "r034-al6061.json").unlink()
    assert p.call("GET", "/api/changed", {**Q, "p": "JHD", "card": on.id})[1] == {"changed": None}


# ---------------------------------------------------------------- the queue and marks

def test_the_machine_queue(tmp_path):
    s2 = "f" * 24
    waiting = Card("c" * 24, "P-2016 plate", f"{STUDIO_W}\nQty: 1", "ready_for_cam", "https://t/w")
    p = Panel(tmp_path, cards=[Card(SHEET, "6061 3/16in - 4 mm - 2 parts - 25 min - r034 S1", "", "sheet_review", "u1"),
                               Card(s2, "6061 1/8in - 4 mm - 1 part - 15 min - r034 S2", "", "ready_to_cut", "u2"),
                               Card("9" * 24, "Someone's own sheet - 99 min", "", "sheet_review", "u3"), waiting])
    for sid, i in ((SHEET, 1), (s2, 2)):
        p.h.store.register_sheet(sid, "r034", True, [], job="r034-al6061", material="al6061", thickness_in=0.125,
                                 index=i, url="u")
    queue = p.call("GET", "/api/studio", Q)[1]["queue"]
    assert queue == {"sheets": 2, "review": 1, "ready": 1, "minutes": 40, "waiting": 1}


def test_parts_already_checked_get_a_mark_in_the_list(tmp_path):
    parts = [dict(x, microversionId="m1") for x in PARTS]
    transport = FakeTransport([("GET", "/bodydetails", [resp(body=plate_body(rectangle(4, 2), 0.21))]),
                               ("GET", "/parts/", [resp(body=parts)] * 5)])
    p = Panel(tmp_path, transport=transport)
    assert all(r["mark"] is None for r in p.call("GET", "/api/studio", Q)[1]["parts"])
    p.call("GET", "/api/check", {**Q, "p": "JHD"})
    marks = {r["id"]: r["mark"] for r in p.call("GET", "/api/studio", Q)[1]["parts"]}
    assert marks == {"JHD": "bad", "JHG": None, "JHK": None}                      # 0.21 in isn't stock


# ---------------------------------------------------------------- a whole assembly

OTHER = "8" * 24
HEADERS = [{"id": "q", "propertyName": "quantity"}, {"id": "n", "propertyName": "name"},
           {"id": "m", "propertyName": "material"}]


def row(did, wvm, wvmid, eid, pid, name, qty, material, configuration="default", standard=False):
    return {"itemSource": {"documentId": did, "wvmType": wvm, "wvmId": wvmid, "elementId": eid, "partId": pid,
                           "configuration": configuration, "isStandardContent": standard},
            "headerIdToValue": {"q": float(qty), "n": name, "m": {"displayName": material} if material else None}}


BOM = {"headers": HEADERS, "rows": [
    row(D, "w", W, E, "JHD", "P-2014 gusset", 4, "Aluminum - 6061"),
    row(OTHER, "v", "6" * 24, "5" * 24, "JQX", "P-3001 side plate", 2, "Aluminum - 6061"),
    row(D, "w", W, E, "JHG", "P-2015 window", 1, None),
    row(D, "w", W, E, "JHK", "P-2016 spacer", 4, "3D Printed PLA"),
    row(D, "w", W, E, "JHC", "P-2017 bracket", 2, "Aluminum - 6061", configuration="List_abc=1"),
    row("4" * 24, "v", "3" * 24, "2" * 24, "JSC", "#10-32 screw", 40, "Steel", standard=True),
    row(D, "w", W, E, "JHB", "P-2018 queued", 1, "Aluminum - 6061")]}


def assembly_panel(tmp_path):
    transport = FakeTransport([("GET", "/bom", [resp(body=BOM)] * 3),
                               ("GET", "/elements", [resp(body=[{"id": E, "name": "Intake", "elementType": "ASSEMBLY"}])])])
    queued = Card("c" * 24, "P-2018 queued", f"{STUDIO_W}\nQty: 1\nPart ID: JHB", "ready_for_cam", "https://t/q")
    return Panel(tmp_path, transport=transport, cards=[queued])


def test_the_bom_is_read_flattened():
    rows = bom_rows(BOM)
    assert rows[0] == {"did": D, "wvm": "w", "wvmid": W, "eid": E, "pid": "JHD", "name": "P-2014 gusset", "qty": 4,
                       "material": "Aluminum - 6061", "configured": False, "standard": False}
    assert rows[4]["configured"] and rows[5]["standard"]


def test_an_assembly_lists_its_parts_with_counts_and_cards(tmp_path):
    p = assembly_panel(tmp_path)
    status, a = p.call("GET", "/api/assembly", Q)
    assert status == 200 and a["name"] == "Intake"
    assert [i["here"] for i in a["items"]] == [True] * 5 + [False]          # this document's parts first
    items = {i["name"]: (i["qty"], i["sendable"], i["why"], i["chip"]["status"]) for i in a["items"]}
    assert items == {"P-2014 gusset": (4, True, "", "none"), "P-3001 side plate": (2, True, "", "none"),
                     "P-2015 window": (1, False, "No material", "none"), "P-2016 spacer": (4, False, "Not cut here", "none"),
                     "P-2017 bracket": (2, False, "Configured", "none"), "P-2018 queued": (1, False, "", "queued")}


def test_sending_an_assembly_makes_a_card_per_part_from_its_own_part_studio(tmp_path):
    p = assembly_panel(tmp_path)
    keys = {i["name"]: i["key"] for i in p.call("GET", "/api/assembly", Q)[1]["items"]}
    status, r = p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "tabs": True, "items": [
        {"key": keys["P-2014 gusset"], "qty": 4}, {"key": keys["P-3001 side plate"], "qty": 3},
        {"key": keys["P-2015 window"], "qty": 1}, {"key": keys["P-2018 queued"], "qty": 1}]})
    assert status == 200 and r["list"] == "Ready for CAM"
    assert [(x["name"], x["sent"], x.get("why")) for x in r["results"]] == [
        ("P-2014 gusset", True, None), ("P-3001 side plate", True, None),
        ("P-2015 window", False, "has no material AutoCAM cuts: open its Part Studio's panel"),
        ("P-2018 queued", False, "already has a card")]
    made = {c.name: c for c in card_in(p, "ready_for_cam")}
    other = made["P-3001 side plate"].desc.split("\n")
    assert other[:3] == [f"https://cad.onshape.com/documents/{OTHER}/v/{'6' * 24}/e/{'5' * 24}", "Qty: 3", "Part ID: JQX"]
    assert made["P-2014 gusset"].desc.startswith(f"{STUDIO_W}\nQty: 4\nPart ID: JHD")
    assert len([u for _, u, _, _ in p.transport.sent if "/bom" in u]) == 1         # one call for the whole assembly
