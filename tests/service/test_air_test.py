"""The "Air test" box on sheet cards: the sheet's checked program, raised so it cuts nothing."""

import shutil

from autocam_core.tapguard import parse_code
from test_runner_autostart import add, harness, run, sheet, step_card, taps_on


def air_files(h, card_id):
    return [(aid, name, data) for aid, (cid, name, data) in h.tracker.files.items()
            if cid == card_id and name.endswith("_AIRTEST.tap")]


def lowest_z(data):
    zs = []
    for line in data.decode("ascii").splitlines():
        if line.startswith("[") or not line.strip():
            continue
        words = parse_code(line)
        if ("G", 53.0) not in words:
            zs += [v for letter, v in words if letter == "Z" and v is not None]
    return min(zs)


def test_tick_the_box_to_get_an_air_test_and_untick_to_remove_it(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate", qty=2))
    run(h)
    s1 = sheet(h)
    h.runner.tick()
    assert h.tracker.checklists[(s1.id, "Air test")] == [["Add an air test program (cuts nothing)", False]]
    assert air_files(h, s1.id) == []                                   # off by default

    h.tracker.tick_all(s1.id, "Air test")
    h.runner.tick()
    [(_, name, data)] = air_files(h, s1.id)
    assert name == "6061_0p125_r001_S1_AIRTEST.tap"
    assert lowest_z(data) == 0.625                                     # 1/8 in sheet + 0.5 in gap
    assert data.count(b"M0") == 1 and b"[AIR TEST - RAISED 0.625 IN" in data   # the real pause is kept
    assert "Air test added: 6061_0p125_r001_S1_AIRTEST.tap" in h.tracker.comments_on(s1.id)[-1]
    real = next(d for _, (c, n, d) in h.tracker.files.items() if c == s1.id and n == "6061_0p125_r001_S1.tap")
    assert lowest_z(real) == 0.0                                       # the real program is untouched
    h.runner.tick()
    assert len(air_files(h, s1.id)) == 1                               # not added twice

    h.tracker.tick_all(s1.id, "Air test", done=False)
    h.runner.tick()
    assert air_files(h, s1.id) == [] and taps_on(h, s1.id) == ["6061_0p125_r001_S1.tap"]


def test_a_rebuilt_sheet_gets_a_new_air_test(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    run(h)
    s1 = sheet(h)
    h.runner.tick()
    h.tracker.tick_all(s1.id, "Air test")
    h.runner.tick()
    add(h, step_card("c2", "gusset"))
    run(h)                                                             # c2 joins the open sheet
    h.runner.tick()
    [(_, name, data)] = air_files(h, s1.id)
    assert name == "6061_0p125_r002_S1_AIRTEST.tap" and data.count(b"[outer") == 2
    assert h.tracker.checklists[(s1.id, "Air test")][0][1] is True    # the box keeps its state


def test_works_in_ready_to_cut_and_says_why_when_it_cannot(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    run(h)
    s1 = sheet(h)
    h.runner.tick()
    h.tracker.tick_all(s1.id, "Review")
    card = h.tracker.cards[s1.id]
    h.tracker.cards[s1.id] = card.__class__(**{**card.__dict__, "list_key": "ready_to_cut"})   # a person moves it
    shutil.rmtree(h.queue.done / "r001-al6061")                        # the job folder was cleaned up
    h.tracker.tick_all(s1.id, "Air test")
    h.runner.tick()
    h.runner.tick()
    assert h.list_of(s1.id) == "ready_to_cut" and air_files(h, s1.id) == []
    said = [c for c in h.tracker.comments_on(s1.id) if c.startswith("Couldn't make the air test")]
    assert said == ["Couldn't make the air test: this sheet's checked program isn't in the job folder any more."]


def test_no_box_on_a_sheet_that_cannot_be_cut(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    h.runner.tick()
    from fakeworker import run_fake_worker
    run_fake_worker(h.queue, reject_sheet=True)
    h.runner.tick()
    h.runner.tick()
    bad = sheet(h, "NOT CUTTABLE")
    assert (bad.id, "Air test") not in h.tracker.checklists
