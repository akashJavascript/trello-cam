"""The pinned `System` card (M5): is everything alive, and how much Onshape budget is left.

Pure text; the runner decides when to write it. The Fusion worker writes queue/worker_heartbeat.json
every poll (M2); a heartbeat older than `stale_after_s` means Fusion or the add-in isn't running.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class Health:
    now: datetime
    heartbeat: Optional[Dict[str, Any]]      # {"ts": ISO, "job": id or None, "fusion_version": ...}
    queue_incoming: int
    queue_processing: int
    month_calls: int
    month_soft: int
    year_calls: int
    year_cap: int
    latch: Optional[Dict[str, Any]]
    active_run: Optional[str]
    last_error: Optional[str]


def heartbeat_age_s(now: datetime, heartbeat: Optional[Dict[str, Any]]) -> Optional[float]:
    if not heartbeat or "ts" not in heartbeat:
        return None
    try:
        ts = datetime.strptime(heartbeat["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return (now - ts).total_seconds()


def is_stale(h: Health, stale_after_s: float) -> bool:
    age = heartbeat_age_s(h.now, h.heartbeat)
    return age is None or age > stale_after_s


def render(h: Health, stale_after_s: float) -> str:
    age = heartbeat_age_s(h.now, h.heartbeat)
    if age is None:
        worker = "**Fusion worker: no heartbeat yet.** Start Fusion with the auto-CAM add-in on the shop PC."
    elif age > stale_after_s:
        worker = (f"**Fusion worker: last seen {age / 60:.0f} min ago.** Check that Fusion and the add-in are "
                  "running on the shop PC.")
    else:
        job = (h.heartbeat or {}).get("job")
        worker = f"Fusion worker: alive ({'running ' + job if job else 'idle'})."
    lines = [
        f"Updated {h.now.strftime('%Y-%m-%d %H:%M')} UTC",
        worker,
        f"Hot folder: {h.queue_incoming} waiting, {h.queue_processing} running.",
        f"Active run: {h.active_run or 'none'}.",
        f"Onshape calls: {h.month_calls} this month (soft limit {h.month_soft}), "
        f"{h.year_calls} of {h.year_cap} this budget year.",
    ]
    if h.latch:
        lines.append(f"**Onshape calls are STOPPED** (402 at {h.latch.get('ts', '?')}). After checking with the "
                     "enterprise admin, reset with `python -m autocam_service ledger reset-latch`.")
    if h.last_error:
        lines.append(f"Last error: {h.last_error}")
    return "\n".join(lines)
