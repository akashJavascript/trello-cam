"""Air test programs: a sheet's real program raised so the cutter runs above the sheet and cuts nothing.

The operator runs it before the real program to watch the paths, the clamps and the pauses. It's made from
the exact bytes that passed the sheet check, by adding `lift` to every Z and R value. Lines with G53 are left
alone: `G53 Z` and the park are machine coordinates, already at the top. Every move, feed, pause, spindle and
mist command is the real one.

lift = sheet thickness + gap, so the program's lowest point (a through cut at Z0, the spoilboard) ends up
`gap` above the top of the stock. check_air_test runs the same guard and pause checks as the real program,
except "every outline reaches the stock bottom", and adds the opposite: nothing may come below
stock top + gap.
"""

import re
from dataclasses import replace
from typing import Mapping, Sequence

from .schema_job import Job
from .sheetcheck import SheetCheck, check_sheet_program, outer_depth_problems
from .tapguard import parse_code

SUFFIX = "_AIRTEST"
_WORD = re.compile(r"(?<![A-Z])([ZR])( *)([+-]?(?:\d+\.?\d*|\.\d+))")
_LINE = re.compile(r"([^\r\n]*)(\r\n|\n|$)")
IN_AIR_TOL_IN = 1e-6


def air_test_name(tap_name: str) -> str:
    """6061_0p1875_r005_S1.tap -> 6061_0p1875_r005_S1_AIRTEST.tap"""
    stem, dot, ext = tap_name.rpartition(".")
    return f"{stem}{SUFFIX}.{ext}" if dot else f"{tap_name}{SUFFIX}"


def _number(value: float) -> str:
    """The post's style: up to 4 decimals, a trailing dot for whole numbers (Z1.)."""
    text = f"{value:.4f}".rstrip("0")
    return "0." if text in ("-0.", "0.") else text


def _lift_code(code: str, lift_in: float) -> str:
    return _WORD.sub(lambda m: f"{m.group(1)}{m.group(2)}{_number(float(m.group(3)) + lift_in)}", code)


def _lift_line(line: str, lift_in: float) -> str:
    if not line.strip() or line.lstrip().startswith("[") and line.rstrip().endswith("]") and line.count("[") == 1:
        return line                                          # comment-only line
    try:
        if ("G", 53.0) in parse_code(line):
            return line                                      # machine coordinates: already at the top
    except ValueError:
        return line                                          # the guard rejects it either way
    parts = re.split(r"(\[[^\]]*\])", line)                  # leave anything inside [...] as it is
    return "".join(p if p.startswith("[") else _lift_code(p, lift_in) for p in parts)


def lift_program(text: str, lift_in: float, note: str = "") -> str:
    """Every Z and R value raised by lift_in, line endings kept. `note` goes in as a comment after line 1."""
    if lift_in <= 0:
        raise ValueError("an air test is raised, never lowered")
    out = []
    for i, m in enumerate(_LINE.finditer(text)):
        line, end = m.group(1), m.group(2)
        if not line and not end:
            break
        out.append(_lift_line(line, lift_in) + end)
        if i == 0 and note:
            out.append(f"[{note}]{end or chr(13) + chr(10)}")
    return "".join(out)


def note_for(lift_in: float, gap_in: float) -> str:
    return f"AIR TEST - RAISED {_number(lift_in)} IN - CUTTER STAYS {_number(gap_in)} IN ABOVE THE SHEET"


def check_air_test(data: bytes, job: Job, thickness_in: float, tool_key: str, outer_order: Sequence[str],
                   part_counts: Mapping[str, int], gap_in: float) -> SheetCheck:
    """The sheet check for an air test: the same guard and pauses, the outlines must NOT reach the stock bottom,
    and no Z (or drill R) comes below the stock top + gap."""
    check = check_sheet_program(data, job, thickness_in, tool_key, outer_order, part_counts)
    text = data.decode("ascii", errors="replace")
    expected = set(outer_depth_problems(text, job.guard.z_floor_in))
    problems = [p for p in check.plan_problems if p not in expected]
    lowest = check.guard.min_z_in
    floor = thickness_in + gap_in
    if lowest is None or lowest < floor - IN_AIR_TOL_IN:
        problems.append(f"an air test must stay at or above Z{floor:.4f} (stock top + {gap_in:g} in); "
                        f"its lowest Z is {'none' if lowest is None else f'{lowest:.4f}'}")
    return replace(check, plan_problems=tuple(problems))
