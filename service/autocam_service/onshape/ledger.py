"""The Onshape call ledger: every request the service makes, persisted before it is sent.

The enterprise shares 10,000 calls a year; only successful (2xx/3xx) calls count. Each request
writes a `pending` line before it goes out and a `done` line after. A pending line with no done
line (the service died mid-request) is counted as spent, so the count is never too low.
A 402 from Onshape writes a latch file; every later call is refused until a human resets it.

File format: one JSON object per line (append-only, fsync'd), in state/onshape_ledger.jsonl.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def month_start(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def budget_year_start(now: datetime, month_day: str) -> datetime:
    """Start of the current budget year, given its first day as 'MM-DD' (UTC)."""
    month, day = (int(x) for x in month_day.split("-"))
    now = now.astimezone(timezone.utc)
    start = now.replace(month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
    return start if start <= now else start.replace(year=start.year - 1)


class Ledger:
    def __init__(self, path: Path, clock: Callable[[], datetime] = utc_now):
        self.path = Path(path)
        self.latch_path = self.path.with_name("onshape_latch.json")
        self.clock = clock

    def _append(self, entry: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def begin(self, *, run_id: str, method: str, url: str, purpose: str, part_key: Optional[str] = None) -> str:
        call_id = uuid.uuid4().hex[:12]
        self._append({"id": call_id, "phase": "pending", "ts": _iso(self.clock()), "run_id": run_id,
                      "method": method, "url": url, "purpose": purpose, "part_key": part_key})
        return call_id

    def finish(self, call_id: str, status: int, ms: int, billable: bool) -> None:
        self._append({"id": call_id, "phase": "done", "ts": _iso(self.clock()), "status": status,
                      "billable": billable, "ms": ms})

    def blocked(self, *, run_id: str, method: str, url: str, purpose: str, reason: str) -> None:
        self._append({"id": uuid.uuid4().hex[:12], "phase": "blocked", "ts": _iso(self.clock()), "run_id": run_id,
                      "method": method, "url": url, "purpose": purpose, "reason": reason})

    def entries(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue  # a torn last line from a crash; its pending twin still counts
        return out

    def billable_since(self, since: datetime) -> int:
        started: Dict[str, datetime] = {}
        billable: Dict[str, bool] = {}
        for e in self.entries():
            if e.get("phase") == "pending":
                started[e["id"]] = _parse(e["ts"])
            elif e.get("phase") == "done":
                billable[e["id"]] = bool(e.get("billable"))
        return sum(1 for cid, ts in started.items() if ts >= since and billable.get(cid, True))

    def month_count(self) -> int:
        return self.billable_since(month_start(self.clock()))

    def year_count(self, year_start_mm_dd: str) -> int:
        return self.billable_since(budget_year_start(self.clock(), year_start_mm_dd))

    # ---- 402 latch
    def latched(self) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(self.latch_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None if not self.latch_path.exists() else {"reason": "unreadable latch file"}

    def latch(self, reason: str) -> None:
        self.latch_path.parent.mkdir(parents=True, exist_ok=True)
        self.latch_path.write_text(json.dumps({"ts": _iso(self.clock()), "reason": reason}) + "\n", encoding="utf-8")

    def reset_latch(self) -> bool:
        if self.latch_path.exists():
            self.latch_path.unlink()
            return True
        return False
