"""Bent (sheet metal) parts (2026-11-01): a run cuts the flat pattern and the sheet card says how to bend it; the
panel checks the flat and shows the bends. Shapes as Onshape gives them (read off a real one, 2026-11-01): the parts
list with includeFlatParts has the flat as a part of its own (isFlattenedBody, unflattenedPartId) with the bent
part's name, and bend lines as wire bodies."""

import json

import pytest

from autocam_service.cards import parse_card, scribe_value
from autocam_service.onshape.export import flat_of, parse_bend_table, pick_part
from autocam_service.tracker.base import Card
from fakeonshape import FakeTransport, resp
from fakeworker import run_fake_worker
from onshape_body import plate_body, rectangle
from test_panel import Q, Panel
from test_runner_e2e import STUDIO, SYS, Harness

PARTS = [{"name": "Part 22", "partId": "JHD", "bodyType": "solid", "material": {"displayName": "Aluminum - 5052"}},
         {"name": "Bend line 1", "partId": "JHW", "bodyType": "wire"},
         {"name": "Part 22", "partId": "JHF", "bodyType": "solid", "isFlattenedBody": True, "unflattenedPartId": "JHD",
          "material": {"displayName": "Aluminum - 5052"}}]
BENDS = {"table": {"tableRows": [
    {"columnIdToCell": {"name": {"parameter": {"value": "Bend B"}}, "angle": {"parameter": {"value": 45.0, "units": "degree"}},
                        "direction": {"parameter": {"value": "Down"}}, "sequence": {"parameter": {"value": 2.0}},
                        "radius": {"parameter": {"value": 0.0023, "units": "meter"}}}},
    {"columnIdToCell": {"name": {"parameter": {"value": "Bend A"}}, "angle": {"parameter": {"value": 90.0, "units": "degree"}},
                        "direction": {"parameter": {"value": "Up"}}, "sequence": {"parameter": {"value": 1.0}},
                        "radius": {"parameter": {"value": 0.09, "units": "inch"}}}}]}}


def test_the_bend_table_and_the_flat():
    assert parse_bend_table(BENDS) == [
        {"name": "Bend A", "angle_deg": 90.0, "direction": "up", "radius_in": 0.09, "order": 1},
        {"name": "Bend B", "angle_deg": 45.0, "direction": "down", "radius_in": 0.0023 / 0.0254, "order": 2}]
    assert pick_part(PARTS, "Part 22")["partId"] == "JHD"                        # not "two parts named Part 22"
    assert flat_of(PARTS, "JHD")["partId"] == "JHF" and flat_of(PARTS, "JHF") is None


def test_a_run_cuts_the_flat_pattern_and_the_sheet_card_says_how_to_bend_it(tmp_path):
    transport = FakeTransport([
        ("GET", "/sheetmetal/bendtable", [resp(body=BENDS)]),
        ("GET", "/parts/", [resp(body=PARTS)]),
        ("POST", "/translations", [resp(body={"id": "T1"})]),
        ("GET", "/translations/T1", [resp(body={"requestState": "DONE", "resultExternalDataIds": ["F1"]})]),
        ("GET", "/externaldata/F1", [resp(200, b"ISO-10303-21; flat")]),
    ])
    h = Harness(tmp_path, transport=transport, card_list=[
        Card(SYS, "System", "", "control", "https://trello.example/c/sys"),
        Card("ca", "Part 22", f"Qty: 2\n{STUDIO}", "ready_for_cam", "https://trello.example/c/ca")])
    h.runner.tick()
    [post] = [b for m, u, _, b in transport.sent if m == "POST"]
    assert json.loads(post)["partIds"] == "JHF"                                   # the flat is what's exported
    [job_id] = h.queue.pending()
    job = json.loads(h.store.job_text(job_id))
    assert job["parts"][0]["onshape"]["part_id"] == "JHD"                         # the part on record is the bent one
    assert [b["name"] for b in job["parts"][0]["bends"]] == ["Bend A", "Bend B"]
    run_fake_worker(h.queue)
    h.runner.tick()
    [sheet] = [c for c in h.tracker.cards.values() if c.list_key == "sheet_review"]
    assert ("BEND AFTER CUTTING (these are cut flat)\n- Part 22: Bend A 90° up, then Bend B 45° down "
            "(R 0.09, 0.091 in)") in sheet.desc
    assert "1. Part 22 (1 of 2) - bent\n2. Part 22 (2 of 2) - bent" in sheet.desc


def test_the_panel_checks_the_flat_and_shows_the_bends(tmp_path):
    parts = [dict(p, microversionId="m1") for p in PARTS]
    flat = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25)], pid="JHF")
    transport = FakeTransport([("GET", "/partid/JHF/bodydetails", [resp(body=flat)]),
                               ("GET", "/sheetmetal/bendtable", [resp(body=BENDS)]),
                               ("GET", "/parts/", [resp(body=parts)] * 3)])
    p = Panel(tmp_path, transport=transport)
    rows = p.call("GET", "/api/studio", Q)[1]["parts"]
    assert [(r["name"], r["bent"]) for r in rows] == [("Part 22", True)]          # one row: no flat, no bend line
    r = p.call("GET", "/api/check", {**Q, "p": "JHD"})[1]
    assert r["ok"] and r["thickness"] == "1/8 in"
    assert r["bent"] == "Cut flat, then 2 bends: Bend A 90° up, then Bend B 45° down (R 0.09, 0.091 in)."
    assert p.call("GET", "/api/part", {**Q, "p": "JHD"})[1]["part"]["bent"] is True


@pytest.mark.parametrize("said, way", [("endmill", "endmill"), ("End mill", "endmill"), ("yes", "endmill"),
                                       ("chamfer mill", "chamfer"), ("45 deg chamfer", "chamfer"), ("no", ""),
                                       ("off", ""), ("sharpie", None)])
def test_what_a_scribe_line_says(said, way):
    assert scribe_value(said) == way


def test_a_scribe_line_on_a_card():
    card = Card("ca", "Part 22", f"Qty: 2\n{STUDIO}\nScribe: chamfer mill", "ready_for_cam", "https://t/c")
    assert parse_card(card).scribe == "chamfer"
    assert parse_card(Card("cb", "Part 22", f"Qty: 2\n{STUDIO}", "ready_for_cam", "https://t/c")).scribe == ""
    bad = parse_card(Card("cc", "Part 22", f"Qty: 2\n{STUDIO}\nScribe bend lines: marker", "ready_for_cam", "x"))
    assert "Scribe: endmill" in bad.problems[0]


def test_a_scribed_bent_part_from_the_panel_to_the_job(tmp_path):
    parts = [dict(p, microversionId="m1") for p in PARTS]
    transport = FakeTransport([("GET", "/sheetmetal/bendtable", [resp(body=BENDS)]),
                               ("GET", "/parts/", [resp(body=parts)] * 4)])
    p = Panel(tmp_path, transport=transport)
    assert p.call("GET", "/api/studio", Q)[1]["scribe"] is True
    status, r = p.call("POST", "/api/send", body={**Q, "p": "JHD", "qty": 2, "name": "Maya", "scribe": "chamfer",
                                                  "drafts": True})
    assert status == 200, r
    [card] = [c for c in p.t.cards.values() if c.list_key == "inbox" and c.name == "Part 22"]
    assert "Scribe: chamfer mill" in card.desc.split("\n") and parse_card(card).scribe == "chamfer"
    assert p.call("GET", "/api/part", {**Q, "p": "JHD"})[1]["card"]["form"]["scribe"] == "chamfer"   # made over
    assert p.call("POST", "/api/send", body={**Q, "p": "JHD", "qty": 2, "name": "M", "scribe": "laser"})[0] == 400


def test_a_scribe_line_reaches_the_job(tmp_path):
    transport = FakeTransport([
        ("GET", "/sheetmetal/bendtable", [resp(body=BENDS)]),
        ("GET", "/parts/", [resp(body=PARTS)]),
        ("POST", "/translations", [resp(body={"id": "T1"})]),
        ("GET", "/translations/T1", [resp(body={"requestState": "DONE", "resultExternalDataIds": ["F1"]})]),
        ("GET", "/externaldata/F1", [resp(200, b"ISO-10303-21; flat")]),
    ])
    h = Harness(tmp_path, transport=transport, card_list=[
        Card(SYS, "System", "", "control", "https://trello.example/c/sys"),
        Card("ca", "Part 22", f"Qty: 2\n{STUDIO}\nScribe: endmill", "ready_for_cam", "https://trello.example/c/ca")])
    h.runner.tick()
    [job_id] = h.queue.pending()
    job = json.loads(h.store.job_text(job_id))
    assert job["parts"][0]["scribe"] == "endmill"
    assert job["scribe"]["depth_in"] == 0.012 and job["scribe"]["chamfer"]["number"] == 16
    assert json.loads(job["scribe"]["chamfer_tool_json"])["guid"] == "915d08db-80ad-44e2-8fd5-6b9ed2129f59"
