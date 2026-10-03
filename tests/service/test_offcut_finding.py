"""Finding offcuts at the machine: numbers, "the offcut isn't on the rack", and scraps added by hand."""

import json

from autocam_core.schema_job import read_job
from autocam_service.tracker.base import Card
from fakeworker import run_fake_worker
from test_offcuts_service import cut, first_sheet, offcut_cards
from test_runner_autostart import add, run, sheet, step_card

NOT_FOUND = "The offcut isn't on the rack"
RENEST = "Re-nest it on other stock (a new run and a new review)"
NEW_SHEET = "Cut it on a new sheet (the same program)"


def pieces(h):
    return json.loads(h.store.offcuts_file.read_text())


def tick_item(h, card_id, item, checklist="Stock"):
    for entry in h.tracker.checklists[(card_id, checklist)]:
        if entry[0] == item:
            entry[1] = True
            return
    raise AssertionError(f"no {item!r} on {card_id}: {h.tracker.checklists[(card_id, checklist)]}")


def on_an_offcut(tmp_path):
    """Offcut #1 (0.5 to 8.5 in used), and sheet card s2 nested on it (its part at 9 to 13 in)."""
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off1] = offcut_cards(h)
    add(h, step_card("c2", "gusset"))
    run(h)
    s2 = sheet(h)
    h.runner.tick()                                       # the boxes appear
    assert h.tracker.checklists[(s2.id, "Stock")] == [[NOT_FOUND, False]]
    assert h.store.sheet_cards()[s2.id]["bands"] == [[9.0, 13.0]]
    return h, off1, s2


def add_offcut(h, card_id, used, number):
    h.tracker.cards[card_id] = Card(card_id, f"#{number} offcut", "", "offcuts", f"https://trello.example/c/{card_id}")
    p = pieces(h)
    p[card_id] = {"material": "al6061", "thickness_in": 0.125, "used": used, "beside": [],
                  "last": {"label": "r000 S1", "stretch": used[0], "turned": False}, "reserved_by": None,
                  "url": f"https://trello.example/c/{card_id}", "number": number}
    h.store.offcuts_file.write_text(json.dumps(p))


# ---------------------------------------------------------------- the offcut isn't on the rack

def test_another_offcut_the_same_program_fits_takes_over(tmp_path):
    h, off1, s2 = on_an_offcut(tmp_path)
    add_offcut(h, "off2", [[0.5, 4.0]], 2)                # 4.5 in on is free: the part at 9 to 13 in fits
    tick_item(h, s2.id, NOT_FOUND)
    h.runner.tick()
    p = pieces(h)
    assert p[off1.id]["missing"] and off1.id in h.tracker.archived
    assert "Not found at the machine for r002 S1" in h.tracker.comments_on(off1.id)[-1]
    assert p["off2"]["reserved_by"] == s2.id and p[off1.id]["reserved_by"] is None
    card = h.tracker.cards[s2.id]
    assert card.name.startswith("6061 1/8in offcut #2 - ")
    assert ("Stock: offcut #2 (6061 1/8in), not a new sheet (https://trello.example/c/off2). Put it in the same way "
            "round as for r000 S1") in card.desc
    assert "(Offcut #1 wasn't on the rack; this one fits the same program.)" in card.desc
    assert h.tracker.comments_on(s2.id)[-1].startswith("Offcut #1 isn't on the rack, but this program fits offcut #2 "
                                                       "as it is: no new run, no new review.")
    h.runner.tick()
    assert h.tracker.checklists[(s2.id, "Stock")] == [[NOT_FOUND, False]]            # fresh, for offcut #2
    cut(h, s2.id)
    assert pieces(h)["off2"]["used"] == [[0.5, 4.0], [9.0, 13.0]]                   # the cut is on #2
    assert pieces(h)[off1.id]["used"] == [[0.5, 8.5]]                               # #1 untouched


def test_with_no_offcut_that_fits_it_asks_and_a_new_sheet_takes_the_same_program(tmp_path):
    h, off1, s2 = on_an_offcut(tmp_path)
    add_offcut(h, "off2", [[5.0, 44.0]], 2)               # the part's band (9 to 13 in) is used either way round
    tick_item(h, s2.id, NOT_FOUND)
    h.runner.tick()
    assert "no other offcut fits this program as it is" in h.tracker.comments_on(s2.id)[-1]
    assert [i for i, _ in h.tracker.checklists[(s2.id, "Stock")]] == [RENEST, NEW_SHEET]
    h.runner.tick()                                       # asked once
    assert sum("no other offcut fits" in c for c in h.tracker.comments_on(s2.id)) == 1
    tick_item(h, s2.id, NEW_SHEET)
    h.runner.tick()
    card = h.tracker.cards[s2.id]
    assert card.name.startswith("6061 1/8in - 4 mm O-flute ALU")
    assert ("Stock: a new 6061 1/8in sheet, 24 x 48 (offcut #1 wasn't on the rack; the program is the same). The "
            "parts are cut 9 to 13 in from the front") in card.desc
    assert pieces(h)[off1.id]["reserved_by"] is None and (s2.id, "Stock") not in h.tracker.checklists
    h.runner.tick()
    assert h.tracker.checklists[(s2.id, "Offcut")] == [["Keep the rest of the sheet for the next run", True]]
    cut(h, s2.id)
    [new] = [c for c in offcut_cards(h) if c.id not in ("off2",)]
    assert new.name.startswith("#3 - ") and pieces(h)[new.id]["used"] == [[9.0, 13.0]]


def test_re_nesting_sends_the_parts_back_with_the_rush_label(tmp_path):
    h, off1, s2 = on_an_offcut(tmp_path)
    tick_item(h, s2.id, NOT_FOUND)
    h.runner.tick()
    tick_item(h, s2.id, RENEST)
    h.runner.tick()
    assert s2.id in h.tracker.archived and h.store.sheet_cards()[s2.id]["archived"]
    assert h.list_of("c2") == "ready_for_cam" and "Rush" in h.tracker.cards["c2"].labels
    assert "wasn't on the rack" in h.tracker.comments_on("c2")[-1]
    h.runner.tick()                                       # the rush run, without the missing offcut
    job = read_job(next(h.queue.incoming.glob("r003-*.json")))
    assert [p.card_id for p in job.parts] == ["c2"] and job.offcuts == ()
    assert h.store.load("r003").rush


def test_a_missing_offcut_is_offered_again_once_its_card_is_back(tmp_path):
    h, off1, s2 = on_an_offcut(tmp_path)
    tick_item(h, s2.id, NOT_FOUND)
    h.runner.tick()
    h.runner.tick()
    assert off1.id in pieces(h)                           # kept on record while archived
    h.tracker.archived.remove(off1.id)                    # found it: the card goes back to Offcuts
    h.runner.tick()
    assert not pieces(h)[off1.id]["missing"]
    assert h.tracker.comments_on(off1.id)[-1] == "Back on the rack: runs can use it again."


# ---------------------------------------------------------------- numbers

def test_offcuts_from_before_numbers_get_one(tmp_path):
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off1] = offcut_cards(h)
    p = pieces(h)
    del p[off1.id]["number"]                               # made before offcuts had numbers
    h.store.offcuts_file.write_text(json.dumps(p))
    h.runner.tick()
    assert pieces(h)[off1.id]["number"] == 2              # numbers are never given twice
    assert h.tracker.cards[off1.id].name.startswith("#2 - ")
    assert h.tracker.comments_on(off1.id)[-1] == "This is offcut #2: write #2 on it."


# ---------------------------------------------------------------- scraps added by hand

def scrap(h, desc, cid="sc1"):
    h.tracker.cards[cid] = Card(cid, "piece from the old rack", desc, "offcuts", f"https://trello.example/c/{cid}")


def test_a_scrap_card_becomes_an_offcut_that_never_turns(tmp_path):
    h, s1 = first_sheet(tmp_path)
    scrap(h, "Material: 6061\nThickness: 1/8\"\nLength: 20 in")
    h.runner.tick()
    piece = pieces(h)["sc1"]
    assert piece["scrap"] and piece["length_in"] == 20.0 and piece["used"] == [[20.0, 48.0]]
    card = h.tracker.cards["sc1"]
    assert card.name == "#1 - 6061 1/8in offcut - 19 in free"
    assert card.desc.startswith("A 20 in long piece of 6061 1/8in, added by hand. It always goes in pushed to the "
                                "front, never turned round.")
    assert h.tracker.comments_on("sc1")[-1].startswith("Added as offcut #1. Write #1 on it.")
    h.tracker.tick_all(s1.id, "Review")                   # s1 is reviewed: new parts don't join it
    add(h, step_card("c2", "gusset"))
    h.runner.tick()
    job = read_job(next(h.queue.incoming.glob("r002-*.json")))
    assert [(o.id, o.can_turn) for o in job.offcuts] == [("sc1", False)]
    run_fake_worker(h.queue)
    h.runner.tick()
    s2 = next(c for c in h.sheet_cards() if "r002" in c.name)
    assert ("Stock: offcut #1 (6061 1/8in), not a new sheet (https://trello.example/c/sc1). It's a 20 in piece: "
            "push it against the front stop, either end first.") in s2.desc


def test_a_scrap_card_that_says_too_little_gets_one_reply(tmp_path):
    h, _ = first_sheet(tmp_path)
    scrap(h, "Material: 6061\nLength: 20")
    h.runner.tick()
    h.runner.tick()
    [reply] = h.tracker.comments_on("sc1")
    assert reply.startswith("Not added as an offcut yet: No Thickness line.") and "Length: 20" in reply
    scrap(h, "Material: 6061\nThickness: 1/2\nLength: 20")
    h.runner.tick()
    assert "Thickness 1/2 isn't a stock thickness of 6061" in h.tracker.comments_on("sc1")[-1]
    scrap(h, "Material: 6061\nThickness: 0.125\nLength: 5")
    h.runner.tick()
    assert "too short to nest on" in h.tracker.comments_on("sc1")[-1]
    scrap(h, "Material: unobtanium\nThickness: 0.125\nLength: 20")
    h.runner.tick()
    assert "isn't one we cut" in h.tracker.comments_on("sc1")[-1]
    assert not h.store.offcuts_file.exists() or "sc1" not in pieces(h)
