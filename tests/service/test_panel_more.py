"""The panel's second batch (2026-11-01): changed since it was CAM'd (the manufacturing version), the machine queue,
marks in the parts list, faces to click for a fit, cost and sheet use, and sending a whole assembly."""

import copy
import json

from autocam_service.cards import parse_card
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
    # Onshape's SELECTION names a clicked edge by its id (2026-11-01): a hole's rim is the hole, an outline edge the outline
    assert r["pick"]["h0ta"] == r["pick"]["h0bb"] == {"kind": "hole", "size": 0.25}
    assert "h1ta" not in r["pick"] and r["pick"]["et0"] == r["pick"]["ev2"] == {"kind": "outline"}
    share = 8.0 / 0.7 / (48 * 24)                                               # WCP's $174.99 sheet, 70% used
    assert r["use"] == {"area_in2": 8.0, "sheet_share": share, "each": round(174.99 * share, 2), "box_in": extra["box_in"]}
    unpriced = check(g, CFG, "al5052", extra)["use"]                             # WCP sells no 1/8 in 5052
    assert unpriced["each"] is None and unpriced["sheet_share"] == share


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
                       "material": "Aluminum - 6061", "configuration": "", "standard": False}
    assert rows[4]["configuration"] and rows[5]["standard"]


def test_an_assembly_lists_its_parts_with_counts_and_cards(tmp_path):
    p = assembly_panel(tmp_path)
    status, a = p.call("GET", "/api/assembly", Q)
    assert status == 200 and a["name"] == "Intake"
    assert [i["here"] for i in a["items"]] == [True] * 5 + [False]          # this document's parts first
    items = {i["name"]: (i["qty"], i["sendable"], i["why"], i["chip"]["status"]) for i in a["items"]}
    assert items == {"P-2014 gusset": (4, True, "", "none"), "P-3001 side plate": (2, True, "", "none"),
                     "P-2015 window": (1, False, "No material", "none"), "P-2016 spacer": (4, False, "Not cut here", "none"),
                     "P-2017 bracket (List_abc: 1)": (2, True, "", "none"),            # configured: sendable now
                     "P-2018 queued": (1, False, "", "queued")}


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


# ---------------------------------------------------------------- changed before it's cut (the service checks)

from autocam_service.panel.precut import PrecutWatch                                  # noqa: E402


def precut_panel(tmp_path, now_state, then, now):
    on = Card("a" * 24, "P-2014 gusset", f"{STUDIO_W}\nQty: 2\nPart ID: JHD", "nested", "https://t/on")
    transport = FakeTransport([("GET", "/currentmicroversion", [resp(body={"microversion": now_state})] * 3),
                               ("GET", f"/m/{MID}/e/{E}/partid/JHD/bodydetails", [resp(body=then)]),
                               ("GET", f"/w/{W}/e/{E}/partid/JHD/bodydetails", [resp(body=now)])])
    p = Panel(tmp_path, transport=transport, cards=[on, Card(SHEET, "6061 - 25 min - r034 S1", "", "ready_to_cut", "u")])
    p.h.store.register_sheet(SHEET, "r034", True, [on.id], job="r034-al6061", material="al6061", thickness_in=0.125,
                             index=1, url="u", extra={"label": "r034 S1"})
    p.h.store.save_job("r034-al6061", json.dumps({"run_id": "r034", "parts": [{
        "card_id": on.id, "name": "P-2014 gusset", "onshape": {"did": D, "vid": W, "eid": E, "part_id": "JHD",
                                                               "url": STUDIO_W, "microversion": MID}}]}))
    return p, PrecutWatch(p.onshape, p.board, p.h.store, p.t)


def test_a_sheet_in_ready_to_cut_is_told_when_a_part_changed(tmp_path):
    then = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25)])
    now = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.5)])
    p, watch = precut_panel(tmp_path, "8" * 24, then, now)
    assert watch.tick() == 1
    [said] = p.t.comments_on(SHEET)
    assert "P-2014 gusset was changed in Onshape after this sheet was CAM'd (1 hole added, 1 hole removed)" in said
    calls = len(p.transport.sent)
    assert watch.tick() == 0 and len(p.transport.sent) == calls                   # checked once per sheet program


def test_an_unchanged_document_costs_one_call(tmp_path):
    body = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25)])
    p, watch = precut_panel(tmp_path, MID, body, body)
    assert watch.tick() == 0 and p.t.comments_on(SHEET) == []
    assert [u.split("/api/v10")[1] for _, u, _, _ in p.transport.sent] == [f"/documents/d/{D}/w/{W}/currentmicroversion"]


def test_out_of_calls_the_sheet_is_checked_later(tmp_path):
    body = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25)])
    p, watch = precut_panel(tmp_path, "8" * 24, body, body)
    p.onshape.calls_per_day = 0
    assert watch.tick() == 0 and p.transport.sent == []
    p.onshape.calls_per_day = 60
    watch.tick()
    assert len(p.transport.sent) == 3                                               # state, then, now


# ---------------------------------------------------------------- "I CAM'd it myself" from the panel

from test_handprograms import program                                                # noqa: E402


def send_program(p, data, **kw):
    body = {**Q, "p": "JHD", "name": "Maya", "thickness": 0.1875, "server": "https://cad.onshape.com",
            "program": {"filename": "P-2014.tap", "text": data.decode("ascii")}, **kw}
    return p.call("POST", "/api/send-program", body=body)


def test_an_own_program_goes_on_a_card_linked_to_the_part(tmp_path):
    p = Panel(tmp_path)
    status, r = send_program(p, program(), cutter="")
    assert status == 200 and r["own"] and r["list"] == "Ready for CAM"
    [card] = card_in(p, "ready_for_cam")
    assert card.desc.split("\n")[:5] == [STUDIO_W, "Part ID: JHD", "Material: 6061", "Thickness: 3/16",
                                         "Own program: the attached program, CAM'd by hand (AutoCAM places it and "
                                         "checks the placed copy)"]
    assert [name for cid, name, _ in p.t.files.values() if cid == card.id] == ["P-2014.tap"]
    seen = p.t.get_card(card.id)
    assert p.h.runner._own_program_card(seen)                                     # placed, not nested
    assert seen.id not in [c.id for c in p.h.runner.ready_cards()]
    assert p.call("GET", "/api/part", {**Q, "p": "JHD"})[1]["card"]["status"] == "queued"


def test_an_own_program_the_guard_stops_never_reaches_trello(tmp_path):
    p = Panel(tmp_path)
    status, r = send_program(p, program(z_cut="-0.05"))
    assert status == 400 and r["error"].startswith("AutoCAM's safety check stopped your program: goes below Z0")
    status, r = send_program(p, program(units="G21"))
    assert status == 400
    status, r = send_program(p, program(), thickness=0.21)
    assert status == 400 and "isn't a stock thickness" in r["error"]
    status, r = send_program(p, program(), program={"filename": "notes.txt", "text": "G0"})
    assert status == 400 and ".tap" in r["error"]
    assert card_in(p, "ready_for_cam") == [] and card_in(p, "inbox") == [] and p.t.files == {}


# ---------------------------------------------------------------- making a card over keeps what it said

def test_a_drafts_card_made_over_starts_from_it_and_keeps_what_the_panel_doesnt_write(tmp_path):
    typed = Card("b" * 24, "P-2014 gusset", f"{STUDIO_W}\nQty: 3\nFit: 1.125 hole\nNote: deburr\nmake it from the "
                                            "scrap pile", "inbox", "https://t/d", ("Face", "Tool 1/8"))
    p = Panel(tmp_path, cards=[typed])
    p.t.add_checklist(typed.id, "Nest", ["Nest this part"], checked=False)
    form = p.call("GET", "/api/part", {**Q, "p": "JHD"})[1]["card"]["form"]
    assert form == {"qty": 3, "face": True, "rush": False, "smoked": False, "tool_eighth": True, "nest": False,
                    "tabs": True, "fit": {"kind": "hole", "size": 1.125}, "scribe": "", "notes": "deburr",
                    "kept": ["make it from the scrap pile"]}
    status, _ = p.call("POST", "/api/send", body={**Q, "p": "JHD", "card": typed.id, "qty": 3, "name": "Maya",
                                                   "face": True, "tool_eighth": True, "nest": True, "notes": "deburr",
                                                   "fit": {"kind": "hole", "size": "1.125"}})
    assert status == 200
    card = p.t.get_card(typed.id)
    lines = card.desc.split("\n")
    assert lines[-1] == "make it from the scrap pile" and lines.count("Fit: 1.125 hole") == 1
    assert lines.count("Note: deburr") == 1 and sum(1 for ln in lines if ln.startswith("Qty")) == 1
    assert set(card.labels) == {"Face", "Tool 1/8"} and card.list_key == "ready_for_cam"


def test_the_eighth_switch_and_an_assemblys_face_and_rush(tmp_path):
    (tmp_path / "asm").mkdir()
    p = Panel(tmp_path)
    assert p.send(tool_eighth=True)[0] == 200
    [card] = card_in(p, "ready_for_cam")
    assert "Tool 1/8" in card.labels and parse_card(p.t.get_card(card.id)).force_small_tool
    a = assembly_panel(tmp_path / "asm")
    keys = {i["name"]: i["key"] for i in a.call("GET", "/api/assembly", Q)[1]["items"]}
    a.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "face": True, "rush": True,
                                            "items": [{"key": keys["P-2014 gusset"], "qty": 4}]})
    [made] = [c for c in card_in(a, "ready_for_cam") if c.name == "P-2014 gusset"]
    assert set(made.labels) == {"Face", "Rush"}


def test_an_own_program_placed_by_hand_from_the_panel(tmp_path):
    p = Panel(tmp_path)
    status, _ = send_program(p, program(), place_myself=True)
    assert status == 200
    [card] = card_in(p, "ready_for_cam")
    assert "Place it myself: the operator zeros it on a piece they pick (AutoCAM checks it as it is)" in card.desc
    for att, (_, _, data) in p.t.files.items():                                    # what Trello would hand back
        p.t.downloads[att] = data
    p.h.runner.own_programs()
    [sheet] = [c for c in p.t.cards.values() if c.list_key == "sheet_review"]
    assert "placed by hand - own program P-2014.tap - P-2014 gusset" in sheet.name


# ---------------------------------------------------------------- the page's own refresh (2026-11-01, the user asked
# for everything to refresh faster): every few seconds, statuses only, and never an Onshape call

def test_the_pages_own_refresh_never_calls_onshape(tmp_path):
    on = Card("a" * 24, "P-2014 gusset", f"{STUDIO_W}\nQty: 2\nPart ID: JHD", "ready_for_cam", "https://t/a")
    p = Panel(tmp_path, cards=[on])
    cached = {**Q, "cached": "1"}
    assert p.call("GET", "/api/studio", cached)[1] == {"stale": True}          # nothing read yet: the page waits
    assert p.call("GET", "/api/part", {**cached, "p": "JHD"})[1] == {"stale": True}
    assert p.onshape_calls() == []
    first = p.call("GET", "/api/studio", Q)[1]
    assert first["autoRefreshS"] == 15 and len(p.onshape_calls()) == 1
    p.clock.t += 3600                                  # long past when a normal read would ask Onshape again
    p.t._move(on.id, "nested")
    rows = {r["name"]: r["chip"]["status"] for r in p.call("GET", "/api/studio", cached)[1]["parts"]}
    assert rows["P-2014 gusset"] != "queued" and set(rows) == {r["name"] for r in first["parts"]}
    assert p.call("GET", "/api/part", {**cached, "p": "JHD"})[1]["card"] is not None
    assert len(p.onshape_calls()) == 1


def test_an_assemblys_own_refresh_never_calls_onshape(tmp_path):
    p = assembly_panel(tmp_path)
    assert p.call("GET", "/api/assembly", {**Q, "cached": "1"})[1] == {"stale": True}
    assert p.transport.sent == []
    p.call("GET", "/api/assembly", Q)
    calls = len(p.transport.sent)
    p.clock.t += 3600
    a = p.call("GET", "/api/assembly", {**Q, "cached": "1"})[1]
    assert a["name"] == "Intake" and len(a["items"]) == 6 and a["autoRefreshS"] == 15
    assert len(p.transport.sent) == calls


def test_a_face_or_edge_clicked_in_the_cad_finds_its_part_without_calling_onshape(tmp_path):
    body = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25)])
    p, _ = changed_panel(tmp_path, body, body)
    p.call("GET", "/api/studio", Q)
    assert p.call("GET", "/api/which-part", {**Q, "ids": "H00"})[1] == {"part": ""}   # not opened yet: not known
    p.call("GET", "/api/check", {**Q, "p": "JHD"})                                     # opening it reads its shape
    calls = len(p.transport.sent)
    assert p.call("GET", "/api/which-part", {**Q, "ids": "H00"})[1] == {"part": "JHD"}          # a face
    assert p.call("GET", "/api/which-part", {**Q, "ids": "zz9,h0ta"})[1] == {"part": "JHD"}     # an edge
    assert p.call("GET", "/api/which-part", {**Q, "ids": "t0"})[1] == {"part": "JHD"}          # a corner
    assert p.call("GET", "/api/which-part", {**Q, "ids": "zz9"})[1] == {"part": ""}
    assert p.call("GET", "/api/which-part", {**Q, "ids": "<b>"})[1] == {"part": ""}
    assert len(p.transport.sent) == calls


def test_the_page_links_its_script_by_content_so_a_deploy_reaches_browsers_at_once(tmp_path):
    p = Panel(tmp_path)
    page = p.app.handle("GET", "/panel", {"key": p.app._key.decode()}, {})
    html = page.body.decode()
    assert page.headers["Cache-Control"] == "no-store"
    import re
    js = re.search(r'src="(/static/panel\.js\?v=[0-9a-f]{12})"', html).group(1)
    assert re.search(r'href="/static/panel\.css\?v=[0-9a-f]{12}"', html)
    served = p.app.handle("GET", js.split("?")[0], {"v": js.split("v=")[1]}, {})
    assert served.status == 200 and b"whichPart" in served.body
