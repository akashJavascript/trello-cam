"""Better nests (core 0.23.0, the user's pick of "1 and 2"): one nest order, Fusion's Arrange told which parts
matter most (priority by size) and, if [nest] fillers is on, which small ones fill in; then every sheet with a
free stretch squeezed toward its front.

Until 0.23.0 the parts were also arranged biggest first and longest first: on every real job the three orders
tied, so Fusion's Arrange ignores the order it's given. The fake Arrange here packs in rows, higher priority first,
then in the order given.
"""

import autocam_worker.pipeline as pipeline
from rig import Rig, plate


def hidden(fake):
    return {c.split()[1] for c in fake.calls if c.startswith("discard ")}


def test_priority_by_size_does_what_biggest_first_did_without_another_try(tmp_path):
    rig = Rig(tmp_path)
    region = rig.job([("x", 1, plate(name="x"), (1.0, 1.0))]).fixture.nest_region_in
    s = rig.cfg.nest.part_spacing_in
    assert (region[2] - region[0] - 2 * s, region[3] - region[1] - 2 * s) == (21.0, 38.5)    # X across, Y along
    # As listed, the five small plates would fill three rows and push the big one onto a second sheet, 18 in of
    # it. The big plate's very high priority puts it first: one small plate goes to the second sheet, 7 in.
    job = rig.job([("small", 5, plate(name="small"), (9.0, 7.0)), ("long", 1, plate(name="long"), (20.0, 18.0))])
    result = rig.run(job)
    assert len(result.sheets) == 2 and all(p.placed == p.qty for p in result.parts)
    assert [[(p.part_key, p.count) for p in sh.parts] for sh in result.sheets] == [[("p01", 4), ("p02", 1)],
                                                                                  [("p01", 1)]]
    levels, fillers = rig.fake.arrange_hints[0]
    assert levels == {"p02.1": 2, **{f"p01.{n}": 1 for n in range(1, 6)}} and fillers == ()   # 63 / 360 in2: high
    assert not any("~2" in c for c in rig.fake.calls)                  # no second order tried: no "~2" copies


def test_one_kind_of_part_has_no_priorities(tmp_path):
    rig = Rig(tmp_path)
    result = rig.run(rig.job([("gusset", 4, plate(name="gusset"), (6.0, 4.0))]))
    assert all(levels == {} for levels, _ in rig.fake.arrange_hints)
    assert not any("~2" in c for c in rig.fake.copies) and not any("beat the listed order" in n for n in result.notes)


def test_priorities_can_be_switched_off(tmp_path):
    rig = Rig(tmp_path, priority_by_size=False)
    rig.run(rig.job([("a", 1, plate(name="a"), (5.0, 5.0)), ("b", 1, plate(name="b"), (18.0, 16.0))]))
    assert all(levels == {} for levels, _ in rig.fake.arrange_hints)


def test_small_parts_are_fillers_only_when_asked(tmp_path):
    parts = [("spacer", 3, plate(name="spacer"), (2.0, 1.5)), ("side", 1, plate(name="side"), (20.0, 12.0))]
    rig = Rig(tmp_path)
    rig.run(rig.job(parts))
    assert all(fillers == () for _, fillers in rig.fake.arrange_hints)          # off by default
    (tmp_path / "on").mkdir()
    rig = Rig(tmp_path / "on", fillers=True)
    rig.run(rig.job(parts))
    levels, fillers = rig.fake.arrange_hints[0]
    assert set(fillers) == {"p01.1", "p01.2", "p01.3"} and levels == {"p02.1": 2}   # the side plate, very high
    (tmp_path / "small").mkdir()
    rig = Rig(tmp_path / "small", fillers=True)                        # nothing over twice filler_max_in: none
    rig.run(rig.job([("spacer", 3, plate(name="spacer"), (2.0, 1.5)), ("tab", 1, plate(name="tab"), (6.0, 3.0))]))
    assert all(fillers == () for _, fillers in rig.fake.arrange_hints)


def test_every_sheet_with_a_free_stretch_is_squeezed(tmp_path, monkeypatch):
    seen = []
    real = pipeline._squeeze

    def spy(adapter, job, best, idx, *rest):
        seen.append(best.envelopes[idx[-1]][0])                       # the sheet's origin
        return real(adapter, job, best, idx, *rest)
    monkeypatch.setattr(pipeline, "_squeeze", spy)
    rig = Rig(tmp_path)
    result = rig.run(rig.job([("gusset", 40, plate(name="gusset"), (6.0, 4.0))]))
    assert len(result.sheets) == 2 and len(set(seen)) == 2              # both sheets, not just the last


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
    # Every copy was made before the first Arrange: Fusion refuses copies made after one (r012, r013).
    assert not rig.fake.late
