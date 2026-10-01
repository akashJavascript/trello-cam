"""Run state: what a run queued and which Trello writes have already landed (idempotent resume).

Trello writes aren't idempotent (a retried attachment upload makes a second attachment), so
every write is recorded under a step key right after it succeeds, and skipped if it's already
recorded. A crash between the write and the record can repeat at most that one write.

    state/runs/r017.json          the run
    state/jobs/r017-al6061.json   the service's own copy of each job it submitted
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from autocam_core.hotfolder import write_atomic

QUEUED, PUBLISHED, FAILED = "queued", "published", "failed"


@dataclass
class JobState:
    material: str
    parts: Dict[str, str]                 # part_key -> card_id
    status: str = QUEUED
    queued_utc: str = ""
    timeout_reported: bool = False


@dataclass
class RunState:
    run_id: str
    started_utc: str
    trigger_card: str
    dry_run: bool = False
    done: bool = False
    jobs: Dict[str, JobState] = field(default_factory=dict)
    cards: Dict[str, Dict[str, str]] = field(default_factory=dict)   # card_id -> {"name", "url"}
    writes: Dict[str, str] = field(default_factory=dict)             # step key -> result (card/attachment id)
    notes: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "RunState":
        data = json.loads(text)
        data["jobs"] = {k: JobState(**v) for k, v in data.get("jobs", {}).items()}
        return cls(**data)


class RunStore:
    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir) / "runs"
        self.jobs_dir = Path(state_dir) / "jobs"

    def _path(self, run_id: str) -> Path:
        return self.dir / f"{run_id}.json"

    def run_ids(self) -> List[str]:
        if not self.dir.exists():
            return []
        return sorted(p.stem for p in self.dir.glob("r*.json"))

    def next_run_id(self) -> str:
        numbers = [int(r[1:]) for r in self.run_ids() if r[1:].isdigit()]
        return f"r{(max(numbers) + 1) if numbers else 1:03d}"

    def save(self, state: RunState) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        write_atomic(self._path(state.run_id), state.to_json().encode("utf-8"))

    def load(self, run_id: str) -> RunState:
        return RunState.from_json(self._path(run_id).read_text(encoding="utf-8"))

    def save_job(self, job_id: str, job_text: str) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(self.jobs_dir / f"{job_id}.json", job_text.encode("utf-8"))

    def job_text(self, job_id: str) -> Optional[str]:
        path = self.jobs_dir / f"{job_id}.json"
        return path.read_text(encoding="utf-8") if path.exists() else None

    def active(self) -> Optional[RunState]:
        for run_id in reversed(self.run_ids()):
            state = self.load(run_id)
            if not state.done:
                return state
        return None


def once(store: RunStore, state: RunState, key: str, action) -> str:
    """Do a Trello write at most once per run: record its result under `key` right after it succeeds."""
    if key in state.writes:
        return state.writes[key]
    result = action()
    state.writes[key] = "" if result is None else str(result)
    store.save(state)
    return state.writes[key]
