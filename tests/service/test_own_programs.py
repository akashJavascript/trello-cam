"""Someone's own program, placed by AutoCAM (option 3; own_programs.py): a card in Ready for CAM with a .tap
instead of a part gets a spot, a moved copy of the program checked there, a card in Sheet review, and its cut
recorded when that card goes to Cut."""

from autocam_service.own_programs import find_spot, placed_name, shift_program
from autocam_service.tracker.base import Attachment, Card
from test_handprograms import program
from test_offcut_finding import pieces
from test_offcuts_service import cut, first_sheet, offcut_cards
from test_runner_autostart import harness

REGION = (1.25, 1.0, 22.75, 39.5)


# ---------------------------------------------------------------- moving a program

def test_shifting_moves_x_and_y_only_where_they_are_positions():
    src = ("[P1]\r\nG90\r\nG20\r\nG53 Z\r\nG4 X4.\r\nG0 X0. Y0.\r\nG2 X1. Y1. I0.5 J0.5\r\n"
           "G81 X2. Y2. Z0. R0.3 F20.\r\nX3.\r\nG80\r\n[cut X9 here]\r\nG53 P10\r\n")
    out = shift_program(src, 5, 10, "MOVED").split("\r\n")
    assert out[:6] == ["[P1]", "[MOVED]", "G90", "G20", "G53 Z", "G4 X4."]          # the dwell keeps its seconds
    assert out[6:11] == ["G0 X5. Y10.", "G2 X6. Y11. I0.5 J0.5", "G81 X7. Y12. Z0. R0.3 F20.", "X8.", "G80"]
    assert out[11:13] == ["[cut X9 here]", "G53 P10"]


def test_placed_names_are_safe():
    assert placed_name("P-3001 v2.tap", "H4") == "P-3001_v2_H4_PLACED.tap"


# ---------------------------------------------------------------- finding a spot

BOX = (-0.125, -0.125, 3.125, 2.125)          # what program() cuts, with a 1/4 in cutter


def test_no_offcut_means_a_new_sheets_front_left_corner():
    spot = find_spot(BOX, {}, REGION, 24, 48, 0.5)
    assert spot.offcut_id is None and (spot.dx, spot.dy) == (1.375, 1.125)
    assert spot.cut_loaded == (1.25, 1.0, 4.5, 3.25)


def test_the_smallest_room_that_fits_wins():
    offcuts = {"big": {"used": [[0.5, 30.0]], "beside": [], "last": {"turned": False}},
               "room": {"used": [[0.5, 10.0]], "beside": [[12.0, 1.0, 22.75, 10.0]], "last": {"turned": False}}}
    spot = find_spot(BOX, offcuts, REGION, 24, 48, 0.5)
    assert (spot.offcut_id, spot.room, spot.turned) == ("room", 0, False)
    assert spot.cut_loaded == (12.0, 1.0, 15.25, 3.25)


def test_an_offcut_is_spun_round_only_if_it_has_to_be():
    full_front = {"used": [[0.5, 39.0]], "beside": [], "last": {"turned": False}}   # only the back end is free
    spot = find_spot(BOX, {"o": full_front}, REGION, 24, 48, 0.5)
    assert spot.offcut_id == "o" and spot.turned
    assert spot.cut_own == (19.5, 44.75, 22.75, 47.0)        # its own coordinates: the far end, from end A
    scrap = {**full_front, "scrap": True}
    assert find_spot(BOX, {"o": scrap}, REGION, 24, 48, 0.5).offcut_id is None      # a scrap is never turned


def test_a_program_bigger_than_a_sheet_has_no_spot():
    assert find_spot((0, 0, 30, 5), {}, REGION, 24, 48, 0.5) is None


# ---------------------------------------------------------------- on the board

def own_card(h, cid="o1", desc="Material: 6061\nThickness: 1/8", data=None, name="P-3001.tap"):
    card = Card(cid, "P-3001", desc, "ready_for_cam", f"https://trello.example/c/{cid}",
                attachments=(Attachment(f"tap-{cid}", name, f"https://trello.example/a/{cid}"),))
    h.tracker.cards[cid] = card
    h.tracker.downloads[f"tap-{cid}"] = data if data is not None else program()
    return card


def sheet_cards(h):
    return [c for c in h.tracker.cards.values() if c.list_key == "sheet_review" and c.id not in h.tracker.archived]


def placed_files(h, card_id):
    return {name: data for _, (cid, name, data) in h.tracker.files.items() if cid == card_id and name.endswith(".tap")}


def test_an_own_program_goes_on_a_new_sheet_moved_to_its_spot(tmp_path):
    h = harness(tmp_path)
    own_card(h)
    h.runner.tick()
    [s] = sheet_cards(h)
    assert s.name == "6061 1/8in - own program P-3001.tap - P-3001 - H1"
    [(name, data)] = placed_files(h, s.id).items()
    assert name == "P-3001_H1_PLACED.tap"
    lines = data.decode("ascii").split("\r\n")
    assert lines[1].startswith("[AUTOCAM H1 - P-3001.tap MOVED X+1.625 Y+0.875") and "G0 X1.625 Y0.875" in lines
    assert "zero X/Y there, as for AutoCAM's sheets" in s.desc and "move to X 1.6250 Y 0.8750" in s.desc
    assert "Stock: a new 6061 1/8in sheet, 24 x 48." in s.desc
    assert [c for c, _ in h.tracker.checklists if c == s.id]                       # Review, At the machine, Offcut
    assert h.tracker.cards["o1"].list_key == "nested"
    assert "Placed on a new 6061 1/8in sheet" in h.tracker.comments_on("o1")[-1]
    assert not list(h.queue.incoming.glob("*.json"))                              # no Fusion run for it
    assert ("o1", "Nest") not in h.tracker.checklists                              # not a part to nest


def test_an_own_program_on_an_offcut_is_recorded_there_when_cut(tmp_path):
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off1] = offcut_cards(h)
    own_card(h)
    h.runner.tick()
    [s] = [c for c in sheet_cards(h) if "own program" in c.name]
    assert s.name.startswith("6061 1/8in offcut #1 - own program")
    assert pieces(h)[off1.id]["reserved_by"] == s.id
    assert "Stock: offcut #1 (6061 1/8in)" in s.desc
    before = pieces(h)[off1.id]
    h.tracker.tick_all(s.id, "Review")
    cut(h, s.id)
    after = pieces(h)[off1.id]
    assert after["reserved_by"] is None
    assert (after["used"], after["beside"]) != (before["used"], before["beside"])     # the cut is taken out
    assert "hand_cuts" not in after                                                # a placed program, not a hand cut
    assert after["last"]["label"] == "H1"
    assert "H1 (P-3001.tap) was cut from it." in h.tracker.comments_on(off1.id)[-1]
    assert h.tracker.cards["o1"].list_key == "cut"                                 # the part card follows


def test_the_rest_of_a_new_sheet_becomes_an_offcut(tmp_path):
    h = harness(tmp_path)
    own_card(h)
    h.runner.tick()
    [s] = sheet_cards(h)
    h.tracker.tick_all(s.id, "Review")
    cut(h, s.id)
    [off] = offcut_cards(h)
    p = pieces(h)[off.id]
    assert p["number"] == 1 and p["last"]["label"] == "H1"
    assert "The rest of the sheet is offcut #1" in h.tracker.comments_on(s.id)[-1]


def test_moving_it_back_to_ready_for_cam_replaces_its_card(tmp_path):
    h = harness(tmp_path)
    own_card(h)
    h.runner.tick()
    [first] = sheet_cards(h)
    card = h.tracker.cards["o1"]
    h.tracker.cards["o1"] = card.__class__(**{**card.__dict__, "list_key": "ready_for_cam"})
    h.runner.tick()
    [second] = sheet_cards(h)
    assert second.id != first.id and first.id in h.tracker.archived and second.name.endswith("H2")
    assert h.tracker.comments_on(first.id)[-1] == "Replaced: its part card came back to Ready for CAM."


def refused(h):
    assert h.tracker.cards["o1"].list_key == "needs_fixing" and not sheet_cards(h)
    return h.tracker.comments_on("o1")[-1]


def test_cards_it_cant_place_go_to_needs_fixing_saying_why(tmp_path):
    h = harness(tmp_path)
    own_card(h, desc="Material: 6061")
    h.runner.tick()
    assert "No Thickness line" in refused(h)

    (tmp_path / "2").mkdir()
    h = harness(tmp_path / "2")
    own_card(h, data=program(z_cut="-0.13"))
    h.runner.tick()
    assert "Z0 on the spoilboard" in refused(h)

    (tmp_path / "3").mkdir()
    h = harness(tmp_path / "3")
    own_card(h, data=program(x1=30.0))
    h.runner.tick()
    assert "more than a sheet's cutting area" in refused(h)

    (tmp_path / "4").mkdir()
    h = harness(tmp_path / "4")
    own_card(h, data=program().replace(b"G90\r\n", b"G91\r\n"))
    h.runner.tick()
    assert "failed AutoCAM's checks" in refused(h)


def test_placing_programs_can_be_switched_off(tmp_path):
    h = harness(tmp_path)
    object.__setattr__(h.cfg, "hand_cuts", h.cfg.hand_cuts.__class__(place_programs=False))
    own_card(h)
    h.runner.tick()
    h.runner.tick()
    assert not sheet_cards(h)                                   # an ordinary part card then: no link, no STEP
    assert "No Onshape link" in h.tracker.comments_on("o1")[-1]


# ---------------------------------------------------------------- "Place it myself" (2026-11-01)

def test_place_it_myself_puts_the_program_as_it_is_in_the_operators_list(tmp_path):
    h = harness(tmp_path)
    own_card(h, desc="Material: 6061\nThickness: 1/8\nPlace it myself")
    h.runner.tick()
    [s] = sheet_cards(h)
    assert s.name == "6061 1/8in - placed by hand - own program P-3001.tap - P-3001 - H1"
    assert placed_files(h, s.id) == {"P-3001.tap": program()}                    # not moved: as it was made
    assert "Zero X/Y on the piece where its zero goes: it cuts X -0.12 to +3.12 in and Y -0.12 to +2.12 in" in s.desc
    assert "at least 4.2 x 3.2 in" in s.desc
    assert h.tracker.checklist(s.id, "Review").total == 2 and h.tracker.checklist(s.id, "Offcut") is None
    assert h.tracker.get_card("o1").list_key == "nested"
    info = h.store.sheet_cards()[s.id]
    assert info["cuttable"] and info["own"]["by_hand"] and info["offcut_id"] is None
    h.tracker.tick_all(s.id, "Review")
    cut(h, s.id)
    assert offcut_cards(h) == []                                                # no piece AutoCAM knows of
    assert h.tracker.get_card("o1").list_key == "cut"


def test_place_it_myself_is_still_checked(tmp_path):
    h = harness(tmp_path)
    own_card(h, desc="Material: 6061\nThickness: 1/8\nPlace it myself", data=program(z_cut="-0.13"))
    h.runner.tick()
    assert sheet_cards(h) == [] and h.tracker.get_card("o1").list_key == "needs_fixing"
    assert "failed AutoCAM's checks" in h.tracker.comments_on("o1")[-1] or "Z0" in h.tracker.comments_on("o1")[-1]
