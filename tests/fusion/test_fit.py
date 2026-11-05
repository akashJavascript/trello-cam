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


# ---- outlines last (core 0.26.0): a hole fit's part stays solid in the sheet until its OUTLINES program

def test_a_hole_fit_leaves_the_parts_outlines_for_the_outlines_program_run_last(tmp_path):
    from autocam_core.names import outlines_program_name
    rig = Rig(tmp_path)
    job = hole_job(rig)
    assert job.parts[0].fit.outline_last and not job.parts[1].fit
    result = rig.run(job)
    assert result.status == "ok", (result.sheets[0].errors, [p.errors for p in result.parts])
    (sheet,) = result.sheets
    assert sheet.outlines_last == ("p01-1", "p01-2") and sheet.outer_order[-2:] == sheet.outlines_last
    assert sheet.main_order == ("p02-1",)
    main = rig.fake.sheets[sheet.name]
    assert [o for o in main["ops"] if o.startswith("[outer]")] == ["[outer] p02-1"]   # the spacer only
    assert "[outer] p01-1" not in main["tab_points"]
    late = sheet.programs[-1]
    assert (late.kind, late.name, late.runs) == ("outlines", outlines_program_name(sheet.name), "after")
    assert late.tap and late.guard.passed and [(q.part_key, q.count) for q in late.parts] == [("p01", 2)]
    out = rig.fake.sheets[late.name]
    assert out["ops"] == ["[outer] p01-1", "[outer] p01-2"]                 # its outlines alone, in cut order
    assert out["tab_points"]["[outer] p01-1"] and out["tab_points"]["[outer] p01-2"]   # held by tabs, as ever
    assert [p.kind for p in sheet.programs] == ["fit"] * 4 + ["outlines"]  # after every lap
    (vs,) = rig.service_view(job, result).sheets
    assert vs.cuttable and all(v.ok for v in vs.programs)


def test_a_sheet_of_only_hole_fit_parts_has_no_outline_in_its_own_program(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("retainer", 2, plate(holes=(0.5,)), (4.0, 4.0))], fits={"retainer": ("hole", 0.5)})
    result = rig.run(job)
    assert result.status == "ok", result.sheets[0].errors
    (sheet,) = result.sheets
    main = rig.fake.sheets[sheet.name]
    assert not [o for o in main["ops"] if o.startswith("[outer]")]          # the template's op, deleted untouched
    assert sheet.main_order == () and sheet.outlines_last == ("p01-1", "p01-2")
    assert rig.service_view(job, result).sheets[0].cuttable


def test_an_outline_fit_and_the_switch_off_leave_nothing_for_last(tmp_path):
    rig = Rig(tmp_path)
    job = rig.job([("slider", 1, plate(), (5.0, 3.0))], fits={"slider": ("outline", 0.0)})
    assert not job.parts[0].fit.outline_last                                # its laps are on the outline itself
    result = rig.run(job)
    assert result.sheets[0].outlines_last == () and "outlines" not in [p.kind for p in result.sheets[0].programs]
    rig = Rig(_sub(tmp_path, "off"))
    job = hole_job(rig)
    job = dataclasses.replace(job, parts=tuple(
        dataclasses.replace(p, fit=dataclasses.replace(p.fit, outline_last=False)) if p.fit else p for p in job.parts))
    result = rig.run(job)
    (sheet,) = result.sheets
    assert sheet.outlines_last == () and "[outer] p01-1" in rig.fake.sheets[sheet.name]["ops"]
    assert "outlines" not in [p.kind for p in sheet.programs] and rig.service_view(job, result).sheets[0].cuttable


def test_an_outlines_program_that_fails_its_check_stops_the_sheet(tmp_path):
    from autocam_core.names import outlines_program_name
    rig = Rig(tmp_path)
    job = hole_job(rig)
    rig.fake.below_floor.add(outlines_program_name("6061_0p125_r001_S1"))
    result = rig.run(job)
    (sheet,) = result.sheets
    assert sheet.tap is None and any("OUTLINES" in e.msg and "below" in e.msg for e in sheet.errors)


def test_the_service_wants_the_outlines_program_and_the_outlines_left_out(tmp_path):
    from autocam_core.schema_result import result_json
    rig = Rig(tmp_path)
    job = hole_job(rig)
    result = rig.run(job)
    s = result.sheets[0]
    for bad, said in (
            (dataclasses.replace(s, programs=s.programs[:-1]), "has no outlines program"),
            (dataclasses.replace(s, outlines_last=("p01-1",)), "aren't the hole-fit parts'"),
            (dataclasses.replace(s, outlines_last=()), "don't match the cut order")):
        sub = Rig(_sub(tmp_path, f"t{len(said)}"))
        sub.out.mkdir(parents=True)
        for f in rig.out.iterdir():
            if f.is_file():
                (sub.out / f.name).write_bytes(f.read_bytes())
        changed = dataclasses.replace(result, sheets=(bad,))
        (sub.out / "result.json").write_text(result_json(changed), encoding="utf-8")
        (vs,) = sub.service_view(job, changed).sheets
        problems = list(vs.problems) + [p for v in vs.programs for p in v.problems]
        assert not vs.cuttable and any(said in p for p in problems), (said, problems)


def test_the_rooms_a_sheet_leaves_count_the_outlines_cut_last(tmp_path):
    from autocam_worker.pipeline import _cut_text
    rig = Rig(tmp_path)
    job = hole_job(rig)
    result = rig.run(job)
    (sheet,) = result.sheets
    late = sheet.programs[-1]

    class S:                                       # what _cut_text reads of the worker's sheet
        outlines_result = late
    text = _cut_text(rig.out, S, sheet.tap)
    assert text.startswith((rig.out / sheet.tap).read_text()) and text.endswith((rig.out / late.tap).read_text())
    S.outlines_result = None
    assert _cut_text(rig.out, S, sheet.tap) == (rig.out / sheet.tap).read_text()
