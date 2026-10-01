"""The hot folder between the service and the Fusion worker (brief, "Hot folder contract").

    queue/
      incoming/<job_id>.json         the service writes it (temp file + atomic rename)
      processing/<job_id>.json       the worker moves it here while running it
      processing/<job_id>.attempts   how many times the worker has started it
      processing/<job_id>.out/       a fresh, empty output folder for the current attempt
      done/<job_id>/                 result.json + outputs; appears in one atomic rename
      failed/<job_id>/               job.json + error.txt (+ partial/ outputs)
      worker_heartbeat.json

Jobs are files, so nothing queued is lost when Fusion or the service restarts. On worker
startup, anything left in processing/ is re-queued, unless it has already been started
max_attempts times (it probably crashes Fusion), in which case it goes to failed/.
"""

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .names import is_safe_name

RESULT = "result.json"
HEARTBEAT = "worker_heartbeat.json"


class QueueError(Exception):
    pass


@dataclass(frozen=True)
class Claim:
    job_id: str
    job_path: Path      # processing/<job_id>.json
    attempt: int        # 1 on the first try
    out_dir: Path       # empty folder for this attempt's outputs


def _retry(fn, *args):
    # Windows: antivirus or the indexer can briefly hold a file that was just written.
    for i in range(5):
        try:
            return fn(*args)
        except PermissionError:
            if i == 4:
                raise
            time.sleep(0.2 * (i + 1))


def write_atomic(path: Path, data: bytes) -> None:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    _retry(os.replace, tmp, path)


def _check_id(job_id: str) -> str:
    if not is_safe_name(job_id):
        raise QueueError(f"job id {job_id!r} must be letters, digits, '-' or '_'")
    return job_id


class Queue:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.incoming = self.root / "incoming"
        self.processing = self.root / "processing"
        self.done = self.root / "done"
        self.failed = self.root / "failed"

    def ensure(self) -> "Queue":
        for d in (self.incoming, self.processing, self.done, self.failed):
            d.mkdir(parents=True, exist_ok=True)
        return self

    # ---- service side
    def submit(self, job_id: str, job_text: str) -> Path:
        _check_id(job_id)
        for d in (self.incoming, self.processing, self.done, self.failed):
            if (d / f"{job_id}.json").exists() or (d / job_id).exists():
                raise QueueError(f"job {job_id} already exists in {d.name}/")
        path = self.incoming / f"{job_id}.json"
        write_atomic(path, job_text.encode("utf-8"))
        return path

    def finished(self) -> List[str]:
        """Jobs with a result (done/) or an error (failed/), oldest first."""
        out = [d for d in self.done.iterdir() if d.is_dir() and (d / RESULT).is_file()]
        out += [d for d in self.failed.iterdir() if d.is_dir() and (d / "error.txt").is_file()]
        return [d.name for d in sorted(out, key=lambda d: (d.stat().st_mtime, d.name))]

    def read_heartbeat(self) -> Optional[Dict[str, Any]]:
        try:
            return json.loads((self.root / HEARTBEAT).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # ---- worker side
    def pending(self) -> List[str]:
        jobs = [p for p in self.incoming.glob("*.json") if not p.name.startswith(".")]
        return [p.stem for p in sorted(jobs, key=lambda p: (p.stat().st_mtime, p.name))]

    def attempts(self, job_id: str) -> int:
        try:
            return int((self.processing / f"{_check_id(job_id)}.attempts").read_text().strip())
        except (OSError, ValueError):
            return 0

    def claim(self, job_id: str) -> Optional[Claim]:
        """Move a job to processing/ and give it a fresh output folder. None if it's gone."""
        _check_id(job_id)
        src = self.incoming / f"{job_id}.json"
        dst = self.processing / f"{job_id}.json"
        try:
            _retry(os.replace, src, dst)
        except FileNotFoundError:
            return None
        attempt = self.attempts(job_id) + 1
        write_atomic(self.processing / f"{job_id}.attempts", str(attempt).encode())
        out = self.processing / f"{job_id}.out"
        if out.exists():
            shutil.rmtree(out)
        out.mkdir()
        return Claim(job_id, dst, attempt, out)

    def complete(self, claim: Claim) -> Path:
        """Publish the outputs: the whole folder appears in done/ at once, result.json already inside."""
        if not (claim.out_dir / RESULT).is_file():
            raise QueueError(f"{claim.job_id}: no {RESULT} in {claim.out_dir}")
        target = self.done / claim.job_id
        if target.exists():
            raise QueueError(f"{claim.job_id}: done/{claim.job_id} already exists")
        _retry(os.replace, claim.out_dir, target)
        self._forget(claim.job_id)
        return target

    def fail(self, job_id: str, error: str) -> Path:
        _check_id(job_id)
        target = self.failed / job_id
        n = 2
        while target.exists():
            target = self.failed / f"{job_id}-{n}"
            n += 1
        target.mkdir(parents=True)
        for src in (self.processing / f"{job_id}.json", self.incoming / f"{job_id}.json"):
            if src.exists():
                _retry(os.replace, src, target / "job.json")
                break
        out = self.processing / f"{job_id}.out"
        if out.exists() and any(out.iterdir()):
            _retry(os.replace, out, target / "partial")
        write_atomic(target / "error.txt", (error.rstrip() + "\n").encode("utf-8"))
        self._forget(job_id)
        return target

    def recover(self, max_attempts: int) -> Tuple[List[str], List[str]]:
        """Worker startup: re-queue interrupted jobs; fail the ones that keep getting interrupted."""
        requeued, failed = [], []
        for path in sorted(self.processing.glob("*.json")):
            job_id = path.stem
            n = self.attempts(job_id)
            if n >= max_attempts:
                self.fail(job_id, f"the worker started this job {n} times and never finished it "
                                  "(Fusion crashed or was closed mid-job)")
                failed.append(job_id)
            else:
                out = self.processing / f"{job_id}.out"
                if out.exists():
                    shutil.rmtree(out)
                _retry(os.replace, path, self.incoming / path.name)
                requeued.append(job_id)
        return requeued, failed

    def write_heartbeat(self, info: Dict[str, Any]) -> None:
        write_atomic(self.root / HEARTBEAT, (json.dumps(info, indent=2) + "\n").encode("utf-8"))

    def _forget(self, job_id: str) -> None:
        for p in (self.processing / f"{job_id}.json", self.processing / f"{job_id}.attempts"):
            if p.exists():
                p.unlink()
        out = self.processing / f"{job_id}.out"
        if out.exists():
            shutil.rmtree(out)
