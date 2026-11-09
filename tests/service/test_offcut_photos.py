"""Offcuts from a phone photo (offcut_photos.py; the shop page's Offcuts tab): the photo ends up as the card a person
would write, and the service's own offcut pass adds it, numbers it and records its cuts."""
import base64

import pytest

pytest.importorskip("cv2")

from autocam_service.offcut_photos import OffcutPhotos  # noqa: E402
from autocam_service.offcuts import OffcutStore  # noqa: E402
from autocam_service.panel.api import material_label, material_line  # noqa: E402
from autocam_service.shoplist import ListError  # noqa: E402
from test_photo_find import HOLES, PHOTO_CORNERS, covers, photo  # noqa: E402
from test_panel import Panel  # noqa: E402
from test_prints import PHONE, SHOP  # noqa: E402
from test_runner_autostart import harness  # noqa: E402


def offcut_photos(h, tmp_path):
    h.cfg.paths.state.mkdir(parents=True, exist_ok=True)     # a real install has it from the start
    return OffcutPhotos(h.tracker, h.cfg, OffcutStore(h.store.offcuts_file), tmp_path / "photos",
                        material_label, material_line)


def b64(raw):
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode()


def test_a_photo_becomes_a_scrap_card_and_the_service_adds_it_with_its_cuts(tmp_path):
    h = harness(tmp_path)
    op = offcut_photos(h, tmp_path)
    got = op.photo(b64(photo("film", "concrete", HOLES)), "al6061", 30)
    assert got["corners"] is not None
    flat = op.flat(got["photo"], got["corners"], "al6061", 30)
    assert flat["image"].startswith("data:image/jpeg;base64,") and flat["how"] == "look"
    for hole in HOLES:
        assert any(covers(b, hole) for b in flat["boxes"])
    saved = op.save(got["photo"], flat["boxes"], "Akash", "al6061", "1/8", 30)
    card = h.tracker.cards[saved["card"]]
    assert card.list_key == "offcuts"
    assert "Material: 6061" in card.desc and "Thickness: 1/8" in card.desc and "Length: 30" in card.desc
    assert card.desc.count("Cut by hand:") == len(HOLES)
    assert any(f[0] == card.id and f[1] == "photo.jpg" for f in h.tracker.files.values())
    h.runner.tick()                                # the service's offcut pass: as for a card typed by hand
    piece = OffcutStore(h.store.offcuts_file).get(card.id)
    assert piece["scrap"] and piece["length_in"] == 30.0 and piece["number"] == 1
    assert len(piece["hand_cuts"]) == len(HOLES)
    assert h.tracker.comments_on(card.id)[-1].startswith("Added as offcut #1.")
    view = op.view()
    row = next(o for o in view["offcuts"] if o["id"] == card.id)
    assert row["number"] == 1 and row["length"] == 30.0 and row["thickness"] == "1/8" and row["used"]


def test_a_re_photo_adds_only_new_cuts_to_the_offcuts_card(tmp_path):
    h = harness(tmp_path)
    op = offcut_photos(h, tmp_path)
    got = op.photo(b64(photo("film", "concrete", HOLES[:2])), "al6061", 30)
    flat = op.flat(got["photo"], got["corners"], "al6061", 30)
    card_id = op.save(got["photo"], flat["boxes"], "Akash", "al6061", "1/8", 30)["card"]
    h.runner.tick()
    # someone cut two more parts out of it by hand: photograph it again
    again = op.photo(b64(photo("film", "concrete", HOLES)), target=card_id)
    flat2 = op.flat(again["photo"], again["corners"], target=card_id)
    assert len(flat2["boxes"]) == 2                 # the two already on record aren't offered again
    r = op.save(again["photo"], flat2["boxes"], "Maya", target=card_id)
    assert r["cuts"] == 2 and r["number"] == 1
    h.runner.tick()
    piece = OffcutStore(h.store.offcuts_file).get(card_id)
    assert len(piece["hand_cuts"]) == 4
    assert "Photographed on the shop page by Maya: 2 new hand cuts." in h.tracker.comments_on(card_id)


def test_gone_archives_the_card_and_the_rack_drops_it(tmp_path):
    h = harness(tmp_path)
    op = offcut_photos(h, tmp_path)
    got = op.photo(b64(photo("film", "concrete", HOLES[:1])), "al6061", 30)
    card_id = op.save(got["photo"], [], "Akash", "al6061", "1/8", 30)["card"]
    h.runner.tick()
    assert [o["id"] for o in op.view()["offcuts"]] == [card_id]
    op.gone(card_id, "Akash")
    assert card_id in h.tracker.archived
    del h.tracker.cards[card_id]                   # Trello's list no longer has it
    op.forget_board()
    assert op.view()["offcuts"] == []


def test_what_people_send_is_checked(tmp_path):
    h = harness(tmp_path)
    op = offcut_photos(h, tmp_path)
    raw = b64(photo("film", "concrete", HOLES))
    with pytest.raises(ListError, match="Pick the material"):
        op.photo(raw, "unobtanium", 30)
    with pytest.raises(ListError, match="length should be 1 to 48"):
        op.photo(raw, "al6061", 60)
    with pytest.raises(ListError, match="photo AutoCAM can read"):
        op.photo(b64(b"not a photo at all"), "al6061", 30)
    got = op.photo(raw, "al6061", 30)
    with pytest.raises(ListError, match="Bad request"):
        op.flat(got["photo"], [[0, 0], [1, 1]], "al6061", 30)
    with pytest.raises(ListError, match="Pick the thickness"):
        op.save(got["photo"], [], "Akash", "al6061", "7/8", 30)
    with pytest.raises(ListError, match="photo is gone"):
        op.save("0123456789abcdef", [], "Akash", "al6061", "1/8", 30)
    with pytest.raises(ListError, match="isn't on record"):
        op.photo(raw, target="nope")
    # boxes are kept to the piece; one with no size is dropped
    card_id = op.save(got["photo"], [[-3, 2, 4, 9], [5, 5, 5, 9], [20, 25, 30, 40]], "Akash", "al6061", "1/8", 30)["card"]
    lines = [ln for ln in h.tracker.cards[card_id].desc.splitlines() if ln.startswith("Cut by hand")]
    assert lines == ["Cut by hand: 2 to 9 along, 0 to 4 across", "Cut by hand: 25 to 30 along, 20 to 24 across"]


def test_the_shop_page_walks_through_it(tmp_path):
    p = Panel(tmp_path)
    p.app.offcut_photos = OffcutPhotos(p.t, p.h.cfg, OffcutStore(p.h.store.offcuts_file), tmp_path / "photos",
                                       material_label, material_line)
    status, d = p.call("GET", "/api/shop", q={"device": PHONE, "offcuts": "1"}, key=SHOP)
    assert status == 200 and d["offcutsOn"] and d["offcuts"] == [] and d["offcutFinder"]
    assert any(m["key"] == "al6061" and "1/8" in m["thicknesses"] for m in d["offcutMaterials"])
    status, got = p.call("POST", "/api/shop/offcut-photo", key=SHOP, body={
        "device": PHONE, "data": b64(photo("film", "concrete", HOLES)), "material": "al6061", "length": 30})
    assert status == 200 and got["corners"]
    status, flat = p.call("POST", "/api/shop/offcut-flat", key=SHOP, body={
        "device": PHONE, "photo": got["photo"], "corners": PHOTO_CORNERS, "material": "al6061", "length": 30})
    assert status == 200 and len(flat["boxes"]) == len(HOLES)
    status, saved = p.call("POST", "/api/shop/offcut-save", key=SHOP, body={
        "device": PHONE, "photo": got["photo"], "boxes": flat["boxes"], "material": "al6061", "thickness": "1/8",
        "length": 30})
    assert status == 200 and p.t.cards[saved["card"]].list_key == "offcuts"
    status, err = p.call("POST", "/api/shop/offcut-save", key=SHOP, body={
        "device": PHONE, "photo": got["photo"], "boxes": [], "material": "al6061", "thickness": "9/8", "length": 30})
    assert status == 400 and err["error"] == "Pick the thickness."
    status, _ = p.call("POST", "/api/shop/offcut-photo", key="wrong", body={"device": PHONE})
    assert status == 403
