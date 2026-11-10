"""Offcuts from a phone photo (offcut_photos.py; the shop page's Offcuts tab): the photo ends up as the card a person
would write, and the service's own offcut pass adds it, numbers it and records its cuts."""
import base64
import json

import pytest

pytest.importorskip("cv2")

from autocam_service.offcut_photos import OffcutPhotos  # noqa: E402
from autocam_service.offcuts import OffcutStore  # noqa: E402
from autocam_service.panel.api import material_label, material_line  # noqa: E402
from autocam_service.photo_report import corner_miss_in, report, score  # noqa: E402
from autocam_service.shoplist import ListError  # noqa: E402
from test_photo_find import HOLES, PHOTO_CORNERS, covers, photo  # noqa: E402
from test_panel import Panel  # noqa: E402
from test_prints import PHONE, SHOP  # noqa: E402
from test_runner_autostart import harness  # noqa: E402


def offcut_photos(h, tmp_path, **kw):
    h.cfg.paths.state.mkdir(parents=True, exist_ok=True)     # a real install has it from the start
    return OffcutPhotos(h.tracker, h.cfg, OffcutStore(h.store.offcuts_file), tmp_path / "photos",
                        material_label, material_line, **kw)


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


def kept(tmp_path):
    folder = tmp_path / "photos" / "kept"
    return sorted(d for d in folder.iterdir() if d.is_dir()) if folder.is_dir() else []


def test_a_saved_photo_is_kept_with_what_was_suggested_and_what_people_saved(tmp_path):
    h = harness(tmp_path)
    op = offcut_photos(h, tmp_path)
    got = op.photo(b64(photo("film", "concrete", HOLES)), "al6061", 30)
    flat = op.flat(got["photo"], got["corners"], "al6061", 30)
    saved = flat["boxes"][1:] + [[1.0, 26.0, 3.0, 28.0]]      # people deleted one and drew one
    op.save(got["photo"], saved, "Akash", "al6061", "1/8", 30)
    [folder] = kept(tmp_path)
    assert (folder / "photo.jpg").read_bytes() == (tmp_path / "photos" / f"{got['photo']}.jpg").read_bytes()
    text = (folder / "record.json").read_text()
    r = json.loads(text)
    assert r["kind"] == "new" and r["material"] == "al6061" and r["thickness"] == "1/8" and r["length"] == 30
    assert r["how"] == "look" and r["suggested"] == flat["boxes"] and r["saved"] == saved
    assert r["suggested_corners"] == got["corners"] and r["corners"] == got["corners"]
    assert "Akash" not in text                               # no names kept
    s = score(r)
    assert s["holes"] == len(saved) and s["missed"] == 1 and s["extra"] == 1 and s["corners"] == "right"
    out = report(tmp_path / "photos" / "kept")
    assert "Offcut photos kept: 1" in out and "by look" in out and "1 of 1" in out


def test_a_re_photo_is_kept_too_and_only_the_newest_are(tmp_path):
    h = harness(tmp_path)
    now = [1_800_000_000.0]
    op = offcut_photos(h, tmp_path, clock=lambda: now[0], keep=2)
    got = op.photo(b64(photo("film", "concrete", HOLES[:2])), "al6061", 30)
    flat = op.flat(got["photo"], got["corners"], "al6061", 30)
    card_id = op.save(got["photo"], flat["boxes"], "Akash", "al6061", "1/8", 30)["card"]
    h.runner.tick()
    for _ in range(2):
        now[0] += 60
        again = op.photo(b64(photo("film", "concrete", HOLES)), target=card_id)
        flat2 = op.flat(again["photo"], again["corners"], target=card_id)
        op.save(again["photo"], flat2["boxes"], "Maya", target=card_id)
    folders = kept(tmp_path)
    assert len(folders) == 2                                  # keep = 2: the first one went
    r = json.loads((folders[-1] / "record.json").read_text())
    assert r["kind"] == "re-photo" and r["number"] == 1 and r["known"] and len(r["suggested"]) == 2


def test_keep_0_keeps_nothing(tmp_path):
    h = harness(tmp_path)
    op = offcut_photos(h, tmp_path, keep=0)
    got = op.photo(b64(photo("film", "concrete", HOLES)), "al6061", 30)
    flat = op.flat(got["photo"], got["corners"], "al6061", 30)
    op.save(got["photo"], flat["boxes"], "Akash", "al6061", "1/8", 30)
    assert kept(tmp_path) == []


def test_the_report_counts_holes_found_missed_and_extra(tmp_path):
    folder = tmp_path / "kept"
    corners = [[100, 900], [340, 900], [340, 420], [100, 420]]          # 24 x 48 in at 10 px/in
    for name, how, sug_corners in (("20261110-180000-a", "look", corners[2:] + corners[:2]),   # same, turned round
                                   ("20261110-181000-b", "outline", None)):
        (folder / name).mkdir(parents=True)
        (folder / name / "record.json").write_text(json.dumps({
            "kind": "new", "material": "al6061", "thickness": "1/8", "width": 24, "length": 48, "how": how,
            "corners": corners, "suggested_corners": sug_corners,
            "suggested": [[0, 0, 2, 2], [5, 5, 6, 6.2], [20, 20, 21, 21]],
            "saved": [[0, 0, 2, 2], [5, 5, 7, 7], [10, 10, 12, 12]]}))
    r = json.loads((folder / "20261110-180000-a" / "record.json").read_text())
    assert score(r) == {"holes": 3, "found": 1, "missed": 2, "extra": 1, "corners": "right", "corner_in": 0.0}
    out = report(folder)
    assert "by look (film, wood)" in out and "by outline (bare metal)" in out
    assert "all" in out and "Most holes missed:" in out and "2 of 3 missed, 1 extra" in out
    moved = [[x + 8, y] for x, y in corners]                             # 0.8 in off
    assert abs(corner_miss_in(moved, corners, 24, 48) - 0.8) < 1e-6
    assert corner_miss_in(None, corners, 24, 48) is None
    assert report(tmp_path / "nothing").startswith("No offcut photos kept yet")


def test_what_the_pages_photo_check_measured_is_kept_with_the_photo(tmp_path):
    h = harness(tmp_path)
    op = offcut_photos(h, tmp_path)
    q = {"sharp": 0.183, "bright": 121.4, "tilt": 3.2, "problem": "blurry", "via": "camera", "anyway": True,
         "name": "Akash", "sharp2": 9, "extra": {"a": 1}}
    got = op.photo(b64(photo("film", "concrete", HOLES)), "al6061", 30, quality=q)
    flat = op.flat(got["photo"], got["corners"], "al6061", 30)
    op.save(got["photo"], flat["boxes"], "Akash", "al6061", "1/8", 30)
    [folder] = kept(tmp_path)
    r = json.loads((folder / "record.json").read_text())
    assert r["quality"] == {"sharp": 0.183, "bright": 121.4, "tilt": 3.2, "problem": "blurry", "via": "camera",
                            "anyway": True}
    got2 = op.photo(b64(photo("film", "concrete", HOLES)), "al6061", 30,
                    quality={"sharp": "high", "bright": 900, "tilt": float("nan"), "via": "fax"})
    notes = json.loads((tmp_path / "photos" / f"{got2['photo']}.json").read_text())
    assert notes["quality"] is None                           # nothing usable in it
    out = report(tmp_path / "photos" / "kept")
    assert "Photo check: 1 from the page's camera, 0 from the camera app; 1 blurry and 0 dark ones used anyway." in out


def test_a_tap_on_a_missed_hole_gets_its_box_and_where_boxes_came_from_is_kept(tmp_path, monkeypatch):
    h = harness(tmp_path)
    op = offcut_photos(h, tmp_path)
    got = op.photo(b64(photo("film", "concrete", HOLES)), "al6061", 30)
    flat = op.flat(got["photo"], got["corners"], "al6061", 30)
    assert isinstance(flat["maybes"], list)
    hole = HOLES[2]
    r = op.grow(got["photo"], got["corners"], [(hole[0] + hole[2]) / 2, (hole[1] + hole[3]) / 2], "al6061", 30)
    assert r["box"] and covers(r["box"], hole)
    assert op.grow(got["photo"], got["corners"], [11, 13], "al6061", 30) == {"box": None}     # the piece itself
    for bad in (None, [1], ["a", 2], [float("nan"), 3]):
        with pytest.raises(ListError, match="Bad request"):
            op.grow(got["photo"], got["corners"], bad, "al6061", 30)
    boxes = flat["boxes"][:2] + [r["box"], [1.0, 26.0, 3.0, 28.0]]
    op.save(got["photo"], boxes, "Akash", "al6061", "1/8", 30, kinds=["found", "found", "tap", "drawn"])
    [folder] = kept(tmp_path)
    rec = json.loads((folder / "record.json").read_text())
    assert rec["kinds"] == {"found": 2, "maybe": 0, "tap": 1, "drawn": 1} and rec["maybes"] == flat["maybes"]
    assert "Saved boxes: 2 found, 0 maybes kept, 1 tapped, 1 drawn by hand." in report(tmp_path / "photos" / "kept")
    # taps are capped (each is about half a second of the laptop)
    monkeypatch.setattr("autocam_service.offcut_photos.TAPS_PER_MINUTE", 2)      # two taps so far this minute
    with pytest.raises(ListError, match="Lots of taps") as e:
        op.grow(got["photo"], got["corners"], [5, 5], "al6061", 30)
    assert e.value.status == 429


def test_kinds_that_dont_match_the_boxes_are_ignored(tmp_path):
    h = harness(tmp_path)
    op = offcut_photos(h, tmp_path)
    got = op.photo(b64(photo("film", "concrete", HOLES)), "al6061", 30)
    flat = op.flat(got["photo"], got["corners"], "al6061", 30)
    op.save(got["photo"], flat["boxes"], "Akash", "al6061", "1/8", 30, kinds=["found"])         # one short
    rec = json.loads((kept(tmp_path)[0] / "record.json").read_text())
    assert rec["kinds"] is None


def test_the_shop_page_asks_for_a_tapped_hole(tmp_path):
    p = Panel(tmp_path)
    p.app.offcut_photos = OffcutPhotos(p.t, p.h.cfg, OffcutStore(p.h.store.offcuts_file), tmp_path / "photos",
                                       material_label, material_line)
    status, got = p.call("POST", "/api/shop/offcut-photo", key=SHOP, body={
        "device": PHONE, "data": b64(photo("film", "concrete", HOLES)), "material": "al6061", "length": 30})
    hole = HOLES[0]
    status, r = p.call("POST", "/api/shop/offcut-grow", key=SHOP, body={
        "device": PHONE, "photo": got["photo"], "corners": PHOTO_CORNERS, "material": "al6061", "length": 30,
        "at": [(hole[0] + hole[2]) / 2, (hole[1] + hole[3]) / 2]})
    assert status == 200 and covers(r["box"], hole)
