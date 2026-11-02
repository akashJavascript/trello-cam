"""A material with its own sheet size (2026-11-02): SWYFT SRPP comes 24 x 24 in, MAXComposite 47 x 23. Its jobs nest,
clamp and make stock on that sheet, not [sheet]'s 24 x 48."""

from rig import Rig, plate


def boxes(rig, sheet):
    s = rig.fake.sheets[sheet]
    ox, oy = s["origin"]
    return [(b.x0 - ox, b.y0 - oy, b.x1 - ox, b.y1 - oy) for b in (rig.fake.box(c) for c in s["copies"])]


def test_a_swyft_job_nests_on_its_24_in_square_sheet(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("panel", 5, plate(0.125, name="panel"), (10.0, 10.0))], material="srpp_swyft")
    assert (job.sheet.length_in, job.sheet.width_in) == (24.0, 24.0)
    x0, y0, x1, y1 = job.fixture.nest_region_in
    assert 0 < x0 and x1 <= 24.0 and 0 < y0 and y1 <= 24.0
    result = rig.run(job)
    assert len(result.sheets) == 2 and all(s.tap for s in result.sheets)     # five 10 in squares: four a sheet
    for name, s in rig.fake.sheets.items():
        assert s["size"] == (24.0, 24.0)
        for bx0, by0, bx1, by1 in boxes(rig, name):
            assert x0 - 1e-6 <= bx0 and bx1 <= x1 + 1e-6 and y0 - 1e-6 <= by0 and by1 <= y1 + 1e-6


def test_the_same_parts_in_6061_fit_one_24_by_48_sheet(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("panel", 5, plate(0.125, name="panel"), (10.0, 10.0))], material="al6061")
    assert (job.sheet.length_in, job.sheet.width_in) == (48.0, 24.0)
    assert len(rig.run(job).sheets) == 1


def test_maxcomposite_keeps_clear_of_the_clamp_on_its_23_in_edge(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("panel", 1, plate(0.125, name="panel"), (6.0, 6.0))], material="srpp_maxcomposite")
    assert (job.sheet.length_in, job.sheet.width_in) == (47.0, 23.0)
    assert job.fixture.nest_region_in == (1.25, 0.5, 21.75, 39.5)              # 6061's ends at 22.75
    assert job.fixture.clamp_zones_in[1] == (21.75, 0.0, 23.0, 47.0)          # the clamp on its far edge
