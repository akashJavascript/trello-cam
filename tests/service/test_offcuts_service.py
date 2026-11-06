"""Offcuts on the board: kept when a sheet is cut, offered to the next run, reserved, used up (offcuts.py)."""

import json
from datetime import timedelta

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


def test_the_offcut_just_cut_is_on_the_bed_until_another_day_or_another_sheet_is_loaded(tmp_path):
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off] = offcut_cards(h)
    assert h.runner.on_bed() == off.id                     # the rest of the sheet just cut: still on the machine
    add(h, step_card("c2", "gusset"))
    h.runner.tick()
    job = read_job(next(h.queue.incoming.glob("*.json")))
    assert [(o.id, o.on_bed) for o in job.offcuts] == [(off.id, True)]
    machine = h.store.offcuts_file.with_name("machine.json")
    stamp = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")                   # noqa: E731
    machine.write_text(json.dumps({"loaded": {s1.id: stamp(h.now - timedelta(minutes=30))}}))
    assert h.runner.on_bed() == off.id                     # Loaded for the sheet it came from: still there
    machine.write_text(json.dumps({"loaded": {s1.id: stamp(h.now - timedelta(minutes=30)),
                                              "other": stamp(h.now + timedelta(minutes=1))}}))
    assert h.runner.on_bed() is None                       # another sheet went on the machine: it's on the rack
    machine.unlink()
    h.now += timedelta(days=1)
    assert h.runner.on_bed() is None                       # cut yesterday: taken off since


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


def test_the_cut_end_is_where_the_cuts_reach_the_edge_not_where_the_last_one_sat():
    from autocam_service.offcuts import load_line
    # offcut #4 after r101 (2026-11-05): H1/H2 at the front edge, r101's p0209 from 12.6 to 38.8 in, the back 9 in clean
    used = [(0.25, 12.38), (12.63, 38.755)]
    line = load_line("PC clear 1/8in", "u", "r101 S1", (12.63, 38.755), False, 48.0, last_turned=False, used=used)
    assert "the same way round as for r101 S1: the end where its parts were cut at the front (by you)" in line
    spun = load_line("PC clear 1/8in", "u", "r101 S1", (12.63, 38.755), True, 48.0, last_turned=False, used=used)
    assert "the end where its parts were cut goes at the back (hanging off the bed)" in spun
    # both ends cut about as close (offcut #3): the last cut decides, as before
    both = load_line("PC clear 1/8in", "u", "r100 S1", (38.96, 47.485), True, 48.0, last_turned=True,
                     used=[(0.5, 38.68), (38.96, 47.485)])
    assert "the end where its parts were cut at the front (by you)" in both


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


def test_parts_beside_earlier_cuts_that_reach_past_them_are_recorded(tmp_path):
    # Offcut #4 after r110 (2026-11-05): a second p0209 in the room beside r101's cut ran past that cut's band; its
    # stretch must be recorded, or a nest turned round could be put on it.
    h = harness(tmp_path, step_card("c1", "plate", qty=2))
    run_beside(h)
    cut(h, sheet(h).id)
    [off] = offcut_cards(h)
    before = json.loads(h.store.offcuts_file.read_text())[off.id]
    assert before["used"] == [[0.5, 8.5]] and before["beside"] == [[7.75, 0.5, 22.75, 8.5]]
    add(h, step_card("c2", "tab"))
    run(h, beside=True, beside_reach=1.25)                # in the room beside, 1.25 in past its end
    h.runner.tick()
    s2 = sheet(h)
    assert h.store.sheet_cards()[s2.id]["beside_along"] == [[0.5, 9.75]]
    cut(h, s2.id)
    after = json.loads(h.store.offcuts_file.read_text())[off.id]
    assert after["used"] == [[0.5, 9.75]]


def test_the_gap_after_earlier_cuts_is_said_to_be_on_purpose(tmp_path):
    """P-0509 started about 1 in past r068's cuts on offcut #3 and it looked like a mistake (2026-11-04): the slack
    went from 0.5 to 0.25 in, and the cards say the gap is meant."""
    h = harness(tmp_path, step_card("c1", "plate", qty=2), nest__offcut_gap_in=0.25)
    run(h)
    s1 = sheet(h)
    h.runner.tick()
    cut(h, s1.id)
    [off] = offcut_cards(h)
    # 0.5 in kept round the earlier parts, 0.25 in slack, 0.25 in for the new parts' cutter (said as 0.75 until
    # 2026-11-06, leaving out the room the new parts kept)
    assert "Parts put on it later start about 1 in past the earlier cuts, on purpose" in off.desc
    piece = h.runner.offcuts.get(off.id)
    assert h.runner._stock_text(off.id, piece["material"], piece["thickness_in"], False).endswith(
        "The parts start about 1 in past the earlier cuts on purpose (part spacing, plus a little in case it sits "
        "slightly differently).")
    # nested as still clamped from its last cut (core 0.31.0): 1/16 in of slack (2026-11-06), and the card says so
    assert h.runner._stock_text(off.id, piece["material"], piece["thickness_in"], False, still=True).endswith(
        "The parts start about 0.81 in past the earlier cuts on purpose. It was nested as still clamped from r001 S1, "
        "with almost no slack for loading it back: if it was taken off since, line it up exactly as it was.")


def test_a_polycarbonate_job_keeps_its_own_part_spacing(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate", material="PC"), step_card("c2", "bracket"))
    h.runner.tick()
    spacing = {read_job(f).material.key: read_job(f).nest.part_spacing_in for f in h.queue.incoming.glob("*.json")}
    assert spacing == {"pc_clear": 0.4, "al6061": 0.5}


def test_the_shop_keeps_a_quarter_inch_of_slack():
    from autocam_service.config import load_config
    assert load_config().nest.offcut_gap_in == 0.25


def test_a_sheet_nested_on_the_offcut_still_clamped_says_so(tmp_path):
    # core 0.31.0: the offcut the last sheet left, cut today with nothing loaded since, is nested with no loading
    # slack, and its sheet card tells whoever cuts it.
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    add(h, step_card("c2", "gusset"))
    run(h)
    s2 = sheet(h)
    [off] = offcut_cards(h)
    assert h.runner.on_bed() == off.id
    assert "It was nested as still clamped from r001 S1, with almost no slack for loading it back" in s2.desc
    assert "about 0.81 in past the earlier cuts" in s2.desc


def test_putting_the_piece_back_on_the_rack_takes_it_off_the_bed(tmp_path):
    # 2026-11-06, the user: the Done step says keep it clamped, with a button for putting it back on the rack.
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off] = offcut_cards(h)
    machine = h.store.offcuts_file.with_name("machine.json")
    machine.write_text(json.dumps({"racked": {s1.id: h.now.strftime("%Y-%m-%dT%H:%M:%SZ")}}))
    assert h.runner.on_bed() is None
    add(h, step_card("c2", "gusset"))
    h.runner.tick()
    job = read_job(next(h.queue.incoming.glob("*.json")))
    assert [(o.id, o.on_bed) for o in job.offcuts] == [(off.id, False)]
    machine.write_text(json.dumps({"racked": {"another-sheet": h.now.strftime("%Y-%m-%dT%H:%M:%SZ")}}))
    assert h.runner.on_bed() == off.id                     # another sheet's piece racked: this one still clamped
