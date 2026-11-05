"""Fit series on the board (fits.py): the Fit: line, the laps on the sheet card, and what's learned from the tick."""

from dataclasses import replace

from autocam_core.schema_job import read_job
from autocam_service.fits import FitStore, parse_fit, stocks
from fakeworker import run_fake_worker
from test_air_test import programs
from test_runner_autostart import add, harness, run, sheet, step_card


def fit_card(cid="c1", line="Fit: 0.5 hole", qty=2, name="plate"):
    card = step_card(cid, name, qty=qty)
    return replace(card, desc=card.desc + "\n" + line)


def test_fit_lines():
    assert parse_fit("Fit: 1.125 hole") == ("hole", 1.125) and parse_fit("fit: hole .875") == ("hole", 0.875)
    assert parse_fit("Fit: outline") == ("outline", 0.0) and parse_fit("Qty: 2") is None
    assert parse_fit("Fit: 1/2 hole") == ("hole", 0.5)                     # not a 1 in hole
    assert parse_fit("Fit: 1-1/8 hole") == ("hole", 1.125) == parse_fit("Fit: 1 1/8 hole")
    assert parse_fit('Fit: 3/4" hole') == ("hole", 0.75)
    assert parse_fit("Fit: 22 mm hole") == ("hole", 0.8661)
    assert "isn't a fit AutoCAM knows" in parse_fit("Fit: snug")
    assert "isn't a fit AutoCAM knows" in parse_fit("Fit: 1/0 hole")
    assert "More than one Fit: line" in parse_fit("Fit: outline\nFit: 1 hole")


def test_the_stocks_start_tight_and_centre_on_what_fit_last_time():
    assert stocks(0.003, 0.001, 4) == (0.003, 0.002, 0.001, 0.0, -0.001)
    assert stocks(0.003, 0.001, 4, learned=0.0) == (0.002, 0.001, 0.0, -0.001, -0.002)


def test_a_fit_card_gets_its_laps_steps_and_checklist(tmp_path):
    h = harness(tmp_path, fit_card(), step_card("c2", "spacer"))
    h.runner.tick()
    job = read_job(next(h.queue.incoming.glob("*.json")))
    plate_spec = next(p for p in job.parts if p.card_id == "c1")
    assert plate_spec.fit.kind == "hole" and plate_spec.fit.diameter_in == 0.5 and plate_spec.tabs
    assert plate_spec.fit.stocks_in == (0.003, 0.002, 0.001, 0.0, -0.001) and plate_spec.fit.outline_last
    run_fake_worker(h.queue)
    h.runner.tick()
    s1 = sheet(h)
    assert sorted(programs(h, s1.id)) == ["6061_0p125_r001_S1.tap"] + [f"6061_0p125_r001_S1_FIT1_{k}.tap"
                                                                        for k in range(1, 5)] + \
        ["6061_0p125_r001_S1_OUTLINES.tap"]
    desc = h.tracker.cards[s1.id].desc
    assert "2. Fit plate's 0.5 holes (2 copies)" in desc
    # outlines last: the plate stays in the sheet while its holes are lapped, then the LAST program cuts it out
    assert "Fit plate's 0.5 holes (2 copies): cut 0.003 in tight on purpose. Try it." in desc
    assert "Leave plate in" not in desc                                  # it's still part of the sheet
    assert "Run 6061_0p125_r001_S1_FIT1_1.tap to _FIT1_4.tap, one at a time, until it fits." in desc
    assert "3. 6061_0p125_r001_S1_OUTLINES.tap · about 1 min · once it fits: cuts out plate" in desc
    assert "1. spacer\n" in desc and "2. plate (1 of 2) - fit - cut out last" in desc
    assert "stops after each part" not in desc                           # the sheet's program cuts one outline
    rows = h.tracker.checklists[(s1.id, "Fit: plate 0.5 hole")]
    assert [r[0] for r in rows] == ["Fits after the sheet's program (+0.003)", "Fits after FIT1_1 (+0.002)",
                                    "Fits after FIT1_2 (+0.001)", "Fits after FIT1_3 (+0)", "Fits after FIT1_4 (-0.001)",
                                    "Still tight after the last one"]


def test_with_outlines_last_off_the_part_is_cut_out_first_and_lapped_on_its_tabs(tmp_path):
    h = harness(tmp_path, fit_card(), step_card("c2", "spacer"), fit__outlines_last=False)
    run(h)
    s1 = sheet(h)
    assert "6061_0p125_r001_S1_OUTLINES.tap" not in programs(h, s1.id)
    desc = h.tracker.cards[s1.id].desc
    assert "Leave plate in at its stop." in desc and "OUTLINES" not in desc
    assert "1. plate (1 of 2) - fit\n" in desc and "3. spacer\n" in desc


def test_the_lap_that_fit_is_learned_for_the_next_sheet(tmp_path):
    h = harness(tmp_path, fit_card())
    run(h)
    s1 = sheet(h)
    for row in h.tracker.checklists[(s1.id, "Fit: plate 0.5 hole")]:
        row[1] = row[0] == "Fits after FIT1_3 (+0)"
    h.runner.tick()
    assert FitStore(h.store.offcuts_file.with_name("fits.json")).learned("al6061|hole|0.5000") == 0.0
    assert h.tracker.comments_on(s1.id)[-1] == ("Recorded: plate's 0.5 hole fit at +0 in of radial stock. The next "
                                               "sheet with it starts at +0.002.")
    h.runner.tick()
    assert sum("Recorded:" in c for c in h.tracker.comments_on(s1.id)) == 1          # once
    h.tracker.tick_all(s1.id, "Review")
    add(h, fit_card("c3", name="other plate", qty=1))
    h.runner.tick()
    job = read_job(next(h.queue.incoming.glob("r002-*.json")))
    assert job.parts[0].fit.stocks_in == (0.002, 0.001, 0.0, -0.001, -0.002)


def test_fit_lines_it_cant_use_go_to_needs_fixing(tmp_path):
    h = harness(tmp_path, fit_card(line="Fit: snug"))
    h.runner.tick()
    assert h.tracker.cards["c1"].list_key == "needs_fixing"
    assert "isn't a fit AutoCAM knows" in h.tracker.comments_on("c1")[-1]


def test_fits_can_be_switched_off(tmp_path):
    h = harness(tmp_path, fit_card())
    object.__setattr__(h.cfg, "fit", h.cfg.fit.__class__(enabled=False))
    h.runner.tick()
    job = read_job(next(h.queue.incoming.glob("*.json")))
    assert job.parts[0].fit is None
