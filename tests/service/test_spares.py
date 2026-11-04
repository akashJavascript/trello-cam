"""Spares (2026-11-04, the mockup the user approved: a count per send, robot parts first, a Spares line in the Part
Studio too): a card of their own with the Spare label, sent from the Onshape panel (a Part Studio's form or status
view, an assembly's Spares switch) or the phone's Send spares; never the part's own card, never "a duplicate"."""

from datetime import datetime, timezone

from autocam_service.cards import PartRequest, parse_card
from autocam_service.panel.api import PanelApp, shop_key
from autocam_service.panel.board import short_day
from autocam_service.people import People
from autocam_service.tracker.base import Card
from test_assemblies import watched_panel
from test_bought import bought_bom
from test_panel import KEY, Q, Panel, card_in
from test_runner_e2e import STUDIO

SHOP = shop_key(KEY)
PHONE = "phone-aaaa"


def spare_cards(p):
    return [c for c in p.t.cards.values() if "Spare" in c.labels]


def own_cards(p, list_key="ready_for_cam"):
    return [c for c in card_in(p, list_key) if "Spare" not in c.labels]


# ---------------------------------------------------------------- the Part Studio

def test_the_form_sends_the_part_and_its_spares_on_cards_of_their_own(tmp_path):
    p = Panel(tmp_path)
    status, data = p.send(qty=4, spares=2, face=True, rush=True, tabs=False)
    assert status == 200 and data["card"]["qty"] == 4 and data["spares"] == "2 spares waiting to nest"
    [own] = own_cards(p)
    [spare] = spare_cards(p)
    assert spare.list_key == "ready_for_cam" and spare.name == own.name == "P-2014 gusset"
    assert spare.desc.split("\n")[:3] == [own.desc.split("\n")[0], "Qty: 2", "Part ID: JHD"]
    assert set(spare.labels) == {"Spare", "Face", "Rush"}                # the form's options, and Spare
    assert p.t.checklists[(spare.id, "Tabs")] == [["Hold it in with tabs", False]]
    req = parse_card(p.t.get_card(spare.id), face_label="Face", spare_label="Spare")
    assert isinstance(req, PartRequest) and req.spare and req.qty == 2
    assert not parse_card(p.t.get_card(own.id)).spare
    # the part's own status is its own card's: the spares don't count
    part = p.call("GET", "/api/part", {**Q, "p": "JHD"})[1]
    assert part["card"]["qty"] == 4 and part["spares"] == "2 spares waiting to nest"


def test_quantity_0_sends_only_spares_and_more_are_added_to_the_waiting_card(tmp_path):
    p = Panel(tmp_path)
    p.send(qty=2)
    assert p.send(qty=2)[0] == 409                                      # the part already has a card
    status, data = p.send(qty=0, spares=2)                              # spares never clash with it
    assert status == 200 and data["card"] is None and data["spares"] == "2 spares waiting to nest"
    p.send(qty=0, spares=1)
    [spare] = spare_cards(p)
    assert "Qty: 3" in spare.desc and len(own_cards(p)) == 1
    assert p.send(qty=0, spares=0)[0] == 400 and p.send(qty=1, spares=-1)[0] == 400


def test_the_status_view_copies_the_parts_card(tmp_path):
    p = Panel(tmp_path)
    p.send(qty=3, smoked=True, face=True, rush=True, tabs=False, p="JHD")
    [own] = own_cards(p)
    p.t.move(own.id, "nested")
    status, data = p.call("POST", "/api/spares", body={**Q, "p": "JHD", "n": 2, "name": "Akash", "u": "user1"})
    assert status == 200 and data["spares"] == "2 spares waiting to nest"
    [spare] = spare_cards(p)
    assert set(spare.labels) == {"Spare", "Face"}                       # not Rush: spares are never urgent
    assert spare.desc.split("\n")[-1] == "Spares sent from the Onshape panel by Akash"
    assert p.t.checklists[(spare.id, "Tabs")] == [["Hold it in with tabs", False]]
    assert p.call("POST", "/api/spares", body={**Q, "p": "JHG", "n": 1, "name": "Akash"})[0] == 409   # no card yet


def test_what_a_parts_spares_say(tmp_path):
    p = Panel(tmp_path)
    p.send(qty=0, spares=2)
    [spare] = spare_cards(p)
    line = lambda: p.call("GET", "/api/part", {**Q, "p": "JHD"})[1]["spares"]
    p.t.move(spare.id, "nested")
    p.h.store.register_sheet("s" * 24, "r071", True, [spare.id], job="r071-al6061", material="al6061",
                             thickness_in=0.25, index=1, url="u", extra={"label": "r071 S1"})
    p.board.invalidate()
    assert line() == "2 spares on r071 S1"
    p.t.move(spare.id, "cut")
    p.h.store.mark_sheet_cut("s" * 24, "2026-11-03T18:00:00Z")
    p.board.invalidate()
    assert line() == f"2 spares cut {short_day('2026-11-03T18:00:00Z')}"
    assert short_day("2026-11-03T18:00:00Z").startswith("Nov ") and short_day("soon") == ""


def test_spares_are_never_taken_for_a_duplicate_card(tmp_path):
    p = Panel(tmp_path)
    p.send(qty=2, spares=1)
    p.send(qty=0, spares=1, drafts=True)                                # a second spares card, in Drafts
    p.h.runner.ready_cards()
    assert not [t for _, t in p.t.comments if "Another card has this part too" in t]


# ---------------------------------------------------------------- an assembly's Spares switch

def test_an_assemblys_spares_switch_sends_a_count_per_plate(tmp_path):
    p = watched_panel(tmp_path, [bought_bom()])
    a = p.call("GET", "/api/assembly", Q)[1]
    plate = {i["name"]: i for i in a["items"]}["P-2014 gusset"]
    assert plate["spareable"] and plate["spares"] == ""
    r = p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [{"key": plate["key"], "qty": 4}],
                                               "spares": [{"key": plate["key"], "n": 2}]})[1]
    assert [x["name"] for x in r["results"] if x["sent"]] == ["P-2014 gusset", "P-2014 gusset spares"]
    [spare] = spare_cards(p)
    assert "Qty: 2" in spare.desc and "Sent from the Onshape panel by Siddhi" in spare.desc
    a = p.call("GET", "/api/assembly", {**Q, "cached": "1"})[1]
    plate = {i["name"]: i for i in a["items"]}["P-2014 gusset"]
    assert not plate["sendable"] and plate["spareable"] and plate["spares"] == "2 spares waiting to nest"
    assert plate["chip"]["label"] == "Waiting to nest"                  # its own card's
    r = p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [],
                                               "spares": [{"key": plate["key"], "n": 0}, {"key": "nope", "n": 1}]})[1]
    assert [x["sent"] for x in r["results"]] == [False, False]


# ---------------------------------------------------------------- the phone

def phone_panel(tmp_path):
    cut = Card("c" * 24, "P-2014 gusset x2", f"{STUDIO.replace('/v/', '/w/')}\nPart ID: JHD\nMaterial: 6061",
               "cut", "https://t/c1", labels=("Smoked", "Rush"))
    queued = Card("d" * 24, "P-2015 window", f"{STUDIO.replace('/v/', '/w/')}\nQty: 1\nPart ID: JHG", "ready_for_cam",
                  "https://t/c2")
    p = Panel(tmp_path, cards=[cut, queued])
    p.people = People(p.h.cfg.paths.state / "people.json")
    p.app = PanelApp(p.h.cfg, KEY, p.onshape, p.board, p.names, clock=p.clock, people=p.people)
    p.h.store.register_sheet("s" * 24, "r071", True, [cut.id], job="r071-al6061", material="al6061",
                             thickness_in=0.25, index=1, url="u", extra={"label": "r071 S1"})
    p.h.store.mark_sheet_cut("s" * 24, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return p, cut


def shop(p, **q):
    status, data = p.call("GET", "/api/shop", {"device": PHONE, **q}, key=SHOP)
    assert status == 200, data
    return data


def test_the_phone_offers_every_plate_autocam_has_nested_and_sends_their_spares(tmp_path):
    p, cut = phone_panel(tmp_path)
    assert "choices" not in shop(p)
    assert shop(p, choices="1")["choices"] == [
        {"title": "6061 aluminum 1/4in", "rows": [{"id": cut.id, "name": "P-2014 gusset", "sub": ""}]}]  # not P-2015
    p.call("POST", "/api/shop/me", body={"device": PHONE, "name": "Maya"}, key=SHOP)
    status, r = p.call("POST", "/api/shop/spares", body={"device": PHONE, "items": [{"id": cut.id, "n": 3}]}, key=SHOP)
    assert status == 200 and r["sent"] == 3
    [spare] = spare_cards(p)
    assert spare.list_key == "ready_for_cam" and spare.name == "P-2014 gusset"
    assert spare.desc.split("\n") == [STUDIO.replace("/v/", "/w/"), "Qty: 3", "Part ID: JHD", "Material: 6061",
                                      "Spares sent from the shop page by Maya"]
    assert set(spare.labels) == {"Spare", "Smoked"}                     # its stock, not its rush
    assert shop(p, plates="1")["spares"] == [{"id": spare.id, "name": "P-2014 gusset", "qty": 3,
                                              "where": "Waiting to nest", "cut": False}]
    assert shop(p, choices="1")["choices"][0]["rows"][0]["sub"] == "3 spares waiting to nest"


def test_the_phone_checks_what_it_sends(tmp_path):
    p, cut = phone_panel(tmp_path)
    post = lambda body, key=SHOP: p.call("POST", "/api/shop/spares", body=body, key=key)[0]
    assert post({"device": PHONE, "items": [{"id": cut.id, "n": 0}]}) == 400
    assert post({"device": PHONE, "items": []}) == 400
    assert post({"device": "x", "items": [{"id": cut.id, "n": 1}]}) == 400
    assert post({"device": PHONE, "items": [{"id": cut.id, "n": 1}]}, key=KEY) == 403   # the panel's key isn't the shop's
    status, r = p.call("POST", "/api/shop/spares", body={"device": PHONE, "items": [{"id": "e" * 24, "n": 1}]}, key=SHOP)
    assert status == 200 and r["sent"] == 0 and r["results"][0]["why"] == "isn't on the board any more"


def test_the_plates_tab_marks_spare_plates(tmp_path):
    p, cut = phone_panel(tmp_path)
    p.call("POST", "/api/shop/spares", body={"device": PHONE, "items": [{"id": cut.id, "n": 2}]}, key=SHOP)
    [spare] = spare_cards(p)
    p.t.move(spare.id, "nested")
    sheet = Card("f" * 24, "6061 1/4in - 4 mm O-flute ALU - 2 parts - 9 min - r072 S1", "", "ready_to_cut", "u")
    p.t.cards[sheet.id] = sheet                       # a person moved it there (automation never does)
    p.h.store.register_sheet(sheet.id, "r072", True, [spare.id], job="r072-al6061", material="al6061",
                             thickness_in=0.25, index=1, url="u", extra={"label": "r072 S1", "counts": {spare.id: 2}})
    v = shop(p, plates="1")
    [s] = [x for x in v["sheets"] if x["id"] == sheet.id]
    assert s["plates"] == [{"id": spare.id, "name": "P-2014 gusset", "qty": 2, "who": "", "spare": True}]
