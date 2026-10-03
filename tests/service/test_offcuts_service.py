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
    assert off.name == "#1 - 6061 1/8in offcut - 38 in free"      # turned round: 0.5 to 39 in, nearly a whole sheet
    assert "Last cut: r001 S1. Used along its length: 0.5 to 8.5 in." in off.desc
    assert h.tracker.comments_on(s1.id)[-1] == (f"The rest of the sheet is offcut #1: {off.url}. Write #1 on it "
                                                 "before it goes on the rack.")
    assert "Write #1 on the piece" in off.desc
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
    assert s2.name.startswith("6061 1/8in offcut #1 - 4 mm O-flute ALU")
    assert (f"Stock: offcut #1 (6061 1/8in), not a new sheet ({off.url}). Put it in the same way round as for "
            "r001 S1: the end where its parts were cut at the front (by you).") in s2.desc
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
    assert after.id == off.id and after.name == "#1 - 6061 1/8in offcut - 30 in free"      # spun round: 0.5 to 30.5 in
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
    assert "the same way round as for r006 S1: the end where its parts were cut at the front (by you)" in same
    spun = load_line("6061 3/16in", "u", "r006 S1", (0.5, 7.3), True, 48.0, last_turned=False)
    assert ("Spin it round from how it was for r006 S1 (flat, same side up, don't flip it over): the end where its "
            "parts were cut goes at the back (hanging off the bed).") in spun


def test_sheets_from_the_old_layout_are_retired_and_their_parts_sent_back(tmp_path):
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off] = offcut_cards(h)
    add(h, step_card("c2", "gusset"))
    run(h)
    s2 = sheet(h)
    info = h.store.sheet_cards()[s2.id]
    old = json.loads(h.store.job_text(info["job"]))
    old["core_version"] = "0.3.1"                         # as if it was made before the layout change
    h.store.save_job(info["job"], json.dumps(old))
    h.runner.tick()
    assert s2.id in h.tracker.archived and not [n for _, (c, n, _) in h.tracker.files.items() if c == s2.id]
    assert "turned 90 degrees" in h.tracker.comments_on(s2.id)[-1]
    assert h.list_of("c2") == "ready_for_cam" and "laid out the wrong way round" in h.tracker.comments_on("c2")[-1]
    assert json.loads(h.store.offcuts_file.read_text())[off.id]["reserved_by"] is None
    before = len(h.tracker.comments)
    h.runner.tick()
    assert len([c for c in h.tracker.comments if "turned 90" in c[1]]) == 1                # once


# ---- room beside the parts cut (offcuts.py)

def run_beside(h):
    run(h, beside=True)
    h.runner.tick()                                       # the boxes appear


def test_the_room_beside_a_sheets_parts_is_kept_with_its_offcut(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate", qty=2))
    run_beside(h)
    s1 = sheet(h)
    cut(h, s1.id)
    [off] = offcut_cards(h)
    assert off.name == "#1 - 6061 1/8in offcut - 38 in free + room for small parts"
    assert "Room beside the parts already cut, filled first with parts that fit: 15.0 x 8.0 in." in off.desc
    piece = json.loads(h.store.offcuts_file.read_text())[off.id]
    assert piece["beside"] == [[7.75, 0.5, 22.75, 8.5]]


def test_the_next_run_fills_the_room_beside_the_cut_first(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate", qty=2))
    run_beside(h)
    cut(h, sheet(h).id)
    [off] = offcut_cards(h)
    add(h, step_card("c2", "gusset"))
    h.runner.tick()
    job = read_job(next(h.queue.incoming.glob("*.json")))
    assert [(o.id, o.beside_in) for o in job.offcuts] == [(off.id, ((7.75, 0.5, 22.75, 8.5),))]
    run_fake_worker(h.queue, beside=True)
    h.runner.tick()
    s2 = sheet(h)
    assert "the end where its parts were cut at the front (by you)" in s2.desc        # loaded as before
    cut(h, s2.id)
    piece = json.loads(h.store.offcuts_file.read_text())[off.id]
    assert piece["beside"] == [] and piece["used"] == [[0.5, 8.5]]                   # the stretch is untouched
    assert piece["last"]["label"] == "r002 S1" and piece["reserved_by"] is None
    assert h.tracker.comments_on(off.id)[-1] == "r002 S1 was cut from it. 38 in free now."
    assert h.tracker.cards[off.id].name == "#1 - 6061 1/8in offcut - 38 in free"


def test_an_offcut_with_only_room_beside_its_cuts_is_kept(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate", qty=2))
    run_beside(h)
    cut(h, sheet(h).id)
    [off] = offcut_cards(h)
    piece = json.loads(h.store.offcuts_file.read_text())
    piece[off.id]["beside"] = []                          # so the next nest goes in the free stretch
    h.store.offcuts_file.write_text(json.dumps(piece))
    add(h, step_card("c2", "gusset", qty=9))              # about 36 in of the 38.5 free
    run_beside(h)
    cut(h, sheet(h).id)
    after = json.loads(h.store.offcuts_file.read_text())[off.id]                      # not used up: kept
    assert after["beside"] == [[7.75, 9.0, 22.75, 45.0]] and off.id not in h.tracker.archived
    assert h.tracker.cards[off.id].name == "#1 - 6061 1/8in offcut - small parts only"
    assert h.tracker.comments_on(off.id)[-1] == ("r002 S1 was cut from it. No free stretch now, plus room beside "
                                                "the cuts for small parts.")
    # the next run still gets it, for its room beside the cuts
    add(h, step_card("c3", "tab"))
    h.runner.tick()
    job = read_job(next(h.queue.incoming.glob("r003*.json")))
    assert [o.id for o in job.offcuts] == [off.id]
