"""The Onshape panel (panel/), offline: scripted Onshape, fake Trello, no sockets. The cards it makes are read back
by the same parser and runner as typed ones."""

import copy
import json
import logging
from dataclasses import replace

import pytest

from autocam_service.cards import PartRequest, parse_card
from autocam_service.config import ConfigError, parse_config
from autocam_service.fits import parse_fit
from autocam_service.materials import resolve_material
from autocam_service.onshape.client import OnshapeClient
from autocam_service.onshape.export import pick_part
from autocam_service.onshape.ledger import PANEL_RUN
from autocam_service.panel.api import Names, PanelApp
from autocam_service.panel.board import PanelBoard, read_part_card, with_qty
from autocam_service.panel.onshape_reads import (NoAccess, OutOfCalls, PanelOnshape, occurrence_counts,
                                                 pick_assemblies)
from autocam_service.panel.server import Tunnel
from autocam_service.tracker.base import Card
from fakeonshape import FakeTransport, resp
from fakeworker import run_fake_worker
from test_runner_e2e import D, E, RAW, REPO_ROOT, STUDIO, SYS, W, Harness

KEY = "k" * 32
Q = {"d": D, "wvm": "w", "w": W, "e": E}
AL6061 = {"id": "Aluminum - 6061", "displayName": "Aluminum - 6061", "libraryName": "Onshape Material Library"}
AL5052 = {"id": "Aluminum - 5052", "displayName": "Aluminum - 5052", "libraryName": "Onshape Material Library"}
PARTS = [
    {"name": "P-2014 gusset", "partId": "JHD", "bodyType": "solid", "material": AL6061},
    {"name": "P-2015 window", "partId": "JHG", "bodyType": "solid", "material": None},
    {"name": "P-2016 plate", "partId": "JHK", "bodyType": "solid", "material": {"displayName": "Polycarbonate"}},
    {"name": "P-10 sketch", "partId": "JHS", "bodyType": "sheet"},
]
DEFINITION = {
    "rootAssembly": {
        "instances": [{"id": "i1", "type": "Part", "documentId": D, "elementId": E, "partId": "JHD"},
                      {"id": "s1", "type": "Assembly", "documentId": D, "elementId": "f" * 24},
                      {"id": "i9", "type": "Part", "documentId": D, "elementId": E, "partId": "JHD", "suppressed": True}],
        "occurrences": [{"path": ["i1"]}, {"path": ["s1"]}, {"path": ["s1", "j1"]}, {"path": ["s1", "j2"]},
                        {"path": ["i9"]}]},
    "subAssemblies": [{"instances": [{"id": "j1", "type": "Part", "documentId": D, "elementId": E, "partId": "JHD"},
                                     {"id": "j2", "type": "Part", "documentId": D, "elementId": E, "partId": "JHG"}]}],
}
ELEMENTS = [{"id": "1" * 24, "name": "Intake", "elementType": "ASSEMBLY"},
            {"id": "2" * 24, "name": "Robot", "elementType": "ASSEMBLY"}]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def routes(**extra):
    r = {"parts": [resp(body=PARTS)] * 5, "elements": [resp(body=ELEMENTS)],
         "assemblies": [resp(body=DEFINITION), resp(body={"rootAssembly": {"instances": [], "occurrences": []}})],
         "metadata": [resp(body={})]}
    r.update(extra)
    return FakeTransport([("GET", "/parts/", r["parts"]), ("GET", "/elements", r["elements"]),
                          ("GET", "/assemblies/", r["assemblies"]), ("POST", "/metadata/", r["metadata"])])


class Panel:
    def __init__(self, tmp_path, transport=None, cards=(), per_day=40, **cfg):
        self.h = Harness(tmp_path, card_list=[Card(SYS, "System", "", "control", "https://trello.example/c/sys"),
                                              *cards], **cfg)
        self.clock = Clock()
        self.transport = transport or routes()
        c = self.h.cfg

        def client_for(n):
            return OnshapeClient(base_url=c.onshape.base_url, access_key="AK", secret_key="SK", ledger=self.h.ledger,
                                 transport=self.transport, run_id=PANEL_RUN, max_calls=n, retry_after_max_wait_s=0,
                                 sleep=lambda s: None)

        self.onshape = PanelOnshape(ledger=self.h.ledger, client_for=client_for, cache_root=c.paths.cache,
                                    calls_per_day=per_day, calls_per_year=100, year_start="01-01",
                                    parts_cache_s=600, assembly_cache_s=1800, max_assemblies=4, clock=self.clock)
        self.board = PanelBoard(self.h.tracker, c, self.h.store, clock=self.clock)
        self.names = Names(c.paths.state / "panel_users.json")
        self.app = PanelApp(c, KEY, self.onshape, self.board, self.names, clock=self.clock)

    @property
    def t(self):
        return self.h.tracker

    def call(self, method, path, q=None, body=None, key=KEY):
        headers = {"x-autocam-key": key} if key else {}
        r = self.app.handle(method, path, dict(q or {}), headers, json.dumps(body).encode() if body is not None else b"")
        data = json.loads(r.body) if r.content_type.startswith("application/json") else r.body
        return r.status, data

    def send(self, **kw):
        body = {**Q, "p": "JHD", "qty": 2, "name": "Akash", "u": "user1", "server": "https://cad.onshape.com", **kw}
        return self.call("POST", "/api/send", body=body)

    def onshape_calls(self):
        return [u.split("/api/v10")[1].split("?")[0] for _, u, _, _ in self.transport.sent]


def card_in(p, list_key):
    return [c for c in p.t.cards.values() if c.list_key == list_key and c.id != SYS]


# ---------------------------------------------------------------- Onshape reads

def test_occurrences_are_counted_into_subassemblies_and_suppressed_ones_left_out():
    counts = occurrence_counts(DEFINITION)
    assert counts == {(D, E, "JHD"): 2, (D, E, "JHG"): 1, (D, E, "JHD", ""): 2, (D, E, "JHG", ""): 1}


def test_assemblies_named_like_the_whole_robot_are_counted_first():
    els = [{"id": "a", "name": "Intake", "elementType": "ASSEMBLY"}, {"id": "b", "name": "Part Studio 1",
           "elementType": "PARTSTUDIO"}, {"id": "c", "name": "Full Robot", "elementType": "ASSEMBLY"}]
    assert [e["id"] for e in pick_assemblies(els, 4)] == ["c", "a"]
    assert [e["id"] for e in pick_assemblies(els, 1)] == ["c"]


def test_a_part_studios_parts_are_one_call_kept_for_a_while(tmp_path):
    p = Panel(tmp_path)
    p.onshape.parts(D, "w", W, E)
    p.onshape.parts(D, "w", W, E)
    p.onshape.parts(D, "w", W, E, fresh=True)        # a Refresh right away uses the read it just made
    assert p.onshape_calls() == [f"/parts/d/{D}/w/{W}/e/{E}"]
    p.clock.t += 601
    p.onshape.parts(D, "w", W, E)
    assert len(p.onshape_calls()) == 2
    assert p.h.ledger.day_count(panel=True) == 2 and p.h.ledger.month_count(panel=False) == 0


def test_the_panel_has_its_own_call_limit_and_runs_dont_count_it(tmp_path):
    p = Panel(tmp_path, per_day=1)
    p.onshape.parts(D, "w", W, E)
    with pytest.raises(OutOfCalls):
        p.onshape.parts(D, "w", "9" * 24, E)
    assert p.h.ledger.month_count(panel=True) == 1 and p.h.ledger.month_count(panel=False) == 0
    assert p.h.ledger.month_count() == 1
    status, data = p.call("GET", "/api/studio", {**Q, "w": "9" * 24})
    assert status == 429 and data["kind"] == "calls"


def test_a_document_the_teams_account_cant_open(tmp_path):
    p = Panel(tmp_path, transport=routes(parts=[resp(403, {"message": "no"})] * 2))
    with pytest.raises(NoAccess):
        p.onshape.parts(D, "w", W, E)
    status, data = p.call("GET", "/api/studio", {**Q, "fresh": "1"})
    assert status == 403 and data["kind"] == "noaccess"


def test_quantities_come_from_the_documents_assemblies_once_for_every_part(tmp_path):
    p = Panel(tmp_path)
    status, data = p.call("GET", "/api/qty", {**Q, "p": "JHD"})
    assert status == 200 and data["assemblies"] == [{"name": "Robot", "count": 2}]
    assert data["counted"] == 2 and data["total"] == 2
    status, data = p.call("GET", "/api/qty", {**Q, "p": "JHG"})
    assert data["assemblies"] == [{"name": "Robot", "count": 1}]
    assert p.onshape_calls() == [f"/documents/d/{D}/w/{W}/elements", f"/assemblies/d/{D}/w/{W}/e/{'2' * 24}",
                                 f"/assemblies/d/{D}/w/{W}/e/{'1' * 24}"]


def test_quantities_wait_when_the_panel_is_out_of_calls(tmp_path):
    p = Panel(tmp_path, per_day=1)
    status, data = p.call("GET", "/api/qty", {**Q, "p": "JHD"})
    assert status == 200 and data["assemblies"] == [] and "used its Onshape calls" in data["note"]
    assert p.onshape_calls() == []


# ---------------------------------------------------------------- the page and the key

def test_every_request_needs_the_key(tmp_path):
    p = Panel(tmp_path)
    assert p.call("GET", "/api/studio", Q, key="")[0] == 403
    assert p.call("GET", "/api/studio", Q, key="x" * 32)[0] == 403
    r = p.app.handle("GET", "/panel", {"key": "nope"}, {})
    assert r.status == 403
    r = p.app.handle("GET", "/panel", {"key": KEY, "documentId": D}, {})
    assert r.status == 200 and b"/static/panel.js" in r.body
    assert "frame-ancestors https://*.onshape.com" in r.headers["Content-Security-Policy"]
    assert r.headers["Referrer-Policy"] == "no-referrer"          # the key is in the page's address
    assert p.app.handle("GET", "/static/panel.js", {}, {}).status == 200
    for bad in ("../api.py", "..%2fapi.py", "index.html", ".hidden.js", "nope.js"):
        assert p.app.handle("GET", "/static/" + bad, {}, {}).status == 404
    assert p.onshape_calls() == []


def test_the_studio_lists_solid_parts_with_their_material_and_status(tmp_path):
    sent = Card("c" * 24, "P-2014 gusset", f"{STUDIO.replace('/v/', '/w/')}\nQty: 3", "ready_for_cam", "https://t/c1")
    p = Panel(tmp_path, cards=[sent])
    status, data = p.call("GET", "/api/studio", {**Q, "u": "user1"})
    assert status == 200 and data["writable"] and data["user"] == ""
    rows = {r["name"]: r for r in data["parts"]}
    assert list(rows) == ["P-2014 gusset", "P-2015 window", "P-2016 plate"]       # no sheet bodies
    assert rows["P-2014 gusset"]["chip"] == {"status": "queued", "label": "Waiting to nest"}
    assert rows["P-2014 gusset"]["material"]["key"] == "al6061"
    assert rows["P-2015 window"]["material"] is None and rows["P-2015 window"]["chip"]["label"] == "Not sent"
    assert {m["value"]: m["onshape"] for m in data["materials"]} == {
        "al6061": "Aluminum - 6061", "al5052": "Aluminum - 5052", "pc_clear": "Polycarbonate",
        "pc_smoked": "Polycarbonate"}


# ---------------------------------------------------------------- sending

def test_a_sent_card_is_one_the_runner_reads(tmp_path):
    p = Panel(tmp_path)
    status, data = p.send(qty=4, tabs=False, face=True, rush=True, notes="deburr it\nQty: 9 please")
    assert status == 200 and data["list"] == "Ready for CAM"
    [card] = card_in(p, "ready_for_cam")
    assert card.name == "P-2014 gusset"
    assert card.desc.split("\n") == [f"https://cad.onshape.com/documents/{D}/w/{W}/e/{E}", "Qty: 4", "Part ID: JHD",
                                     "Sent from the Onshape panel by Akash", "Note: deburr it Qty: 9 please"]
    assert set(card.labels) == {"Face", "Rush"}
    assert p.t.checklists[(card.id, "Nest")] == [["Nest this part", True]]
    assert p.t.checklists[(card.id, "Tabs")] == [["Hold it in with tabs", False]]
    req = parse_card(p.t.get_card(card.id))
    assert isinstance(req, PartRequest)
    assert (req.name, req.qty, req.part_id, req.face, req.link.wvm) == ("P-2014 gusset", 4, "JHD", True, "w")
    p.h.runner.ready_cards()                                     # the service's tick adds no second box
    assert sum(1 for c in p.t.get_card(card.id).checks if c.checklist == "Nest") == 1
    assert p.names.get("user1") == "Akash"
    assert p.call("GET", "/api/studio", {**Q, "u": "user1"})[1]["user"] == "Akash"


def test_drafts_and_the_enterprise_host(tmp_path):
    p = Panel(tmp_path)
    status, data = p.send(drafts=True, server="https://5940.onshape.com")
    assert status == 200 and data["list"] == "Drafts"
    [card] = card_in(p, "inbox")
    assert card.desc.startswith(f"https://5940.onshape.com/documents/{D}/w/{W}/e/{E}")
    p.send(p="JHK", server="https://evil.example.com")
    [card] = card_in(p, "ready_for_cam")
    assert card.desc.startswith("https://cad.onshape.com/")      # not an Onshape host: the configured one


def test_a_part_that_already_has_a_card_isnt_sent_twice(tmp_path):
    p = Panel(tmp_path)
    p.send()
    status, data = p.send()
    assert status == 409 and data["kind"] == "duplicate" and data["card"]["status"] == "queued"
    assert len(card_in(p, "ready_for_cam")) == 1
    [card] = card_in(p, "ready_for_cam")
    status, data = p.call("POST", "/api/qty", body={**Q, "p": "JHD", "card": card.id, "qty": 6})
    assert status == 200 and not data["renest"] and data["card"]["qty"] == 6
    assert "Qty: 6" in p.t.get_card(card.id).desc and "Qty: 2" not in p.t.get_card(card.id).desc


def test_a_new_quantity_for_a_part_on_a_sheet_nests_it_again(tmp_path):
    p = Panel(tmp_path)
    p.send()
    [card] = card_in(p, "ready_for_cam")
    p.t.move(card.id, "nested")
    p.clock.t += 100
    status, data = p.call("POST", "/api/qty", body={**Q, "p": "JHD", "card": card.id, "qty": 3})
    assert status == 200 and data["renest"] and p.t.get_card(card.id).list_key == "ready_for_cam"
    p.t.move(card.id, "cut")
    status, _ = p.call("POST", "/api/qty", body={**Q, "p": "JHD", "card": card.id, "qty": 5})
    assert status == 409                                       # cut: send a new one instead
    assert p.send()[0] == 200                                  # ...which is allowed


def test_with_qty_replaces_every_quantity_line():
    desc = "x2\nhttps://cad.onshape.com/documents/aaa\nQuantity: 2\nnote"
    assert with_qty(desc, 5) == "https://cad.onshape.com/documents/aaa\nQty: 5\nnote"


def test_a_material_picked_in_the_panel_is_set_in_onshape(tmp_path):
    p = Panel(tmp_path)
    cached = p.h.cfg.paths.cache / "onshape" / "parts_old.json"
    cached.parent.mkdir(parents=True)
    cached.write_text(json.dumps([{"partId": "X", "material": AL5052}]))
    assert p.send(p="JHG")[0] == 400                          # no material: pick one
    p.call("GET", "/api/studio", Q)
    status, data = p.send(p="JHG", material="al5052")
    assert status == 200 and data["material"] == {"setInOnshape": True, "why": "", "line": ""}
    [(method, url, _, body)] = [s for s in p.transport.sent if s[0] == "POST"]
    assert url.endswith(f"/metadata/d/{D}/w/{W}/e/{E}/p/JHG")
    assert json.loads(body) == {"jsonType": "metadata-part", "partId": "JHG",
                                "properties": [{"propertyId": "57f3fb8efa3416c06701d615", "value": AL5052}]}
    [card] = card_in(p, "ready_for_cam")
    assert "Material:" not in card.desc


def test_if_onshape_wont_take_the_material_the_card_carries_it(tmp_path):
    p = Panel(tmp_path, transport=routes(metadata=[resp(403, {"message": "read only key"})]))
    status, data = p.send(p="JHG", material="pc_smoked")       # copied from this studio's polycarbonate part
    assert status == 200 and not data["material"]["setInOnshape"] and data["material"]["line"] == "PC smoked"
    [card] = card_in(p, "ready_for_cam")
    assert "Material: PC smoked" in card.desc and "Smoked" in card.labels
    req = parse_card(p.t.get_card(card.id))
    choice = resolve_material(None, req.material_hint, req.smoked, p.h.cfg.onshape.material_map, p.h.cfg.materials)
    assert choice.key == "pc_smoked"


def test_a_version_cant_be_changed_so_the_material_goes_on_the_card(tmp_path):
    p = Panel(tmp_path)
    status, data = p.send(wvm="v", p="JHG", material="al6061")
    assert status == 200 and data["material"]["line"] == "6061" and "version" in data["material"]["why"]
    assert not [s for s in p.transport.sent if s[0] == "POST"]


def test_fits_from_the_panel(tmp_path):
    p = Panel(tmp_path)
    assert p.send(fit={"kind": "hole", "size": "big"})[0] == 400
    assert p.send(nest=False, fit={"kind": "outline"})[0] == 400       # a fit can't be placed by hand
    status, _ = p.send(tabs=False, fit={"kind": "hole", "size": "1-1/8"})
    assert status == 200
    [card] = card_in(p, "ready_for_cam")
    assert "Fit: 1.125 hole" in card.desc and parse_fit(card.desc) == ("hole", 1.125)
    assert p.t.checklists[(card.id, "Tabs")] == [["Hold it in with tabs", True]]   # a fitted part is tabbed


def test_too_many_sends_in_a_minute(tmp_path):
    p = Panel(tmp_path, panel__sends_per_minute=1)
    assert p.send()[0] == 200
    assert p.send(p="JHK")[0] == 429
    p.clock.t += 61
    assert p.send(p="JHK")[0] == 200


def test_a_name_is_needed(tmp_path):
    p = Panel(tmp_path)
    status, data = p.send(name="  \n ")
    assert status == 400 and "name" in data["error"]


# ---------------------------------------------------------------- status

def test_a_sent_back_card_shows_why_and_is_made_over(tmp_path):
    back = Card("b" * 24, "P-2014 gusset", f"{STUDIO}\nQty: 1", "needs_fixing", "https://t/back", ("Face",))
    p = Panel(tmp_path, cards=[back])
    p.t.comment(back.id, "Not queued:\n- 0.210 in thick isn't a stock size.\n\nFix it, then move this card back to "
                         "Ready for CAM. (Card format: ...)")
    status, data = p.call("GET", "/api/part", {**Q, "p": "JHD"})
    assert data["card"]["status"] == "back" and data["card"]["reason"] == ("0.210 in thick isn't a "
                                                                           "stock size.")
    assert p.send()[0] == 409                                  # not a second card...
    status, data = p.send(card=back.id, qty=3)                 # ...but this one, made over
    assert status == 200
    card = p.t.get_card(back.id)
    assert card.list_key == "ready_for_cam" and "Face" not in card.labels and "Qty: 3" in card.desc
    assert len(card_in(p, "ready_for_cam")) == 1


def test_status_follows_the_sheet_to_cut(tmp_path):
    sheet = "e" * 24
    on = Card("a" * 24, "P-2014 gusset", f"{STUDIO}\nQty: 2", "nested", "https://t/on")
    p = Panel(tmp_path, cards=[on, Card(sheet, "6061 ... r003 S1", "", "sheet_review", "https://t/s1")])
    p.h.store.register_sheet(sheet, "r003", True, [on.id], job="r003-al6061", material="al6061", thickness_in=0.125,
                             index=1, url="https://t/s1", extra={"label": "r003 S1"})

    def steps():
        p.clock.t += 31
        d = p.call("GET", "/api/part", {**Q, "p": "JHD"})[1]["card"]
        return d["label"], [s["state"] for s in d["steps"]]

    assert steps() == ("On r003 S1", ["done", "done", "now", "todo"])
    p.t._move(sheet, "ready_to_cut")              # a person moves it there, never AutoCAM
    assert steps() == ("On r003 S1", ["done", "done", "done", "now"])
    p.h.store.mark_sheet_cut(sheet)
    assert steps() == ("On r003 S1", ["done", "done", "done", "done"])
    att = p.t.attach_file(sheet, "r003_S1.png", b"PNG!", "image/png")
    p.t.downloads[att] = b"PNG!"
    assert p.app.handle("GET", "/api/preview", {"key": KEY, "sheet": sheet}, {}).body == b"PNG!"
    assert p.app.handle("GET", "/api/preview", {"key": KEY, "sheet": "f" * 24}, {}).status == 404
    assert p.app.handle("GET", "/api/preview", {"key": "", "sheet": sheet}, {}).status == 403
    assert att


def test_a_card_matches_by_part_id_before_name(tmp_path):
    renamed = Card("a" * 24, "old name", f"{STUDIO}\nQty: 2\nPart ID: JHD", "ready_for_cam", "https://t/a")
    other = Card("b" * 24, "P-2015 window", f"{STUDIO}\nQty: 1\nPart ID: ZZZ", "ready_for_cam", "https://t/b")
    p = Panel(tmp_path, cards=[renamed, other])
    rows = {r["name"]: r["chip"]["status"] for r in p.call("GET", "/api/studio", Q)[1]["parts"]}
    assert rows == {"P-2014 gusset": "queued", "P-2015 window": "none", "P-2016 plate": "none"}


def test_selection_messages_are_logged_a_few_a_day(tmp_path, caplog):
    p = Panel(tmp_path)
    with caplog.at_level(logging.INFO, logger="autocam.panel"):
        for _ in range(35):
            p.call("POST", "/api/selection", body={"message": {"messageName": "SELECTION", "selections": []}})
    assert sum("selection message" in r.message for r in caplog.records) == 30


# ---------------------------------------------------------------- the card format and the runner

def test_part_id_lines():
    card = Card("c1", "renamed", f"{STUDIO}\nQty: 1\nPart ID: JHD", "ready_for_cam", "u")
    assert parse_card(card).part_id == "JHD"
    two = replace(card, desc=card.desc + "\nPart ID: JHG")
    assert "Two different Part ID lines" in parse_card(two).problems[0]
    parts = [{"name": "new name", "partId": "JHD", "bodyType": "solid"}, {"name": "renamed", "partId": "JHX"}]
    assert pick_part(parts, "renamed", "JHD")["partId"] == "JHD"       # the id wins over the name
    assert pick_part(parts, "renamed", "GONE")["partId"] == "JHX"      # an id that's gone: by name, as before


def test_a_material_line_covers_a_linked_part_with_none_in_onshape(tmp_path):
    bare = [{"name": "hood_gusset", "partId": "JHD", "bodyType": "solid"}]
    transport = FakeTransport([
        ("GET", "/parts/", [resp(body=bare)]),
        ("POST", "/translations", [resp(body={"id": "T1"})]),
        ("GET", "/translations/T1", [resp(body={"requestState": "DONE", "resultExternalDataIds": ["F1"]})]),
        ("GET", "/externaldata/F1", [resp(200, b"ISO-10303-21; gusset")]),
    ])
    h = Harness(tmp_path, transport=transport, card_list=[
        Card(SYS, "System", "", "control", "https://trello.example/c/sys"),
        Card("ca", "hood_gusset", f"Qty: 2\n{STUDIO}\nMaterial: 6061", "ready_for_cam", "https://trello.example/c/ca")])
    h.runner.tick()
    assert h.queue.pending() == ["r001-al6061"]


def test_a_card_from_the_panel_goes_all_the_way_through_a_run(tmp_path):
    p = Panel(tmp_path)
    assert p.send(qty=2)[0] == 200
    [card] = card_in(p, "ready_for_cam")
    p.h.transport = FakeTransport([
        ("GET", "/currentmicroversion", [resp(body={"microversion": "m" * 24})]),
        ("GET", "/parts/", [resp(body=PARTS)]),
        ("POST", "/translations", [resp(body={"id": "T1"})]),
        ("GET", "/translations/T1", [resp(body={"requestState": "DONE", "resultExternalDataIds": ["F1"]})]),
        ("GET", "/externaldata/F1", [resp(200, b"ISO-10303-21; gusset")]),
    ])
    p.h.runner.tick()
    assert p.h.queue.pending() == ["r001-al6061"]
    run_fake_worker(p.h.queue)
    p.h.runner.tick()
    assert p.t.get_card(card.id).list_key == "nested"
    p.clock.t += 31
    detail = p.call("GET", "/api/part", {**Q, "p": "JHD"})[1]["card"]
    assert detail["label"] == "On r001 S1" and detail["sheets"][0]["where"] == "sheet_review"


# ---------------------------------------------------------------- the tunnel and config

class FakeProc:
    pid = 4242


def test_the_tunnel_is_started_once_and_left_running(tmp_path):
    folder = tmp_path / "tunnel"
    folder.mkdir()
    started, running = [], set()
    tunnel = Tunnel(folder, tmp_path / "state", tmp_path / "logs", popen=lambda cmd, **kw: started.append(cmd) or FakeProc(),
                    is_running=lambda pid: pid in running)
    assert not tunnel.ensure() and started == []              # not set up yet
    (folder / "cloudflared.exe").write_bytes(b"")
    (folder / "config.yml").write_text("tunnel: x\n")
    assert tunnel.ensure() and len(started) == 1
    assert started[0][1:3] == ["tunnel", "--config"] and started[0][-1] == "run"
    running.add(4242)
    assert tunnel.ensure() and len(started) == 1              # a restarted service finds it running
    running.clear()
    assert tunnel.ensure() and len(started) == 2              # it stopped: started again


def test_panel_config():
    cfg = parse_config(copy.deepcopy(RAW), root=REPO_ROOT)
    assert cfg.panel.enabled and cfg.panel.public_url == "https://autocam.team5940.org"
    bad = copy.deepcopy(RAW)
    bad["panel"]["public_url"] = "http://autocam.team5940.org"
    bad["panel"]["onshape_calls_per_year"] = 9000
    with pytest.raises(ConfigError) as e:
        parse_config(bad, root=REPO_ROOT)
    assert any("public_url" in x for x in e.value.errors) and any("10000" in x for x in e.value.errors)
    none = copy.deepcopy(RAW)
    del none["panel"]
    none["assumed"] = {k: v for k, v in none["assumed"].items() if not k.startswith("panel.")}
    assert not parse_config(none, root=REPO_ROOT).panel.enabled
