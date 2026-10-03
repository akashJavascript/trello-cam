"""Tabs from the board: the part card's "Hold it in with tabs" box, into the job, onto the sheet card."""

import json
from dataclasses import replace
from pathlib import Path

from autocam_core.schema_job import read_job
from fakeworker import run_fake_worker
from test_air_test import AIR, programs, tick
from test_runner_autostart import harness, sheet, step_card
from test_runner_e2e import RAW

TABS = "Hold it in with tabs"


def test_with_tabs_off_by_default_part_cards_get_an_unticked_box_and_it_reaches_the_job(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate", qty=2), step_card("c2", "gusset"), tabs__default=False)
    h.runner.ready_cards()
    assert h.tracker.checklists[("c1", "Tabs")] == [[TABS, False]]
    h.tracker.tick_all("c2", "Tabs")
    h.runner.tick()
    job = read_job(next(h.queue.incoming.glob("r001-*.json")))
    assert {p.card_id: p.tabs for p in job.parts} == {"c1": False, "c2": True}
    assert job.tabs.distance_in == 2.5
    run_fake_worker(h.queue)
    h.runner.tick()
    desc = sheet(h).desc
    assert "Parts marked tabs stay held to the sheet by small tabs" in desc
    assert "3. gusset - tabs" in desc and "1. plate (1 of 2)\n" in desc


def test_tabs_are_on_by_default(tmp_path):
    h = harness(tmp_path, step_card("c1", "plate"))                   # config/autocam.toml: [tabs] default = true
    h.runner.tick()
    assert h.tracker.checklists[("c1", "Tabs")] == [[TABS, True]]
    assert [p.tabs for p in read_job(next(h.queue.incoming.glob("r001-*.json"))).parts] == [True]
    run_fake_worker(h.queue)
    h.runner.tick()
    desc = sheet(h).desc
    assert "Every part is held to the sheet by small tabs" in desc and "- tabs" not in desc   # said once


def test_a_tabbed_sheet_through_the_real_pipeline_with_an_air_test(tmp_path, monkeypatch):
    tests = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(tests / "fusion"))
    monkeypatch.syspath_prepend(str(tests / "core"))
    from fakeadapter import FakeAdapter
    from geombuilder import PlateBuilder
    from autocam_worker.worker import Worker
    import autocam_service.jobs as jobs
    monkeypatch.setattr(jobs, "fusion_path", lambda p: Path(p).resolve().as_posix())

    h = harness(tmp_path, step_card("c1", "plate", qty=2))
    for key, t in RAW["templates"].items():
        f = tmp_path / "templates" / f"{key}.f3dhsm-template"
        if f.exists():
            guid = RAW["tools"][t["tool"]]["guid"]
            f.write_text(json.dumps([[op, guid] for op in ("[bore] holes", "[inner] cutouts", "[outer] outline")]))
    h.runner.ready_cards()
    h.tracker.tick_all("c1", "Tabs")
    h.runner.tick()
    fake = FakeAdapter()
    for job_id in h.queue.pending():
        for part in read_job(h.queue.incoming / f"{job_id}.json").parts:
            b = PlateBuilder(part.name)
            b.hole(0.25)
            fake.register(Path(part.step), b.build(), (4.0, 3.0))
    worker = Worker(h.queue, lambda job: fake, max_attempts=2, fusion_version="fake")
    while worker.tick():
        pass
    h.runner.tick()
    s1 = sheet(h)
    [main] = programs(h, s1.id).values()
    assert main.count(b"G1 Z0.04") == 2                                # a tab on each copy's outline
    h.runner.tick()                                                    # the Options box appears
    tick(h, s1.id, AIR)
    h.runner.tick()
    air = programs(h, s1.id)["6061_0p125_r001_S1_AIRTEST.tap"].decode("ascii")
    assert air.count("Z0.665") == 2                                    # the tabs, raised with the rest
    assert "couldn't" not in " ".join(h.tracker.comments_on(s1.id))
