"""The Options checklist on sheet cards: cut without stopping, and the air test (a raised copy that cuts nothing)."""

import shutil

from autocam_core.pauses import PauseSpec, insert
from autocam_core.tapguard import parse_code
from fakeworker import run_fake_worker
from test_runner_autostart import add, harness, run, sheet, step_card

NO_STOP = "Cut the whole sheet without stopping"
AIR = "Add an air test program (cuts nothing)"


def tick(h, card_id, item, done=True):
    for row in h.tracker.checklists[(card_id, "Options")]:
        if row[0] == item:
            row[1] = done


def programs(h, card_id):
    return {name: data for _, (cid, name, data) in h.tracker.files.items() if cid == card_id and name.endswith(".tap")}


def lowest_z(data):
    zs = []
    for line in data.decode("ascii").splitlines():
        if line.startswith("[") or not line.strip():
            continue
        words = parse_code(line)
        if ("G", 53.0) not in words:
            zs += [v for letter, v in words if letter == "Z" and v is not None]
    return min(zs)


def two_part_sheet(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate", qty=2))
    run(h)
    s1 = sheet(h)
    h.runner.tick()                                                    # the Options box appears
    return h, s1


def test_options_start_unticked(tmp_path):
    h, s1 = two_part_sheet(tmp_path)
    assert h.tracker.checklists[(s1.id, "Options")] == [[NO_STOP, False], [AIR, False]]
    assert list(programs(h, s1.id)) == ["6061_0p125_r001_S1.tap"]


def test_cut_without_stopping_swaps_the_program_and_back(tmp_path):
    h, s1 = two_part_sheet(tmp_path)
    stopping = programs(h, s1.id)["6061_0p125_r001_S1.tap"]
    assert stopping.count(b"M0") == 1 and "It stops after each part" in h.tracker.cards[s1.id].desc

    tick(h, s1.id, NO_STOP)
    h.runner.tick()
    [(name, plain)] = programs(h, s1.id).items()
    assert name == "6061_0p125_r001_S1_NOSTOP.tap" and b"M0" not in plain
    desc = h.tracker.cards[s1.id].desc
    assert "Program: 6061_0p125_r001_S1_NOSTOP.tap" in desc and "without stopping" in desc and "stops after" not in desc
    assert h.tracker.comments_on(s1.id)[-1] == "Now cuts the whole sheet without stopping: 6061_0p125_r001_S1_NOSTOP.tap."
    # exactly the posted program: put the stops back and it's the original byte for byte
    order = ["p01-1", "p01-2"]
    assert insert(plain.decode("ascii"), order, PauseSpec(mist=True)).encode("ascii") == stopping
    h.runner.tick()
    assert len(programs(h, s1.id)) == 1                                # nothing changes on the next pass

    tick(h, s1.id, NO_STOP, done=False)
    h.runner.tick()
    assert programs(h, s1.id) == {"6061_0p125_r001_S1.tap": stopping}
    assert "It stops after each part" in h.tracker.cards[s1.id].desc
    assert h.tracker.comments_on(s1.id)[-1] == "Back to stopping after each part: 6061_0p125_r001_S1.tap."


def test_air_test_on_and_off(tmp_path):
    h, s1 = two_part_sheet(tmp_path)
    tick(h, s1.id, AIR)
    h.runner.tick()
    files = programs(h, s1.id)
    air = files["6061_0p125_r001_S1_AIRTEST.tap"]
    assert lowest_z(air) == 0.625 and lowest_z(files["6061_0p125_r001_S1.tap"]) == 0.0
    assert air.count(b"M0") == 1 and b"[AIR TEST - RAISED 0.625 IN" in air
    assert "Air test added: 6061_0p125_r001_S1_AIRTEST.tap. It traces each part's outline once, at 200 in/min" in \
        h.tracker.comments_on(s1.id)[-1]
    feeds = {w for line in air.decode("ascii").split("\r\n") for w in line.split() if w.startswith("F")}
    assert feeds == {"F200."}
    h.runner.tick()
    assert len(programs(h, s1.id)) == 2                                # not added twice
    tick(h, s1.id, AIR, done=False)
    h.runner.tick()
    assert list(programs(h, s1.id)) == ["6061_0p125_r001_S1.tap"]


def test_both_options_air_test_the_program_that_runs(tmp_path):
    h, s1 = two_part_sheet(tmp_path)
    tick(h, s1.id, AIR)
    h.runner.tick()
    tick(h, s1.id, NO_STOP)
    h.runner.tick()
    files = programs(h, s1.id)
    assert sorted(files) == ["6061_0p125_r001_S1_NOSTOP.tap", "6061_0p125_r001_S1_NOSTOP_AIRTEST.tap"]
    assert b"M0" not in files["6061_0p125_r001_S1_NOSTOP_AIRTEST.tap"]


def test_a_rebuilt_sheet_gets_its_options_again(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    run(h)
    s1 = sheet(h)
    h.runner.tick()
    tick(h, s1.id, NO_STOP)
    tick(h, s1.id, AIR)
    h.runner.tick()
    add(h, step_card("c2", "gusset"))
    run(h)                                                             # c2 joins the open sheet
    h.runner.tick()
    files = programs(h, s1.id)
    assert sorted(files) == ["6061_0p125_r002_S1_NOSTOP.tap", "6061_0p125_r002_S1_NOSTOP_AIRTEST.tap"]
    assert files["6061_0p125_r002_S1_NOSTOP.tap"].count(b"[outer") == 2
    assert "without stopping" in h.tracker.cards[s1.id].desc
    assert [r[1] for r in h.tracker.checklists[(s1.id, "Options")]] == [True, True]   # the boxes keep their state


def test_one_part_sheets_never_stop_anyway(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    run(h)
    s1 = sheet(h)
    h.runner.tick()
    before = len(h.tracker.comments_on(s1.id))
    tick(h, s1.id, NO_STOP)
    h.runner.tick()
    assert list(programs(h, s1.id)) == ["6061_0p125_r001_S1.tap"]       # same program, no rename, nothing said
    assert len(h.tracker.comments_on(s1.id)) == before


def test_works_in_ready_to_cut_and_says_why_when_it_cannot(tmp_path):
    h, s1 = two_part_sheet(tmp_path)
    h.tracker.tick_all(s1.id, "Review")
    card = h.tracker.cards[s1.id]
    h.tracker.cards[s1.id] = card.__class__(**{**card.__dict__, "list_key": "ready_to_cut"})   # a person moves it
    shutil.rmtree(h.queue.done / "r001-al6061")                        # the job folder was cleaned up
    tick(h, s1.id, AIR)
    h.runner.tick()
    h.runner.tick()
    assert h.list_of(s1.id) == "ready_to_cut" and list(programs(h, s1.id)) == ["6061_0p125_r001_S1.tap"]
    said = [c for c in h.tracker.comments_on(s1.id) if c.startswith("Couldn't apply the options")]
    assert said == ["Couldn't apply the options: this sheet's checked program isn't in the job folder any more."]


def test_the_first_versions_air_test_box_and_file_are_replaced(tmp_path):
    # Seen on r005's card: the first version's ticked "Air test" box had attached an air test.
    h = harness(tmp_path, step_card("c1", "plate"))
    run(h)
    s1 = sheet(h)
    h.tracker.add_checklist(s1.id, "Air test", ["Add an air test program (cuts nothing)"], checked=True)
    h.tracker.attach_file(s1.id, "readme.txt", b"x", "text/plain")
    h.tracker._attach_file(s1.id, "6061_0p125_r001_S1_AIRTEST.tap", b"old air test", "text/plain")
    h.runner.tick()
    assert (s1.id, "Air test") not in h.tracker.checklists and (s1.id, "Options") in h.tracker.checklists
    h.runner.tick()
    assert list(programs(h, s1.id)) == ["6061_0p125_r001_S1.tap"]      # the old air test is gone until re-ticked


def test_no_options_on_a_sheet_that_cannot_be_cut(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    h.runner.tick()
    run_fake_worker(h.queue, reject_sheet=True)
    h.runner.tick()
    h.runner.tick()
    bad = sheet(h, "NOT CUTTABLE")
    assert (bad.id, "Options") not in h.tracker.checklists


def test_an_air_test_made_the_old_way_is_remade(tmp_path):
    h, s1 = two_part_sheet(tmp_path)
    tick(h, s1.id, AIR)
    h.runner.tick()
    [old] = [a for a, (c, n, _) in h.tracker.files.items() if c == s1.id and n.endswith("_AIRTEST.tap")]
    h.tracker.files[old] = (s1.id, h.tracker.files[old][1], b"the first version's air test")
    info = h.store.sheet_cards()[s1.id]
    h.store.set_options(s1.id, {**info["options"], "air": True})       # what the first version recorded
    h.runner.tick()
    files = programs(h, s1.id)
    assert len(files) == 2 and old not in h.tracker.files
    assert b"OUTLINES ONLY, ONE LAP EACH, AT 200 IPM" in files["6061_0p125_r001_S1_AIRTEST.tap"]
    assert h.store.sheet_cards()[s1.id]["options"]["air"] == "one-lap@200"


def test_sheet_use_line():
    import types
    from autocam_service.sheet_cards import sheet_use
    s = types.SimpleNamespace(parts_area_in2=288.0, usable_area_in2=808.5, free_length_in=14.4)
    assert sheet_use(s) == "Sheet use: 36% of the cutting area is parts, and the last 14 in are empty."
    assert sheet_use(types.SimpleNamespace(parts_area_in2=500.0, usable_area_in2=808.5, free_length_in=0.3)) == \
        "Sheet use: 62% of the cutting area is parts."
    assert sheet_use(types.SimpleNamespace(parts_area_in2=None, usable_area_in2=None, free_length_in=None)) is None
