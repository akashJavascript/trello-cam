"""Assemblies AutoCAM keeps an eye on without anyone opening the panel (panel/assemblies.py, 2026-11-02): the shop's
lists marked when the assembly changes, and cards for parts from another document's saved version following the
assembly to a newer version. Offline: scripted Onshape, fake Trello."""

from autocam_service.bought import PartList
from autocam_service.panel.api import PanelApp
from autocam_service.panel.assemblies import AssemblyWatch, assembly_of
from autocam_service.shafts import ShaftList
from autocam_service.tracker.base import Card
from fakeonshape import FakeTransport, resp
from onshape_body import plate_body, rectangle
from test_bought import WHEEL, bought_bom, VENDOR
from test_panel import KEY, Q, Panel, card_in
from test_panel_more import HEADERS, SHEET, row
from test_runner_e2e import D, E, W
from test_shafts import HEX, LIB, LIBV, Clock

TEAMDOC, V1, V2, EL = "1" * 24, "6" * 24, "5" * 24, "8" * 24
H = 3600.0
OURS = {"owner": {"id": "652459d6412ce911b57afb73", "type": 1, "name": "Team 5940 BREAD"}, "public": False}
MKCAD = {"owner": {"id": "594ae8f3feede50f8a5cb929", "type": 0}, "public": True}


def watched_panel(tmp_path, boms, extra_routes=(), cards=()):
    transport = FakeTransport([("GET", "/bom", [resp(body=b) for b in boms] + [resp(body=boms[-1])] * 5),
                               ("GET", "/elements", [resp(body=[{"id": E, "name": "Shooter", "elementType": "ASSEMBLY"}])] * 5),
                               ("GET", f"/documents/{VENDOR}", [resp(body=MKCAD)] * 2),
                               ("GET", f"/documents/{TEAMDOC}", [resp(body=OURS)] * 2), *extra_routes])
    p = Panel(tmp_path, transport=transport, cards=cards)
    p.shafts = ShaftList(p.h.cfg.paths.state / "shafts.json", now=Clock())
    p.parts = PartList(p.h.cfg.paths.state / "bought_parts.json", now=Clock())
    p.watch = AssemblyWatch(p.onshape, p.board, p.h.cfg, p.h.cfg.paths.state / "panel_assemblies.json",
                            shafts=p.shafts, parts=p.parts)
    p.app = PanelApp(p.h.cfg, KEY, p.onshape, p.board, p.names, clock=p.clock, shafts=p.shafts, parts=p.parts,
                     assemblies=p.watch)
    return p


def boms_read(p):
    return len([u for _, u, _, _ in p.transport.sent if u.endswith("/bom") or "/bom?" in u])


def with_shaft(bom, length="Length=0.46355+meter", wheels=4):
    bom = {"headers": bom["headers"], "rows": [dict(r) for r in bom["rows"]]}
    bom["rows"][1] = dict(bom["rows"][1], headerIdToValue={**bom["rows"][1]["headerIdToValue"], "q": float(wheels)})
    bom["rows"].append(row(LIB, "v", LIBV, HEX, "JFD", "Hex Shaft", 4, "Aluminum - 6061", configuration=length))
    return bom


def test_the_shop_lists_are_marked_without_anyone_opening_the_panel(tmp_path):
    then = with_shaft(bought_bom())
    now = with_shaft(bought_bom(), length="Length=0.5+meter", wheels=6)       # the shaft got longer, 6 wheels now
    p = watched_panel(tmp_path, [then, now])
    a = p.call("GET", "/api/assembly", Q)[1]
    shaft = a["shafts"][0]
    wheel = {b["name"]: b for b in a["bought"]}[WHEEL]
    p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "server": "https://5940.onshape.com", "items": [],
                                           "shafts": [{"key": shaft["key"], "qty": 4, "ends": ["10-32", "10-32"]}],
                                           "bought": [{"key": wheel["key"], "qty": 4}]})
    assert p.watch.wanted() == {(D, W)}                                        # Onshape's webhook covers it too
    before = boms_read(p)
    p.clock.t += 700
    assert p.watch.tick(now=1000.0) == 0 and boms_read(p) == before           # first seen: it was just read
    p.watch.tick({(D, W, E)}, now=1000.0 + 600)                                # Onshape: it changed...
    assert boms_read(p) == before                                              # ...but read at most once an hour
    p.watch.tick(now=1000.0 + H + 1)
    assert boms_read(p) == before + 1
    assert p.shafts.all()[0]["changed"] is True                               # "Changed in CAD" on the phone
    assert p.parts.all()[0]["cad_now"] == 6                                   # "CAD has 6"
    p.watch.tick(now=1000.0 + 2 * H)
    assert boms_read(p) == before + 1                                          # nothing new reported: no read


def test_with_no_change_reported_it_reads_on_the_slow_schedule(tmp_path):
    p = watched_panel(tmp_path, [with_shaft(bought_bom())])
    a = p.call("GET", "/api/assembly", Q)[1]
    p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [],
                                           "bought": [{"key": a["bought"][0]["key"], "qty": 1}]})
    p.watch.tick(now=0.0)
    before = boms_read(p)
    p.clock.t += 700
    p.watch.tick(now=11 * H)
    assert boms_read(p) == before
    p.watch.tick(now=12 * H + 1)                                               # [panel] change_check_hours
    assert boms_read(p) == before + 1
    p.parts.remove(p.parts.all()[0]["id"])
    assert p.watch.wanted() == set()                                           # nothing open: not watched


# ---------------------------------------------------------------- a part from another document's saved version

def plate_in(version, qty=2):
    return {"headers": HEADERS, "rows": [row(D, "w", W, E, "JHD", "P-2014 gusset", 4, "Aluminum - 6061"),
                                         row(TEAMDOC, "v", version, EL, "JQB", "P-3001 side plate", qty, "Aluminum - 6061")]}


def version_panel(tmp_path, then, now):
    shapes = [("GET", f"/v/{V1}/e/{EL}/partid/JQB/bodydetails", [resp(body=then)]),
              ("GET", f"/v/{V2}/e/{EL}/partid/JQB/bodydetails", [resp(body=now)])]
    p = watched_panel(tmp_path, [plate_in(V1), plate_in(V2)], shapes,
                      cards=[Card(SHEET, "6061 - 25 min - r034 S1", "", "ready_to_cut", "u")])
    a = p.call("GET", "/api/assembly", Q)[1]
    key = {i["name"]: i["key"] for i in a["items"]}["P-3001 side plate"]
    r = p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "server": "https://5940.onshape.com",
                                               "items": [{"key": key, "qty": 2}]})[1]
    assert r["results"][0]["sent"]
    [card] = [c for c in card_in(p, "ready_for_cam") if c.name.startswith("P-3001")]
    assert f"/documents/{TEAMDOC}/v/{V1}/e/{EL}" in card.desc
    p.watch.tick(now=0.0)
    p.clock.t += 700
    return p, card


def test_a_card_follows_its_assembly_to_a_newer_version(tmp_path):
    then = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25)], pid="JQB")
    now = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25), (3, 1, 0.25)], pid="JQB")
    p, card = version_panel(tmp_path, then, now)
    assert p.watch.tick(now=13 * H) == 1
    desc = p.t.get_card(card.id).desc
    assert f"/documents/{TEAMDOC}/v/{V2}/e/{EL}" in desc and V1 not in desc   # the next run nests the new one
    [said] = p.t.comments_on(card.id)
    assert said == "Shooter uses a newer version of this part now: 1 hole added. This card links to that version now."
    assert p.watch.tick(now=26 * H) == 0 and len(p.t.comments_on(card.id)) == 1   # once per version


def test_a_card_on_a_sheet_gets_cad_changed_when_its_new_version_differs(tmp_path):
    then = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25)], pid="JQB")
    now = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.5)], pid="JQB")
    p, card = version_panel(tmp_path, then, now)
    p.t.move(card.id, p.h.cfg.trello.targets["part_nested"])
    p.h.store.register_sheet(SHEET, "r034", True, [card.id], job="r034-al6061", material="al6061", thickness_in=0.125,
                             index=1, url="u", extra={"label": "r034 S1"})
    assert p.watch.tick(now=13 * H) == 1
    said = " ".join(p.t.comments_on(SHEET))
    assert "P-3001 side plate was changed in Onshape after this sheet was CAM'd (1 hole added, 1 hole removed)" in said
    assert p.h.cfg.labels.cad_changed in p.t.get_card(card.id).labels


def test_a_cut_card_or_one_relinked_by_hand_is_left_alone(tmp_path):
    body = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25)], pid="JQB")
    p, card = version_panel(tmp_path, body, body)
    desc = p.t.get_card(card.id).desc.replace(V1, "4" * 24)                   # someone pointed it elsewhere
    p.t.update_card(card.id, card.name, desc)
    assert p.watch.tick(now=13 * H) == 0 and p.t.get_card(card.id).desc == desc
    (tmp_path / "cut").mkdir()
    p2, card2 = version_panel(tmp_path / "cut", body, body)
    p2.t.move(card2.id, p2.h.cfg.trello.targets["part_cut"])
    assert p2.watch.tick(now=13 * H) == 0 and V1 in p2.t.get_card(card2.id).desc
    assert p2.watch.wanted() == set()                                          # forgotten once cut


def test_an_items_assembly_link():
    assert assembly_of(f"https://5940.onshape.com/documents/{D}/w/{W}/e/{E}") == (D, "w", W, E)
    assert assembly_of("https://example.com/nope") is None


def test_the_background_loop_runs_it_before_reported_changes_are_forgotten(tmp_path):
    from autocam_service.panel.precut import PrecutWatch
    calls = []

    class Watch:
        def wanted(self):
            return {(D, W)}

        def tick(self, told, now):
            calls.append((told, now))
    p = Panel(tmp_path)
    PrecutWatch(p.onshape, p.board, p.h.store, p.t, assemblies=Watch()).tick(now=5.0)
    assert calls == [(set(), 5.0)]
