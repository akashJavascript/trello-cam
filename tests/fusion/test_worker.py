"""The add-in's job loop (autocam_worker/worker.py) against the real hot folder and a fake Fusion."""

import json
import sys
from dataclasses import replace
from datetime import datetime, timezone

from autocam_core.hotfolder import Queue
from autocam_core.schema_job import job_json
from autocam_core.schema_result import read_result
from autocam_service.health import heartbeat_age_s
from autocam_worker import pipeline
from autocam_worker.worker import Worker, load_settings
from autocam_service.config import REPO_ROOT
from rig import Rig, plate


def setup(tmp_path, **job_changes):
    rig = Rig(tmp_path)
    job = replace(rig.job([("gusset", 2, plate(holes=(0.25,)), (4.0, 4.0))]), **job_changes)
    queue = Queue(tmp_path / "queue").ensure()
    log = []
    worker = Worker(queue, lambda j: rig.fake, max_attempts=2, fusion_version="2705.1.15", log=log.append,
                    clock=lambda: 1_790_000_000.0)
    return rig, job, queue, worker, log


def test_runs_a_queued_job_into_done(tmp_path):
    rig, job, queue, worker, log = setup(tmp_path)
    queue.submit(job.job_id, job_json(job))
    assert worker.wants_tick()
    assert worker.tick() == job.job_id
    result = read_result(queue.done / job.job_id / "result.json")
    assert result.status == "ok" and result.worker.attempt == 1
    assert (queue.done / job.job_id / result.sheets[0].tap).is_file()
    assert queue.pending() == [] and list(queue.processing.iterdir()) == []
    assert rig.fake.finished is False          # the document is closed after every job
    assert worker.tick() is None and not worker.wants_tick()


def test_heartbeat_is_what_the_service_reads(tmp_path):
    _, job, queue, worker, _ = setup(tmp_path)
    worker.heartbeat()
    beat = queue.read_heartbeat()
    assert beat["job"] is None and beat["fusion_version"] == "2705.1.15"
    now = datetime.fromtimestamp(1_790_000_030, tz=timezone.utc)
    assert heartbeat_age_s(now, beat) == 30


def test_job_that_cant_run_goes_to_failed_with_the_reason(tmp_path):
    _, job, queue, worker, _ = setup(tmp_path, core_version="0.0.1")
    queue.submit(job.job_id, job_json(job))
    worker.tick()
    error = (queue.failed / job.job_id / "error.txt").read_text()
    assert "CORE_VERSION_MISMATCH" in error
    assert worker.last_error.startswith(job.job_id) and queue.read_heartbeat()["last_error"] == worker.last_error


def test_worker_bug_fails_the_job_with_the_traceback(tmp_path):
    rig, job, queue, worker, _ = setup(tmp_path)

    def boom(job_id):
        raise KeyError("not an AdapterError")
    rig.fake.begin = boom
    queue.submit(job.job_id, job_json(job))
    worker.tick()
    error = (queue.failed / job.job_id / "error.txt").read_text()
    assert error.startswith("worker error:") and "KeyError" in error


def test_unreadable_job_fails(tmp_path):
    _, job, queue, worker, _ = setup(tmp_path)
    data = json.loads(job_json(job))
    data["material"]["family"] = 7
    queue.submit(job.job_id, json.dumps(data))
    worker.tick()
    assert "can't be read" in (queue.failed / job.job_id / "error.txt").read_text()


def test_start_up_requeues_an_interrupted_job_and_fails_one_that_keeps_crashing(tmp_path):
    rig, job, queue, worker, log = setup(tmp_path)
    crashing = replace(job, job_id="r001-crashes")
    queue.submit(job.job_id, job_json(job))
    queue.submit(crashing.job_id, job_json(crashing))
    queue.claim(job.job_id)                    # Fusion died during attempt 1
    queue.claim(crashing.job_id)
    queue.recover(max_attempts=5)
    queue.claim(crashing.job_id)               # ... and during attempt 2
    worker.tick()
    assert (queue.done / job.job_id / "result.json").is_file()
    assert read_result(queue.done / job.job_id / "result.json").worker.attempt == 2
    assert "started this job 2 times" in (queue.failed / crashing.job_id / "error.txt").read_text()
    assert any("re-queued" in line for line in log)


def test_never_two_jobs_at_once(tmp_path):
    rig, job, queue, worker, _ = setup(tmp_path)
    queue.submit(job.job_id, job_json(job))
    worker.busy = True                          # Fusion re-entering the handler mid-job
    assert worker.tick() is None and not worker.wants_tick()
    worker.busy = False
    setattr(sys, pipeline._RUNNING, "t1-manual")   # a manual autocam_run job is running
    try:
        assert worker.tick() is None and queue.pending() == [job.job_id]
    finally:
        setattr(sys, pipeline._RUNNING, None)


def test_a_job_started_during_another_is_refused(tmp_path):
    rig, job, _, _, _ = setup(tmp_path)
    nested = []

    def begin(job_id):
        try:
            pipeline.process_job(job, rig.fake, tmp_path / "nested")
        except pipeline.JobFailed as e:
            nested.append(str(e))
    rig.fake.begin = begin
    rig.run(job)
    assert "already running" in nested[0]
    assert pipeline.job_running() is None


def test_adapter_error_in_one_sheet_is_not_a_worker_failure(tmp_path):
    rig, job, queue, worker, _ = setup(tmp_path)
    rig.fake.fail_post.add("6061_0p125_r001_S1")
    queue.submit(job.job_id, job_json(job))
    worker.tick()
    result = read_result(queue.done / job.job_id / "result.json")
    assert result.sheets[0].tap is None and result.sheets[0].errors[0].code == "POST_FAILED"


def test_settings_come_from_the_repo_config():
    s = load_settings(REPO_ROOT)
    assert s.queue == REPO_ROOT / "queue" and s.logs == REPO_ROOT / "logs" and s.max_attempts == 2
