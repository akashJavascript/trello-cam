"""Parts drawn off square laid square in every Arrange (core 0.29.0, the user's pick, 2026-11-05: "Do 1").

Fusion's Arrange keeps a part at the angle it's drawn at in CAD (p0201 at 11 deg, r093-r103), so a part drawn off
square was nested, and cut, at a slant. Before nesting, a probe lays one copy of each part flat behind the sheets and
measures it; for one off square, two more are laid along its longest straight edge, turned on one way and the other,
and the way that comes out square goes to every later Arrange. The fake Arrange keeps a part at its angle
(rig.fake.tilted) unless it's given an edge and turned the right way on from it (edge_skew, rotation_sign).
"""

import autocam_worker.pipeline as pipeline
from rig import Rig, plate

BAR = (15.0, 2.0)
SLANT = 11.0


def log(rig):
    return (rig.out / pipeline.LOG).read_text(encoding="utf-8")


def bars(rig, qty=3):
    return rig.run(rig.job([("bar", qty, plate(name="bar"), BAR)]))


def test_a_part_drawn_at_an_angle_is_nested_square(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.tilted = {"p01": SLANT}
    result = bars(rig)
    [sheet] = result.sheets
    assert all(p.placed == p.qty for p in result.parts)
    assert sheet.free_length_in == 38.5 - 6.5                 # three square rows from Fusion's own nest
    assert "drawn off square, laid square in every Arrange: p01 (turned +0.00 deg on from its edge)" in log(rig)
    assert set(rig.fake.arrange_along[0]) == {"p01.1", "p01.2", "p01.3"}  # the first nest Arrange: every copy
    assert "stays as Fusion nested it" in log(rig)            # nothing left for the straight rows to do


def test_the_probe_finds_which_way_fusion_turns_it(tmp_path):
    # p0201-Mirrored: its edges run 2.67 deg off its smallest rectangle, and Fusion turned it the other way from its
    # reference's counterclockwise (r103).
    rig = Rig(tmp_path)
    rig.fake.tilted, rig.fake.edge_skew, rig.fake.rotation_sign = {"p01": SLANT}, {"p01": 2.67}, -1.0
    result = bars(rig)
    assert result.sheets[0].free_length_in == 38.5 - 6.5
    assert "p01 (turned -2.67 deg on from its edge)" in log(rig)
    [first, second] = rig.fake.probe_along
    assert first == {} and sorted(t for _, t in second.values()) == [-2.67, 2.67]   # each way, once
    assert {t for a in rig.fake.arrange_along for _, t in a.values()} == {-2.67}


def test_a_part_drawn_square_is_left_as_arrange_lays_it(tmp_path):
    rig = Rig(tmp_path)
    result = bars(rig)
    assert all(p.placed == p.qty for p in result.parts)
    assert rig.fake.probe_along == [{}]                       # measured once, nothing to turn
    assert not any(rig.fake.arrange_along) and "drawn off square" not in log(rig)


def test_a_part_the_probe_cant_square_is_nested_as_arrange_lays_it(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.tilted, rig.fake.edge_skew, rig.fake.rotation_sign = {"p01": SLANT}, {"p01": 2.67}, 0.0   # neither way
    result = bars(rig)
    assert all(p.placed == p.qty for p in result.parts)
    assert "drawn off square, but Arrange didn't lay it square: p01" in log(rig)
    assert not any(a for a in rig.fake.arrange_along if set(a) & {"p01.1", "p01.2", "p01.3"})


def test_the_probe_copies_are_hidden_and_never_cut(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.tilted = {"p01": SLANT}
    result = bars(rig)
    assert {f"discard p01.1{m}" for m in pipeline.PROBES} <= set(rig.fake.calls)
    assert sum(p.count for p in result.sheets[0].parts) == 3
    assert not rig.fake.late                                  # made before the first Arrange, like every copy


def test_square_parts_off_makes_no_probe(tmp_path):
    rig = Rig(tmp_path, square_parts=False)
    rig.fake.tilted = {"p01": SLANT}
    bars(rig)
    assert not any("~m" in c for c in rig.fake.calls) and rig.fake.probe_along == []
