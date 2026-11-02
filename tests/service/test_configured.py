"""Configured parts (2026-11-01, from the old panel): a configured Part Studio's configuration rides in the card's
link (?configuration=...), and every Onshape read and export of that part carries it. Each configuration is its
own part: its own card, cache entries and CAD-changed comparisons."""

import json

from autocam_service.onshape.cache import OnshapeCache
from autocam_service.onshape.export import Exporter
from autocam_service.onshape.urls import configuration_of, normal_configuration, parse_link
from autocam_service.panel.onshape_reads import configuration_label, occurrence_counts
from fakeonshape import FakeTransport, resp
from test_onshape import POLLS, D as OD, E as OE, V as OV, client, export_routes, ledger  # noqa: F401 (fixture)
from test_panel import PARTS, Q, Panel, card_in
from test_runner_e2e import D, E, W

CFG = "Length=19+in;List_x3=Option_2"                    # as Onshape writes it: "+" is Onshape's own
ENCODED = "Length%3D19%2Bin%3BList_x3%3DOption_2"
DEFINITION = {"configurationParameters": [
    {"parameterId": "Length", "parameterName": "Length"},
    {"parameterId": "List_x3", "parameterName": "Size", "options": [{"option": "Option_1", "optionName": "Narrow"},
                                                                     {"option": "Option_2", "optionName": "Wide"}]}]}


def test_the_configuration_rides_in_the_link():
    url = f"https://cad.onshape.com/documents/{OD}/v/{OV}/e/{OE}?configuration={ENCODED}"
    link = parse_link(url)
    assert link.configuration == CFG and link.url == url and parse_link(link.url).configuration == CFG
    plain = parse_link(f"https://cad.onshape.com/documents/{OD}/v/{OV}/e/{OE}")
    assert plain.configuration == "" and plain.key != link.key and plain.studio != link.studio
    assert link.query(a="1") == {"a": "1", "configuration": CFG} and plain.query() == {}
    assert parse_link(url + "&renderMode=0").configuration == CFG                 # other browser parameters
    assert [normal_configuration(v) for v in ("default", "", "{$configuration}", " Default ")] == ["", "", "", ""]
    assert configuration_of("not a link") == ""


def test_a_run_exports_that_configuration(ledger, tmp_path):
    link = parse_link(f"https://cad.onshape.com/documents/{OD}/v/{OV}/e/{OE}?configuration={ENCODED}")
    t = FakeTransport(export_routes())
    got = Exporter(client(ledger, t), OnshapeCache(tmp_path / "cache"), POLLS, sleep=lambda s: None,
                   clock=lambda: 0.0).export(link, "hood_gusset", "p01")
    assert not got.from_cache
    parts_get = next(u for m, u, _, _ in t.sent if m == "GET" and "/parts/" in u)
    assert f"configuration={ENCODED}" in parts_get
    [(_, _, _, body)] = [s for s in t.sent if s[0] == "POST"]
    assert json.loads(body) == {"formatName": "STEP", "partIds": "JHD", "storeInDocument": False, "configuration": CFG}
    plain = parse_link(f"https://cad.onshape.com/documents/{OD}/v/{OV}/e/{OE}")
    assert not Exporter(None, OnshapeCache(tmp_path / "cache"), POLLS).is_cached(plain, "hood_gusset")   # its own


def test_labels_name_the_parameters_and_options():
    assert configuration_label(CFG, DEFINITION) == "Length: 19 in; Size: Wide"
    assert configuration_label(CFG, {}) == "Length: 19 in; List_x3: Option_2"       # without the definition
    assert configuration_label("", DEFINITION) == ""
    # as the Shooter's BOM has them (read 2026-11-01): Onshape keeps lengths in meters
    assert configuration_label("ID=0.004978400000000001+meter;Length=0.0047625+meter", {}) == "ID: 0.196 in; Length: 0.1875 in"
    assert configuration_label("Angle=30+deg", {}) == "Angle: 30 deg"


def test_quantities_count_only_that_configuration():
    definition = {"rootAssembly": {
        "instances": [{"id": "a", "type": "Part", "documentId": D, "elementId": E, "partId": "JHD", "configuration": CFG},
                      {"id": "b", "type": "Part", "documentId": D, "elementId": E, "partId": "JHD", "configuration": CFG},
                      {"id": "c", "type": "Part", "documentId": D, "elementId": E, "partId": "JHD",
                       "configuration": "default"}],
        "occurrences": [{"path": ["a"]}, {"path": ["b"]}, {"path": ["c"]}]}}
    counts = occurrence_counts(definition)
    assert counts[(D, E, "JHD")] == 3 and counts[(D, E, "JHD", CFG)] == 2 and counts[(D, E, "JHD", "")] == 1


def configured_panel(tmp_path):
    transport = FakeTransport([("GET", "/parts/", [resp(body=PARTS)] * 6),
                               ("GET", "/configuration", [resp(body=DEFINITION)] * 2)])
    return Panel(tmp_path, transport=transport)


def test_the_panel_reads_sends_and_matches_by_configuration(tmp_path):
    p = configured_panel(tmp_path)
    cq = {**Q, "c": CFG}
    status, st = p.call("GET", "/api/studio", cq)
    assert status == 200 and st["configuration"] == "Length: 19 in; Size: Wide"
    assert all(f"configuration={ENCODED}" in u for m, u, _, _ in p.transport.sent if "/parts/" in u)
    status, r = p.send(c=CFG)
    assert status == 200, r
    [card] = card_in(p, "ready_for_cam")
    assert card.name == "P-2014 gusset (Length: 19 in; Size: Wide)"
    assert card.desc.split("\n")[0] == f"https://cad.onshape.com/documents/{D}/w/{W}/e/{E}?configuration={ENCODED}"
    assert p.send(c=CFG)[0] == 409                                               # that configuration has a card
    status, r = p.send()                                                         # the default one is another part
    assert status == 200, r
    rows = {r["name"]: r["chip"]["status"] for r in p.call("GET", "/api/studio", {**Q, "cached": "1"})[1]["parts"]}
    assert rows["P-2014 gusset"] == "queued"
    assert len([u for _, u, _, _ in p.transport.sent if "/configuration" in u]) == 1   # its names read once


def test_a_configured_part_of_an_assembly_is_sent_with_its_configuration(tmp_path):
    from test_panel_more import assembly_panel
    p = assembly_panel(tmp_path)
    items = {i["name"]: i for i in p.call("GET", "/api/assembly", Q)[1]["items"]}
    bracket = items["P-2017 bracket (List_abc: 1)"]
    assert bracket["sendable"] and bracket["why"] == ""
    p.transport.routes.append(("GET", "/configuration", [resp(body={"configurationParameters": [
        {"parameterId": "List_abc", "parameterName": "Holes", "options": [{"option": "1", "optionName": "Two"}]}]})]))
    status, r = p.call("POST", "/api/send-many", body={**Q, "name": "Siddhi", "items": [{"key": bracket["key"], "qty": 2}]})
    assert status == 200 and r["results"][0]["sent"], r
    [card] = [c for c in card_in(p, "ready_for_cam") if c.name.startswith("P-2017")]
    assert card.name == "P-2017 bracket (Holes: Two)"
    assert card.desc.split("\n")[0].endswith("?configuration=List_abc%3D1")
