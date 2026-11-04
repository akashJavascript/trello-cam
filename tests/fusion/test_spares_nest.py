"""Spares (core 0.24.0; the user, 2026-11-04: "robot parts should come first"): a card with the Spare label is
nested after the robot parts. It gets only the room they leave on the sheet where the last of them went (right of
them and behind them), then sheets of its own, so a spare never moves a robot part to a later sheet.

The fake Arrange packs in rows, higher priority first, so before 0.24.0 a big spare (very high priority by size)
went first and pushed robot parts onto the next sheet.
"""

from autocam_service.sheet_cards import sheet_description
from rig import Rig, plate


def sheets_of(result):
    return [[(p.part_key, p.count) for p in sh.parts] for sh in result.sheets]


def test_a_big_spare_never_pushes_robot_parts_onto_the_next_sheet(tmp_path):
    rig = Rig(tmp_path)
    # Six 9 x 7 gussets take three rows (21.5 in). The 20 x 18 spare fits neither beside them (2.5 in) nor
    # behind them (16.75 in), so it gets a sheet of its own. Arranged together, it would have gone first and
    # left two gussets for the second sheet.
    job = rig.job([("gusset", 6, plate(name="gusset"), (9.0, 7.0)), ("big", 1, plate(name="big"), (20.0, 18.0))],
                  spares={"big"})
    result = rig.run(job)
    assert all(p.placed == p.qty for p in result.parts)
    assert sheets_of(result) == [[("p01", 6)], [("p02", 1)]]
    assert all(set(h) <= {"p01.1", "p01.2", "p01.3", "p01.4", "p01.5", "p01.6"} or set(h) <= {"p02.1"}
               for h in rig.fake.arrange_hints if h)                 # never ranked against each other


def test_spares_fill_the_room_behind_the_robot_parts(tmp_path):
    rig = Rig(tmp_path)
    s = rig.cfg.nest.part_spacing_in
    job = rig.job([("plate", 2, plate(name="plate"), (10.0, 5.0)), ("tab", 4, plate(name="tab"), (4.0, 3.0))],
                  spares={"tab"})
    region = job.fixture.nest_region_in
    result = rig.run(job)
    assert sheets_of(result) == [[("p01", 2), ("p02", 4)]]           # one sheet: the spares joined it
    y0 = region[1] + s                                                # the sheet's area starts here along Y
    first = rig.fake.arrange_envelopes[0]
    behind = [e for e in rig.fake.arrange_envelopes[1:3] if abs(e[1] - (y0 + 5.0 + s)) < 1e-6]
    assert first[1] == y0 and behind and behind[0][0] == first[0]    # behind the 5 in row of plates
    assert all(rig.fake.boxes[f"p02.{n}"].y0 >= rig.fake.boxes["p01.1"].y1 + s - 1e-6 for n in range(1, 5)) or \
        any("~s" in c for c in rig.fake.copies)                       # (unless the squeeze packed them anew)


def test_spares_alone_nest_like_any_part(tmp_path):
    rig = Rig(tmp_path)
    result = rig.run(rig.job([("tab", 4, plate(name="tab"), (4.0, 3.0))], spares={"tab"}))
    assert sheets_of(result) == [[("p01", 4)]] and result.parts[0].placed == 4


def test_spares_wait_for_a_later_run_when_the_robot_parts_take_every_sheet(tmp_path):
    rig = Rig(tmp_path, max_sheets_per_group=1)
    job = rig.job([("gusset", 10, plate(name="gusset"), (9.0, 7.0)), ("tab", 2, plate(name="tab"), (8.0, 7.0))],
                  spares={"tab"})
    result = rig.run(job)
    by = {p.part_key: p for p in result.parts}
    assert by["p01"].placed == 10 and by["p02"].placed == 0           # five rows of gussets: no room for the spares
    assert sheets_of(result) == [[("p01", 10)]]


def test_the_sheet_card_marks_the_spares(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("plate", 2, plate(name="plate"), (10.0, 5.0)), ("tab", 2, plate(name="tab"), (4.0, 3.0))],
                  spares={"tab"})
    result = rig.run(job)
    view = rig.service_view(job, result)
    (vs,) = view.sheets
    desc = sheet_description(job, view, vs, resume_key="ENTER", part_cards={"p01": ("Plate", ""), "p02": ("Tab", "")})
    assert "Parts marked spare go in the spares box, not on the robot." in desc
    assert "Tab (1 of 2) - spare" in desc and "Plate (1 of 2)\n" in desc + "\n" and "Plate (1 of 2) - spare" not in desc
