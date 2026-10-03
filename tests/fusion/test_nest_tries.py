"""Better nests: the parts are arranged in a few orders and the best nest is kept (pipeline.NEST_ORDERS).

The fake Arrange packs in rows in the order it's given, so the order matters here. Whether it matters to
Fusion's Arrange is what the worker log ("kept 'biggest first' (...)") will show on real jobs.
"""

from rig import Rig, plate


def hidden(fake):
    return {c.split()[1] for c in fake.calls if c.startswith("discard ")}


def test_the_best_of_the_orders_is_kept(tmp_path):
    rig = Rig(tmp_path)
    region = rig.job([("x", 1, plate(name="x"), (1.0, 1.0))]).fixture.nest_region_in
    s = rig.cfg.nest.part_spacing_in
    assert (region[2] - region[0] - 2 * s, region[3] - region[1] - 2 * s) == (38.5, 21.0)
    # As listed, the five small plates fill a row and a half and the long plate needs a second sheet of its own,
    # which it fills to 38 in. Biggest first leaves the small ones for the second sheet, and they reach 36.75 in.
    job = rig.job([("small", 5, plate(name="small"), (9.0, 7.0)), ("long", 1, plate(name="long"), (38.0, 14.0))])
    result = rig.run(job)
    assert len(result.sheets) == 2 and all(p.placed == p.qty for p in result.parts)
    assert [[(p.part_key, p.count) for p in sh.parts] for sh in result.sheets] == [[("p02", 1)], [("p01", 5)]]
    assert any("nesting the parts biggest first beat the listed order (6 placed on 2 sheet(s), last 36.8 in "
               "instead of 6 placed on 2 sheet(s), last 38.0 in)" in n for n in result.notes)
    # Each try had its own copies; the listed try's were hidden, and the copies left are the winner's.
    assert {f"p01.{n}" for n in range(1, 6)} | {"p02.1"} <= hidden(rig.fake)
    assert set(rig.fake.copies) == {f"p01.{n}~2" for n in range(1, 6)} | {"p02.1~2"}


def test_one_kind_of_part_is_arranged_once(tmp_path):
    rig = Rig(tmp_path)
    result = rig.run(rig.job([("gusset", 4, plate(name="gusset"), (6.0, 4.0))]))
    assert len(rig.fake.arrange_envelopes) == 1 and not any("~" in c for c in rig.fake.copies)
    assert not any("beat the listed order" in n for n in result.notes)


def test_a_tie_keeps_the_listed_order(tmp_path):
    rig = Rig(tmp_path)
    result = rig.run(rig.job([("a", 1, plate(name="a"), (5.0, 5.0)), ("b", 1, plate(name="b"), (8.0, 6.0))]))
    assert len(rig.fake.arrange_envelopes) == 2              # listed, then biggest first (= longest first: skipped)
    assert not any("beat" in n for n in result.notes)
    hidden = {c.split()[1] for c in rig.fake.calls if c.startswith("discard ")}
    assert {"p01.1~2", "p02.1~2"} <= hidden and not {"p01.1", "p02.1"} & hidden


def test_sheet_use_is_reported(tmp_path):
    rig = Rig(tmp_path)
    result = rig.run(rig.job([("gusset", 3, plate(name="gusset"), (6.0, 4.0))]))
    [sheet] = result.sheets
    assert sheet.parts_area_in2 == 3 * 48.0 and sheet.usable_area_in2 == 38.5 * 21.0
    assert sheet.free_length_in == 38.5 - (3 * 6.0 + 2 * 0.25)
