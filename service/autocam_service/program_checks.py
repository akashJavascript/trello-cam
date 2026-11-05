"""The checks AutoCAM runs on its own programs, for programs someone CAM'd by hand (2026-11-04, the user: "the same
ones you run on the autocam to make sure they don't crash").

Every hand program AutoCAM takes goes through them: an own program it places on an offcut or a new sheet, one with
"Place it myself", and a card with the Check label, which is only checked (nothing placed, no sheet card):

- the guard AutoCAM's own sheets get (tapguard): nothing below the spoilboard, only codes WinCNC runs (no M0, no tool
  numbers), inches, no sideways rapid below the plate top, the spindle off at a stop and on for every cut, parked
  with the spindle off at the end; and where it's known where it runs, clear of the clamps, inside the machine's
  reach and on the sheet;
- the mist its material wants (on for aluminum, off for polycarbonate), like AutoCAM's sheets;
- its own stops held to what AutoCAM's stop blocks do (pauses.stop_problems): cutter up at the clamps' height and
  the mist off when it stops, and coming back down from there after ENTER.

What a hand program doesn't get: AutoCAM's sheets are also checked against the parts themselves in Fusion (the
cutter never inside a part). A hand program has no parts to check against, so it still wants simulating.
"""

import hashlib
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from autocam_core.fixture import clamp_clear_z
from autocam_core.pauses import stop_problems
from autocam_core.tapguard import GuardReport, GuardSpec, check_program

from .config import Config

Rect = Tuple[float, float, float, float]


@dataclass(frozen=True)
class HandCheck:
    report: GuardReport
    stops: Tuple[str, ...]          # the program's own stops that aren't safe

    @property
    def passed(self) -> bool:
        return self.report.passed and not self.stops

    def problems(self, limit: int = 6) -> Tuple[str, ...]:
        """One line per kind of problem, the first few places each."""
        r, out = self.report, []
        if r.offenders:
            out.append(f"goes below the spoilboard (lowest Z {r.min_z_in:.4f} in): " + "; ".join(r.offenders[:3]))
        if r.clamp_violations:
            out.append("too low over a clamp: " + "; ".join(r.clamp_violations[:3]))
        out += list(r.problems[:limit])
        if len(r.problems) > limit:
            out.append(f"and {len(r.problems) - limit} more like these")
        if not r.passed and not out:
            out.append(r.summary())
        return tuple(out) + self.stops

    def summary(self) -> str:
        return "; ".join(self.problems(limit=3))


def hand_spec(cfg: Config, key: str, thickness_in: float, cutter_in: float, *, zones: Sequence[Rect] = (),
              sheet_in: Optional[Tuple[float, float]] = None) -> GuardSpec:
    """The guard for a hand program of this material and thickness. zones and sheet_in only when it's known where it
    runs (placed by AutoCAM, or a Check card zeroed at a sheet's front-left corner); then the reach is checked too."""
    return GuardSpec(z_floor_in=cfg.machine.z_floor_in, allowed_g=frozenset(cfg.tapguard.allowed_g),
                     allowed_m=frozenset(cfg.tapguard.allowed_m), park=cfg.machine.park,
                     clamp_zones_in=tuple(zones),
                     clamp_clear_z_in=clamp_clear_z(thickness_in, cfg.clamps.height_in, cfg.tapguard.clamp_margin_in),
                     tool_radius_in=cutter_in / 2, reach_y_in=cfg.machine.reach_y_in if sheet_in else None,
                     mist=cfg.materials[key].use_mist, stock_top_in=thickness_in, sheet_in=sheet_in)


def check_hand(data: bytes, spec: GuardSpec) -> HandCheck:
    stops = stop_problems(data.decode("ascii", errors="replace"), spec.clamp_clear_z_in, bool(spec.mist))
    return HandCheck(check_program(data, spec), tuple(stops))


def signature(attachment_id: str, desc: str, labels: Sequence[str]) -> str:
    """What a Check card's result depends on: in Drafts, a new file or changed lines or labels check it again."""
    text = "\n".join([attachment_id, desc or "", ",".join(sorted(labels))])
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _num(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") if v else "0"


def check_comment(program: str, check: HandCheck, *, stock: str, material: str, mist: bool, cutter_in: float,
                  on_sheet: bool, clamps: str, again: str) -> str:
    """The Check card's comment: passes or fails, and what it was checked as."""
    where = (f"X/Y zeroed at a full sheet's front-left corner (clamps: {clamps})" if on_sheet else
             "zeroed wherever you put it (Place it myself), so the clamps, the reach and the sheet's edges aren't "
             "checked: keep clear of them yourself")
    how = f"Checked as {stock} with a {_num(cutter_in)} in cutter, Z0 on the spoilboard, {where}."
    if not check.passed:
        lines = [f"Checked {program}: it fails AutoCAM's checks.", how, ""]
        lines += [f"- {p}" for p in check.problems()]
        r = check.report
        if on_sheet and (r.clamp_violations or any(w in p for p in r.problems for w in ("off the sheet", "reach"))):
            lines += ["", "Not zeroed at a sheet's front-left corner? Add a line Place it myself: then the clamps, the "
                          "reach and the sheet's edges are yours to keep clear of."]
        if any("mist" in p.lower() for p in check.problems()):
            lines += ["", f"Mist: {material} wants it {'on' if mist else 'off'}, so "
                          f"{'tick' if mist else 'untick'} Use mist coolant in the post dialog when you post it."]
        lines += ["", f"In Fusion: fix it, post it again and attach the new file. {again}"]
        return "\n".join(lines)
    r = check.report
    lines = [f"Checked {program}: it passes AutoCAM's checks.", how, "",
             f"- Lowest point Z{_num(r.min_z_in or 0.0)}: never below the spoilboard.",
             "- Only codes the machine runs, in inches; no sideways rapid below the plate top; parked with the spindle "
             "off at the end."]
    if on_sheet:
        lines.append("- Clear of the clamps, inside the machine's reach and on the sheet.")
    if r.stop_count:
        lines.append(f"- {r.stop_count} stops, each with the cutter up and the spindle off." if r.stop_count > 1 else
                     "- 1 stop, with the cutter up and the spindle off.")
    lines.append(f"- Mist {'on' if mist else 'off'}, as {material} needs.")
    lines += ["", "Still simulate it in Fusion: these checks can't see your parts, so they can't tell if it cuts "
                  "into one."]
    return "\n".join(lines)
