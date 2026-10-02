"""The add-in's job loop (M2), in plain Python: tested offline; autocam_addin.py only wires it to Fusion.

- tick() runs on Fusion's main thread (the add-in's custom event). It runs at most one job, never two at
  once: Fusion re-enters event handlers while a job waits on toolpaths (adsk.doEvents), so `busy` guards it.
- The first tick puts interrupted jobs back in the queue, or fails the ones that were already started
  max_attempts times (they probably crash Fusion).
- A job that can't run (JobFailed) or hits a worker bug goes to failed/ with the reason. A Fusion crash
  leaves it in processing/ for the next start to pick up.
- heartbeat() is file IO only, so the add-in's background thread calls it too (queue/worker_heartbeat.json;
  the service's System card reads it).
"""

import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from autocam_core import CORE_VERSION
from autocam_core.hotfolder import Claim, Queue, QueueError
from autocam_core.schema_job import Job, read_job

from .adapter import Adapter
from .pipeline import JobFailed, job_running, process_job


def utc_stamp(t: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


@dataclass(frozen=True)
class Settings:
    queue: Path
    logs: Path
    max_attempts: int


def load_settings(repo: Path) -> Settings:
    """The three things the worker needs from config/autocam.toml (jobs carry everything else)."""
    try:
        import tomllib
    except ImportError:  # Python < 3.11 (tests only; Fusion bundles 3.14)
        import tomli as tomllib
    data = tomllib.loads((Path(repo) / "config" / "autocam.toml").read_text(encoding="utf-8"))
    paths = data.get("paths", {})
    return Settings(queue=Path(repo) / paths.get("queue", "queue"), logs=Path(repo) / paths.get("logs", "logs"),
                    max_attempts=int(data.get("fusion", {}).get("max_attempts", 2)))


class Worker:
    def __init__(self, queue: Queue, make_adapter: Callable[[Job], Adapter], *, max_attempts: int,
                 fusion_version: str, log: Callable[[str], None] = lambda msg: None,
                 clock: Callable[[], float] = time.time):
        self.queue = queue
        self.make_adapter = make_adapter
        self.max_attempts = max_attempts
        self.fusion_version = fusion_version
        self.log = log
        self.clock = clock
        self.busy = False
        self.recovered = False
        self.current: Optional[str] = None
        self.started: Optional[float] = None
        self.last_error: Optional[str] = None
        self._beat = threading.Lock()

    def wants_tick(self) -> bool:
        """Cheap enough for the background thread: is there anything for the main thread to do?"""
        return not self.busy and not job_running() and (not self.recovered or bool(self.queue.pending()))

    def heartbeat(self) -> None:
        info = {"ts": utc_stamp(self.clock()), "job": self.current,
                "job_started": utc_stamp(self.started) if self.started else None,
                "fusion_version": self.fusion_version, "core_version": CORE_VERSION,
                "python": sys.version.split()[0], "pid": os.getpid(), "last_error": self.last_error}
        with self._beat:
            self.queue.write_heartbeat(info)

    def tick(self) -> Optional[str]:
        """Run the oldest waiting job, if any. Returns its id."""
        if self.busy or job_running():
            return None
        self.busy = True
        try:
            if not self.recovered:
                requeued, failed = self.queue.recover(self.max_attempts)
                self.recovered = True
                if requeued or failed:
                    self.log(f"start-up: re-queued {requeued or 'nothing'}, failed {failed or 'nothing'}")
            for job_id in self.queue.pending():
                claim = self.queue.claim(job_id)
                if claim is None:
                    continue
                self.current, self.started = job_id, self.clock()
                self.heartbeat()
                self._run(claim)
                return job_id
            return None
        finally:
            self.busy = False
            self.current = self.started = None
            try:
                self.heartbeat()
            except OSError as e:
                self.log(f"heartbeat failed: {e}")

    def _fail(self, job_id: str, error: str) -> None:
        self.last_error = f"{job_id}: {error.splitlines()[0]}"
        self.log(f"{job_id} failed: {error}")
        self.queue.fail(job_id, error)

    def _run(self, claim: Claim) -> None:
        self.log(f"{claim.job_id}: attempt {claim.attempt}")
        try:
            job = read_job(claim.job_path)
        except Exception as e:  # noqa: BLE001 - unreadable job: nothing to retry
            self._fail(claim.job_id, f"job.json can't be read: {type(e).__name__}: {e}")
            return
        if job.job_id != claim.job_id:
            self._fail(claim.job_id, f"job.json says it is {job.job_id}")
            return
        try:
            result = process_job(job, self.make_adapter(job), claim.out_dir, attempt=claim.attempt, keep_open=False)
        except JobFailed as e:
            self._fail(claim.job_id, str(e))
            return
        except Exception:  # noqa: BLE001 - a worker bug; the traceback is the useful part
            self._fail(claim.job_id, "worker error:\n" + traceback.format_exc())
            return
        try:
            self.queue.complete(claim)
        except (QueueError, OSError) as e:
            self._fail(claim.job_id, f"the job ran but its outputs couldn't be published: {e}")
            return
        self.log(f"{claim.job_id}: {result.status}, {len(result.sheets)} sheet(s)")
