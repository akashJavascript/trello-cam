"""Rush cards, offcuts freed when their sheet card is archived uncut, and the season's stock tally."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from autocam_core.schema_job import read_job
from autocam_service.tally import CutSheet, season_text
from fakeworker import run_fake_worker
from test_offcuts_service import cut, first_sheet, offcut_cards
from test_runner_autostart import add, harness, run, sheet, step_card
from test_runner_e2e import NOW, SYS


def rush_card(cid, name, label="Rush"):
    return replace(step_card(cid, name), labels=(label,))


def job_of(h, run_id):
    return read_job(next(h.queue.incoming.glob(f"{run_id}-*.json")))


# ---------------------------------------------------------------- Rush

def test_a_rush_card_is_nested_at_once_and_on_its_own(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    run(h)
    s1 = sheet(h)                                         # open: in Sheet review, nothing ticked
    h.runner.watch.delay_s = 120
    add(h, step_card("c2", "gusset"))
    add(h, rush_card("c3", "bracket", label=" rush "))    # any case and spacing
    h.runner.tick()
    # At once, with only the rush card: the open sheet isn't rebuilt and the other card keeps waiting.
    job = job_of(h, "r002")
    assert [p.card_id for p in job.parts] == ["c3"]
    assert h.store.load("r002").rush
    assert "Rush: nested right away and on their own" in "\n".join(h.tracker.comments_on(SYS))
    run_fake_worker(h.queue)
    h.runner.tick()
    rushed = [c for c in h.sheet_cards() if c.name.startswith("RUSH - ")]
    assert len(rushed) == 1 and rushed[0].name.endswith("r002 S1") and rushed[0].id != s1.id
    assert h.tracker.cards[s1.id].name.endswith("r001 S1")                         # left alone
    assert h.store.sheet_cards()[rushed[0].id]["rush"] is True
    # The other card's run starts after the usual wait, and fills the open sheet, not the rush sheet.
    h.now = NOW + timedelta(seconds=121)
    h.runner.tick()
    job3 = job_of(h, "r003")
    assert sorted(p.card_id for p in job3.parts) == ["c1", "c2"]                   # c1 carried from s1, not c3


def test_rush_waits_for_a_run_already_going_then_goes_first(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))
    h.runner.tick()                                       # r001 is in Fusion
    add(h, rush_card("c2", "bracket"))
    h.runner.tick()
    assert h.store.run_ids() == ["r001"]
    h.runner.report_health()
    assert "Ready for CAM: 1 card waiting (1 Rush, nested as soon as this run is done)." in h.tracker.cards[SYS].desc
    run_fake_worker(h.queue)
    h.runner.tick()                                       # r001 published
    h.runner.tick()
    assert h.store.run_ids() == ["r001", "r002"] and h.store.load("r002").rush


# ---------------------------------------------------------------- offcuts held by sheet cards that went away

def held_offcut(tmp_path):
    """An offcut, and a second sheet card nested onto it (which holds it)."""
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off] = offcut_cards(h)
    add(h, step_card("c2", "gusset"))
    run(h)
    s2 = sheet(h)
    assert json.loads(h.store.offcuts_file.read_text())[off.id]["reserved_by"] == s2.id
    return h, off, s2


def test_an_offcut_is_free_again_when_its_sheet_card_is_archived_uncut(tmp_path):
    h, off, s2 = held_offcut(tmp_path)
    h.tracker.archive(s2.id)                              # someone decided not to cut it
    h.runner.tick()
    piece = json.loads(h.store.offcuts_file.read_text())[off.id]
    assert piece["reserved_by"] is None and piece["used"] == [[0.5, 8.5]]          # nothing recorded as cut
    assert h.tracker.comments_on(off.id)[-1] == "Free again: r002 S1 was archived without being cut."
    assert h.store.sheet_cards()[s2.id]["archived"]
    add(h, step_card("c3", "bracket"))                    # the next run gets it
    h.runner.tick()
    assert [o.id for o in job_of(h, "r003").offcuts] == [off.id]


def test_an_offcut_is_free_again_when_its_sheet_card_is_deleted(tmp_path):
    h, off, s2 = held_offcut(tmp_path)
    del h.tracker.cards[s2.id]
    h.runner.tick()
    assert json.loads(h.store.offcuts_file.read_text())[off.id]["reserved_by"] is None
    assert h.tracker.comments_on(off.id)[-1] == "Free again: r002 S1 was deleted without being cut."


def test_a_sheet_card_archived_from_cut_counts_as_cut(tmp_path):
    h, off, s2 = held_offcut(tmp_path)
    h.tracker.cards[s2.id] = replace(h.tracker.cards[s2.id], list_key="cut")
    h.tracker.archive(s2.id)                              # before a pass saw it in Cut
    h.runner.tick()
    piece = json.loads(h.store.offcuts_file.read_text())[off.id]
    assert piece["reserved_by"] is None and len(piece["used"]) == 2                 # its stretch is recorded
    assert h.store.sheet_cards()[s2.id]["cut"]
    assert "was cut from it" in h.tracker.comments_on(off.id)[-1]


def test_a_held_offcut_is_left_alone_while_its_sheet_card_is_around_or_trello_is_down(tmp_path):
    h, off, s2 = held_offcut(tmp_path)
    h.runner.tick()
    assert json.loads(h.store.offcuts_file.read_text())[off.id]["reserved_by"] == s2.id
    real = h.tracker.get_card
    h.tracker.get_card = lambda card_id: (_ for _ in ()).throw(RuntimeError("Trello 503")) if card_id == s2.id \
        else real(card_id)
    h.tracker.archive(s2.id)
    h.runner.tick()
    assert json.loads(h.store.offcuts_file.read_text())[off.id]["reserved_by"] == s2.id   # not "gone": try later


# ---------------------------------------------------------------- the season's stock tally

def test_season_text():
    since = datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert season_text([], since, 1152.0, 0) == "Stock since Sep 1: nothing cut yet."
    cut_sheets = [CutSheet("6061 1/8in", False, 300.0), CutSheet("6061 1/8in", False, 200.0),
                  CutSheet("6061 3/16in", False, 100.0), CutSheet("6061 1/8in", True, 76.0),
                  CutSheet("PC smoked 1/8in", True, None)]
    assert season_text(cut_sheets, since, 1152.0, 2) == (
        "Stock since Sep 1: 3 new sheets (6061 1/8in x2, 6061 3/16in x1), 2 cut from offcuts. Parts: 676 sq in, "
        "20% of the new sheets (2 offcuts still on the shelf). (1 cut sheet with no parts area on record.)")


def test_the_system_card_tallies_sheets_cut_this_season(tmp_path):
    h, s1 = first_sheet(tmp_path)                         # 2 plates, 9 sq in each
    h.runner.report_health()
    assert "Stock since Sep 1: nothing cut yet." in h.tracker.cards[SYS].desc
    cut(h, s1.id)
    assert h.store.sheet_cards()[s1.id]["cut_utc"] == "2026-10-01T22:00:00Z"
    h.runner.report_health()
    assert ("Stock since Sep 1: 1 new sheet (6061 1/8in x1), 0 cut from offcuts. Parts: 18 sq in, 2% of the new "
            "sheets (1 offcut still on the shelf).") in h.tracker.cards[SYS].desc
    # a new season starts the count again
    h.now = datetime(2027, 9, 2, tzinfo=timezone.utc)
    assert h.runner.season_text(h.now) == "Stock since Sep 1: nothing cut yet."
