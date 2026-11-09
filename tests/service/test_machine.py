"""The machine page (machine.py, the mockup the user approved 2026-11-05): the operator's screen on the ShopSabre's
PC. Real sheet cards from a whole run (the fake worker), moved to Ready to cut; every button is a box on the card or
the card moving, so the service's own passes do the rest."""

import json
from dataclasses import replace

from autocam_service.machine import UNDO_S, MachineError, MachineView
from autocam_service.offcuts import OffcutStore
from autocam_service.panel.api import PanelApp, machine_key, shop_key
from autocam_service.panel.plates import PlatesView
from autocam_service.people import People
from autocam_service.program_checks import CheckedPrograms
from autocam_service.tracker.base import Attachment, Card
from test_fit_service import fit_card
from test_handprograms import program
from test_panel import KEY, Panel
from test_program_checks import check_card
from test_runner_autostart import harness, run, sheet, step_card

import pytest


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def machine(h, clock=None):
    plates = PlatesView(h.tracker, h.cfg, h.store, People(h.cfg.paths.state / "people.json"), lambda k: k)
    return MachineView(h.tracker, h.cfg, h.store, OffcutStore(h.store.offcuts_file),
                       CheckedPrograms(h.cfg.paths.state / "checked_programs.json"), plates,
                       h.cfg.paths.state / "machine.json", clock=clock or Clock(), today=lambda: "1970-01-12")


def to_ready(h, card_id):
    """Someone reviewed it and moved it to Ready to cut (automation never does)."""
    h.tracker.tick_all(card_id, h.cfg.trello.checklist_name)
    h.tracker.cards[card_id] = replace(h.tracker.cards[card_id], list_key="ready_to_cut")


def fitted_sheet(tmp_path):
    h = harness(tmp_path, fit_card(), step_card("c2", "spacer", qty=2))   # two spacers: the program stops once
    run(h)
    s1 = sheet(h)
    h.runner.tick()                                   # the options pass: Options and Offcut boxes on the card
    to_ready(h, s1.id)
    return h, s1


def ticked(h, card_id, checklist):
    return {item: done for item, done in h.tracker.checklists.get((card_id, checklist), [])}


def test_the_list_and_a_sheets_steps_in_the_order_theyre_done(tmp_path):
    h, s1 = fitted_sheet(tmp_path)
    m = machine(h)
    [item] = m.view()["sheets"]
    assert item["id"] == s1.id and item["group"] == "ready" and item["title"] == "6061 1/8 in"
    assert item["sub"].startswith("New sheet") and item["sub"].endswith("r001 S1") and item["progress"] is None
    assert [p["name"] for p in item["plates"]] == ["plate", "spacer"]
    d = m.sheet(s1.id)
    assert [s["t"] for s in d["steps"]] == ["load", "run", "fit", "run", "finish"]
    load, main, fit, outlines, finish = d["steps"]
    assert load["where"] == "New sheet" and load["mist"] and "left and right" in load["clamps"] and load["cutter"]
    assert main["file"] == "6061_0p125_r001_S1.tap" and main["main"] and main["stops"] >= 1
    assert main["nostop"] == {"file": "6061_0p125_r001_S1_NOSTOP.tap", "on": False, "ready": False}
    assert fit["feature"] == "0.5 hole" and [l["file"] for l in fit["laps"]] == [
        f"6061_0p125_r001_S1_FIT1_{k}.tap" for k in range(1, 5)] and len(fit["items"]) == 6 and fit["answered"] is None
    assert outlines["file"] == "6061_0p125_r001_S1_OUTLINES.tap" and outlines["note"] == "Cuts the fitted parts out"
    assert finish["kind"] == "new"
    assert d["at"] == {"step": 0, "rep": 1, "lap": 0, "lapRan": False, "rest": ""}


def test_the_buttons_tick_the_cards_boxes(tmp_path):
    h, s1 = fitted_sheet(tmp_path)
    m = machine(h)
    t = h.cfg.trello
    m.act({"id": s1.id, "do": "loaded"})
    assert all(ticked(h, s1.id, t.machine_checklist_name).values())
    assert list(json.loads(m.path.read_text())["loaded"]) == [s1.id]  # when: the runner's on_bed reads it
    m.act({"id": s1.id, "do": "stops", "on": False})               # the switch off: the no-stop version
    assert ticked(h, s1.id, t.options_checklist)[t.no_stop_item]
    h.runner.tick()                                                 # the service makes it, as if ticked on Trello
    run_step = m.sheet(s1.id)["steps"][1]
    assert run_step["nostop"] == {"file": "6061_0p125_r001_S1_NOSTOP.tap", "on": True, "ready": True}
    m.act({"id": s1.id, "do": "fit", "series": 1, "at": 2})         # fit after the second lap
    fit = m.sheet(s1.id)["steps"][2]
    assert fit["answered"] == fit["items"][2] and ticked(h, s1.id, "Fit: plate 0.5 hole")[fit["items"][2]]
    if any(c == t.offcut_checklist for (cid, c) in h.tracker.checklists if cid == s1.id):
        m.act({"id": s1.id, "do": "keep", "on": False})
        assert ticked(h, s1.id, t.offcut_checklist)[t.offcut_item] is False
        assert m.sheet(s1.id)["steps"][-1]["keep"] is False
    with pytest.raises(MachineError):
        m.act({"id": s1.id, "do": "fit", "series": 1, "at": 9})


def test_where_a_sheet_is_up_to_is_kept(tmp_path):
    h, s1 = fitted_sheet(tmp_path)
    m = machine(h)
    m.act({"id": s1.id, "do": "progress", "step": 2, "lap": 1})
    assert m.sheet(s1.id)["at"] == {"step": 2, "rep": 1, "lap": 1, "lapRan": False, "rest": ""}
    assert m.view()["sheets"][0]["progress"] == {"step": 3, "of": 5}
    assert machine(h).sheet(s1.id)["at"]["step"] == 2              # a new page (or a restart) opens there too


def test_move_to_cut_waits_so_it_can_be_undone(tmp_path):
    h, s1 = fitted_sheet(tmp_path)
    clock = Clock()
    m = machine(h, clock)
    m.act({"id": s1.id, "do": "finish"})
    v = m.view()
    assert v["sheets"] == [] and v["cut"][0]["id"] == s1.id and v["cut"][0]["undo"] == int(UNDO_S)
    assert h.tracker.cards[s1.id].list_key == "ready_to_cut"       # not yet
    m.act({"id": s1.id, "do": "undo"})
    assert [s["id"] for s in m.view()["sheets"]] == [s1.id] and m.view()["cut"] == []
    m.act({"id": s1.id, "do": "finish"})
    clock.t += UNDO_S + 1
    m.flush()
    assert h.tracker.cards[s1.id].list_key == "cut"
    cut = m.view()["cut"]
    assert cut[0]["id"] == s1.id and cut[0]["undo"] == 0
    with pytest.raises(MachineError) as e:
        m.act({"id": s1.id, "do": "undo"})                          # in Cut: only Trello moves it back
    assert e.value.status == 409
    h.runner.tick()                                                 # the service follows it to Cut as ever
    assert h.store.sheet_cards()[s1.id].get("cut")


def test_something_went_wrong_sends_it_back_to_review(tmp_path):
    h, s1 = fitted_sheet(tmp_path)
    m = machine(h)
    m.act({"id": s1.id, "do": "progress", "step": 1})
    m.act({"id": s1.id, "do": "problem", "what": ["Bit broke", "Sheet moved"]})
    assert h.tracker.cards[s1.id].list_key == "sheet_review"
    assert h.tracker.comments_on(s1.id)[-1].startswith("Sent back from the machine: Bit broke, Sheet moved.")
    assert m.view()["sheets"] == []
    with pytest.raises(MachineError) as e:
        m.sheet(s1.id)
    assert e.value.status == 409


def test_the_air_test_is_a_step_when_its_ticked(tmp_path):
    h, s1 = fitted_sheet(tmp_path)
    t = h.cfg.trello
    h.tracker.set_check(s1.id, t.options_checklist, t.air_test_item, True)
    h.runner.tick()
    steps = machine(h).sheet(s1.id)["steps"]
    assert [s["t"] for s in steps][:3] == ["load", "air", "run"]
    assert steps[1]["file"].endswith("_AIRTEST.tap") and steps[1]["gap"]


def test_on_an_offcut_it_starts_with_finding_it_and_says_when_it_isnt_there(tmp_path):
    h, s1 = fitted_sheet(tmp_path)
    t = h.cfg.trello
    store = OffcutStore(h.store.offcuts_file)
    piece = h.tracker.create_card("offcuts", "6061 1/8in offcut #3", "")
    store.put(piece.id, {"material": "al6061", "thickness_in": 0.125, "used": [[0.0, 6.0]], "beside": [],
                         "last": {"label": "r000 S1", "stretch": [0.0, 6.0], "turned": False}, "number": 3,
                         "reserved_by": s1.id})
    reg = h.store.sheet_cards()
    reg[s1.id]["offcut_id"] = piece.id
    h.store._write_sheets(reg)
    h.tracker.add_checklist(s1.id, t.stock_checklist, [t.not_found_item, t.cut_away_item])
    m = machine(h)
    assert m.view()["sheets"][0]["sub"].startswith("Offcut #3")
    find, load = m.sheet(s1.id)["steps"][:2]
    assert find["t"] == "find" and find["n"] == 3 and find["canMiss"] and not find["missing"] and find["size"]
    assert load["where"] == "Offcut #3" and load["how"] == "The cut end goes at the front"
    m.act({"id": s1.id, "do": "cantfind"})
    assert ticked(h, s1.id, t.stock_checklist)[t.not_found_item]
    assert m.sheet(s1.id)["steps"][0]["missing"]                   # until the service has looked
    assert m.sheet(s1.id)["steps"][-1]["kind"] == "offcut"
    # Done: what's left stays clamped unless someone says it's going back on the rack (2026-11-06)
    assert m.sheet(s1.id)["steps"][-1]["racked"] is False
    m.act({"id": s1.id, "do": "rack", "on": True})
    assert m.sheet(s1.id)["steps"][-1]["racked"] is True and s1.id in json.loads(m.path.read_text())["racked"]
    m.act({"id": s1.id, "do": "rack", "on": False})
    assert m.sheet(s1.id)["steps"][-1]["racked"] is False and s1.id not in json.loads(m.path.read_text())["racked"]


def test_a_part_placed_by_hand_gets_a_piece_and_runs_once_per_copy(tmp_path):
    h = harness(tmp_path)
    card = h.tracker.create_card("sheet_review", "6061 1/8in - placed by hand - own program arm.tap x3 - arm - H1", "")
    h.tracker.attach_file(card.id, "arm.nc", program(), "text/plain")
    h.store.register_sheet(card.id, "H1", True, [])
    reg = h.store.sheet_cards()
    reg[card.id].update(label="H1", material="al6061", thickness_in=0.125, by_hand=True,
                        own={"program": "arm.nc", "by_hand": True, "box": [0.0, 0.0, 4.0, 3.0]})
    h.store._write_sheets(reg)
    h.tracker.add_checklist(card.id, h.cfg.trello.checklist_name, h.cfg.trello.checklist)
    to_ready(h, card.id)
    m = machine(h)
    [item] = m.view()["sheets"]
    assert item["group"] == "hand" and item["sub"].startswith("Placed by hand · run it 3 times")
    steps = m.sheet(card.id)["steps"]
    assert [s["t"] for s in steps] == ["piece", "loadhand", "run", "finishhand"]
    margin = 2 * h.cfg.nest.part_spacing_in
    assert steps[0]["size"] == f"{4 + margin:.1f} × {3 + margin:.1f} in"
    assert steps[2]["file"] == "arm.nc" and steps[2]["repeat"] == 3
    assert m.act({"id": card.id, "do": "loaded"}) == {"ok": True}      # no At the machine boxes: fine
    with pytest.raises(MachineError) as e:
        m.act({"id": card.id, "do": "keep", "on": True})               # no Offcut box: said, the page stays put
    assert e.value.status == 422 and "Do it on Trello" in str(e.value)


def test_a_checked_program_shows_and_moves_to_cut_once_its_run(tmp_path):
    h = harness(tmp_path)
    card = check_card(h, list_key="inbox")
    CheckedPrograms(h.cfg.paths.state / "checked_programs.json").record(
        card.id, {"tap-k1-0": {"name": "P-3001.tap", "passed": True}}, ["tap-k1-0"], stock="6061 1/8in", on_sheet=True)
    clock = Clock()
    m = machine(h, clock)
    [item] = m.view()["sheets"]
    assert item["group"] == "check" and item["plates"] == [{"name": "P-3001.tap", "qty": 1}]
    assert m.sheet(card.id)["steps"] == [{"t": "checkrun", "file": "P-3001.tap", "zero": "the sheet's front-left corner"}]
    name, data = m.file(card.id, "P-3001.tap")
    assert name == "P-3001.tap" and data
    m.act({"id": card.id, "do": "finish"})
    clock.t += UNDO_S + 1
    m.flush()
    assert h.tracker.cards[card.id].list_key == "cut" and h.tracker.comments_on(card.id)[-1] == "Run at the machine."


def test_the_check_label_records_what_passed(tmp_path):
    h = harness(tmp_path)
    check_card(h)
    h.runner.tick()
    rec = CheckedPrograms(h.cfg.paths.state / "checked_programs.json").all()["k1"]
    assert rec["files"] == {"tap-k1-0": {"name": "P-3001.tap", "passed": True}} and rec["on_sheet"]


def test_the_page_has_its_own_key_and_downloads_programs(tmp_path):
    p = Panel(tmp_path)
    h = p.h
    to_run = fit_card()
    h.tracker.cards[to_run.id] = to_run
    h.tracker.downloads[f"att-{to_run.id}"] = b"ISO-10303-21; plate"
    run(h)
    s1 = sheet(h)
    to_ready(h, s1.id)
    p.app.machine = machine(h)
    mk = machine_key(KEY)
    assert mk not in (KEY, shop_key(KEY))
    page = p.app.handle("GET", "/machine", {"key": mk}, {})
    assert page.status == 200 and b"machine.js" in page.body
    assert p.app.handle("GET", "/machine", {"key": KEY}, {}).status == 403          # the panel's key isn't it
    assert p.call("GET", "/api/machine", key=shop_key(KEY))[0] == 403
    status, v = p.call("GET", "/api/machine", key=mk)
    assert status == 200 and v["sheets"][0]["id"] == s1.id
    r = p.app.handle("GET", "/api/machine/file", {"id": s1.id, "name": "6061_0p125_r001_S1.tap", "key": mk}, {})
    assert r.status == 200 and r.headers["Content-Disposition"] == 'attachment; filename="6061_0p125_r001_S1.tap"'
    assert r.body.startswith(b"[6061_0p125_r001_S1]")
    nope = p.app.handle("GET", "/api/machine/file", {"id": s1.id, "name": "6061_0p125_r001_S1.png", "key": mk}, {})
    assert nope.status == 404
    status, d = p.call("POST", "/api/machine/act", body={"id": s1.id, "do": "progress", "step": 1}, key=mk)
    assert status == 200 and json.loads((h.cfg.paths.state / "machine.json").read_text())["progress"][s1.id]["step"] == 1


def test_it_stopped_part_way_makes_the_rest_from_the_next_part(tmp_path):
    # 2026-11-08: r110 was stopped after its first part and the rest was made by hand; now the page makes it.
    h = harness(tmp_path, step_card("c1", "plate", qty=3))           # three plates: two stops
    run(h)
    s1 = sheet(h)
    h.runner.tick()
    to_ready(h, s1.id)
    m = machine(h)
    main = next(s for s in m.sheet(s1.id)["steps"] if s.get("main"))
    assert main["parts"] == ["plate (1 of 3)", "plate (2 of 3)", "plate (3 of 3)"] and main["stops"] == 2
    got = m.act({"id": s1.id, "do": "resume", "after": 1})
    assert got == {"ok": True, "file": "6061_0p125_r001_S1_FROM2.tap", "from": 2}
    name, data = m.file(s1.id, got["file"])
    text = data.decode("ascii")
    assert text.splitlines()[0] == "[6061_0p125_r001_S1_FROM2]"
    assert "[outer p01-1]" not in text and "[outer p01-2]" in text and "[outer p01-3]" in text
    assert text.count("G4 [TAKE PART") == 1                       # the stop after part 2 is still there
    assert "the rest of 6061_0p125_r001_S1 after part 1 (plate (1 of 3)) came out" in h.tracker.comments_on(s1.id)[-1]
    # asked again: the same program, not a second copy
    assert m.act({"id": s1.id, "do": "resume", "after": 1})["file"] == got["file"]
    assert [a.name for a in h.tracker.get_card(s1.id).attachments].count(got["file"]) == 1
    # the page remembers it was running the rest
    m.act({"id": s1.id, "do": "progress", "step": 1, "rest": got["file"]})
    assert m.sheet(s1.id)["at"]["rest"] == got["file"]
    for after in (0, 3):                                           # not a stop: before the first part, after the last
        with pytest.raises(MachineError) as e:
            m.act({"id": s1.id, "do": "resume", "after": after})
        assert e.value.status == 400


def test_the_rest_runs_without_stops_when_the_sheet_does(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate", qty=3))
    run(h)
    s1 = sheet(h)
    h.runner.tick()
    to_ready(h, s1.id)
    m = machine(h)
    m.act({"id": s1.id, "do": "stops", "on": False})               # "Stop after each part" off
    got = m.act({"id": s1.id, "do": "resume", "after": 1})
    assert got["file"] == "6061_0p125_r001_S1_FROM2_NOSTOP.tap"
    assert "G4 [TAKE PART" not in m.file(s1.id, got["file"])[1].decode("ascii")
