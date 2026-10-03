"""The System card's description: is everything alive, what's waiting, how much Onshape budget is left.

Pure text; the runner decides when to write it (runner.report_health). The Fusion add-in writes
queue/worker_heartbeat.json every 10 s while Fusion runs (M2); a heartbeat older than `stale_after_s` means
Fusion or the add-in isn't running. Plain text, local times, as short as it can be.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class Health:
    now: datetime
    heartbeat: Optional[Dict[str, Any]]      # {"ts": ISO, "job": id or None, "core_version": ...}
    service_core: str
    queue_incoming: int
    queue_processing: int
    month_calls: int
    month_soft: int
    year_calls: int
    year_cap: int
    latch: Optional[Dict[str, Any]]
    active_run: Optional[str]
    last_error: Optional[str]
    waiting_cards: int = 0
    next_run_in_s: Optional[float] = None
    rush_cards: int = 0                      # waiting cards with the Rush label
    season: str = ""                         # the stock tally line (tally.py)


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


def stuck(h: Health, stale_after_s: float) -> bool:
    """Work is waiting for a Fusion that isn't running."""
    return is_stale(h, stale_after_s) and bool(h.queue_incoming or h.queue_processing)


def local_time(dt: datetime) -> str:
    local = dt.astimezone()
    return f"{local:%I:%M %p}".lstrip("0") + f", {local:%b} {local.day}"


def _minutes(seconds: float) -> str:
    m = max(1, round(seconds / 60))
    return f"{m} min" if m < 120 else f"{m / 60:.0f} h"


def _fusion(h: Health, stale_after_s: float) -> str:
    age = heartbeat_age_s(h.now, h.heartbeat)
    if age is None:
        return "Fusion: no sign of the auto-CAM add-in yet. Start Fusion with the add-in on the shop PC."
    if age > stale_after_s:
        return (f"Fusion: NOT RUNNING (last seen {_minutes(age)} ago). Start Fusion with the auto-CAM add-in "
                "on the shop PC.")
    beat = h.heartbeat or {}
    line = f"Fusion: running job {beat['job']}." if beat.get("job") else "Fusion: running, idle."
    core = beat.get("core_version")
    if core and core != h.service_core:
        if _version(core) < _version(h.service_core):
            line += (f" The add-in runs older code ({core}; the service is {h.service_core}): in Fusion, Stop and "
                     "Run autocam_addin.")
        else:
            line += (f" The service runs older code ({h.service_core}; the add-in is {core}). It restarts into new "
                     "code by itself within a few minutes; if this stays, restart the service window.")
    return line


def _version(v: str) -> tuple:
    return tuple(int(n) if n.isdigit() else 0 for n in str(v).split("."))


def render(h: Health, stale_after_s: float, with_time: bool = True) -> str:
    """with_time=False: the same text without the timestamp (to tell whether anything changed)."""
    jobs = h.queue_incoming + h.queue_processing
    run = f"Run: {h.active_run}, {jobs} job{'s' if jobs != 1 else ''} in Fusion." if h.active_run else "Run: none."
    if h.waiting_cards:
        cards = f"Ready for CAM: {h.waiting_cards} card{'s' if h.waiting_cards != 1 else ''} waiting"
        if h.rush_cards:
            cards += (f" ({h.rush_cards} Rush, nested " + ("as soon as this run is done)." if h.active_run else
                                                         "within a minute)."))
        elif h.active_run:
            cards += ", the next run starts after this one."
        elif h.next_run_in_s is not None:
            cards += f", the next run starts in about {_minutes(h.next_run_in_s)}."
        else:
            cards += "."
    else:
        cards = "Ready for CAM: nothing new waiting."
    lines = [_fusion(h, stale_after_s), run, cards,
             f"Onshape calls: {h.month_calls} this month (warning at {h.month_soft}), "
             f"{h.year_calls} of {h.year_cap} this budget year."]
    if h.season:
        lines.append(h.season)
    if h.latch:
        lines.append("ONSHAPE CALLS ARE STOPPED: Onshape said the calls ran out (402). After checking with the "
                     "enterprise admin, run: autocam ledger reset-latch")
    if h.last_error:
        lines.append(f"Last error: {h.last_error}")
    if with_time:
        lines.insert(0, f"Status at {local_time(h.now)}. The service updates this while it runs.")
    return "\n".join(lines)
