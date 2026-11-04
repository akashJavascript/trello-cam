"""Who does what (people.py, panel/plates.py; the assigning mockup the user approved 2026-11-04): names on shafts,
bought parts, sheets (who cuts) and plates (who finishes), from the Onshape panel when sending and from the phone
page; soft (anyone can still start); a phone says whose it is once."""

from datetime import datetime, timedelta, timezone

import pytest

from autocam_service.panel.api import PanelApp, shop_key
from autocam_service.people import KEEP_DAYS, People, clean_name
from autocam_service.shoplist import ListError
from autocam_service.tracker.base import Card
from test_assemblies import watched_panel, with_shaft
from test_bought import WHEEL, bought_bom
from test_panel import KEY, Q

SHOP = shop_key(KEY)
PHONE, OTHER = "phone-aaaa", "phone-bbbb"
SHEET1, PART1, PART2 = "a" * 24, "b" * 24, "c" * 24


class Now:
    def __init__(self):
        self.t = datetime(2026, 11, 4, 15, 0)

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- the names

def test_names_are_kept_once_as_first_typed(tmp_path):
    people = People(tmp_path / "people.json")
    assert people.add(" maya ") == "maya" and people.add("Maya") == "maya"          # the same person
    assert people.add("Sam K.") == "Sam K." and people.names() == ["maya", "Sam K."]
    assert people.add("") == "" and clean_name(None) == ""
    for bad in ("4lex", "Maya!", "x" * 30, "<b>"):
        with pytest.raises(ListError):
            people.add(bad)


def test_a_phone_says_whose_it_is(tmp_path):
    people = People(tmp_path / "people.json")
    assert people.me(PHONE) == ""
    assert people.set_me(PHONE, "Alex") == "Alex" and people.me(PHONE) == "Alex" and people.me(OTHER) == ""
    with pytest.raises(ListError):
        people.set_me(PHONE, "")
    with pytest.raises(ListError):
        people.set_me("x", "Alex")                                                  # not a phone id


def test_sheets_and_plates_are_assigned_and_drop_off_later(tmp_path):
    now = Now()
    people = People(tmp_path / "people.json", now=now)
    assert people.assign("sheets", [SHEET1], "Priya") == "Priya"
    people.assign("plates", [PART1, PART2], "Sam")
    assert people.assigned("plates") == {PART1: "Sam", PART2: "Sam"} and people.who("sheets", SHEET1) == "Priya"
    people.assign("plates", [PART2], "")                                            # Nobody
    assert people.assigned("plates") == {PART1: "Sam"}
    with pytest.raises(ListError):
        people.assign("plates", ["not a card!"], "Sam")
    with pytest.raises(ListError):
        people.assign("bins", [PART1], "Sam")
    now.t += timedelta(days=KEEP_DAYS + 1)
    assert people.assigned("plates") == {} and people.names() == ["Priya", "Sam"]  # names stay


# ---------------------------------------------------------------- the panel and the phone

def app_with_people(tmp_path, boms, cards=()):
    p = watched_panel(tmp_path, boms, cards=cards)
    p.people = People(p.h.cfg.paths.state / "people.json")
    p.app = PanelApp(p.h.cfg, KEY, p.onshape, p.board, p.names, clock=p.clock, shafts=p.shafts, parts=p.parts,
                     assemblies=p.watch, people=p.people)
    return p


def shop(p, device=PHONE, plates=False):
    q = {"device": device, **({"plates": "1"} if plates else {})}
    status, data = p.call("GET", "/api/shop", q, key=SHOP)
    assert status == 200, data
    return data


def test_sending_from_the_panel_assigns_everything_ticked(tmp_path):
    p = app_with_people(tmp_path, [with_shaft(bought_bom())])
    a = p.call("GET", "/api/assembly", Q)[1]
    assert a["people"] == []
    plate = {i["name"]: i["key"] for i in a["items"]}["P-2014 gusset"]
    wheel = {b["name"]: b["key"] for b in a["bought"]}[WHEEL]
    r = p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "server": "https://5940.onshape.com",
                                               "items": [{"key": plate, "qty": 4}], "assign": "Maya",
                                               "shafts": [{"key": a["shafts"][0]["key"], "qty": 4, "ends": ["10-32", "10-32"]}],
                                               "bought": [{"key": wheel, "qty": 3}]})[1]
    assert all(x["sent"] for x in r["results"]), r
    assert p.people.names() == ["Maya", "Siddhi"]                       # the sender's on the list too
    [card_id] = p.people.assigned("plates")
    assert p.people.who("plates", card_id) == "Maya" and p.t.get_card(card_id).name.startswith("P-2014")
    assert p.shafts.all()[0]["who"] == "Maya" and p.parts.all()[0]["who"] == "Maya"
    a = p.call("GET", "/api/assembly", {**Q, "cached": "1"})[1]
    assert {i["name"]: i["who"] for i in a["items"]}["P-2014 gusset"] == "Maya"
    assert a["shafts"][0]["listed"]["who"] == "Maya" and a["people"] == ["Maya", "Siddhi"]
    v = shop(p)
    assert v["shafts"][0]["who"] == "Maya" and v["parts"][0]["who"] == "Maya" and v["people"] == ["Maya", "Siddhi"]


def test_the_phone_assigns_and_anyone_can_still_start(tmp_path):
    p = app_with_people(tmp_path, [with_shaft(bought_bom())])
    a = p.call("GET", "/api/assembly", Q)[1]
    p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [],
                                           "shafts": [{"key": a["shafts"][0]["key"], "qty": 4, "ends": ["", ""]}],
                                           "bought": [{"key": a["bought"][0]["key"], "qty": 1}]})
    assert p.call("POST", "/api/shop/me", body={"device": PHONE, "name": "Alex"}, key=SHOP)[1] == {"me": "Alex"}
    shaft = shop(p)["shafts"][0]
    part = shop(p)["parts"][0]
    status, r = p.call("POST", "/api/shop/assign", body={"device": PHONE, "kind": "shaft", "ids": [shaft["id"]], "who": "Priya"},
                       key=SHOP)
    assert status == 200 and r == {"who": "Priya"}
    p.call("POST", "/api/shop/assign", body={"device": PHONE, "kind": "part", "ids": [part["id"]], "who": "alex"}, key=SHOP)
    v = shop(p)
    assert v["me"] == "Alex" and v["shafts"][0]["who"] == "Priya" and v["parts"][0]["who"] == "Alex"   # as kept
    assert shop(p, OTHER)["me"] == ""
    # soft: another phone starts Priya's shaft anyway, and the list says who
    p.call("POST", "/api/shop/me", body={"device": OTHER, "name": "Sam"}, key=SHOP)
    assert p.call("POST", "/api/shop/start", body={"id": shaft["id"], "device": OTHER}, key=SHOP)[0] == 200
    s = shop(p)["shafts"][0]
    assert s["started"] and not s["mine"] and s["startedBy"] == "Sam" and s["who"] == "Priya"
    p.call("POST", "/api/shop/done", body={"id": shaft["id"], "device": OTHER}, key=SHOP)
    assert shop(p)["done"][0]["by"] == "Sam"
    # Nobody takes a name off; a finished shaft keeps who it was for
    p.call("POST", "/api/shop/assign", body={"device": PHONE, "kind": "part", "ids": [part["id"]], "who": ""}, key=SHOP)
    assert shop(p)["parts"][0]["who"] == ""
    status, r = p.call("POST", "/api/shop/assign", body={"device": PHONE, "kind": "shaft", "ids": ["zz"], "who": "Sam"}, key=SHOP)
    assert status == 400
    assert p.call("POST", "/api/shop/assign", body={"device": "x", "kind": "part", "ids": [part["id"]], "who": "Sam"},
                  key=SHOP)[0] == 400
    assert p.call("POST", "/api/shop/assign", body={"device": PHONE, "kind": "part", "ids": [part["id"]], "who": "Sam"},
                  key=KEY)[0] == 403                                     # the panel's key isn't the shop's


def test_all_on_a_group_skips_what_finished_meanwhile(tmp_path):
    p = app_with_people(tmp_path, [with_shaft(bought_bom())])
    a = p.call("GET", "/api/assembly", Q)[1]
    p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [],
                                           "bought": [{"key": b["key"], "qty": 1} for b in a["bought"]]})
    ids = [x["id"] for x in shop(p)["parts"]]
    assert len(ids) == 3
    p.call("POST", "/api/shop/part-bin", body={"id": ids[0], "device": OTHER, "n": 1}, key=SHOP)   # in the bin meanwhile
    assert p.parts.assign(ids, "Jordan") == 2
    assert [x.get("who") for x in p.parts.all()] == [None, "Jordan", "Jordan"]


# ---------------------------------------------------------------- the Plates tab

def plates_panel(tmp_path):
    cards = [Card(SHEET1, "RUSH - 6061 1/4in - 4 mm O-flute ALU - 2 parts - 25 min - r071 S1", "", "ready_to_cut", "u"),
             Card(PART1, "P-2011 shooter side plate", "https://cad.onshape.com/documents/x\nQty: 4", "nested", "u1"),
             Card(PART2, "P-2014 gusset x2", "", "nested", "u2")]
    p = app_with_people(tmp_path, [bought_bom()], cards=cards)
    p.h.store.register_sheet(SHEET1, "r071", True, [PART1, PART2], job="r071-al6061", material="al6061",
                             thickness_in=0.25, index=1, url="u", extra={"label": "r071 S1", "counts": {PART1: 2}})
    return p


def test_the_plates_tab_shows_the_sheets_to_cut_and_their_plates(tmp_path):
    p = plates_panel(tmp_path)
    assert "sheets" not in shop(p)                                      # Trello only while Plates shows
    v = shop(p, plates=True)
    assert v["platesOn"] and v["sheets"] == [{
        "id": SHEET1, "title": "6061 aluminum 1/4in", "sub": "r071 S1 · 25 min", "rush": True, "cut": False, "who": "",
        "plates": [{"id": PART1, "name": "P-2011 shooter side plate", "qty": 2, "who": ""},     # 2 on this sheet
                   {"id": PART2, "name": "P-2014 gusset", "qty": 2, "who": ""}]}]
    p.call("POST", "/api/shop/assign", body={"device": PHONE, "kind": "sheet", "ids": [SHEET1], "who": "Alex"}, key=SHOP)
    p.call("POST", "/api/shop/assign", body={"device": PHONE, "kind": "plate", "ids": [PART2], "who": "Maya"}, key=SHOP)
    s = shop(p, plates=True)["sheets"][0]
    assert s["who"] == "Alex" and [x["who"] for x in s["plates"]] == ["", "Maya"]


def test_the_plates_tab_reads_trello_at_most_every_30_s(tmp_path):
    p = plates_panel(tmp_path)
    reads = []
    real = p.t.list_cards
    p.t.list_cards = lambda key: reads.append(key) or real(key)
    shop(p, plates=True)
    shop(p, plates=True)
    # Ready to cut and On a sheet, and the part cards for the Spares section (the panel's lists), once each
    assert sorted(reads) == sorted(["ready_to_cut", "nested", "inbox", "ready_for_cam", "needs_fixing", "nested", "cut"])
    p.clock.t += 31
    shop(p, plates=True)
    assert len(reads) == 14


def test_a_sheet_cut_today_stays_for_whoever_finishes_its_plates(tmp_path):
    p = plates_panel(tmp_path)
    shop(p, plates=True)                                                 # the plates' names are seen
    p.t.move(SHEET1, "cut")
    p.h.store.mark_sheet_cut(SHEET1, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    p.clock.t += 31
    [s] = shop(p, plates=True)["sheets"]
    assert s["cut"] and s["title"] == "6061 aluminum 1/4in" and len(s["plates"]) == 2
