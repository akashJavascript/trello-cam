"""Parts placed by hand ("Nest this part" unticked): one copy, alone, long side along X, zeroed at its box's
front-left corner; the program is run once per copy ordered."""

import dataclasses

from rig import Rig, plate


def test_a_part_placed_by_hand_gets_a_program_of_its_own(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("gusset", 2, plate(name="gusset"), (6.0, 3.0)), ("bracket", 3, plate(name="bracket"), (5.0, 2.0))])
    job = dataclasses.replace(job, parts=(job.parts[0], dataclasses.replace(job.parts[1], by_hand=True)))
    result = rig.run(job)
    nested, by_hand = result.sheets
    assert [(p.part_key, p.count) for p in nested.parts] == [("p01", 2)] and not nested.by_hand
    assert by_hand.by_hand and by_hand.repeat == 3 and [(p.part_key, p.count) for p in by_hand.parts] == [("p02", 1)]
    assert by_hand.tap and not by_hand.errors and by_hand.used_y_in is None and by_hand.parts_area_in2 is None
    fake = rig.fake.sheets[by_hand.name]
    assert fake["size"] == (5.0, 2.0)                    # the stock is the part's own box
    [cid] = fake["copies"]
    box = rig.fake.boxes[cid]
    assert fake["origin"] == (box.x0, box.y0)            # zero: the box's front-left corner
    assert [a for a in rig.fake.calls if a.startswith("add_copy") and "p02" in a] == []    # one copy only
    bracket = next(p for p in result.parts if p.part_key == "p02")
    assert bracket.placed == 3 and bracket.sheets == (2,)
    # its program leaves the box (the cutter goes round the outside of the part): fine when placed by hand
    program = (rig.out / by_hand.tap).read_text()
    assert "X-0." in program


def test_a_part_that_wont_lie_long_side_along_x_is_rejected(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("post", 1, plate(name="post"), (2.0, 5.0))])   # the fake can't turn it round
    job = dataclasses.replace(job, parts=(dataclasses.replace(job.parts[0], by_hand=True),))
    result = rig.run(job)
    assert result.sheets == () or not result.sheets
    [part] = result.parts
    assert any("long side along X" in e.msg for e in part.errors)
