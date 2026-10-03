"""Air test programs: a sheet's real program, outlines only, raised so the cutter runs above the sheet.

The operator runs it before the real program to watch each part's outline against the clamps, and the
pauses. It's made from the exact bytes that passed the sheet check (air_test_program):
1. outlines_only: only the `[outer <part>]` ops keep their moves. The other ops (holes, cutouts, pockets) lose
   their moves and comments but keep their spindle, mist and mode lines, so the spindle still starts. The
   outlines are always the last ops. A check makes sure every kept move is the same move it was: the same
   motion mode and, for anything but a rapid, the same start point.
2. one_lap: each outline is traced once, the lap at its final depth, entered straight down. The other laps
   and the ramps between them go. Every lap move kept starts where it did, in the same mode.
   set_feed: every feed move runs at one speed (machine.air_test_feed_ipm); rapids stay rapids.
3. lift_program: `lift` is added to every Z and R value. Lines with G53 are left alone: `G53 Z` and the park
   are machine coordinates, already at the top. The stops between parts are kept exactly.

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
_FEED = re.compile(r"(?<![A-Z])F *[+-]?(?:\d+\.?\d*|\.\d+)")
OP_TAGS = ("drill", "bore", "bearing", "pocket", "inner", "outer")
_MOTION_G = (0.0, 1.0, 2.0, 3.0, 73.0, 81.0, 82.0, 83.0)
_FEED_G = (1.0, 2.0, 3.0, 73.0, 81.0, 82.0, 83.0)


class AirTestError(ValueError):
    pass
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


def note_for(lift_in: float, gap_in: float, feed_ipm: float = 0) -> str:
    feed = f" - OUTLINES ONLY, ONE LAP EACH, AT {_number(feed_ipm).rstrip('.')} IPM" if feed_ipm else ""
    return f"AIR TEST - RAISED {_number(lift_in)} IN - CUTTER STAYS {_number(gap_in)} IN ABOVE THE SHEET{feed}"


def _is_comment(line: str) -> bool:
    s = line.strip()
    return s.startswith("[") and s.endswith("]") and s.count("[") == 1


def op_tag(line: str):
    """"[outer p01-1]" -> "outer", "[bore holes]" -> "bore"; None for any other line."""
    if not _is_comment(line):
        return None
    word = line.strip()[1:-1].split(" ", 1)[0].lower()
    return word if word in OP_TAGS else None


def _words(line: str):
    if not line.strip() or _is_comment(line):
        return None
    try:
        return parse_code(line.rstrip("\r\n"))
    except ValueError:
        return None


def _moves(lines):
    """Per line: (motion mode, start point) for a motion line, else None. G53 forgets the position."""
    mode, pos, out = None, (None, None, None), []
    for line in lines:
        words = _words(line)
        if words is None:
            out.append(None)
            continue
        gs = [v for k, v in words if k == "G" and v is not None]
        if 53.0 in gs:
            pos = (None, None, None)
            out.append(None)
            continue
        for g in gs:
            if g in _MOTION_G:
                mode = g
            elif g == 80.0:
                mode = None
        axes = {k: v for k, v in words if k in "XYZ" and v is not None}
        if axes and 4.0 not in gs:
            out.append((mode, pos))
            pos = tuple(axes.get(k, pos[i]) for i, k in enumerate("XYZ"))
        else:
            out.append(None)
    return out


def outlines_only(text: str) -> str:
    """Keep the moves of the `[outer ...]` ops only (see the module docstring); raises AirTestError if that would
    change any kept move."""
    lines = re.split(r"(?<=\n)", text)                     # keep the line endings
    keep, op = [], None
    for line in lines:
        tag = op_tag(line)
        if tag is not None:
            op = tag
        code = _words(line)
        moving = code is not None and ("G", 53.0) not in code and ("G", 4.0) not in code and \
            any(k in "XYZ" for k, _ in code)
        drop = op not in (None, "outer") and (tag is not None or moving)
        keep.append(not drop)
    if not any(op_tag(l) == "outer" for l in lines):
        raise AirTestError("the program has no outline ([outer ...]) ops")
    before = [m for m, k in zip(_moves(lines), keep) if k]
    kept = [l for l, k in zip(lines, keep) if k]
    for i, (was, now) in enumerate(zip(before, _moves(kept))):
        if was is None:
            continue
        if now is None or now[0] != was[0] or (was[0] != 0.0 and now[1] != was[1]):
            raise AirTestError(f"taking out the other ops would change the move {kept[i].strip()!r}")
    return "".join(kept)


def _track(lines):
    """Per line: (mode, start, end) for a motion line, else None (as _moves, plus where it ends)."""
    out = []
    starts = _moves(lines)
    for line, move in zip(lines, starts):
        if move is None:
            out.append(None)
            continue
        words = _words(line)
        axes = {k: v for k, v in words if k in "XYZ" and v is not None}
        end = tuple(axes.get(k, move[1][i]) for i, k in enumerate("XYZ"))
        out.append((move[0], move[1], end))
    return out


def _same_z(a, b) -> bool:
    return a is not None and b is not None and abs(a - b) < 1e-6


_PLAIN = re.compile(r"^\s*(?:G0*90\s*)?(?:F\s*[\d.]+\s*)?$")


TAB_MAX_IN = 0.25    # a lap at the final depth may rise this far over a tab and come back down


def one_lap(text: str) -> str:
    """Each `[outer]` op cut down to one lap: rapid to where the lap at its final depth starts, down to that
    depth, the lap, back up. The lap runs from the first feed move at that depth to the last one, through any
    tabs (feed moves up to TAB_MAX_IN above it). An op with anything but moves between its first and last move
    is left as it is. Raises AirTestError if a kept lap move would start anywhere else or in another mode."""
    lines = re.split(r"(?<=\n)", text)
    track = _track(lines)
    starts = [i for i, l in enumerate(lines) if op_tag(l) is not None] + [len(lines)]
    out, kept = list(lines), {}                  # kept: new line -> original line, for the check
    for s, e in reversed(list(zip(starts, starts[1:]))):
        if op_tag(lines[s]) != "outer":
            continue
        moving = [i for i in range(s + 1, e) if track[i] is not None]
        feeds = [i for i in moving if track[i][0] in _FEED_G]
        if not feeds:
            continue
        span = range(moving[0], moving[-1] + 1)
        if any(track[i] is None and lines[i].strip() and not _is_comment(lines[i])
               and not _PLAIN.match(lines[i].strip()) for i in span):
            continue                              # something other than moves in there: leave it alone
        floor = min(track[i][2][2] for i in feeds if track[i][2][2] is not None)
        lap = []
        for i in moving:
            mode, start, end = track[i]
            if mode in _FEED_G and _same_z(start[2], floor) and _same_z(end[2], floor):
                lap.append(i)
            elif lap and mode in _FEED_G and all(z is not None and floor - 1e-6 <= z <= floor + TAB_MAX_IN
                                                 for z in (start[2], end[2])):
                lap.append(i)                     # over a tab and back down
            elif lap:
                break
        while lap and not _same_z(track[lap[-1]][2][2], floor):
            lap.pop()                             # it ends at the final depth, not partway over a tab
        if not lap:
            continue
        nl = "\r\n" if lines[lap[0]].endswith("\r\n") else "\n"
        sx, sy, _ = track[lap[0]][1]
        before = [i for i in moving if i < feeds[0] and track[i][0] == 0.0 and track[i][2][2] != track[i][1][2]]
        retract = track[before[-1]][2][2] if before else None
        last = moving[-1]
        clear = track[last][2][2] if track[last][0] == 0.0 and last not in lap else None
        new = [f"G0 X{_number(sx)} Y{_number(sy)}{nl}"]
        if retract is not None and retract > floor:
            new.append(f"G0 Z{_number(retract)}{nl}")
        new.append(f"G1 Z{_number(floor)}{nl}")
        for n, i in enumerate(lap):
            line = lines[i]
            if n == 0 and not any(k == "G" and v in _MOTION_G for k, v in _words(line)):
                line = f"G{int(track[i][0])} {line}"
            new.append(line)
        if clear is not None:
            new.append(f"G0 Z{_number(clear)}{nl}")
        out[span.start:span.stop] = new
        first = span.start + len(new) - len(lap) - (1 if clear is not None else 0)
        shift = len(new) - len(span)
        kept = {k + shift if k >= span.stop else k: v for k, v in kept.items()}
        kept.update({first + n: i for n, i in enumerate(lap)})
    result = "".join(out)
    after = _track(re.split(r"(?<=\n)", result))
    for new_i, old_i in kept.items():
        if after[new_i] is None or after[new_i][:2] != track[old_i][:2]:
            raise AirTestError(f"one lap would change the move {lines[old_i].strip()!r}")
    return result


def set_feed(text: str, feed_ipm: float) -> str:
    """Every feed move (G1, G2, G3, drill cycles) at feed_ipm: an F word on each, replacing any there was."""
    if feed_ipm <= 0:
        raise ValueError("feed must be positive")
    lines = re.split(r"(?<=\n)", text)
    out = []
    for line, move in zip(lines, _moves(lines)):
        if move is not None and move[0] in _FEED_G:
            body = line.rstrip("\r\n")
            end = line[len(body):]
            parts = re.split(r"(\[[^\]]*\])", body)
            code = "".join(p for p in parts if not p.startswith("["))
            if _FEED.search(code):
                body = "".join(p if p.startswith("[") else _FEED.sub(f"F{_number(feed_ipm)}", p) for p in parts)
            else:
                body = f"{body} F{_number(feed_ipm)}"
            line = body + end
        out.append(line)
    return "".join(out)


def air_test_program(text: str, thickness_in: float, gap_in: float, feed_ipm: float) -> str:
    lift = thickness_in + gap_in
    return lift_program(set_feed(one_lap(outlines_only(text)), feed_ipm), lift, note_for(lift, gap_in, feed_ipm))


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
