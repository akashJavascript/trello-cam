"""A part card for a part another active card already has gets one comment saying where (runner.warn_duplicates)."""

from dataclasses import replace

from autocam_service.tracker.base import Card
from test_runner_autostart import add, harness, step_card
from test_runner_e2e import STUDIO, D, E

OTHER = f"https://cad.onshape.com/documents/{D}/v/aaaaaaaaaaaaaaaaaaaaaaaa/e/bbbbbbbbbbbbbbbbbbbbbbbb"


def linked(cid, name, url=STUDIO, list_key="inbox", qty=1):
    return Card(cid, name, f"Qty: {qty}\n{url}", list_key, f"https://trello.example/c/{cid}")


def warnings(h, cid):
    return [c for c in h.tracker.comments_on(cid) if c.startswith("Another card has this part too")]


def test_the_same_part_on_two_cards_is_pointed_out_once(tmp_path):
    h = harness(tmp_path, linked("a", "P-2015", list_key="needs_fixing"), linked("b", "p-2015", qty=2))
    h.runner.ready_cards()
    assert warnings(h, "b") == ["Another card has this part too: https://trello.example/c/a (Needs fixing). If it's "
                                "the same part, archive one so it isn't cut twice (this card's Qty is added, not "
                                "shared)."]
    assert warnings(h, "a") == []                         # only cards in Drafts and Ready for CAM are checked
    h.runner.ready_cards()
    assert len(warnings(h, "b")) == 1                     # not again until the card changes
    h.tracker.cards["b"] = replace(h.tracker.cards["b"], desc=f"Qty: 3\n{STUDIO}")
    h.runner.ready_cards()
    assert len(warnings(h, "b")) == 2


def test_same_name_in_another_part_studio_or_already_cut_isnt_a_duplicate(tmp_path):
    h = harness(tmp_path, linked("a", "bracket", url=OTHER, list_key="nested"),
                linked("c", "bracket", list_key="cut"), linked("b", "bracket"))
    h.runner.ready_cards()
    assert warnings(h, "b") == []


def test_step_cards_match_by_name(tmp_path):
    h = harness(tmp_path, step_card("a", "spacer", list_key="nested"), step_card("b", "spacer"))
    h.runner.ready_cards()
    assert len(warnings(h, "b")) == 1 and "(On a sheet)" in warnings(h, "b")[0]


def test_a_card_it_cant_read_still_matches_by_its_title(tmp_path):
    bad = Card("a", "P-2015 x2", f"Qty: 1\n{STUDIO}", "needs_fixing", "https://trello.example/c/a")   # two Qtys
    h = harness(tmp_path, bad, linked("b", "P-2015"))
    h.runner.ready_cards()
    assert len(warnings(h, "b")) == 1
