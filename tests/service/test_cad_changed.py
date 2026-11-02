"""CAD changed (2026-11-01, from the teammate's panel): a part that changed in Onshape after it was CAM'd and before
it's cut gets the CAD changed label on its card and its sheet card, and a box on its card to cut it as it was anyway.
Ticking it (or the panel's "Cut it as it is"), or nesting the part again, takes the labels off."""

from autocam_service.panel.board import ACCEPT_ITEM, ACCEPT_LIST
from onshape_body import plate_body, rectangle
from test_panel import Q
from test_panel_more import SHEET, changed_panel, precut_panel

THEN = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25)])
NOW = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.5)])
PART = "a" * 24
LABEL = "CAD changed"


def labels(p, card_id):
    return set(p.t.get_card(card_id).labels)


def accept_box(p):
    return p.t.checklists.get((PART, ACCEPT_LIST))


def test_a_change_before_cutting_marks_both_cards_and_ticking_the_box_clears_them(tmp_path):
    p, watch = precut_panel(tmp_path, "8" * 24, THEN, NOW)
    assert watch.tick() == 1
    assert LABEL in labels(p, PART) and LABEL in labels(p, SHEET)
    assert accept_box(p) == [[ACCEPT_ITEM, False]]
    [told] = p.t.comments_on(PART)
    assert told.startswith("Changed in Onshape after it was CAM'd (1 hole added, 1 hole removed). To cut the new "
                           "version, move this card back to Ready for CAM")
    assert "tick \"Cut it as it was CAM'd\" on its card" in p.t.comments_on(SHEET)[0]
    watch.tick()
    assert LABEL in labels(p, PART)                                   # nothing settled yet: still marked
    p.t.checklists[(PART, ACCEPT_LIST)][0][1] = True                 # someone ticks it in Trello
    watch.tick()
    assert LABEL not in labels(p, PART) and LABEL not in labels(p, SHEET)
    assert accept_box(p) == [[ACCEPT_ITEM, True]]                    # kept, ticked: what was decided
    assert p.t.comments_on(SHEET)[-1] == ("P-2014 gusset: cut as it was CAM'd (the box on its card was ticked).")


def test_the_panel_shows_it_and_can_accept_it(tmp_path):
    p, watch = precut_panel(tmp_path, "8" * 24, THEN, NOW)
    watch.tick()
    detail = p.board.detail(next(pc for pc in p.board.part_cards(fresh=True) if pc.card.id == PART))
    assert detail["cadChanged"] == {"what": ["1 hole added", "1 hole removed"], "cut": False}
    assert detail["changed"] is True                                  # the list's chip
    status, r = p.call("POST", "/api/accept-change", body={**Q, "p": "JHD", "card": PART, "name": "Maya"})
    assert status == 200 and "cadChanged" not in r["card"]
    assert LABEL not in labels(p, PART) and LABEL not in labels(p, SHEET)
    assert "accepted in the Onshape panel by Maya" in p.t.comments_on(SHEET)[-1]
    assert p.call("POST", "/api/accept-change", body={**Q, "p": "JHD", "card": PART})[0] == 409   # nothing open now
    assert [u for _, u, _, _ in p.transport.sent if "/parts/" in u and "bodydetails" not in u] == []   # no call


def test_nesting_it_again_clears_it(tmp_path):
    p, watch = precut_panel(tmp_path, "8" * 24, THEN, NOW)
    watch.tick()
    p.t.move(PART, "ready_for_cam")                                  # back to Ready for CAM (or the panel's button)
    p.board.invalidate()
    watch.tick()
    assert LABEL not in labels(p, PART) and LABEL not in labels(p, SHEET)
    assert accept_box(p) == [[ACCEPT_ITEM, False]]                   # not ticked: it wasn't cut as it was


def test_changed_again_after_it_was_accepted_asks_again(tmp_path):
    p, watch = precut_panel(tmp_path, "8" * 24, THEN, NOW)
    watch.tick()
    p.board.accept_change(next(pc for pc in p.board.part_cards(fresh=True) if pc.card.id == PART), "Maya")
    assert p.board.flag_sheet(SHEET, PART, "P-2014 gusset", ["bigger"], "another shape")
    assert LABEL in labels(p, PART) and accept_box(p) == [[ACCEPT_ITEM, False]]
    assert not p.board.flag_sheet(SHEET, PART, "P-2014 gusset", ["bigger"], "another shape")   # once per change


def test_cut_with_the_change_unsettled_keeps_the_label(tmp_path):
    p, watch = precut_panel(tmp_path, "8" * 24, THEN, NOW)
    watch.tick()
    p.t.move(PART, p.h.cfg.trello.targets["part_cut"])
    p.board.invalidate()
    watch.tick()
    assert LABEL in labels(p, PART)                                   # what was cut is the old version
    detail = p.board.detail(next(pc for pc in p.board.part_cards(fresh=True) if pc.card.id == PART))
    assert detail["cadChanged"]["cut"] is True


def test_sheets_in_review_are_checked_twice_a_day_not_when_just_cammed(tmp_path):
    p, watch = precut_panel(tmp_path, "8" * 24, THEN, NOW)
    p.t.move(SHEET, "sheet_review")
    assert watch.tick(now=1000.0) == 0 and p.transport.sent == []     # just CAM'd: nothing to compare yet
    assert watch.tick(now=1000.0 + 6 * 3600) == 0 and p.transport.sent == []
    assert watch.tick(now=1000.0 + 13 * 3600) == 1                    # 12 h later: compared
    assert LABEL in labels(p, SHEET)
    calls = len(p.transport.sent)
    watch.tick(now=1000.0 + 14 * 3600)
    assert len(p.transport.sent) == calls


def test_the_panel_check_marks_the_cards_too(tmp_path):
    then = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25)])
    now = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25), (3, 1, 0.25)])
    p, on = changed_panel(tmp_path, then, now)
    p.call("GET", "/api/changed", {**Q, "p": "JHD", "card": on.id})
    assert LABEL in labels(p, on.id) and LABEL in labels(p, SHEET)
