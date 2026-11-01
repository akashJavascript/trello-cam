"""Fit series (core 0.16.0): a part's Fit: line has that hole size or its outline cut tight by the sheet's program,
then a lap program per further stock value, each the same op alone with a little less stock."""

import dataclasses

from autocam_core import errors as E
from autocam_core.names import fit_program_name
from rig import Rig, plate

STOCKS = (0.003, 0.002, 0.001, 0.0, -0.001)


def hole_job(rig, fit=("hole", 0.5), holes=(0.5, 0.3)):
    return rig.job([("retainer", 2, plate(holes=holes), (4.0, 4.0)), ("spacer", 1, plate(holes=(0.3,)), (3.0, 3.0))],
                   fits={"retainer": fit})


def test_a_hole_fit_is_cut_tight_with_a_lap_per_step(tmp_path):
    rig = Rig(tmp_path)
    job = hole_job(rig)
    assert job.parts[0].fit.stocks_in == STOCKS and job.parts[0].tabs
    result = rig.run(job)
    assert result.status == "ok", (result.sheets[0].errors, [p.errors for p in result.parts])
    (sheet,) = result.sheets
    s = rig.fake.sheets[sheet.name]
    fit_op = "[bore] holes (2)"                                          # the template's again, not renamed
    assert [cid for cid, _ in s["fills"][fit_op].holes] == ["p01.1", "p01.2"] and s["stock"][fit_op] == 0.003
    assert len(s["fills"]["[bore] holes"].holes) == 3                    # the 0.3 holes, not the fitted ones
    laps = [p for p in sheet.programs if p.kind == "fit"]
    assert [(p.lap, p.stock_in, p.series, p.feature, p.runs) for p in laps] == \
        [(k, st, 1, "0.5 hole", "after") for k, st in enumerate(STOCKS[1:], 1)]
    for p in laps:
        assert p.name == fit_program_name(sheet.name, 1, p.lap) and p.tap and p.guard.passed
        lap = rig.fake.sheets[p.name]
        assert lap["ops"] == ["[bore] holes"] and lap["stock"]["[bore] holes"] == p.stock_in
        assert [cid for cid, _ in lap["fills"]["[bore] holes"].holes] == ["p01.1", "p01.2"]
        assert [(q.part_key, q.count) for q in p.parts] == [("p01", 2)]
    view = rig.service_view(job, result)
    (vs,) = view.sheets
    assert vs.cuttable and all(v.ok for v in vs.programs)


def test_an_outline_fit_keeps_its_tabs_in_every_lap(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("slider", 1, plate(), (5.0, 3.0))], fits={"slider": ("outline", 0.0)})
    result = rig.run(job)
    assert result.status == "ok", result.sheets[0].errors
    (sheet,) = result.sheets
    main = rig.fake.sheets[sheet.name]
    assert main["stock"]["[outer] p01-1"] == 0.003
    points = main["tab_points"]["[outer] p01-1"]
    for p in [p for p in sheet.programs if p.kind == "fit"]:
        lap = rig.fake.sheets[p.name]
        assert lap["ops"] == ["[outer] p01-1"] and lap["stock"]["[outer] p01-1"] == p.stock_in
        assert lap["tab_points"]["[outer] p01-1"] == points                # the same tabs: never cut free
    assert rig.service_view(job, result).sheets[0].cuttable


def test_a_fit_on_a_hole_the_part_doesnt_have_sends_it_back(tmp_path):
    rig = Rig(tmp_path)
    result = rig.run(hole_job(rig, fit=("hole", 0.75)))
    retainer = result.parts[0]
    assert any(e.code == E.NEEDS_MANUAL_CAM and "Fit: 0.75 hole: no bored hole that size" in e.msg
               for e in retainer.errors)
    rig = Rig(_sub(tmp_path, "drilled"))
    result = rig.run(hole_job(rig, fit=("hole", 0.159), holes=(0.159,)))
    assert any("a drilled hole can't be fitted" in e.msg for e in result.parts[0].errors)


def test_a_lap_that_fails_its_check_stops_the_sheet(tmp_path):
    rig = Rig(tmp_path)
    job = hole_job(rig)
    rig.fake.below_floor.add(fit_program_name("6061_0p125_r001_S1", 1, 2))
    result = rig.run(job)
    (sheet,) = result.sheets
    assert sheet.tap is None and any("FIT1_2" in e.msg and "below" in e.msg for e in sheet.errors)


def test_the_service_wants_every_lap(tmp_path):
    from autocam_core.schema_result import result_json
    rig = Rig(tmp_path)
    job = hole_job(rig)
    result = rig.run(job)
    s = result.sheets[0]
    result = dataclasses.replace(result, sheets=(dataclasses.replace(s, programs=s.programs[:2]),))
    (rig.out / "result.json").write_text(result_json(result), encoding="utf-8")
    (vs,) = rig.service_view(job, result).sheets
    assert not vs.cuttable and any("fit laps leave" in p for p in vs.problems)


def _sub(tmp_path, name):
    (tmp_path / name).mkdir()
    return tmp_path / name


def test_a_hole_over_the_bore_range_is_fitted_as_a_cutout_with_its_slug_tabbed_then_laps_without(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("hub", 1, plate(holes=(2.0, 0.5)), (6.0, 6.0))], fits={"hub": ("hole", 2.0)})
    result = rig.run(job)
    assert result.status == "ok", (result.sheets[0].errors, [p.errors for p in result.parts])
    (sheet,) = result.sheets
    s = rig.fake.sheets[sheet.name]
    fit_op = "[inner] cutouts (2)"                                         # the template's [inner] again, alone
    assert [cid for cid, _, _ in s["fills"][fit_op].loops] == ["p01.1"] and s["stock"][fit_op] == 0.003
    assert "[inner] cutouts" not in s["fills"] or not s["fills"]["[inner] cutouts"].loops   # it's not cut twice
    assert s["tab_points"][fit_op]                                        # the slug is held through the program
    laps = [p for p in sheet.programs if p.kind == "fit"]
    assert [p.stock_in for p in laps] == list(STOCKS[1:]) and all(p.tap and p.cutout for p in laps)
    for p in laps:
        lap = rig.fake.sheets[p.name]
        assert lap["ops"] == ["[inner] cutouts"] and lap["stock"]["[inner] cutouts"] == p.stock_in
        assert not lap["tab_points"] and not lap["tabs"]                  # the slug's out by then: a full lap
    from autocam_service.sheet_cards import fit_lines, fit_series
    [series] = fit_series(job, sheet)
    assert "break the slug out by hand" in " ".join(fit_lines(series, sheet))
    assert rig.service_view(job, result).sheets[0].cuttable
