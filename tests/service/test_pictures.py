"""Pictures drawn from the programs (2026-11-04, the user's picks): the sheet card's toolpath picture, and the offcut
card's cover showing what the piece should look like (toolpath.py, drawings.py, card_pictures.py)."""

import io

from PIL import Image

from autocam_core.toolpath import read_program, turn_moves
from autocam_service import drawings
from fakeworker import run_fake_worker
from test_offcuts_service import cut, first_sheet, offcut_cards
from test_runner_autostart import add, run, sheet, step_card

PROGRAM = """[6061_0p125_r001_S1]
G90
G20
G53 Z
[bore holes]
S18000
M3
G0 X5. Y5.
Z0.325
G1 Z0. F20.
G2 X5. Y5. I0.1 J0. F100.
G0 Z1.
[drill holes]
G81 X8. Y5. Z0. R0.3 F40.
X9. Y5.
G80
[outer p01-1]
G0 X2. Y2.
G1 Z0.06 F20.
G1 Z0. X3.
X12. Y2.
Y8.
Z0.04
X11.
Z0.
X2.
Y2.
G0 Z1.
[PART 1 DONE - PAUSE]
G53 Z
M5
G53 P10
G4 [TAKE PART 1 OUT, THEN PRESS ENTER]
[outer p02-1]
G0 X14. Y2.
G1 Z0. F20.
X18.
Y6.
X14.
Y2.
G53 Z
M5
G53 P10
"""


def test_a_program_is_read_into_its_operations_and_how_deep_each_move_goes():
    p = read_program(PROGRAM, 0.125)
    assert p.name == "6061_0p125_r001_S1"
    assert p.ops == ("bore holes", "drill holes", "outer p01-1", "outer p02-1")      # the stop isn't an operation
    assert [op for op, _ in p.outlines()] == ["outer p01-1", "outer p02-1"]          # the CUT ORDER
    drills = [m for m in p.moves if m.drill]
    assert [(m.x0, m.y0) for m in drills] == [(8.0, 5.0), (9.0, 5.0)]
    bore = [m for m in p.moves if m.op == "bore holes"]
    assert len(bore) > 20 and all(m.z == 0.0 for m in bore[1:])                      # a full circle, as chords
    outline = [m for m in p.moves if m.op == "outer p01-1"]
    tab = [m for m in outline if m.z > 0.01 and m.flat]
    ramp = [m for m in outline if m.z > 0.01 and not m.flat]
    assert [(m.x0, m.x1, m.z) for m in tab] == [(12.0, 11.0, 0.04)]                  # the tab's top, flat
    assert ramp and all(m.x0 != m.x1 or m.y0 != m.y1 or m.z > 0 for m in ramp)       # the ramp down and back up
    turned = turn_moves(drills, 24.0, 48.0)
    assert [(m.x0, m.y0) for m in turned] == [(16.0, 43.0), (15.0, 43.0)]


def test_the_toolpath_picture_draws_cuts_where_they_are(tmp_path):
    p = read_program(PROGRAM, 0.125)
    png = drawings.toolpath_png(title="r001 S1: what the program cuts", sub="6061 1/8in, a new sheet", width=24.0,
                                length=48.0, main=p, kerf_in=0.1575, clamps=[(0, 0, 1.25, 48), (22.75, 0, 24, 48)],
                                reach=40.0)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    assert image.size[0] == 1000
    scale = min(760.0 / 24, 1400.0 / 48)
    left = (1000 - 24 * scale) / 2

    def at(x, y):
        return image.getpixel((int(left + x * scale), int(120 + (48 - y) * scale)))
    assert at(7.0, 2.0) == drawings.THROUGH                                          # the outline's bottom edge
    assert at(11.5, 8.0) == drawings.PARTIAL                                         # its tab
    assert at(8.0, 5.0) == drawings.THROUGH                                          # a drilled hole
    assert at(12.0, 30.0) == drawings.STOCK                                          # nothing cut there


def test_a_sheet_card_gets_its_toolpath_picture(tmp_path):
    h, s1 = first_sheet(tmp_path)                       # its first pass in Sheet review draws it
    assert h.store.sheet_cards()[s1.id]["toolpath"]
    h.runner.tick()
    names = [name for cid, name, _ in h.tracker.files.values() if cid == s1.id]
    assert "6061_0p125_r001_S1_toolpath.png" in names
    assert h.tracker.covers[s1.id] != next(a for a, (cid, name, _) in h.tracker.files.items()
                                           if name.endswith("_toolpath.png"))         # the preview stays the cover
    assert names.count("6061_0p125_r001_S1_toolpath.png") == 1                       # once


def pictures_on(h, card_id):
    return [(aid, name) for aid, (cid, name, _) in h.tracker.files.items() if cid == card_id and name.endswith(".png")]


def test_an_offcut_card_shows_what_the_piece_should_look_like_and_keeps_it_current(tmp_path):
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off] = offcut_cards(h)
    h.runner.tick()                                                                  # the scan draws it
    [(first, name)] = pictures_on(h, off.id)
    assert name == "offcut_1.png" and h.tracker.covers[off.id] == first
    piece = h.runner.offcuts.get(off.id)
    assert piece["made_from"] == s1.id and piece["picture"] == first
    h.runner.tick()
    assert pictures_on(h, off.id) == [(first, name)]                                 # unchanged: not drawn again
    # a sheet cut on it: redrawn with both programs, the old picture gone
    add(h, step_card("c2", "gusset"))
    run(h)
    s2 = sheet(h)
    h.runner.tick()
    assert "6061_0p125_r002_S1_toolpath.png" in [n for _, (cid, n, _) in h.tracker.files.items() if cid == s2.id]
    cut(h, s2.id)
    h.runner.tick()
    [(second, _)] = pictures_on(h, off.id)
    assert second != first and h.tracker.covers[off.id] == second
    cuts, sheets = h.runner.pictures.piece_cuts(off.id, h.runner.offcuts.get(off.id))
    assert sheets == [s1.id, s2.id] and all(moves for moves, _ in cuts)


def test_an_older_offcut_finds_the_sheet_that_went_to_cut_when_its_card_was_made(tmp_path):
    """r068 S1 made offcut #3 (2026-11-04 00:49), then H1 was cut on it: #3's record never said so."""
    from datetime import datetime, timedelta, timezone
    from autocam_service.card_pictures import CardPictures
    made = datetime(2026, 11, 4, 0, 49, 30, tzinfo=timezone.utc)
    off = f"{int(made.timestamp()):08x}" + "0" * 16                                  # Trello ids start with the time
    def at(minutes):
        return (made + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    reg = {"r068": {"offcut_done": True, "material": "pc_clear", "cut_utc": at(-0.5)},
           "r060": {"offcut_done": True, "material": "pc_clear", "cut_utc": at(-60)},          # too long before
           "r069": {"offcut_done": True, "material": "al6061", "cut_utc": at(0)},              # another material
           "H1": {"offcut_done": True, "material": "pc_clear", "cut_utc": at(1), "offcut_id": off}}   # cut on it
    pics = CardPictures.__new__(CardPictures)
    assert pics._made_from(off, {"material": "pc_clear"}, reg, ["H1"]) == "r068"
    assert pics._made_from(off, {"material": "pc_clear", "made_from": "x"}, reg, []) == "x"
    assert pics._made_from(off, {"material": "pc_clear", "scrap": True}, reg, []) == ""


def test_a_picture_that_cant_be_drawn_never_stops_the_card(tmp_path, monkeypatch):
    h, s1 = first_sheet(tmp_path)
    calls = []

    def broken(*a, **k):
        calls.append(1)
        raise RuntimeError("no fonts")
    monkeypatch.setattr(drawings, "offcut_png", broken)
    cut(h, s1.id)
    [off] = offcut_cards(h)
    h.runner.tick()
    assert len(calls) == 1
    assert pictures_on(h, off.id) == [] and h.runner.offcuts.get(off.id)["picture_sig"]   # tried once, not again


def test_an_own_program_cut_on_an_offcut_is_drawn_from_its_placed_copy(tmp_path):
    from test_own_programs import own_card, sheet_cards
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off] = offcut_cards(h)
    own_card(h)
    h.runner.tick()
    [s] = [c for c in sheet_cards(h) if "own program" in c.name]
    h.tracker.tick_all(s.id, "Review")
    downloads = []
    real = h.tracker.download
    h.tracker.download = lambda att: downloads.append(att.name) or real(att)
    cut(h, s.id)
    h.runner.tick()
    cuts, sheets = h.runner.pictures.piece_cuts(off.id, h.runner.offcuts.get(off.id))
    assert sheets == [s1.id, s.id] and len(cuts) == 2 and all(moves for moves, _ in cuts)
    assert downloads == [h.store.sheet_cards()[s.id]["own"]["placed"]]               # read once, then kept
    assert (h.store.sheets_file.with_name("pictures") / f"{s.id}.tap").is_file()


def test_used_length_no_program_explains_is_shown_as_such(tmp_path):
    h, s1 = first_sheet(tmp_path)
    cut(h, s1.id)
    [off] = offcut_cards(h)
    piece = h.runner.offcuts.get(off.id)
    piece["used"] = [[0.5, 8.5], [20.0, 30.0]]                                     # a stretch nothing on record cut
    seen = {}
    real = drawings.offcut_png

    def spy(**kw):
        seen.update(kw)
        return real(**kw)
    import autocam_service.card_pictures as cp
    cp.drawings.offcut_png = spy
    try:
        cuts, _ = h.runner.pictures.piece_cuts(off.id, piece)
        h.runner.pictures.offcut_png(off.id, piece, cuts)
    finally:
        cp.drawings.offcut_png = real
    assert seen["unknown"] == [(20.0, 30.0)] and seen["free"] == [(8.5, 20.0), (30.0, 48.0)]
