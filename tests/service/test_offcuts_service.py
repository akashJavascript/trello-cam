"""Offcuts on the board: kept when a sheet is cut, offered to the next run, reserved, used up (offcuts.py)."""

import json

from autocam_core.schema_job import read_job
from fakeworker import run_fake_worker
from test_runner_autostart import add, harness, run, sheet, step_card

KEEP = "Keep the rest of the sheet for the next run"


def offcut_cards(h):
    return [c for c in h.tracker.cards.values() if c.list_key == "offcuts" and c.id not in h.tracker.archived]


def cut(h, card_id):
    card = h.tracker.cards[card_id]
    h.tracker.cards[card_id] = card.__class__(**{**card.__dict__, "list_key": "cut"})   # a person moves it
    h.runner.tick()


def first_sheet(tmp_path, qty=2):
    h = harness(tmp_path, step_card("c1", "plate", qty=qty))
    run(h)
    s1 = sheet(h)
    h.runner.tick()                                       # the boxes appear
    return h, s1


def test_cutting_a_sheet_keeps_the_rest_as_an_offcut(tmp_path):
    h, s1 = first_sheet(tmp_path)
    assert h.tracker.checklists[(s1.id, "Offcut")] == [[KEEP, True]]                 # ticked to start
    cut(h, s1.id)
    [off] = offcut_cards(h)
    assert off.name == "6061 1/8in offcut - 38 in free"           # turned round: 0.5 to 39 in, nearly a whole sheet
    assert "Last cut: r001 S1. Used along its length: 0.5 to 8.5 in." in off.desc
    assert h.tracker.comments_on(s1.id)[-1] == f"The rest of the sheet is in Offcuts: {off.url}"
    assert h.store.sheet_cards()[s1.id]["cut"] and h.list_of("c1") == "cut"
    h.runner.tick()
    assert len(offcut_cards(h)) == 1                                                  # not made twice


def test_unticking_keep_means_no_offcut(tmp_path):
    h, s1 = first_sheet(tmp_path)
    h.tracker.tick_all(s1.id, "Offcut", done=False)
    cut(h, s1.id)
    assert offcut_cards(h) == []


def test_the_next_run_nests_onto_the_offcut_and_says_how_to_load_it(tmp_path):
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off] = offcut_cards(h)
    add(h, step_card("c2", "gusset"))
    h.runner.tick()
    job = read_job(next(h.queue.incoming.glob("*.json")))
    assert [(o.id, o.used_in, o.last_turned) for o in job.offcuts] == [(off.id, ((0.5, 8.5),), False)]
    run_fake_worker(h.queue)
    h.runner.tick()
    s2 = sheet(h)
    assert s2.name.startswith("6061 1/8in offcut - 4 mm O-flute ALU")
    assert (f"Stock: the 6061 1/8in offcut, not a new sheet ({off.url}). Put it in the same way round as for "
            "r001 S1: the end where its parts were cut at the zero corner (front left, by you).") in s2.desc
    assert "Spin" not in s2.desc
    assert json.loads(h.store.offcuts_file.read_text())[off.id]["reserved_by"] == s2.id
    # while that sheet holds it, another material's run can't, and a reviewed one keeps it
    h.tracker.checklists[(s2.id, "Review")][0][1] = True
    add(h, step_card("c3", "bracket"))
    h.runner.tick()
    job3 = read_job(next(h.queue.incoming.glob("r003*.json")))
    assert job3.offcuts == ()


def test_an_offcut_is_updated_after_each_cut_and_archived_when_used_up(tmp_path):
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off] = offcut_cards(h)
    add(h, step_card("c2", "gusset", qty=9))              # about 36 in of the 38.5 free
    run(h)
    s2 = sheet(h)
    cut(h, s2.id)
    piece = json.loads(h.store.offcuts_file.read_text())
    assert off.id not in piece and off.id in h.tracker.archived                      # under 6 in left
    assert h.tracker.comments_on(off.id)[-1] == "Used up: r002 S1 was cut from it."


def test_an_offcut_card_archived_by_hand_is_forgotten(tmp_path):
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off] = offcut_cards(h)
    h.tracker.archive(off.id)                              # someone threw the sheet away
    add(h, step_card("c2", "gusset"))
    h.runner.tick()
    job = read_job(next(h.queue.incoming.glob("*.json")))
    assert job.offcuts == () and off.id not in json.loads(h.store.offcuts_file.read_text())


def test_an_offcut_with_room_left_is_updated(tmp_path):
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off] = offcut_cards(h)
    add(h, step_card("c2", "gusset", qty=2))
    run(h)
    cut(h, sheet(h).id)
    [after] = offcut_cards(h)
    assert after.id == off.id and after.name == "6061 1/8in offcut - 30 in free"      # spun round: 0.5 to 30.5 in
    assert h.tracker.comments_on(off.id)[-1] == "r002 S1 was cut from it. 30 in free now."
    piece = json.loads(h.store.offcuts_file.read_text())[off.id]
    assert piece["used"] == [[0.5, 8.5], [9.0, 17.0]] and piece["reserved_by"] is None   # same way round both times
    assert piece["last"]["turned"] is False


def test_an_open_sheet_on_an_offcut_keeps_it_when_rebuilt(tmp_path):
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off] = offcut_cards(h)
    add(h, step_card("c2", "gusset"))
    run(h)
    s2 = sheet(h)
    add(h, step_card("c3", "bracket"))                     # joins the open sheet s2
    run(h)
    assert sheet(h).id == s2.id and sheet(h).name.startswith("6061 1/8in offcut") and "r003 S1" in sheet(h).name
    assert json.loads(h.store.offcuts_file.read_text())[off.id]["reserved_by"] == s2.id


def test_the_load_line_says_when_to_spin_it():
    from autocam_service.offcuts import load_line
    same = load_line("6061 3/16in", "u", "r006 S1", (0.5, 7.3), False, 48.0, last_turned=False)
    assert "the same way round as for r006 S1: the end where its parts were cut at the zero corner" in same
    spun = load_line("6061 3/16in", "u", "r006 S1", (0.5, 7.3), True, 48.0, last_turned=False)
    assert ("Spin it round from how it was for r006 S1 (flat, same side up, don't flip it over): the end where its "
            "parts were cut goes at the far end (hanging off the bed).") in spun
