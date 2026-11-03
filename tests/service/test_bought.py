"""The shop's bought-parts list (bought.py, panel/api.py; 2026-11-02): an assembly's bought parts (a vendor's library,
McMaster numbers, belts; no screws or nuts) sent from the Onshape panel, then In the bin or Order on the phone page.
AutoCAM never writes to the order sheet: Order opens it, and the part is "on the order sheet" only once someone says
so. Offline: scripted Onshape, fake Trello."""

import copy
from dataclasses import replace
from datetime import timedelta

import pytest

from autocam_service.bought import PartList, is_hardware, status_chip
from autocam_service.config import ConfigError, Parts, parse_config
from autocam_service.panel.api import PanelApp, shop_key
from autocam_service.shoplist import ListError
from fakeonshape import FakeTransport, resp
from test_panel import KEY, Q, Panel, card_in
from test_panel_more import HEADERS, row
from test_runner_e2e import D, E, RAW, REPO_ROOT, W
from test_shafts import DAY, Clock

VENDOR = "8293a9c30ec90f3f3078fde2"
SHEET = "https://docs.google.com/spreadsheets/d/1AbC_dEf-123/edit#gid=0"
PHONE, OTHER_PHONE = "phone-aaaa-1111", "phone-bbbb-2222"
WHEEL = '3" Solid Urethane Wheel (45A, 1" Wide, 1/2" Hex Bore)'


def entry(source="a/b|x", **kw):
    return {"source": source, "assembly": {"key": "a/b", "name": "Shooter", "url": "https://cad.onshape.com/documents/x"},
            "row": {}, "name": "Kraken X44", "vendor": "WCP", "qty": 2, "sender": "Siddhi", **kw}


# ---------------------------------------------------------------- the list

def test_a_part_goes_in_the_bin_or_on_the_order_sheet_and_back(tmp_path):
    clock = Clock()
    lst = PartList(tmp_path / "bought_parts.json", now=clock)
    x = lst.add([entry()])[0]
    assert x["status"] == "needed" and status_chip(x) == ("", "")      # nothing while it's needed
    with pytest.raises(ListError, match="already on the parts list"):
        lst.add([entry()])
    lst.ordered(x["id"], PHONE)
    assert lst.all()[0]["status"] == "ordered" and status_chip(lst.all()[0]) == ("Ordered", "queued")
    with pytest.raises(ListError, match="on the order sheet already"):
        lst.remove(x["id"])
    lst.bin(x["id"], OTHER_PHONE)                                     # it came in; someone put it in the bin
    assert status_chip(lst.all()[0]) == ("In bin", "good")
    with pytest.raises(ListError, match="already put this in the bin"):
        lst.ordered(x["id"], PHONE)
    with pytest.raises(ListError, match="Only the phone"):
        lst.undo(x["id"], PHONE)
    lst.undo(x["id"], OTHER_PHONE)
    assert lst.all()[0]["status"] == "ordered"                        # back where it was
    view = lst.shop_view(PHONE)
    assert [(p["name"], p["status"], p["vendor"], p["assembly"]) for p in view["parts"]] == [
        ("Kraken X44", "ordered", "WCP", "Shooter")]
    assert view["bin"] == []


def test_the_bin_shows_today_and_drops_off_after_two_weeks(tmp_path):
    clock = Clock()
    lst = PartList(tmp_path / "bought_parts.json", keep_days=14, now=clock)
    x = lst.add([entry()])[0]
    lst.bin(x["id"], PHONE)
    view = lst.shop_view(PHONE)
    assert view["parts"] == [] and [(b["name"], b["undo"], b["when"]) for b in view["bin"]] == [("Kraken X44", True, "6:30 PM")]
    assert lst.shop_view(OTHER_PHONE)["bin"][0]["undo"] is False
    clock.t = DAY + timedelta(days=1)
    assert lst.shop_view(PHONE)["bin"] == []                          # in the bin today only
    with pytest.raises(ListError, match="Only the phone"):
        lst.undo(x["id"], PHONE)                                      # the same day only
    assert len(lst.all()) == 1
    clock.t = DAY + timedelta(days=15)
    assert lst.all() == []


def test_taking_a_needed_part_off_the_list(tmp_path):
    lst = PartList(tmp_path / "bought_parts.json", now=Clock())
    x = lst.add([entry()])[0]
    lst.remove(x["id"])
    assert lst.all() == []
    with pytest.raises(ListError, match="isn't on the list any more"):
        lst.bin(x["id"], PHONE)
    with pytest.raises(ListError, match="Bad request"):
        lst.bin("../../etc", PHONE)


def test_screws_and_nuts_are_hardware():
    for name in ("91259A496_NO THREADS_Alloy Steel Shoulder Screw", "10-32 Nylock Nut", "1/4-20 Locknut", "Setscrew",
                 "Hex Bolt", "Nut Strip (10-32, 0.5in)", "NUTS"):
        assert is_hardware(name), name
    for name in (WHEEL, "Kraken X44 Brushless Motor", "130T 3M 6mm Wide Belt", "Sushi Roller (60A, 3/8\" Hex Bore)",
                 "Coconut roller"):
        assert not is_hardware(name), name


def test_the_order_sheet_must_be_a_google_sheet():
    raw = copy.deepcopy(RAW)
    raw["parts"] = {"enabled": True, "order_sheet_url": SHEET}
    assert parse_config(raw, root=REPO_ROOT).parts == Parts(enabled=True, keep_days=14,
                                                            order_sheet_url=SHEET.split("#")[0])   # "#gid=0" off
    raw["parts"]["order_sheet_url"] = "https://evil.example/spreadsheets/x"
    with pytest.raises(ConfigError, match="order_sheet_url"):
        parse_config(raw, root=REPO_ROOT)
    raw["parts"]["order_sheet_url"] = 'https://docs.google.com/spreadsheets/d/x"><script>'
    with pytest.raises(ConfigError, match="order_sheet_url"):
        parse_config(raw, root=REPO_ROOT)


# ---------------------------------------------------------------- the panel and the phone page

def bought_bom(extra=()):
    return {"headers": HEADERS, "rows": [
        row(D, "w", W, E, "JHD", "P-2014 gusset", 4, "Aluminum - 6061"),
        row(VENDOR, "v", "6" * 24, "7" * 24, "JQA", WHEEL, 3, "Polycarbonate"),
        row(VENDOR, "v", "6" * 24, "7" * 24, "JQN", "Nut Strip (10-32)", 2, "Aluminum - 6061"),
        row(D, "w", W, E, "JB1", "130T 3M 6mm Wide Belt", 2, "Viton Rubber"),
        row(D, "w", W, E, "JB2", "91259A496_NO THREADS_Alloy Steel Shoulder Screw", 1, None),
        row(D, "w", W, E, "JB3", "2766T21 Rubber bumper", 4, None),
        row("4" * 24, "v", "3" * 24, "2" * 24, "JSC", "#10-32 nut", 40, "Steel", standard=True),
        *extra]}


def bought_panel(tmp_path, boms=None, sheet=SHEET):
    boms = boms or [bought_bom()]
    mkcad = {"owner": {"id": "594ae8f3feede50f8a5cb929", "type": 0, "name": "Milkenknights Test Account"}, "public": True}
    transport = FakeTransport([("GET", "/bom", [resp(body=b) for b in boms] + [resp(body=boms[-1])] * 5),
                               ("GET", "/elements", [resp(body=[{"id": E, "name": "Shooter", "elementType": "ASSEMBLY"}])] * 3),
                               ("GET", f"/documents/{VENDOR}", [resp(body=mkcad)])])
    p = Panel(tmp_path, transport=transport)
    p.parts = PartList(p.h.cfg.paths.state / "bought_parts.json", now=Clock())
    cfg = replace(p.h.cfg, parts=Parts(enabled=True, order_sheet_url=sheet))
    p.app = PanelApp(cfg, KEY, p.onshape, p.board, p.names, clock=p.clock, parts=p.parts)
    return p


def test_an_assembly_lists_its_bought_parts_apart_with_no_screws_or_nuts(tmp_path):
    p = bought_panel(tmp_path)
    status, a = p.call("GET", "/api/assembly", Q)
    assert status == 200
    assert [i["name"] for i in a["items"]] == ["P-2014 gusset"]          # bought parts aren't in the plates' list
    assert [(b["name"], b["qty"], b["vendor"], b["sendable"]) for b in a["bought"]] == [
        (WHEEL, 3, "", True), ("130T 3M 6mm Wide Belt", 2, "", True), ("2766T21 Rubber bumper", 4, "McMaster", True)]
    assert a["boughtGone"] == []


def test_the_vendor_column_is_read_when_the_bom_has_one(tmp_path):
    bom = bought_bom()
    bom["headers"] = HEADERS + [{"id": "v", "propertyName": "vendor"}]
    bom["rows"][1]["headerIdToValue"]["v"] = "WCP"
    p = bought_panel(tmp_path, [bom])
    a = p.call("GET", "/api/assembly", Q)[1]
    assert {b["name"]: b["vendor"] for b in a["bought"]}[WHEEL] == "WCP"


def test_bought_parts_go_on_the_list_and_get_checked_off_on_the_phone(tmp_path):
    p = bought_panel(tmp_path)
    a = p.call("GET", "/api/assembly", Q)[1]
    keys = {b["name"]: b["key"] for b in a["bought"]}
    plates = {i["name"]: i["key"] for i in a["items"]}
    status, r = p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "server": "https://5940.onshape.com",
                                                        "items": [{"key": plates["P-2014 gusset"], "qty": 4}],
                                                        "bought": [{"key": keys[WHEEL], "qty": 4},
                                                                   {"key": keys["130T 3M 6mm Wide Belt"], "qty": 2}]})
    assert status == 200
    assert [(x["name"], x["sent"]) for x in r["results"]] == [("P-2014 gusset", True), (WHEEL, True),
                                                               ("130T 3M 6mm Wide Belt", True)]
    assert [c.name for c in card_in(p, "ready_for_cam")] == ["P-2014 gusset"]   # only the plate is a Trello card
    listed = p.parts.all()
    assert [(x["name"], x["qty"], x["sender"], x["status"]) for x in listed] == [
        (WHEEL, 4, "Siddhi", "needed"), ("130T 3M 6mm Wide Belt", 2, "Siddhi", "needed")]
    assert listed[0]["assembly"] == {"key": f"{D}/{E}", "name": "Shooter",
                                     "url": f"https://5940.onshape.com/documents/{D}/w/{W}/e/{E}"}

    shop = p.call("GET", "/api/shop", {"device": PHONE}, key=shop_key(KEY))[1]
    assert shop["partsOn"] and not shop["shaftsOn"] and shop["orderSheet"] == SHEET
    assert [(x["name"], x["qty"], x["status"], x["assembly"]) for x in shop["parts"]] == [
        (WHEEL, 4, "needed", "Shooter"), ("130T 3M 6mm Wide Belt", 2, "needed", "Shooter")]
    wheel, belt = shop["parts"]
    assert p.call("POST", "/api/shop/part-ordered", body={"id": wheel["id"], "device": PHONE}, key=shop_key(KEY))[0] == 200
    assert p.call("POST", "/api/shop/part-bin", body={"id": belt["id"], "device": PHONE}, key=shop_key(KEY))[0] == 200
    a = p.call("GET", "/api/assembly", {**Q, "cached": "1"})[1]
    chips = {b["name"]: (b["listed"]["chip"]["label"], b["sendable"]) for b in a["bought"] if b["listed"]}
    assert chips == {WHEEL: ("Ordered", False), "130T 3M 6mm Wide Belt": ("In bin", True)}   # in the bin: send again later
    assert p.call("POST", "/api/shop/part-undo", body={"id": belt["id"], "device": OTHER_PHONE}, key=shop_key(KEY))[0] == 409
    assert p.call("POST", "/api/shop/part-undo", body={"id": belt["id"], "device": PHONE}, key=shop_key(KEY))[0] == 200
    again = p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [],
                                                    "bought": [{"key": keys[WHEEL], "qty": 4}]})[1]
    assert again["results"][0]["why"] == "is already on the parts list"


def test_only_bought_parts_go_on_the_parts_list(tmp_path):
    p = bought_panel(tmp_path)
    a = p.call("GET", "/api/assembly", Q)[1]
    screw = "/".join([D, E, "JB2"])
    nut_strip = "/".join([VENDOR, "7" * 24, "JQN"])
    r = p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [], "bought": [
        {"key": a["items"][0]["key"], "qty": 1}, {"key": screw, "qty": 1}, {"key": nut_strip, "qty": 2},
        {"key": "nope", "qty": 1}]})[1]
    assert [x["why"] for x in r["results"]] == ["isn't a bought part", "is a screw or nut: those aren't on the parts list",
                                               "is a screw or nut: those aren't on the parts list",
                                               "isn't in this assembly any more"]
    assert p.parts.all() == []
    keys = {b["name"]: b["key"] for b in a["bought"]}                   # and a bought part never makes a card
    r = p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [{"key": keys[WHEEL], "qty": 1}]})[1]
    assert r["results"][0]["why"] == "is a bought part" and card_in(p, "ready_for_cam") == []


def test_a_part_taken_out_of_the_assembly_is_changed_in_cad(tmp_path):
    p = bought_panel(tmp_path, [bought_bom(), {"headers": HEADERS, "rows": bought_bom()["rows"][:1]}])
    a = p.call("GET", "/api/assembly", Q)[1]
    keys = {b["name"]: b["key"] for b in a["bought"]}
    p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [], "bought": [{"key": keys[WHEEL], "qty": 3}]})
    p.clock.t += 700
    a = p.call("GET", "/api/assembly", {**Q, "fresh": "1"})[1]          # the wheel was taken out in CAD
    assert a["bought"] == [] and [(g["name"], g["waiting"]) for g in a["boughtGone"]] == [(WHEEL, True)]
    shop = p.call("GET", "/api/shop", {"device": PHONE}, key=shop_key(KEY))[1]
    assert shop["parts"][0]["changed"] is True
    assert p.call("POST", "/api/part-remove", body={"id": a["boughtGone"][0]["id"]})[0] == 200
    assert p.parts.all() == []


def test_the_phone_page_is_there_with_only_the_parts_list(tmp_path):
    p = bought_panel(tmp_path, sheet="")
    page = p.app.handle("GET", "/shop", {"key": shop_key(KEY)}, {})
    assert page.status == 200 and b'data-tab="parts"' in page.body
    shop = p.call("GET", "/api/shop", {"device": PHONE}, key=shop_key(KEY))[1]
    assert shop["orderSheet"] == "" and shop["shafts"] == [] and shop["parts"] == []   # no sheet: no Order button
    assert p.call("POST", "/api/shop/start", body={"id": "0" * 12, "device": PHONE}, key=shop_key(KEY))[0] == 404
    assert p.call("POST", "/api/shop/part-bin", body={"id": "0" * 12, "device": PHONE}, key=KEY)[0] == 403
    assert p.call("POST", "/api/part-remove", body={"id": "0" * 12}, key=shop_key(KEY))[0] == 403   # the panel's own key


def test_with_the_parts_list_off_bought_parts_stay_where_they_were(tmp_path):
    p = Panel(tmp_path, transport=FakeTransport([
        ("GET", "/bom", [resp(body=bought_bom())] * 3),
        ("GET", "/elements", [resp(body=[{"id": E, "name": "Shooter", "elementType": "ASSEMBLY"}])] * 3),
        ("GET", f"/documents/{VENDOR}", [resp(body={"owner": {"id": "594ae8f3feede50f8a5cb929"}})])]))
    a = p.call("GET", "/api/assembly", Q)[1]
    assert "bought" not in a
    assert {i["name"]: i["why"] for i in a["items"]}["91259A496_NO THREADS_Alloy Steel Shoulder Screw"] == "Bought part"


# ---------------------------------------------------------------- some in the bin, CAD's count, how long ago, notes

def test_some_in_the_bin_and_the_rest_ordered(tmp_path):
    clock = Clock()
    lst = PartList(tmp_path / "bought_parts.json", now=clock)
    x = lst.add([entry(qty=4)])[0]
    lst.bin(x["id"], PHONE, 2)                                         # found 2 of the 4
    part = lst.shop_view(PHONE)["parts"][0]
    assert (part["status"], part["inBin"], part["onOrder"], part["left"]) == ("needed", 2, 0, 2)
    assert status_chip(lst.all()[0]) == ("2 of 4 in bin", "good")
    with pytest.raises(ListError, match="Between 1 and 2"):
        lst.bin(x["id"], PHONE, 3)
    lst.ordered(x["id"], PHONE)                                        # the other 2 go on the order sheet
    part = lst.shop_view(PHONE)["parts"][0]
    assert (part["status"], part["inBin"], part["onOrder"], part["left"], part["orderedDays"]) == ("ordered", 2, 2, 0, 0)
    clock.t = DAY + timedelta(days=15)
    part = lst.shop_view(PHONE)["parts"][0]
    assert part["orderedDays"] == 15 and part["late"] is True          # two weeks: it shows in orange
    lst.bin(x["id"], OTHER_PHONE)                                      # they came in
    assert lst.all()[0]["status"] == "bin" and lst.shop_view(PHONE)["parts"] == []
    assert [(b["n"], b["undo"]) for b in lst.shop_view(OTHER_PHONE)["bin"]] == [(2, True)]
    lst.undo(x["id"], OTHER_PHONE)                                     # back on order, 2 in the bin
    assert counts_of(lst) == (4, 2, 2)


def counts_of(lst):
    from autocam_service.bought import counts
    return counts(lst.all()[0])


def test_only_the_last_in_the_bin_can_be_undone(tmp_path):
    lst = PartList(tmp_path / "bought_parts.json", now=Clock())
    x = lst.add([entry(qty=4)])[0]
    lst.bin(x["id"], PHONE, 1)
    lst.bin(x["id"], OTHER_PHONE, 1)
    assert [(b["n"], b["undo"]) for b in lst.shop_view(PHONE)["bin"]] == [(1, False), (1, False)]
    with pytest.raises(ListError, match="Only the phone"):
        lst.undo(x["id"], PHONE)
    lst.undo(x["id"], OTHER_PHONE)
    assert counts_of(lst) == (4, 1, 0)


def test_a_count_changed_in_cad_shows_until_someone_settles_it(tmp_path):
    more = bought_bom()
    more["rows"][1]["headerIdToValue"]["q"] = 6.0                     # 3 wheels became 6
    p = bought_panel(tmp_path, [bought_bom(), more])
    a = p.call("GET", "/api/assembly", Q)[1]
    keys = {b["name"]: b["key"] for b in a["bought"]}
    p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [],
                                           "bought": [{"key": keys[WHEEL], "qty": 4, "note": "Get the\n45A ones"}]})
    a = p.call("GET", "/api/assembly", {**Q, "cached": "1"})[1]
    wheel = {b["name"]: b for b in a["bought"]}[WHEEL]
    assert wheel["listed"]["cadNow"] is None and wheel["listed"]["qty"] == 4   # sent 4 on purpose: no flag
    assert wheel["listed"]["note"] == "Get the 45A ones"
    p.clock.t += 700
    a = p.call("GET", "/api/assembly", {**Q, "fresh": "1"})[1]
    wheel = {b["name"]: b for b in a["bought"]}[WHEEL]
    assert wheel["listed"]["cadNow"] == 6
    shop = p.call("GET", "/api/shop", {"device": PHONE}, key=shop_key(KEY))[1]
    assert shop["parts"][0]["cadNow"] == 6 and shop["parts"][0]["note"] == "Get the 45A ones"
    sid = wheel["listed"]["id"]
    assert p.call("POST", "/api/part", body={"id": sid, "qty": 6, "note": "Get the 45A ones"})[0] == 200   # Make it 6
    a = p.call("GET", "/api/assembly", {**Q, "cached": "1"})[1]
    wheel = {b["name"]: b for b in a["bought"]}[WHEEL]
    assert wheel["listed"]["cadNow"] is None and wheel["listed"]["qty"] == 6


def test_the_panel_changes_a_parts_quantity_and_note(tmp_path):
    p = bought_panel(tmp_path)
    a = p.call("GET", "/api/assembly", Q)[1]
    keys = {b["name"]: b["key"] for b in a["bought"]}
    p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [], "bought": [{"key": keys[WHEEL], "qty": 3}]})
    sid = p.parts.all()[0]["id"]
    p.call("POST", "/api/shop/part-bin", body={"id": sid, "device": PHONE, "n": 2}, key=shop_key(KEY))
    status, r = p.call("POST", "/api/part", body={"id": sid, "qty": 1, "note": ""})
    assert status == 400 and "2 are in the bin already" in r["error"]
    assert p.call("POST", "/api/part", body={"id": sid, "qty": 5, "note": "x" * 300})[0] == 200
    x = p.parts.all()[0]
    assert (x["qty"], x["in_bin"], x["status"], len(x["note"])) == (5, 2, "needed", 200)
    assert p.call("POST", "/api/part-remove", body={"id": sid})[0] == 409        # some are in the bin: it stays
    assert p.call("POST", "/api/shop/part-bin", body={"id": sid, "device": PHONE, "n": "3"}, key=shop_key(KEY))[0] == 400


def test_the_order_sheet_link_is_the_same_on_every_phone(tmp_path):
    from autocam_service.bought import sheet_link
    from autocam_service.credentials import load_credentials
    mine = "https://docs.google.com/spreadsheets/u/3/d/1FaKe-x_Y/edit?gid=150574251#gid=150574251"
    assert sheet_link(mine) == "https://docs.google.com/spreadsheets/d/1FaKe-x_Y/edit?gid=150574251"
    assert sheet_link("https://docs.google.com.evil.example/spreadsheets/d/x") == ""
    env = tmp_path / ".env"
    env.write_text(f'AUTOCAM_ORDER_SHEET_URL="{mine}"\n', encoding="utf-8")
    assert load_credentials(env, environ={}).get("AUTOCAM_ORDER_SHEET_URL") == mine     # kept out of the public repo
    p = bought_panel(tmp_path, sheet="")
    p.app = PanelApp(p.app.cfg, KEY, p.onshape, p.board, p.names, clock=p.clock, parts=p.parts, order_sheet=mine)
    assert p.call("GET", "/api/shop", {"device": PHONE}, key=shop_key(KEY))[1]["orderSheet"] == sheet_link(mine)


def test_order_copies_the_sheets_row_from_what_the_part_knows(tmp_path):
    bom = bought_bom()
    bom["headers"] = HEADERS + [{"id": "v", "propertyName": "vendor"}, {"id": "pn", "propertyName": "partNumber"}]
    bom["rows"][1]["headerIdToValue"].update(v="WCP", pn="WCP-0322")
    p = bought_panel(tmp_path, [bom])
    a = p.call("GET", "/api/assembly", Q)[1]
    keys = {b["name"]: b["key"] for b in a["bought"]}
    p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [], "bought": [
        {"key": keys[WHEEL], "qty": 3}, {"key": keys["2766T21 Rubber bumper"], "qty": 4},
        {"key": keys["130T 3M 6mm Wide Belt"], "qty": 2}]})
    shop = {x["name"]: x for x in p.call("GET", "/api/shop", {"device": PHONE}, key=shop_key(KEY))[1]["parts"]}
    # Description, Link, Supplier, Part Number, Units: the sheet's columns E to I (shop.js joins them with tabs)
    assert [(x["vendor"], x["number"], x["link"]) for x in shop.values()] == [
        ("WCP", "WCP-0322", ""), ("McMaster", "2766T21", "https://www.mcmaster.com/2766T21/"), ("", "", "")]
