"""Runs that start from Ready for CAM, the "Nest this part" box, and new parts filling open sheets."""

from datetime import timedelta

import pytest

from autocam_service.tracker.base import Attachment, Card
from fakeworker import run_fake_worker
from test_runner_e2e import NOW, SYS, STUDIO, FlakyTracker, Harness

SYSTEM = Card(SYS, "System", "", "control", "https://trello.example/c/sys")


def step_card(cid, name, material="6061", qty=1, list_key="ready_for_cam"):
    """A part card with a .step attachment (no Onshape calls)."""
    return Card(cid, name, f"Qty: {qty}\nMaterial: {material}", list_key, f"https://trello.example/c/{cid}",
                attachments=(Attachment(f"att-{cid}", f"{name}.step", f"https://trello.example/a/{cid}"),))


def harness(tmp_path, *part_cards, **kw):
    h = Harness(tmp_path, card_list=[SYSTEM, *part_cards], **kw)
    for c in part_cards:
        h.tracker.downloads[f"att-{c.id}"] = f"ISO-10303-21; {c.name}".encode()
    return h


def add(h, card):
    h.tracker.cards[card.id] = card
    h.tracker.downloads[f"att-{card.id}"] = f"ISO-10303-21; {card.name}".encode()


def run(h, **worker):
    """One whole run: start it, let the fake Fusion worker do the jobs, publish."""
    h.runner.tick()
    run_fake_worker(h.queue, **worker)
    h.runner.tick()


def sheet(h, material="6061"):
    found = [c for c in h.sheet_cards() if c.name.startswith(material)]
    assert len(found) == 1, [c.name for c in h.sheet_cards()]
    return found[0]


def taps_on(h, card_id):
    return [n for n in h.files_on(card_id) if n.endswith(".tap")]


# ---------------------------------------------------------------- starting runs

def test_a_run_starts_two_minutes_after_the_last_card(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"), delay=120)
    h.runner.tick()
    h.now = NOW + timedelta(seconds=90)
    add(h, step_card("c2", "gusset"))                 # another card: the wait starts again
    h.runner.tick()
    h.now = NOW + timedelta(seconds=180)
    h.runner.tick()
    assert h.store.run_ids() == []
    h.now = NOW + timedelta(seconds=211)
    h.runner.tick()
    assert h.store.run_ids() == ["r001"] and h.queue.pending() == ["r001-al6061"]
    assert h.tracker.comments_on(SYS)[0] == "Run r001 started: 2 parts."


def test_a_card_left_waiting_does_not_start_runs_until_it_changes(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate", qty=40))
    run(h, defer_part="p01")
    assert h.list_of("c1") == "ready_for_cam" and "Didn't fit" in h.tracker.comments_on("c1")[-1]
    for minutes in (5, 10, 60):                       # the service's own comment is not a change
        h.now = NOW + timedelta(minutes=minutes)
        h.runner.tick()
    assert h.store.run_ids() == ["r001"]
    card = h.tracker.cards["c1"]
    h.tracker.cards["c1"] = card.__class__(**{**card.__dict__, "desc": "Qty: 4\nMaterial: 6061"})   # fixed it
    h.runner.tick()
    assert h.store.run_ids() == ["r001", "r002"]


def test_moving_a_card_out_and_back_starts_a_run_again(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate", qty=40))
    run(h, defer_part="p01")
    h.tracker.move("c1", "inbox")
    h.runner.tick()
    h.tracker.move("c1", "ready_for_cam")
    h.runner.tick()
    assert h.store.run_ids() == ["r001", "r002"]


def test_nest_box_unticked_means_placed_by_hand(tmp_path):
    from autocam_core.schema_job import read_job
    h = harness(tmp_path, step_card("c1", "plate"), step_card("c2", "gusset"),
                step_card("c3", "draft", list_key="inbox"),
                Card("tpl", "New part", "", "inbox", "u", is_template=True),
                Card("rm", "How to add a part (read me)", "", "inbox", "u"))
    h.tracker.add_checklist("c2", "Nest", ["Nest this part"], checked=False)
    h.runner.tick()
    # every part card got a ticked box; the template, the read-me card and c2's own box were left alone
    assert h.tracker.checklists[("c1", "Nest")] == [["Nest this part", True]]
    assert h.tracker.checklists[("c3", "Nest")] == [["Nest this part", True]]
    assert h.tracker.checklists[("c2", "Nest")] == [["Nest this part", False]]
    assert ("tpl", "Nest") not in h.tracker.checklists and ("rm", "Nest") not in h.tracker.checklists
    assert h.tracker.comments_on(SYS)[0] == "Run r001 started: 2 parts."
    job = read_job(next(h.queue.incoming.glob("r001-*.json")))
    assert {p.card_id: p.by_hand for p in job.parts} == {"c1": False, "c2": True}


def test_cards_over_the_onshape_limit_wait_for_the_next_run(tmp_path):
    two = [Card("ca", "hood_gusset", f"Qty: 2\n{STUDIO}", "ready_for_cam", "https://trello.example/c/ca"),
           Card("cb", "window", f"Qty: 1\n{STUDIO}", "ready_for_cam", "https://trello.example/c/cb", ("Smoked",)),
           step_card("cd", "spacer", "5052")]
    h = harness(tmp_path, *two, onshape__per_run_max_calls=8)
    h.runner.tick()
    assert sorted(h.queue.pending()) == ["r001-al5052", "r001-al6061"]          # cb would have made it 11 calls
    assert "1 wait for the next run" in h.tracker.comments_on(SYS)[0]
    run_fake_worker(h.queue)
    h.runner.tick()                                   # publishes r001
    h.runner.tick()                                   # cb starts the next run straight away
    assert h.queue.pending() == ["r002-pc_smoked"]


# ---------------------------------------------------------------- open sheets

def test_a_new_part_joins_the_open_sheet_and_the_card_is_updated_in_place(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate", qty=2))
    run(h)
    s1 = sheet(h)
    assert taps_on(h, s1.id) == ["6061_0p125_r001_S1.tap"]
    add(h, step_card("c2", "gusset"))
    h.runner.tick()
    assert "added to open sheets with 1 already there" in h.tracker.comments_on(SYS)[-1]
    run_fake_worker(h.queue)
    h.runner.tick()

    after = sheet(h)
    assert after.id == s1.id                                          # same card, same place
    assert after.name == "6061 1/8in - 4 mm O-flute ALU - 3 parts - 15 min - r002 S1"
    assert taps_on(h, s1.id) == ["6061_0p125_r002_S1.tap"]           # the old program is gone
    assert h.tracker.checklist(s1.id, "Review").done == 0
    assert "Rebuilt with new parts (run r002)" in h.tracker.comments_on(s1.id)[-1]
    assert h.tracker.covers[s1.id] in h.tracker.files
    assert "CUT ORDER\n1. gusset\n2. plate (1 of 2)\n3. plate (2 of 2)" in after.desc
    assert h.list_of("c2") == "nested" and h.tracker.comments_on("c2")[-1] == "On sheet S1 (run r002)."
    assert len(h.tracker.comments_on("c1")) == 1                      # still on the same card: nothing to say
    assert [url for cid, url, _ in h.tracker.links if cid in ("c1", "c2")] == [s1.url, s1.url]
    assert h.tracker.comments_on(SYS)[-1] == "Run r002 done: 1 sheet updated in Sheet review."
    assert len(h.sheet_cards()) == 1
    h.tracker.move(s1.id, "cut")                                      # both parts follow it to Cut
    h.runner.tick()
    assert h.list_of("c1") == "cut" and h.list_of("c2") == "cut"


def test_a_reviewed_sheet_is_never_changed(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    run(h)
    s1 = sheet(h)
    h.tracker.checklists[(s1.id, "Review")][0][1] = True              # someone simulated it
    add(h, step_card("c2", "gusset"))
    run(h)
    sheets = [c for c in h.sheet_cards() if c.name.startswith("6061")]
    assert len(sheets) == 2
    assert taps_on(h, s1.id) == ["6061_0p125_r001_S1.tap"] and h.tracker.cards[s1.id].name.endswith("r001 S1")


def test_other_materials_are_left_alone(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    run(h)
    s1 = sheet(h)
    add(h, step_card("c2", "spacer", "5052"))
    run(h)
    assert taps_on(h, s1.id) == ["6061_0p125_r001_S1.tap"]
    assert sheet(h, "5052").name.endswith("r002 S1")


def test_review_started_during_the_run_keeps_the_sheet_and_the_new_part_waits(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    run(h)
    s1 = sheet(h)
    add(h, step_card("c2", "gusset"))
    h.runner.tick()                                   # r002 takes the open sheet with it
    h.tracker.checklists[(s1.id, "Review")][0][1] = True              # ...and then someone starts reviewing it
    run_fake_worker(h.queue)
    h.runner.tick()
    assert taps_on(h, s1.id) == ["6061_0p125_r001_S1.tap"] and h.tracker.cards[s1.id].name.endswith("r001 S1")
    assert h.list_of("c2") == "ready_for_cam" and "Waiting for the next run" in h.tracker.comments_on("c2")[-1]
    assert h.tracker.comments_on(SYS)[-1] == "Run r002 done: no sheet changed."
    run(h)                                            # r003: the sheet is frozen now, so c2 gets its own
    assert h.list_of("c2") == "nested" and len(h.sheet_cards()) == 2


def test_nothing_new_on_the_sheet_keeps_its_program(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    run(h)
    s1 = sheet(h)
    add(h, step_card("c2", "gusset", qty=40))
    run(h, defer_part="p01")                          # the new part didn't fit
    assert taps_on(h, s1.id) == ["6061_0p125_r001_S1.tap"] and h.tracker.cards[s1.id].name.endswith("r001 S1")
    assert h.list_of("c2") == "ready_for_cam" and h.list_of("c1") == "nested"


def test_a_part_pushed_off_its_sheet_goes_back_to_ready_for_cam(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    run(h)
    s1 = sheet(h)
    add(h, step_card("c2", "big_plate"))
    run(h, defer_part="p02")                          # p02 is c1, carried from the open sheet
    assert h.list_of("c1") == "ready_for_cam" and "Didn't fit with the new parts" in h.tracker.comments_on("c1")[-1]
    assert [url for cid, url, _ in h.tracker.links if cid == "c1"] == []          # its link to S1 is gone
    assert h.list_of("c2") == "nested" and sheet(h).id == s1.id and "big_plate" in sheet(h).desc
    assert "plate (" not in sheet(h).desc.split("CUT ORDER")[1].replace("big_plate", "")
    run(h)                                            # it came back, so the next run nests it again
    assert h.list_of("c1") == "nested" and sheet(h).id == s1.id and sheet(h).name.endswith("r003 S1")


def test_a_changed_part_dragged_back_rebuilds_its_open_sheet(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"), step_card("c2", "gusset"))
    run(h)
    s1 = sheet(h)
    h.tracker.move("c1", "ready_for_cam")             # edited in Onshape, sent again
    run(h)
    assert sheet(h).id == s1.id and sheet(h).name.endswith("r002 S1")
    assert sheet(h).desc.count("plate") == 1          # once, not the old copy and the new one
    assert [url for cid, url, _ in h.tracker.links if cid == "c1"] == [s1.url]


def test_spare_open_sheets_are_archived(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    run(h)
    a = sheet(h)
    h.tracker.checklists[(a.id, "Review")][0][1] = True               # frozen while c2 gets its own sheet
    add(h, step_card("c2", "gusset"))
    run(h)
    b = next(c for c in h.sheet_cards() if c.id != a.id)
    h.tracker.checklists[(a.id, "Review")][0][1] = False              # un-ticked: both are open again
    add(h, step_card("c3", "bracket"))
    run(h)                                            # the fake worker fits everything on one sheet
    assert a.id not in h.tracker.archived and b.id in h.tracker.archived
    assert taps_on(h, b.id) == [] and "Not needed any more" in h.tracker.comments_on(b.id)[-1]
    assert h.tracker.cards[a.id].name == "6061 1/8in - 4 mm O-flute ALU - 3 parts - 15 min - r003 S1"
    assert [url for cid, url, _ in h.tracker.links if cid == "c2"] == [a.url]
    h.tracker.move(a.id, "cut")
    h.runner.tick()
    assert all(h.list_of(c) == "cut" for c in ("c1", "c2", "c3"))


def test_crash_while_updating_a_sheet_resumes_without_duplicates(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    run(h)
    s1 = sheet(h)
    flaky = FlakyTracker(list(h.tracker.cards.values()), fail_on_call=1)   # r002's program upload fails once
    for k in ("files", "links", "link_ids", "checklists", "covers", "comments", "downloads"):
        setattr(flaky, k, getattr(h.tracker, k))
    h.runner = h.make_runner(flaky)
    add(h, step_card("c2", "gusset"))
    h.runner.tick()
    run_fake_worker(h.queue)
    with pytest.raises(ConnectionError):
        h.runner.tick()
    assert taps_on(h, s1.id) == []                    # mid-update: nothing to cut
    h.runner.tick()
    assert taps_on(h, s1.id) == ["6061_0p125_r002_S1.tap"] and len(h.sheet_cards()) == 1
    assert sum("Rebuilt" in t for t in h.tracker.comments_on(s1.id)) == 1
    assert h.tracker.checklist(s1.id, "Review").total == 2 and h.list_of("c2") == "nested"


def test_a_new_part_that_breaks_the_sheet_leaves_it_alone(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    run(h)
    s1 = sheet(h)
    add(h, step_card("c2", "gusset"))
    run(h, reject_sheet=True)
    assert taps_on(h, s1.id) == ["6061_0p125_r001_S1.tap"] and h.tracker.cards[s1.id].name.endswith("r001 S1")
    assert h.list_of("c2") == "needs_fixing" and "With this part added" in h.tracker.comments_on("c2")[-1]
    assert h.list_of("c1") == "nested" and len(h.tracker.comments_on("c1")) == 1


def test_a_dragged_back_part_waits_if_its_sheet_is_being_reviewed(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"), step_card("c2", "gusset"))
    run(h)
    s1 = sheet(h)
    h.tracker.move("c1", "ready_for_cam")
    h.runner.tick()
    h.tracker.checklists[(s1.id, "Review")][0][1] = True
    run_fake_worker(h.queue)
    h.runner.tick()
    assert h.list_of("c1") == "ready_for_cam" and "Waiting for the next run" in h.tracker.comments_on("c1")[-1]
    assert taps_on(h, s1.id) == ["6061_0p125_r001_S1.tap"]


def test_runs_from_before_the_change_report_on_the_system_card(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    h.runner.tick()
    state = h.store.active()
    state.trigger_card = "6ac0026df4db9bd12416eb1f"          # the retired Run nest card
    h.store.save(state)
    run_fake_worker(h.queue)
    h.runner.tick()
    assert h.tracker.comments_on(SYS)[-1] == "Run r001 done: 1 new sheet in Sheet review."
    assert h.tracker.comments_on("6ac0026df4db9bd12416eb1f") == []


def test_a_part_cut_before_doesnt_stop_its_open_sheet_from_being_rebuilt(tmp_path):
    # Seen on the board (r010): the part cards had been on r006, already cut, which kept r009 from reopening.
    h = harness(tmp_path, step_card("c1", "plate"), step_card("c2", "gusset"))
    run(h)
    first = sheet(h)
    h.tracker.move(first.id, "cut")
    h.runner.tick()                                       # both parts follow it to Cut
    for c in ("c1", "c2"):
        h.tracker.move(c, "ready_for_cam")                # to be cut again
    run(h)
    second = next(c for c in h.sheet_cards())
    h.tracker.move("c1", "ready_for_cam")                 # changed, sent again while its new sheet is open
    run(h)
    assert [c.id for c in h.sheet_cards()] == [second.id] and "r003 S1" in h.tracker.cards[second.id].name
