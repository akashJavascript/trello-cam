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
    assert (region[2] - region[0] - 2 * s, region[3] - region[1] - 2 * s) == (21.0, 38.5)    # X across, Y along
    # As listed, the five small plates fill three rows and the big plate needs a second sheet of its own, which
    # it fills to 18 in along Y. Biggest first leaves one small plate for the second sheet: 7 in.
    job = rig.job([("small", 5, plate(name="small"), (9.0, 7.0)), ("long", 1, plate(name="long"), (20.0, 18.0))])
    result = rig.run(job)
    assert len(result.sheets) == 2 and all(p.placed == p.qty for p in result.parts)
    assert [[(p.part_key, p.count) for p in sh.parts] for sh in result.sheets] == [[("p01", 4), ("p02", 1)],
                                                                                  [("p01", 1)]]
    assert any("nesting the parts biggest first beat the listed order (6 placed on 2 sheet(s), last 7.0 in "
               "instead of 6 placed on 2 sheet(s), last 18.0 in)" in n for n in result.notes)
    # Each try had its own copies; the listed try's were hidden, and the copies left are the winner's.
    assert {f"p01.{n}" for n in range(1, 6)} | {"p02.1"} <= hidden(rig.fake)
    assert set(rig.fake.copies) == {f"p01.{n}~2" for n in range(1, 6)} | {"p02.1~2"}


def test_one_kind_of_part_is_arranged_once(tmp_path):
    rig = Rig(tmp_path)
    result = rig.run(rig.job([("gusset", 4, plate(name="gusset"), (6.0, 4.0))]))
    # one order only (no "~2" copies); the squeeze may try shorter areas, but these can't get shorter
    assert not any("~" in c for c in rig.fake.copies) and not any("~2" in c for c in rig.fake.calls)
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
    assert sheet.parts_area_in2 == 3 * 48.0 and sheet.usable_area_in2 == 21.0 * 38.5
    assert sheet.free_length_in == 38.5 - 4.0                # one row of three, 4 in deep, at the front


def test_parts_run_down_the_left_edge_are_squeezed_into_a_strip_across_the_front(tmp_path):
    rig = Rig(tmp_path)
    rig.fake.columns = True                               # pack like Fusion did in r009: down the left edge first
    result = rig.run(rig.job([("gusset", 4, plate(name="gusset"), (6.0, 4.0))]))
    [sheet] = result.sheets
    # One column would run 16.75 in down the sheet; two columns of two take 8.25 in.
    assert sheet.free_length_in == 38.5 - 8.25 and sheet.used_y_in[1] < 0.5 + 0.25 + 8.25 + 0.25 + 1e-6
    assert all(p.placed == p.qty for p in result.parts)
    assert len({c for c in rig.fake.copies}) == 4 and all("~s" in c for c in rig.fake.copies)   # the squeezed copies
