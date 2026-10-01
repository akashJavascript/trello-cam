"""Run state: what a run queued and which Trello writes have already landed (idempotent resume).

Trello writes aren't idempotent (a retried attachment upload makes a second attachment), so
every write is recorded under a step key right after it succeeds, and skipped if it's already
recorded. A crash between the write and the record can repeat at most that one write.

    state/runs/r017.json          the run
    state/jobs/r017-al6061.json   the service's own copy of each job it submitted
    state/sheet_cards.json        every sheet card the service made, and whether its program is cuttable

A run is saved before anything else happens (phase "starting"), so a crash while starting resumes
the same run instead of starting a new one and paying for its Onshape exports again.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from autocam_core.hotfolder import write_atomic

QUEUED, PUBLISHED, FAILED = "queued", "published", "failed"
STARTING, COLLECTING = "starting", "collecting"


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
    phase: str = STARTING
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
    """prefix: "r" for real runs; dry runs use their own folder and "d" so ids never collide."""

    def __init__(self, state_dir: Path, prefix: str = "r"):
        self.dir = Path(state_dir) / "runs"
        self.jobs_dir = Path(state_dir) / "jobs"
        self.sheets_file = Path(state_dir) / "sheet_cards.json"
        self.prefix = prefix

    def _path(self, run_id: str) -> Path:
        return self.dir / f"{run_id}.json"

    def run_ids(self) -> List[str]:
        if not self.dir.exists():
            return []
        return sorted(p.stem for p in self.dir.glob(f"{self.prefix}*.json") if p.stem[1:].isdigit())

    def next_run_id(self) -> str:
        numbers = [int(r[1:]) for r in self.run_ids()]
        return f"{self.prefix}{(max(numbers) + 1) if numbers else 1:03d}"

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

    def register_sheet(self, card_id: str, run_id: str, cuttable: bool) -> None:
        sheets = self.sheet_cards()
        sheets[card_id] = {"run": run_id, "cuttable": cuttable}
        self.sheets_file.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.sheets_file, (json.dumps(sheets, indent=1) + "\n").encode("utf-8"))

    def sheet_cards(self) -> Dict[str, Dict]:
        try:
            return json.loads(self.sheets_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def active(self) -> Optional[RunState]:
        for run_id in reversed(self.run_ids()):
            state = self.load(run_id)
            if not state.done:
                return state
        return None


GAVE_UP = "gave-up"


def once(store: RunStore, state: RunState, key: str, action, max_attempts: Optional[int] = None) -> str:
    """Do a Trello write at most once per run: record its result under `key` right after it succeeds.

    With max_attempts, a write that keeps failing (e.g. the card was deleted) is given up after that
    many ticks and recorded as GAVE_UP, so one broken card can't keep a run active forever.
    """
    if key in state.writes:
        return state.writes[key]
    try:
        result = action()
    except Exception:
        if max_attempts is None:
            raise
        tries = int(state.writes.get(f"{key}#attempts", "0")) + 1
        state.writes[f"{key}#attempts"] = str(tries)
        if tries >= max_attempts:
            state.writes[key] = GAVE_UP
            state.notes.append(f"gave up on {key} after {tries} attempts")
            store.save(state)
            return GAVE_UP
        store.save(state)
        raise
    state.writes[key] = "" if result is None else str(result)
    store.save(state)
    return state.writes[key]
