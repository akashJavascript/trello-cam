"""The shop's shaft list (shafts.py, panel/api.py; 2026-11-02): library shafts read off an assembly's BOM row with no
Onshape call, the card's wording, what can't be made, the list's Start / Stop / Done / Undo, and the panel and phone
page around it. Offline: scripted Onshape, fake Trello."""

import copy
from datetime import datetime, timedelta

import pytest

from autocam_service.config import ConfigError, parse_config
from autocam_service.panel.api import PanelApp, shop_key
from autocam_service.shafts import (Op, ShaftError, ShaftList, configuration_values, inches, problems, recognize,
                                    steps)
from autocam_service.tracker.base import Card
from fakeonshape import FakeTransport, resp
from test_panel import KEY, Q, Panel, card_in
from test_panel_more import HEADERS, row
from test_runner_e2e import D, E, RAW, REPO_ROOT, W

LIB, LIBV = "dee14b205b4629c493a3df74", "9" * 24
HEX, HEX38, LITE, STANDOFF, PRINTED = ("74fa9fa49ead33d7ba3d5ae0", "33e281a702f193825b86827f", "8c1ad05ff19d9d749c567410",
                                       "ac38d53cc6fec9e9cc926402", "41a66f87be72069b4a50d6a9")
CFG = parse_config(copy.deepcopy(RAW), root=REPO_ROOT)
SHAFTS = CFG.shafts
DAY = datetime(2026, 11, 3, 18, 30)


def lib(eid, configuration="default", qty=1, name="Hex Shaft"):
    return {"did": LIB, "wvm": "v", "wvmid": LIBV, "eid": eid, "pid": "JFD", "name": name, "qty": qty,
            "material": "Aluminum - 6061", "configuration": "" if configuration == "default" else configuration,
            "standard": False}


# ---------------------------------------------------------------- reading a shaft off its BOM row

def test_configuration_lengths_in_any_unit():
    assert configuration_values("Length=18.25+in;Auto_Name_Part=true") == {"length": "18.25+in", "auto_name_part": "true"}
    assert inches("0.46355+meter") == 18.25 and inches("18.25+in") == 18.25 and inches("18.25") == 18.25
    assert inches("6.35+mm") == 0.25 and inches("true") is None and inches("") is None


def test_library_shafts_are_known_by_their_tab_and_length():
    assert recognize(lib(HEX, "Length=0.46355+meter"), SHAFTS).label == "1/2 hex, 18.250 in"
    assert recognize(lib(HEX38, "Length=5.25+in"), SHAFTS).label == "3/8 hex, 5.250 in"
    assert recognize(lib(LITE, "Length=2+in"), SHAFTS).stock == "3/8 hex lite"
    assert recognize(lib(HEX), SHAFTS).length_in == 1.0                       # the default configuration
    assert recognize(lib(PRINTED, "Length=1+in"), SHAFTS) is None              # 3d printed standoff: printed
    assert recognize({**lib(HEX, "Length=3+in"), "did": D}, SHAFTS) is None    # not the library


def test_a_standoffs_od_picks_the_round_bar_and_anything_else_is_turned():
    plain = recognize(lib(STANDOFF, "Length=1.5+in;ID=0.15625+in;OD=0.375+in"), SHAFTS)
    assert (plain.stock, plain.ops) == ("3/8 round", [])
    half = recognize(lib(STANDOFF, "Length=3+in;OD=0.0127+meter"), SHAFTS)
    assert (half.stock, half.ops) == ("1/2 round", [])
    thin = recognize(lib(STANDOFF, "Length=1.25+in;OD=0.3+in"), SHAFTS)
    assert thin.stock == "3/8 round" and thin.ops == [Op("turn", 0, d=0.3, auto=True)]
    assert recognize(lib(STANDOFF, "OD=0.625+in"), SHAFTS).problem.startswith("OD 0.625 is bigger than any round bar")
    assert recognize(lib(STANDOFF), SHAFTS).stock == "3/8 round"                # default OD 0.375


def test_real_rows_from_the_shooters_bom():
    """Configuration strings exactly as the Shooter's BOM had them (read 2026-11-02): meters with float noise, and
    3/8 hex lite's Length with no unit at all (it's in inches)."""
    assert recognize(lib(HEX, "Length=0.19843750000000004+meter"), SHAFTS).label == "1/2 hex, 7.813 in"   # halves up
    assert recognize(lib(HEX38, "Length=0.0333412084+meter"), SHAFTS).label == "3/8 hex, 1.313 in"
    assert recognize(lib(LITE, "Length=3.5"), SHAFTS).label == "3/8 hex lite, 3.500 in"
    tapped = recognize(lib(STANDOFF, "ID=0.0039624000000000005+meter;Length=0.0762+meter"), SHAFTS)
    assert (tapped.label, tapped.tapped) == ("3/8 round, 3.000 in", False)       # standoffs start untapped (the user)
    clear = recognize(lib(STANDOFF, "ID=0.004978400000000001+meter;Length=0.0158242+meter"), SHAFTS)
    assert (clear.label, clear.tapped) == ("3/8 round, 0.623 in", False)
    assert recognize(lib(HEX, "Length=0.18415+meter"), SHAFTS).tapped                  # hex shafts: 10-32 to start
    turned = recognize(lib(STANDOFF, "ID=0.005+meter;Length=0.028473400000000003+meter;OD=0.0066040000000000005+meter"),
                       SHAFTS)
    assert turned.label == "3/8 round, 1.121 in" and turned.ops == [Op("turn", 0, d=0.2600, auto=True)]
    assert recognize(lib(PRINTED, "ID=0.005207+meter;Length=0.006104001+meter"), SHAFTS) is None
    assert recognize(lib("f2b1cbecc499a785ca866ec0", "Bore_ID=0.009779+meter;Length=0.0119+meter"), SHAFTS) is None


# ---------------------------------------------------------------- the card's words

def test_one_short_line_per_step():
    tap = ["10-32", "10-32"]
    assert steps(9.5, tap, [Op("groove", 1, at=0.75), Op("groove", 2, at=0.75)], 0.02) == [
        "Grooves 0.750 from both ends, 0.020 per side", "Tap 10-32 both ends"]
    assert steps(18.25, tap, [Op("turn", 1, d=0.5, l=0.4), Op("turn", 2, d=0.5, l=0.4)], 0.02) == [
        "Turn both ends to Ø0.500 × 0.400", "Tap 10-32 both ends"]
    assert steps(5.25, tap, [Op("turn", 1, d=0.375, l=0.3)], 0.02) == ["Turn one end to Ø0.375 × 0.300", "Tap 10-32 both ends"]
    assert steps(3, ["10-32", ""], [], 0.02) == ["Tap 10-32 in one end"]
    assert steps(3, ["8-32", "8-32"], [], 0.02) == ["Tap 8-32 both ends"]
    assert steps(1.25, ["", ""], [Op("turn", 0, d=0.3125, auto=True)], 0.02, "3/8 round") == [
        "Turn down to Ø0.313 from 3/8 round"]                                   # a Standoff's OD that isn't a bar
    assert steps(7, ["", ""], [], 0.02) == []
    # two different one-end steps: then each says which end
    assert steps(6, ["10-32", ""], [Op("groove", 2, at=0.5)], 0.02) == [
        "Groove 0.500 from end 2, 0.020 per side", "Tap 10-32 in end 1"]


def test_what_cant_be_made():
    assert problems("1/2 hex", 10, [Op("turn", 1, d=0.5, l=0.4)]) == []            # hex to its flats: corners off
    assert problems("1/2 hex", 10, [Op("turn", 1, d=0.55, l=0.4)]) == ["Ø0.550 isn't smaller than the 1/2 hex."]
    assert problems("3/8 round", 2, [Op("turn", 1, d=0.375, l=0.4)]) == ["Ø0.375 isn't smaller than the 3/8 round."]
    assert problems("3/8 hex lite", 2, [Op("turn", 1, d=0.2, l=0.4)]) == ["Ø0.200 cuts into the 0.159 hole of the 3/8 hex lite."]
    assert problems("1/2 hex", 5, [Op("groove", 1, at=5.5)]) == ["A groove 5.500 from the end is off the 5.000 shaft."]
    assert problems("1/2 hex", 5, [Op("turn", 1, d=0.4, l=3), Op("turn", 2, d=0.4, l=3)]) == [
        "The turn-downs on the two ends overlap."]


# ---------------------------------------------------------------- the list

class Clock:
    def __init__(self):
        self.t = DAY

    def __call__(self):
        return self.t


def entry(source="a/b|x", **kw):
    return {"source": source, "assembly": {"key": "a/b", "name": "Shooter", "url": "https://cad.onshape.com/documents/x"},
            "row": {}, "stock": "1/2 hex", "length_in": 9.5, "qty": 2, "ends": ["10-32", "10-32"],
            "ops": [Op("groove", 1, at=0.75).to_dict(), Op("groove", 2, at=0.75).to_dict()], "rush": False,
            "sender": "Siddhi", **kw}


def test_start_stop_done_undo_and_two_phones_at_once(tmp_path):
    clock = Clock()
    lst = ShaftList(tmp_path / "shafts.json", now=clock)
    sid = lst.add([entry()])[0]["id"]
    with pytest.raises(ShaftError, match="already on the shaft list"):
        lst.add([entry()])                                        # one at a time per assembly row
    lst.start(sid, "phone-aaaa")
    with pytest.raises(ShaftError, match="Someone already started this") as e:
        lst.start(sid, "phone-bbbb")
    assert e.value.status == 409 and e.value.kind == "taken"
    view = lst.shop_view("phone-aaaa")["shafts"][0]
    assert view["mine"] and view["started"] and view["steps"] == ["Grooves 0.750 from both ends, 0.020 per side",
                                                                   "Tap 10-32 both ends"]
    assert not lst.shop_view("phone-bbbb")["shafts"][0]["mine"]
    assert "phone-aaaa" not in str(lst.shop_view("phone-bbbb"))    # nobody's phone id leaves the server
    lst.stop(sid, "phone-aaaa")
    assert not lst.shop_view("phone-aaaa")["shafts"][0]["started"]
    lst.done(sid, "phone-bbbb")
    v = lst.shop_view("phone-bbbb")
    assert v["shafts"] == [] and v["done"][0]["when"] == "6:30 PM" and v["done"][0]["undo"]
    assert not lst.shop_view("phone-aaaa")["done"][0]["undo"]      # only the phone that did it
    with pytest.raises(ShaftError):
        lst.undo(sid, "phone-aaaa")
    lst.undo(sid, "phone-bbbb")
    assert lst.shop_view("phone-bbbb")["shafts"][0]["id"] == sid


def test_done_ones_drop_off_after_two_weeks_and_only_today_is_shown(tmp_path):
    clock = Clock()
    lst = ShaftList(tmp_path / "shafts.json", keep_done_days=14, now=clock)
    sid = lst.add([entry()])[0]["id"]
    lst.done(sid, "phone-aaaa")
    clock.t = DAY + timedelta(days=1)
    assert lst.shop_view("phone-aaaa")["done"] == [] and lst.latest("a/b|x")["status"] == "done"
    with pytest.raises(ShaftError):
        lst.undo(sid, "phone-aaaa")                                # not the same day
    lst.add([entry()])                                             # done: it can be sent again
    clock.t = DAY + timedelta(days=15)
    assert [x["status"] for x in lst.all()] == ["waiting"]


def test_edit_and_take_off_only_while_nobody_started(tmp_path):
    lst = ShaftList(tmp_path / "shafts.json", now=Clock())
    sid = lst.add([entry()])[0]["id"]
    x = lst.edit(sid, 4, ["1/4-20", ""], [Op("turn", 1, d=0.5, l=0.5)])
    assert (x["qty"], x["ends"], x["ops"]) == (4, ["1/4-20", ""], [{"kind": "turn", "end": 1, "d": 0.5, "l": 0.5}])
    with pytest.raises(ShaftError, match="isn't smaller"):
        lst.edit(sid, 4, ["", ""], [Op("turn", 1, d=0.6, l=0.5)])
    lst.start(sid, "phone-aaaa")
    with pytest.raises(ShaftError, match="already started"):
        lst.edit(sid, 1, ["", ""], [])
    with pytest.raises(ShaftError, match="stays on the list"):
        lst.remove(sid)
    lst.stop(sid, "phone-aaaa")
    lst.remove(sid)
    assert lst.all() == []


def test_changed_in_cad_when_the_shaft_sent_isnt_in_its_assembly_any_more(tmp_path):
    lst = ShaftList(tmp_path / "shafts.json", now=Clock())
    lst.add([entry("a/b|one"), entry("a/b|two"), entry("c/d|one", assembly={"key": "c/d", "name": "Climber", "url": ""})])
    assert lst.mark_changed("a/b", ["a/b|one"]) == 1                # "two" got a new length
    assert [x["changed"] for x in lst.all()] == [False, True, False]
    assert lst.shop_view("")["shafts"][1]["changed"]
    assert lst.mark_changed("a/b", ["a/b|one"]) == 0                # nothing new: the file isn't written
    lst.mark_changed("a/b", ["a/b|one", "a/b|two"])                 # back again
    assert not any(x["changed"] for x in lst.all())


# ---------------------------------------------------------------- the panel and the phone page

def bom_rows_with_shafts():
    return {"headers": HEADERS, "rows": [
        row(D, "w", W, E, "JHD", "P-2014 gusset", 4, "Aluminum - 6061"),
        row(LIB, "v", LIBV, HEX, "JFD", "Hex Shaft", 4, "Aluminum - 6061", configuration="Length=0.46355+meter"),
        row(LIB, "v", LIBV, HEX, "JFD", "Hex Shaft", 1, "Aluminum - 6061", configuration="Length=11+in"),
        row(LIB, "v", LIBV, STANDOFF, "JFD", "Standoff", 6, "Aluminum - 6061", configuration="Length=2.5+in;OD=0.375+in"),
        row(LIB, "v", LIBV, STANDOFF, "JFD", "Standoff", 4, "Aluminum - 6061", configuration="ID=0.196+in;Length=0.75+in"),
        row(LIB, "v", LIBV, STANDOFF, "JFD", "Standoff", 2, "Aluminum - 6061", configuration="Length=1+in;OD=0.75+in"),
        row(LIB, "v", LIBV, PRINTED, "JFD", "3d printed standoff", 4, "PLA")]}


def shaft_panel(tmp_path, boms=None):
    boms = boms or [bom_rows_with_shafts()]
    transport = FakeTransport([("GET", "/bom", [resp(body=b) for b in boms] + [resp(body=boms[-1])] * 5),
                               ("GET", "/elements", [resp(body=[{"id": E, "name": "Intake", "elementType": "ASSEMBLY"}])] * 3)])
    p = Panel(tmp_path, transport=transport)
    p.clock_now = Clock()
    p.shafts = ShaftList(p.h.cfg.paths.state / "shafts.json", now=p.clock_now)
    p.app = PanelApp(p.h.cfg, KEY, p.onshape, p.board, p.names, clock=p.clock, shafts=p.shafts)
    return p


def test_an_assembly_lists_its_shafts_apart_from_its_plates(tmp_path):
    p = shaft_panel(tmp_path)
    status, a = p.call("GET", "/api/assembly", Q)
    assert status == 200
    assert [i["name"] for i in a["items"]] == ["P-2014 gusset", "3d printed standoff"]   # shafts aren't plates
    shafts = {s["label"]: s for s in a["shafts"]}
    assert list(shafts) == ["1/2 hex, 11.000 in", "1/2 hex, 18.250 in", "1/2 round, 1.000 in", "3/8 round, 0.750 in",
                            "3/8 round, 2.500 in"]
    assert shafts["1/2 hex, 18.250 in"]["qty"] == 4 and shafts["1/2 hex, 18.250 in"]["ends"] == ["10-32", "10-32"]
    assert shafts["1/2 hex, 18.250 in"]["sendable"] and shafts["1/2 hex, 18.250 in"]["listed"] is None
    assert shafts["1/2 round, 1.000 in"]["problem"].startswith("OD 0.750") and not shafts["1/2 round, 1.000 in"]["sendable"]
    assert shafts["3/8 round, 0.750 in"]["ends"] == shafts["3/8 round, 2.500 in"]["ends"] == ["", ""]   # untapped
    assert a["grooveDepth"] == 0.02 and a["shaftTap"] == "10-32"


def send_body(a, **kw):
    shafts = {s["label"]: s["key"] for s in a["shafts"]}
    plates = {i["name"]: i["key"] for i in a["items"]}
    return {**Q, "name": "Siddhi", "tabs": True, "rush": True, "drafts": True, "server": "https://5940.onshape.com",
            "items": [{"key": plates["P-2014 gusset"], "qty": 4}],
            "shafts": [{"key": shafts["1/2 hex, 18.250 in"], "qty": 4, "ends": ["10-32", "10-32"],
                        "ops": [{"kind": "turn", "end": 1, "d": 0.5, "l": 0.4}, {"kind": "turn", "end": 2, "d": 0.5, "l": 0.4}]},
                       {"key": shafts["3/8 round, 2.500 in"], "qty": 6, "ends": ["8-32", "8-32"], "ops": []},
                       {"key": shafts["1/2 hex, 11.000 in"], "qty": 1, "ends": ["10-32", "10-32"],
                        "ops": [{"kind": "turn", "end": 1, "d": 0.6, "l": 0.4}]}], **kw}


def test_one_send_puts_plates_on_trello_and_shafts_on_the_list(tmp_path):
    p = shaft_panel(tmp_path)
    a = p.call("GET", "/api/assembly", Q)[1]
    status, r = p.call("POST", "/api/send-many", body=send_body(a))
    assert status == 200
    assert [(x["name"], x["sent"], x.get("why")) for x in r["results"]] == [
        ("P-2014 gusset", True, None), ("1/2 hex, 18.250 in", True, None), ("3/8 round, 2.500 in", True, None),
        ("1/2 hex, 11.000 in", False, "Ø0.600 isn't smaller than the 1/2 hex.")]
    assert [c.name for c in card_in(p, "inbox")] == ["P-2014 gusset"]          # Drafts is for plates only
    listed = p.shafts.all()
    assert [(x["stock"], x["length_in"], x["qty"], x["rush"], x["sender"]) for x in listed] == [
        ("1/2 hex", 18.25, 4, True, "Siddhi"), ("3/8 round", 2.5, 6, True, "Siddhi")]
    assert listed[0]["assembly"] == {"key": f"{D}/{E}", "name": "Intake",
                                     "url": f"https://5940.onshape.com/documents/{D}/w/{W}/e/{E}"}
    a = p.call("GET", "/api/assembly", Q)[1]                                  # now each says it's on the list
    on = {s["label"]: s for s in a["shafts"]}["1/2 hex, 18.250 in"]
    assert not on["sendable"] and on["listed"]["status"] == "waiting" and on["listed"]["chip"]["label"] == ""
    again = p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [],
                                                    "shafts": [{"key": on["key"], "qty": 4}]})[1]
    assert again["results"][0]["why"] == "is already on the shaft list"


def test_a_shaft_key_sent_as_a_plate_never_makes_a_card(tmp_path):
    p = shaft_panel(tmp_path)
    a = p.call("GET", "/api/assembly", Q)[1]
    key = a["shafts"][0]["key"]
    r = p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [{"key": key, "qty": 1}]})[1]
    assert r["results"][0]["why"] == "is a shaft: it goes on the shaft list" and card_in(p, "ready_for_cam") == []


def test_the_sender_changes_or_takes_off_a_waiting_shaft(tmp_path):
    p = shaft_panel(tmp_path)
    a = p.call("GET", "/api/assembly", Q)[1]
    p.call("POST", "/api/send-many", body=send_body(a))
    sid = p.shafts.all()[1]["id"]
    status, _ = p.call("POST", "/api/shaft", body={"id": sid, "qty": 3, "ends": ["10-32", ""],
                                                   "ops": [{"kind": "groove", "end": 1, "at": 0.5}]})
    assert status == 200 and p.shafts.all()[1]["qty"] == 3
    status, r = p.call("POST", "/api/shaft", body={"id": sid, "qty": 3, "ends": ["#$%", ""], "ops": []})
    assert status == 400 and "isn't a tap size" in r["error"]
    assert p.call("POST", "/api/shaft-remove", body={"id": sid})[0] == 200
    assert [x["stock"] for x in p.shafts.all()] == ["1/2 hex"]


def test_a_standoffs_turn_from_its_od_stays_whatever_the_panel_sends(tmp_path):
    rows = bom_rows_with_shafts()
    rows["rows"].append(row(LIB, "v", LIBV, STANDOFF, "JFD", "Standoff", 4, "Aluminum - 6061",
                            configuration="Length=1.25+in;OD=0.3+in"))
    p = shaft_panel(tmp_path, [rows])
    a = p.call("GET", "/api/assembly", Q)[1]
    thin = {s["label"]: s for s in a["shafts"]}["3/8 round, 1.250 in"]
    assert thin["ops"] == [{"kind": "turn", "end": 0, "d": 0.3, "auto": True}]
    p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [], "shafts": [
        {"key": thin["key"], "qty": 4, "ops": [{"kind": "turn", "end": 0, "d": 0.1, "auto": True}]}]})
    assert p.shafts.all()[0]["ops"] == [{"kind": "turn", "end": 0, "d": 0.3, "auto": True}]


def test_a_shaft_whose_length_changed_in_cad_is_tagged(tmp_path):
    later = bom_rows_with_shafts()
    later["rows"][1] = row(LIB, "v", LIBV, HEX, "JFD", "Hex Shaft", 4, "Aluminum - 6061", configuration="Length=18.5+in")
    p = shaft_panel(tmp_path, [bom_rows_with_shafts(), later])
    a = p.call("GET", "/api/assembly", Q)[1]
    p.call("POST", "/api/send-many", body=send_body(a))
    p.clock.t += 10_000                                                       # the BOM is read again
    a = p.call("GET", "/api/assembly", {**Q, "fresh": "1"})[1]
    assert [g["label"] for g in a["shaftsGone"]] == ["1/2 hex, 18.250 in"] and a["shaftsGone"][0]["waiting"]
    assert {s["label"] for s in a["shafts"]} >= {"1/2 hex, 18.500 in"}
    shop = p.call("GET", "/api/shop", key=shop_key(KEY))[1]
    assert [s["changed"] for s in shop["shafts"]] == [True, False]


def test_the_shop_page_has_its_own_key(tmp_path):
    p = shaft_panel(tmp_path)
    a = p.call("GET", "/api/assembly", Q)[1]
    p.call("POST", "/api/send-many", body=send_body(a))
    shop = shop_key(KEY)
    assert shop != KEY and len(shop) == 32
    assert p.call("GET", "/api/shop", key=KEY)[0] == 403                     # the panel's key isn't the shop's
    assert p.call("GET", "/api/assembly", Q, key=shop)[0] == 403              # and the shop's opens nothing else
    assert p.app.handle("GET", "/shop", {"key": KEY}, {}).status == 403
    page = p.app.handle("GET", "/shop", {"key": shop}, {})
    assert page.status == 200 and b"/static/shop.js?v=" in page.body
    status, v = p.call("GET", "/api/shop", {"device": "phone-aaaa"}, key=shop)
    assert status == 200 and [s["stock"] for s in v["shafts"]] == ["1/2 hex", "3/8 round"]
    first = v["shafts"][0]
    assert first["steps"] == ["Turn both ends to Ø0.500 × 0.400", "Tap 10-32 both ends"] and first["rush"]
    assert first["url"].startswith("https://5940.onshape.com/documents/")
    assert p.call("POST", "/api/shop/start", body={"id": first["id"], "device": "phone-aaaa"}, key=shop)[0] == 200
    status, r = p.call("POST", "/api/shop/start", body={"id": first["id"], "device": "phone-bbbb"}, key=shop)
    assert (status, r) == (409, {"error": "Someone already started this", "kind": "taken"})
    assert p.call("POST", "/api/shop/done", body={"id": first["id"], "device": "x"}, key=shop)[0] == 400
    assert p.call("POST", "/api/shop/explode", body={"id": first["id"], "device": "phone-aaaa"}, key=shop)[0] == 404
    p.call("POST", "/api/shop/done", body={"id": first["id"], "device": "phone-aaaa"}, key=shop)
    a = p.call("GET", "/api/assembly", Q)[1]
    assert {s["label"]: s for s in a["shafts"]}["1/2 hex, 18.250 in"]["listed"]["chip"] == {"label": "Done", "status": "good"}


def test_shafts_off_means_no_shop_page_and_no_shaft_rows(tmp_path):
    p = shaft_panel(tmp_path)
    p.app = PanelApp(p.h.cfg, KEY, p.onshape, p.board, p.names, clock=p.clock, shafts=None)
    a = p.call("GET", "/api/assembly", Q)[1]
    assert "shafts" not in a and len(a["items"]) == 7
    assert p.app.handle("GET", "/shop", {"key": shop_key(KEY)}, {}).status == 404


def test_the_shafts_config_is_checked():
    data = copy.deepcopy(RAW)
    data["shafts"]["tabs"]["nope"] = {"stock": "1 inch hex", "length_in": 1.0}
    data["shafts"]["tap"] = "<b>"
    with pytest.raises(ConfigError) as e:
        parse_config(data, root=REPO_ROOT)
    text = str(e.value)
    assert "shafts.tabs.nope: the key must be the library tab's element id" in text
    assert "'1 inch hex' is not one of" in text and "shafts.tap" in text
    assert CFG.shafts.tabs[STANDOFF].od_in == 0.375 and CFG.shafts.groove_depth_in == 0.02


def test_the_qr_page_to_print_keeps_the_link_out_of_the_terminal(tmp_path, capsys):
    pytest.importorskip("segno")
    from autocam_service.cli import main
    env = tmp_path / ".env"
    env.write_text(f"AUTOCAM_PANEL_KEY={KEY}\n", encoding="utf-8")
    out = tmp_path / "shop-qr.html"
    assert main(["--env-file", str(env), "shop-qr", "--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert "<h1>Shafts</h1>" in page and "<svg" in page
    printed = capsys.readouterr().out
    assert shop_key(KEY) not in printed and "Wrote" in printed


# ---------------------------------------------------------------- bought and printed parts aren't plates

VENDOR, TEAMDOC = "8293a9c30ec90f3f3078fde2", "1" * 24


def owner_panel(tmp_path):
    bom = {"headers": HEADERS, "rows": [
        row(D, "w", W, E, "JHD", "P-2014 gusset", 4, "Aluminum - 6061"),
        row(VENDOR, "v", "6" * 24, "7" * 24, "JQA", 'Sushi Roller (60A, 3/8" Hex Bore)', 9, "Polycarbonate"),
        row(TEAMDOC, "v", "6" * 24, "8" * 24, "JQB", "P-3001 side plate", 2, "Aluminum - 6061"),
        row(LIB, "v", LIBV, PRINTED, "JFD", "PLA-STNDF-0.2403", 1, "Aluminum - 6061", configuration="Length=0.006+meter")]}
    mkcad = {"owner": {"id": "594ae8f3feede50f8a5cb929", "type": 0, "name": "Milkenknights Test Account"}, "public": True}
    ours = {"owner": {"id": CFG.panel.company_id, "type": 1, "name": "Team 5940 BREAD"}, "public": False}
    transport = FakeTransport([("GET", "/bom", [resp(body=bom)] * 3),
                               ("GET", "/elements", [resp(body=[{"id": E, "name": "Intake", "elementType": "ASSEMBLY"}])] * 3),
                               ("GET", f"/documents/{VENDOR}", [resp(body=mkcad)]),
                               ("GET", f"/documents/{TEAMDOC}", [resp(body=ours)])])
    p = Panel(tmp_path, transport=transport)
    p.app = PanelApp(p.h.cfg, KEY, p.onshape, p.board, p.names, clock=p.clock,
                     shafts=ShaftList(p.h.cfg.paths.state / "shafts.json", now=Clock()))
    return p


def test_bought_parts_and_library_prints_arent_offered_as_plates(tmp_path):
    p = owner_panel(tmp_path)
    a = p.call("GET", "/api/assembly", Q)[1]
    items = {i["name"]: (i["sendable"], i["why"], i["notOurs"]) for i in a["items"]}
    assert items == {"P-2014 gusset": (True, "", False), "P-3001 side plate": (True, "", False),
                     'Sushi Roller (60A, 3/8" Hex Bore)': (False, "Bought part", True),
                     "PLA-STNDF-0.2403 (Length: 0.2362 in)": (False, "Library part", True)}
    p.call("GET", "/api/assembly", Q)                                          # owners are read once
    assert [u for u in p.onshape_calls() if u.startswith("/documents/") and "/elements" not in u] == [
        f"/documents/{VENDOR}", f"/documents/{TEAMDOC}"]
    assert VENDOR in (p.h.cfg.paths.cache / "document_owners.json").read_text()   # and kept for good
    keys = {i["name"]: i["key"] for i in a["items"]}
    r = p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [
        {"key": keys['Sushi Roller (60A, 3/8" Hex Bore)'], "qty": 9}, {"key": keys["PLA-STNDF-0.2403 (Length: 0.2362 in)"], "qty": 1}]})[1]
    assert [x["why"] for x in r["results"]] == ["is a bought part", "is in the common parts library (printed or bought)"]


def test_the_pages_own_refresh_never_looks_up_owners(tmp_path):
    p = owner_panel(tmp_path)
    p.call("GET", "/api/assembly", Q)                    # the BOM read (fresh owners are looked up here) ...
    before = len(p.onshape_calls())
    p.call("GET", "/api/assembly", {**Q, "cached": "1"})
    assert len(p.onshape_calls()) == before


# ---------------------------------------------------------------- a part clicked in the assembly

def test_a_part_clicked_in_the_assembly_finds_its_row(tmp_path):
    definition = {"rootAssembly": {"instances": [
        {"id": "i1", "type": "Part", "documentId": D, "elementId": E, "partId": "JHD", "configuration": "default"},
        {"id": "IfBvAf3KuZudYUjEe", "type": "Part", "documentId": LIB, "elementId": STANDOFF, "partId": "JFD",
         "configuration": "Auto_Name_Part=true;Length=0.0635+meter;OD=0.009525+meter"}]},
        "subAssemblies": [{"instances": [{"id": "Mpp/5tF1I3bXlPgoO", "type": "Assembly"},
                                         {"id": "j2", "type": "Part", "documentId": LIB, "elementId": HEX, "partId": "JFD",
                                          "configuration": "Auto_Name_Part=true;Length=0.2794+meter"}]}]}
    transport = FakeTransport([("GET", "/bom", [resp(body=bom_rows_with_shafts())] * 3),
                               ("GET", "/elements", [resp(body=[{"id": E, "name": "Intake", "elementType": "ASSEMBLY"}])] * 3),
                               ("GET", "/assemblies/", [resp(body=definition)] * 3)])
    p = Panel(tmp_path, transport=transport)
    p.app = PanelApp(p.h.cfg, KEY, p.onshape, p.board, p.names, clock=p.clock,
                     shafts=ShaftList(p.h.cfg.paths.state / "shafts.json", now=Clock()))
    a = p.call("GET", "/api/assembly", Q)[1]
    shafts = {s["label"]: s["key"] for s in a["shafts"]}
    which = lambda inst: p.call("GET", "/api/which-row", {**Q, "inst": inst})[1]["key"]   # noqa: E731
    assert which("IfBvAf3KuZudYUjEe") == shafts["3/8 round, 2.500 in"]     # Auto_Name_Part and meters: still found
    assert which("j2") == shafts["1/2 hex, 11.000 in"]                     # in a subassembly
    assert which("i1") == next(i["key"] for i in a["items"] if i["name"] == "P-2014 gusset")
    assert which("Mpp/5tF1I3bXlPgoO") == "" and which("<script>") == ""
    def reads():
        return len([u for u in p.onshape_calls() if u.startswith("/assemblies/") and "/bom" not in u])
    assert reads() == 1                      # one read for all of these (an unknown id within the minute: no new read)
    p.clock.t += 120
    assert which("Inew0ne") == "" and reads() == 2       # a part it doesn't know, later: read again once


# ---------------------------------------------------------------- a note on a shaft

def test_a_note_goes_from_the_panel_to_the_card(tmp_path):
    p = shaft_panel(tmp_path)
    a = p.call("GET", "/api/assembly", Q)[1]
    body = send_body(a)
    body["shafts"][0]["note"] = "Leave the ends\nsquare, it's a spacer too"
    p.call("POST", "/api/send-many", body=body)
    card = p.call("GET", "/api/shop", key=shop_key(KEY))[1]["shafts"][0]
    assert card["note"] == "Leave the ends square, it's a spacer too"         # one line
    sid = card["id"]
    p.call("POST", "/api/shaft", body={"id": sid, "qty": 4, "ends": ["10-32", "10-32"], "ops": [], "note": "x" * 300})
    assert p.call("GET", "/api/shop", key=shop_key(KEY))[1]["shafts"][0]["note"] == "x" * 200
