"""The Face label on the board (core 0.14.0): the sheet card gets the FACE program too, and says to run it first."""

from dataclasses import replace

from autocam_service.cards import parse_card
from autocam_service.jobs import face_spec
from fakeworker import run_fake_worker
from test_air_test import NO_STOP, programs, tick
from test_runner_autostart import harness, run, sheet, step_card

FACE = "6061_0p125_r001_S1_FACE.tap"


def faced_card(cid="c1", name="plate", qty=2):
    return replace(step_card(cid, name, qty=qty), labels=("face",))     # any case and spacing


def test_the_face_label_is_read_from_the_card():
    assert parse_card(faced_card()).face
    assert not parse_card(step_card("c1", "plate")).face


def test_the_face_settings_go_into_the_job(tmp_path):
    h = harness(tmp_path)
    spec = face_spec(h.cfg)
    assert spec.depth_in == 0.005 and spec.tool.guid == "4147915e-c136-481d-bcaa-213e9c8e6623"
    assert spec.tool.number == 2 and spec.tool.template_path.endswith("face_6mm.f3dhsm-template")


def test_a_faced_sheet_card_has_both_programs_and_says_to_face_first(tmp_path):
    h = harness(tmp_path, faced_card(), step_card("c2", "spacer"))
    run(h)
    s1 = sheet(h)
    assert sorted(programs(h, s1.id)) == ["6061_0p125_r001_S1.tap", FACE]
    card = h.tracker.cards[s1.id]
    assert "3 parts, 2 faced" in card.name and "19 min" in card.name          # 15 min cut + 4 min face
    desc = card.desc
    assert "Cutters: 6 mm 3-flute ALU (T2) to face, then 4 mm O-flute ALU (T1)." in desc
    assert (f"1. Face first: load the 6 mm 3-flute ALU (T2), measure it on the fixed tool setter, and run {FACE} "
            "(about 4 min). It skims 0.005 in off the tops of the parts marked faced, nothing else.") in desc
    assert "2. Then load the 4 mm O-flute ALU (T1), measure it, and run 6061_0p125_r001_S1.tap (about 15 min)." in desc
    assert "1. plate (1 of 2) - faced" in desc and "3. spacer" in desc and "3. spacer -" not in desc


def test_options_keep_the_face_program(tmp_path):
    h = harness(tmp_path, faced_card())
    run(h)
    s1 = sheet(h)
    h.runner.tick()                                                    # the Options box appears
    tick(h, s1.id, NO_STOP)
    h.runner.tick()
    assert set(programs(h, s1.id)) == {"6061_0p125_r001_S1_NOSTOP.tap", FACE}
    desc = h.tracker.cards[s1.id].desc
    assert f"run {FACE}" in desc and "run 6061_0p125_r001_S1_NOSTOP.tap" in desc


def test_a_face_program_that_fails_its_check_offers_nothing(tmp_path):
    h = harness(tmp_path, faced_card())
    run(h, face_too_deep=True)
    (s1,) = h.sheet_cards()
    assert programs(h, s1.id) == {}
    card = h.tracker.cards[s1.id]
    assert card.name.startswith("NOT CUTTABLE") and "goes below Z0.1199" in card.desc


def test_the_face_label_without_a_face_template_sends_the_card_back(tmp_path):
    h = harness(tmp_path, faced_card(), face__template="fusion/templates/no_such.f3dhsm-template")
    h.runner.tick()
    run_fake_worker(h.queue)
    h.runner.tick()
    card = h.tracker.cards["c1"]
    assert card.list_key == "needs_fixing"
    assert "The Face label needs the face template" in h.tracker.comments_on("c1")[-1]
