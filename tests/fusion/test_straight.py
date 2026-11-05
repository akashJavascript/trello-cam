"""Straight rows after Fusion nests (core 0.27.0, the user's pick, 2026-11-05).

Fusion's Arrange turns parts to any angle, and it stacked three long bars on a slant (p0201: 7.7 in of the sheet's
length, no room beside them). The pipeline also lays a sheet's parts in straight rows, each row placed by Arrange in
an area only as deep as its deepest part, and keeps whichever leaves the better offcut. The fake Arrange here keeps a
part drawn at an angle (rig.fake.tilted) at that angle, like Fusion did with p0201 (r093, r094; in a row too shallow
for it that way it placed nothing, r098): its outline is a turned rectangle, its box much bigger. Since core 0.28.0 a
row's Arrange is given the part's long straight edge to lay along X (setRotationUsingEdge), and then it lies square.
"""

import autocam_worker.pipeline as pipeline
from autocam_core.schema_job import OffcutSpec
from rig import Rig, plate
from test_offcut_nesting import BESIDE, job_with

BAR = (15.0, 2.0)                 # a 15 x 2 in bar
SLANT = 11.0                      # drawn at 11 deg in CAD, like p0201: its box 15.1 x 4.8 in


def log(rig):
    return (rig.out / pipeline.LOG).read_text(encoding="utf-8")


def strips(rig, depth):
    """The Arranges given a straight row's area for parts `depth` deep."""
    return [e for e in rig.fake.arrange_envelopes
            if any(abs(e[3] - e[1] - (depth + slack)) < 1e-6 for slack in pipeline.STRAIGHT_SLACKS_IN)]


def test_bars_on_a_slant_are_laid_straight(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.tilted = {"p01": SLANT}
    result = rig.run(rig.job([("bar", 3, plate(name="bar"), BAR)]))
    [sheet] = result.sheets
    assert all(p.placed == p.qty for p in result.parts)
    # Three rows of one, front to back: 3 x 2 in + 2 x 0.25 in between, from 0.75 in (the nest area's front).
    assert sheet.used_y_in == (0.5, 0.75 + 6.5 + 0.25)
    assert sheet.free_length_in == 38.5 - 6.5
    assert len(strips(rig, 2.0)) == 3                                    # each row its own Arrange, 2.1 in deep
    # The room right of the bars, the whole band deep, is kept for the next nest.
    assert any(r[0] > 15.0 and r[3] - r[1] >= 6.9 for r in sheet.beside_left_in)
    assert "laid in 3 straight row(s)" in log(rig)
    assert not rig.fake.late                              # every copy was made before the first Arrange


def test_parts_fusion_already_laid_straight_stay_as_they_are(tmp_path):
    rig = Rig(tmp_path)
    result = rig.run(rig.job([("gusset", 3, plate(name="gusset"), (6.0, 4.0))]))
    [sheet] = result.sheets
    assert sheet.free_length_in == 38.5 - 4.0                           # one row of three, as Fusion put them
    assert not strips(rig, 4.0)                                         # the rows weren't even tried in Fusion
    assert "stays as Fusion nested it" in log(rig)
    assert not any(c.startswith("discard") and "~r" not in c and "~s" not in c for c in rig.fake.calls)
    assert not any(rig.fake.arrange_along)                              # lying square: no edge to lay it along


def test_a_sheet_with_more_parts_than_straight_max_is_not_tried(tmp_path):
    rig = Rig(tmp_path, straight_max=2)
    rig.fake.tilted = {"p01": SLANT}
    result = rig.run(rig.job([("bar", 3, plate(name="bar"), BAR)]))
    assert not strips(rig, 2.0) and "not tried in straight rows (3 parts, more than 2)" in log(rig)
    assert all(p.placed == p.qty for p in result.parts)


def test_straight_max_0_makes_no_copies_for_it(tmp_path):
    rig = Rig(tmp_path, straight_max=0)
    rig.fake.tilted = {"p01": SLANT}
    rig.run(rig.job([("bar", 3, plate(name="bar"), BAR)]))
    assert not any("~r" in c for c in rig.fake.calls) and not strips(rig, 2.0)


def test_a_part_longer_than_the_sheet_is_wide_is_not_tried(tmp_path):
    rig = Rig(tmp_path)
    result = rig.run(rig.job([("rail", 2, plate(name="rail"), (2.0, 30.0))]))
    assert all(p.placed == p.qty for p in result.parts)
    assert "a part is longer than the sheet is wide" in log(rig)


def test_a_row_fusion_wont_fill_keeps_fusions_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "STRAIGHT_SLACKS_IN", (-0.5, -0.2))     # each row's area too shallow for its part
    rig = Rig(tmp_path)
    rig.fake.no_room_raises = True                                       # like Fusion: an Arrange placing nothing
    rig.fake.tilted = {"p01": SLANT}
    result = rig.run(rig.job([("bar", 3, plate(name="bar"), BAR)]))
    [sheet] = result.sheets
    assert all(p.placed == p.qty for p in result.parts)
    assert "straight rows: Fusion placed 0 of row 1's 1" in log(rig)
    assert sheet.free_length_in < 38.5 - 6.5                             # Fusion's slant, squeezed: longer
    hidden = {c.split()[1] for c in rig.fake.calls if c.startswith("discard ")}
    assert {f"p01.{n}~r" for n in (1, 2, 3)} <= hidden                   # the rows' copies were hidden


def test_rows_on_an_offcut_keep_the_parts_beside_earlier_cuts(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.tilted = {"p02": SLANT}
    offcut = OffcutSpec("off1", 0.125, ((0.5, 4.0),), beside_in=(BESIDE,))
    job = job_with(rig, [("tab", 1, plate(name="tab"), (3.0, 3.0)), ("bar", 2, plate(name="bar"), BAR)], [offcut])
    result = rig.run(job)
    [sheet] = result.sheets
    assert sheet.offcut_id == "off1" and sheet.beside_used == (0,)
    assert sorted((p.part_key, p.count) for p in sheet.parts) == [("p01", 1), ("p02", 2)]
    # The bars in two straight rows behind the old cut: from 4.75 in, 2 + 0.25 + 2 in deep.
    assert sheet.used_y_in == (4.5, 4.75 + 4.25 + 0.25)
    assert "laid in 2 straight row(s)" in log(rig) and not rig.fake.late


def test_the_rows_use_the_parts_real_width_not_their_box_in_cad(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.tilted = {"p01": SLANT}                      # drawn at 11 deg: its box in CAD is 15.1 x 4.8 in
    rig.run(rig.job([("bar", 3, plate(name="bar"), BAR)]))
    assert "smallest rectangles: p01 15.00 x 2.00 in" in log(rig) and "laid in 3 straight row(s)" in log(rig)


def test_a_row_fusion_places_none_of_gets_a_little_more_room(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "STRAIGHT_SLACKS_IN", (-0.5, 0.1))      # the first area too shallow
    rig = Rig(tmp_path)
    rig.fake.no_room_raises = True                        # like Fusion: an Arrange placing nothing raises, moves nothing
    rig.fake.tilted = {"p01": SLANT}
    result = rig.run(rig.job([("bar", 3, plate(name="bar"), BAR)]))
    [sheet] = result.sheets
    assert "laid in 3 straight row(s)" in log(rig) and sheet.free_length_in == 38.5 - 6.5
    assert len(strips(rig, 2.0)) == 6                     # each row: the shallow area, then the one that fit


def test_a_part_drawn_at_an_angle_is_laid_along_its_long_edge_in_each_row(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.tilted = {"p01": SLANT}
    rig.run(rig.job([("bar", 3, plate(name="bar"), BAR)]))
    rows = [a for a in rig.fake.arrange_along if a]
    # each row's Arrange: its copy, laid along one of the bar's long edges (the fake outline's 1st or 3rd, 15 in)
    assert [sorted(a) for a in rows] == [["p01.1~r"], ["p01.2~r"], ["p01.3~r"]]
    assert len({e for a in rows for e in a.values()}) == 1 and set(rows[0].values()) <= {"e1", "e3"}
    assert "off square, laid along a straight edge in the rows: p01" in log(rig)


def test_without_an_edge_to_lay_it_along_a_slanted_part_fits_no_row(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_square_edge", lambda *a: None)     # like 0.27.0 (r098)
    rig = Rig(tmp_path)
    rig.fake.no_room_raises = True
    rig.fake.tilted = {"p01": SLANT}
    result = rig.run(rig.job([("bar", 3, plate(name="bar"), BAR)]))
    [sheet] = result.sheets
    assert "straight rows: Fusion placed 0 of row 1's 1" in log(rig)
    assert all(p.placed == p.qty for p in result.parts) and sheet.free_length_in < 38.5 - 6.5
