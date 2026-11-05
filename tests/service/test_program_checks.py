"""Hand-made programs get AutoCAM's checks (2026-11-04, program_checks.py): the guard, the mist for the material and
their own stops, whether AutoCAM places them or a card with the Check label only asks for them to be checked."""

from autocam_core.pauses import stop_problems
from autocam_service.own_programs import shift_program
from autocam_service.tracker.base import Attachment, Card
from test_handprograms import program
from test_own_programs import own_card, refused, sheet_cards
from test_runner_autostart import harness

SAFE = 1.875                    # 1/8 in plate + 1.5 in clamps + 0.25 margin


def with_stop(stop=("G53 Z", "M5", "M12 C8", "G53 P10", "G4", "M11 C8", "S18000", "M3", "G4 X4.", "G0 X5. Y0.")):
    """program() with a second square after a stop between them."""
    lines = program().decode("ascii").split("\r\n")
    at = lines.index("G0 Z2.125") + 1
    second = ["G0 Z0.325", "G1 Z0. F20.", "G1 X8. F60.", "G1 X5.", "G0 Z2.125"]
    return ("\r\n".join(lines[:at] + list(stop) + second + lines[at:])).encode("ascii")


# ---------------------------------------------------------------- the program's own stops

def test_a_stop_like_autocams_passes():
    assert stop_problems(with_stop().decode(), SAFE, mist=True) == []
    assert stop_problems(program().decode(), SAFE, mist=True) == []           # no stops, nothing to say
    assert stop_problems("G4 X4.\nG0 Z0.1\n", SAFE, mist=False) == []         # a dwell isn't a stop


def test_a_stop_low_over_the_plate_or_with_the_mist_on_fails():
    high = with_stop(("M5", "M12 C8", "G4", "M11 C8", "S18000", "M3", "G0 X5. Y0."))   # still at Z2.125: clear
    assert stop_problems(high.decode(), SAFE, mist=True) == []
    lower = with_stop(("G0 Z1.", "M5", "M12 C8", "G4", "M11 C8", "S18000", "M3", "G0 X5. Y0."))
    problems = stop_problems(lower.decode(), SAFE, mist=True)
    assert any("stops with the cutter at Z1, below Z1.875" in p for p in problems)
    assert any("first sideways move is at Z1" in p for p in problems)
    misty = with_stop(("G53 Z", "M5", "G4", "S18000", "M3", "G0 X5. Y0.")).decode()
    line = misty.split("\r\n").index("G4") + 1
    assert stop_problems(misty, SAFE, mist=True) == [f"line {line}: stops with the mist on (M12 C8 first)"]


def test_after_a_stop_the_mist_comes_back_on_before_cutting():
    dry = with_stop(("G53 Z", "M5", "M12 C8", "G4", "S18000", "M3", "G0 X5. Y0."))
    [p] = stop_problems(dry.decode(), SAFE, mist=True)
    assert "with the mist still off (M11 C8 after the stop)" in p
    assert stop_problems(dry.decode(), SAFE, mist=False) == []                 # polycarbonate: no mist anyway


# ---------------------------------------------------------------- own programs AutoCAM places

def test_an_own_program_with_the_wrong_mist_is_refused(tmp_path):
    h = harness(tmp_path)
    own_card(h, desc="Material: PC\nThickness: 1/8")                          # posted with the mist box ticked
    h.runner.tick()
    assert "mist codes present but mist is off for this material" in refused(h)


def test_an_own_program_with_an_unsafe_stop_is_refused(tmp_path):
    h = harness(tmp_path)
    own_card(h, data=with_stop(("G0 Z1.", "M5", "M12 C8", "G4", "M11 C8", "S18000", "M3", "G0 X5. Y0.")))
    h.runner.tick()
    assert "stops with the cutter at Z1" in refused(h)


# ---------------------------------------------------------------- the Check label

def on_sheet(data: bytes) -> bytes:
    """A program zeroed at a sheet's front-left corner: its part well inside the clamps and the reach."""
    return shift_program(data.decode("ascii"), 3.0, 4.0).encode("ascii")


def check_card(h, cid="k1", desc="Material: 6061\nThickness: 1/8", data=None, list_key="ready_for_cam",
               files=("P-3001.tap",)):
    atts = tuple(Attachment(f"tap-{cid}-{i}", name, f"https://trello.example/a/{cid}/{i}") for i, name in enumerate(files))
    card = Card(cid, "Gusset by hand", desc, list_key, f"https://trello.example/c/{cid}", labels=("Check",),
                attachments=atts)
    h.tracker.cards[cid] = card
    for a in atts:
        h.tracker.downloads[a.id] = data if data is not None else on_sheet(program())
    return card


def test_a_program_that_passes_goes_back_to_drafts_and_nothing_is_placed(tmp_path):
    h = harness(tmp_path)
    check_card(h)
    h.runner.tick()
    assert h.tracker.cards["k1"].list_key == "inbox" and not sheet_cards(h)
    [comment] = h.tracker.comments_on("k1")
    assert comment.startswith("Checked P-3001.tap: it passes AutoCAM's checks.")
    assert "Checked as" in comment and "X/Y zeroed at a full sheet's front-left corner" in comment
    assert "Clear of the clamps, inside the machine's reach and on the sheet." in comment
    assert "Mist on, as 6061 needs." in comment and "Still simulate it in Fusion" in comment
    assert not list(h.queue.incoming.glob("*.json"))                              # no run for it
    h.runner.tick()
    assert len(h.tracker.comments_on("k1")) == 1                                  # in Drafts: checked once


def test_a_program_that_fails_goes_to_needs_fixing_saying_why(tmp_path):
    h = harness(tmp_path)
    check_card(h, data=program(x1=30.0))                                         # off the sheet, past the clamps
    h.runner.tick()
    assert h.tracker.cards["k1"].list_key == "needs_fixing" and not sheet_cards(h)
    [comment] = h.tracker.comments_on("k1")
    assert comment.startswith("Checked P-3001.tap: it fails AutoCAM's checks.")
    assert "too low over a clamp" in comment and "Add a line Place it myself" in comment
    assert "move this card back to Ready for CAM to check it again" in comment


def test_in_drafts_it_stays_and_a_new_file_is_checked_again(tmp_path):
    h = harness(tmp_path)
    card = check_card(h, list_key="inbox", data=program())                     # at X0 Y0: in the left clamp strip
    h.runner.tick()
    assert h.tracker.cards["k1"].list_key == "inbox"
    [first] = h.tracker.comments_on("k1")
    assert "it fails" in first and "It's checked again by itself." in first
    h.tracker.cards["k1"] = Card(card.id, card.name, card.desc, "inbox", card.url, labels=card.labels,
                                 attachments=card.attachments + (Attachment("tap-new", "P-3001 v2.tap", "u"),))
    h.tracker.downloads["tap-new"] = on_sheet(program())
    h.runner.tick()
    comments = h.tracker.comments_on("k1")
    assert len(comments) == 2 and comments[1].startswith("Checked P-3001 v2.tap: it passes")


def test_place_it_myself_skips_the_clamps_reach_and_sheet(tmp_path):
    h = harness(tmp_path)
    check_card(h, desc="Material: 6061\nThickness: 1/8\nPlace it myself", data=program(x1=30.0))
    h.runner.tick()
    [comment] = h.tracker.comments_on("k1")
    assert "it passes" in comment and "zeroed wherever you put it" in comment
    assert "Clear of the clamps" not in comment


def test_missing_lines_or_no_program_are_said(tmp_path):
    h = harness(tmp_path)
    check_card(h, desc="Material: 6061")
    h.runner.tick()
    [comment] = h.tracker.comments_on("k1")
    assert comment.startswith("Not checked:") and "No Thickness line" in comment
    assert h.tracker.cards["k1"].list_key == "needs_fixing"

    (tmp_path / "2").mkdir()
    h = harness(tmp_path / "2")
    check_card(h, files=())
    h.runner.tick()
    assert "No program attached" in h.tracker.comments_on("k1")[0]


def test_zeroed_on_top_of_the_plate_is_explained(tmp_path):
    h = harness(tmp_path)
    check_card(h, data=program(z_cut="-0.13"))
    h.runner.tick()
    [comment] = h.tracker.comments_on("k1")
    assert "Z0 on the spoilboard" in comment and h.tracker.cards["k1"].list_key == "needs_fixing"


def test_wrong_mist_for_the_material_fails_with_the_box_to_untick(tmp_path):
    h = harness(tmp_path)
    check_card(h, desc="Material: PC\nThickness: 1/8")
    h.runner.tick()
    [comment] = h.tracker.comments_on("k1")
    assert "it fails" in comment and "mist codes present but mist is off for this material" in comment
    assert "untick Use mist coolant" in comment
