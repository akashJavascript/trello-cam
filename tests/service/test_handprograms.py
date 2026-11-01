"""Parts cut out of offcuts with someone's own program: the program is attached to the offcut's card with where it
was zeroed, and the cut is worked out from it (handprograms.py; optional beside the typed "Cut by hand:" line)."""

import pytest

from autocam_service.handprograms import footprint, parse_cutter, parse_zeroes, program_cuts
from autocam_service.tracker.base import Attachment
from test_offcut_finding import note, pieces, scrap
from test_offcuts_service import cut, first_sheet, offcut_cards


def program(z_cut="0.", units="G20", x1=3.0, y1=2.0):
    """A square cut from the program's zero to (x1, y1), like a part cut with its own program."""
    lines = ["G90", units, "G53 Z", "S18000", "M3", "G4 X4.", "G0 X0. Y0.", "G0 Z0.325", f"G1 Z{z_cut} F20.",
             f"G1 X{x1:g} F60.", f"G1 Y{y1:g}", "G1 X0.", "G1 Y0.", "G0 Z2.125", "G53 Z", "M5", "G53 P10"]
    return ("\r\n".join(lines) + "\r\n").encode("ascii")


# ---------------------------------------------------------------- reading the program

def test_the_footprint_is_the_cutters_reach_below_the_stock_top():
    box, problems, note_ = footprint(program(), 0.125, 0.25)
    assert problems == [] and note_ == ""
    assert box == pytest.approx((-0.125, -0.125, 3.125, 2.125))


def test_a_program_zeroed_on_top_of_the_stock_is_read_too():
    box, problems, note_ = footprint(program(z_cut="-0.13"), 0.125, 0.25)
    assert box == pytest.approx((-0.125, -0.125, 3.125, 2.125)) and "Z0 was on top of the stock" in note_


def test_programs_it_cant_place_are_refused():
    assert "millimeters" in footprint(program(units="G21"), 0.125, 0.25)[1][0]
    assert "never goes below the top" in footprint(program(z_cut="0.2"), 0.125, 0.25)[1][0]


def test_cutter_sizes():
    assert parse_cutter("Cutter: 4 mm")[0] == pytest.approx(4 / 25.4)
    assert parse_cutter("cutter: 1/8")[0] == 0.125
    assert parse_cutter("Cutter: 0.25 in")[0] == 0.25
    assert parse_cutter("nothing here") == (None, [])
    assert "isn't a size" in parse_cutter("Cutter: the big one")[1][0]


def test_zeroed_lines():
    [z], problems = parse_zeroes("Zeroed at: 10 along, 5.5 across\nZeroed at: 3 along")
    assert (z.along, z.across, z.program) == (10.0, 5.5, None)
    assert "needs both parts" in problems[0]
    [z], _ = parse_zeroes("zeroed at: 2 along, 1 across for P-3001.tap")
    assert z.program == "P-3001.tap"


def test_the_cut_goes_where_it_was_zeroed_and_stops_at_the_piece():
    [pc], problems = program_cuts("Zeroed at: 10 along, 5 across", [("P-3001.tap", program())], 0.125, 24, 48, 0.25)
    assert problems == [] and pc.program == "P-3001.tap" and pc.cut == (4.88, 9.88, 8.12, 12.12)
    [pc], _ = program_cuts("Zeroed at: 0 along, 22 across", [("edge.tap", program())], 0.125, 24, 48, 0.25)
    assert pc.cut == (21.88, 0.0, 24.0, 2.12)                  # the cutter ran off the piece's edges
    [], problems = program_cuts("Zeroed at: 60 along, 5 across", [("off.tap", program())], 0.125, 24, 48, 0.25)
    assert "would have cut off the piece" in problems[0]


def test_several_programs_need_their_lines_named():
    progs = [("a.tap", program()), ("b.tap", program(x1=1.0, y1=1.0))]
    made, problems = program_cuts("Zeroed at: 10 along, 5 across", progs, 0.125, 24, 48, 0.25)
    assert made == [] and all("name each one's Zeroed at line" in p for p in problems)
    made, problems = program_cuts("Zeroed at: 10 along, 5 across for a.tap\nZeroed at: 30 along, 2 across for B.TAP",
                                  progs, 0.125, 24, 48, 0.25)
    assert problems == [] and [(m.program, m.cut) for m in made] == [("a.tap", (4.88, 9.88, 8.12, 12.12)),
                                                                     ("b.tap", (1.88, 29.88, 3.12, 31.12))]
    made, problems = program_cuts("", progs[:1], 0.125, 24, 48, 0.25)
    assert made == [] and "add a line like Zeroed at" in problems[0]


# ---------------------------------------------------------------- on the board

def attach(h, card_id, name, data, att_id=None):
    att_id = att_id or f"att-{name}"
    card = h.tracker.cards[card_id]
    h.tracker.cards[card_id] = card.__class__(**{**card.__dict__, "attachments": card.attachments + (
        Attachment(att_id, name, f"https://trello.example/a/{att_id}"),)})
    h.tracker.downloads[att_id] = data
    return att_id


def test_a_program_on_an_offcut_card_is_recorded_where_it_was_zeroed(tmp_path):
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off1] = offcut_cards(h)
    assert "attach the .tap you ran and add a line like Zeroed at: 10 along, 5 across" in off1.desc
    att = attach(h, off1.id, "P-3001.tap", program())
    note(h, off1.id, "Zeroed at: 20 along, 6 across")
    h.runner.tick()
    h.runner.tick()
    p = pieces(h)[off1.id]
    assert p["hand_cuts"] == [[5.88, 19.88, 9.12, 22.12]] and p["programs"] == [att]
    card = h.tracker.cards[off1.id]
    assert "Cut by hand: 19.88 to 22.12 along, 5.88 to 9.12 across" in card.desc
    assert "Zeroed at: 20 along" not in card.desc                  # the card is rewritten with the cut
    said = [c for c in h.tracker.comments_on(off1.id) if c.startswith("Recorded the hand cut")]
    assert said == ["Recorded the hand cut (19.88 to 22.12 along, 5.88 to 9.12 across (from P-3001.tap)): runs keep "
                    "clear of it. " + said[0].split("runs keep clear of it. ", 1)[1]]


def test_a_program_without_its_zero_gets_one_reply_then_is_recorded(tmp_path):
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off1] = offcut_cards(h)
    attach(h, off1.id, "P-3001.tap", program())
    h.runner.tick()
    h.runner.tick()
    [asked] = [c for c in h.tracker.comments_on(off1.id) if c.startswith("Couldn't record a hand cut")]
    assert "P-3001.tap: add a line like Zeroed at: 10 along, 5 across" in asked
    assert "hand_cuts" not in pieces(h)[off1.id]
    note(h, off1.id, "Zeroed at: 20 along, 6 across\nCutter: 1/2")
    h.runner.tick()
    assert pieces(h)[off1.id]["hand_cuts"] == [[5.75, 19.75, 9.25, 22.25]]     # a 1/2 in cutter reaches further


def test_programs_on_offcut_cards_can_be_switched_off(tmp_path):
    h, s1 = first_sheet(tmp_path)
    object.__setattr__(h.cfg, "hand_cuts", h.cfg.hand_cuts.__class__(programs=False))
    cut(h, s1.id)
    [off1] = offcut_cards(h)
    assert "Zeroed at" not in off1.desc
    attach(h, off1.id, "P-3001.tap", program())
    note(h, off1.id, "Zeroed at: 20 along, 6 across")
    h.runner.tick()
    assert "hand_cuts" not in pieces(h)[off1.id]
    assert not any(c.startswith("Couldn't record") for c in h.tracker.comments_on(off1.id))


def test_a_scrap_card_can_carry_the_program_that_cut_it(tmp_path):
    h, _ = first_sheet(tmp_path)
    scrap(h, "Material: 6061\nThickness: 1/8\nLength: 30\nZeroed at: 2 along, 15 across")
    att = attach(h, "sc1", "bracket.tap", program(x1=6.0, y1=5.0))
    h.runner.tick()
    piece = pieces(h)["sc1"]
    assert piece["hand_cuts"] == [[14.88, 1.88, 21.12, 7.12]] and piece["programs"] == [att]
    assert "Recorded 1 hand cut" in h.tracker.comments_on("sc1")[-1]
