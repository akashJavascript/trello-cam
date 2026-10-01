"""Decide whether a run may spend Onshape calls, before it spends any."""

from dataclasses import dataclass

PROCEED, WARN, REFUSE = "proceed", "warn", "refuse"


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str = ""


def estimate_calls(parts_to_export: int, studios_to_list: int, calls_per_part: int) -> int:
    """Uncached parts x (translation + polls + download), plus one parts-list call per uncached Part Studio version."""
    return parts_to_export * calls_per_part + studios_to_list


def decide(estimate: int, *, month_used: int, year_used: int, latched: bool, per_run_max: int,
           monthly_soft: int, yearly_cap: int) -> Decision:
    if latched:
        return Decision(REFUSE, "Onshape returned 402 (out of API calls); calls are stopped until a mentor "
                                "resets the latch with `python -m autocam_service ledger reset-latch`")
    if estimate > per_run_max:
        return Decision(REFUSE, f"this run needs about {estimate} Onshape calls; the per-run limit is {per_run_max}. "
                                "Run with fewer cards in Ready for CAM")
    if year_used + estimate > yearly_cap:
        return Decision(REFUSE, f"this run needs about {estimate} Onshape calls and {year_used} of this year's "
                                f"{yearly_cap} are used")
    if month_used + estimate > monthly_soft:
        return Decision(WARN, f"Onshape calls this month will be about {month_used + estimate} "
                              f"(soft limit {monthly_soft})")
    return Decision(PROCEED)
