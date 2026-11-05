"""Pause after every part so the operator can pull it (decision 19).

After a part's outline: retract (G53 Z), spindle off (M5), mist off if used (M12 C8), park,
then a bare G4, which waits for the operator to press ENTER, with its comment shown as the prompt (WinCNC
users guide 2.6, p. 93). On resume: mist on if used (M11 C8), spindle on (S.. M3) and the post's 4-second
spin-up dwell (G4 X4). Never a timed dwell for the stop itself.

WinCNC has no M0 (2026-11-03): its M codes are M11-M14, M17, M18, M28, M29, M37, M98, M99 (pp. 112-116), and
M3/M5 are the machine's own CNC.MAC macros. An M0 stopped r068's program with "Syntax - Command or File Not
Found" ("non-supported code or invalid file name", p. 119), so the stop is a bare G4 now. The post never writes
a bare G4 (onDwell always has an X), so a bare G4 is always one of these stops.

Two ways the block reaches the program, both checked by `verify` on the final text:
- "tap_text" (default): `insert` adds the whole block to the posted text, right before the
  next part's outline op (found by the comment line the post writes for it).
- "manual_nc": Fusion Manual NC entries make the post write the stop part, and the post itself
  restarts the spindle at the next op. Only used once the API is proven to create them.

`air_test_program` builds the machine air test from the same block, so the air test proves
exactly what production programs contain.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .names import outer_comment_line
from .tapguard import TOP, parse_code

Words = List[Tuple[str, Optional[float]]]

AIR_TEST_HEADER = (
    "[PAUSE AIR TEST - NO MATERIAL, SET WORK ZERO AS USUAL]",
    "[ALL CUTTING-AREA MOVES ARE 3 IN ABOVE WORK ZERO (Z0 = STOCK BOTTOM)]",
)


class PauseError(Exception):
    pass


STOP_WORDS = [("G", 4.0)]       # a bare G4: WinCNC waits for ENTER


@dataclass(frozen=True)
class PauseSpec:
    mist: bool
    park: str = "G53 P10"
    spindle_rpm: int = 18000
    dwell_s: float = 4.0


@dataclass(frozen=True)
class PauseEntry:
    after: str      # instance id of the part that was just finished, e.g. p03-1
    line: int       # 1-based line number of its stop (the bare G4)


@dataclass(frozen=True)
class PauseCheck:
    ok: bool
    expected: int
    found: int
    entries: Tuple[PauseEntry, ...]
    problems: Tuple[str, ...]


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def stop_lines(part_number: int, spec: PauseSpec) -> List[str]:
    lines = [f"[PART {part_number} DONE - PAUSE]", "G53 Z", "M5"]
    if spec.mist:
        lines.append("M12 C8")
    lines += [spec.park, f"G4 [TAKE PART {part_number} OUT, THEN PRESS ENTER]"]
    return lines


def restart_lines(spec: PauseSpec) -> List[str]:
    lines = ["M11 C8"] if spec.mist else []
    return lines + [f"S{spec.spindle_rpm}", "M3", f"G4 X{_num(spec.dwell_s)}"]


def pause_block(part_number: int, spec: PauseSpec, restart: bool = True) -> List[str]:
    return stop_lines(part_number, spec) + (restart_lines(spec) if restart else [])


def _split(text: str) -> Tuple[List[str], str]:
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.split(newline), newline


def _find_outer_comments(lines: List[str], outer_order: Sequence[str]) -> List[int]:
    if len(set(outer_order)) != len(outer_order):
        raise PauseError("outer_order lists a part twice")
    found = []
    for instance in outer_order:
        target = outer_comment_line(instance)
        hits = [i for i, line in enumerate(lines) if line.strip() == target]
        if len(hits) != 1:
            raise PauseError(f"expected one '{target}' line, found {len(hits)}")
        found.append(hits[0])
    if found != sorted(found):
        raise PauseError("the outline ops are not in the planned cut order")
    return found


def _is_motion(line: str) -> bool:
    try:
        words = parse_code(line)
    except ValueError:
        return False
    if not words or ("G", 53.0) in words or ("G", 4.0) in words:
        return False
    return any(l in ("X", "Y", "Z") for l, _ in words)


def insert(text: str, outer_order: Sequence[str], spec: PauseSpec,
           after_last_part: bool = False) -> str:
    """Add a pause block between consecutive part outlines (and after the last one if asked)."""
    lines, newline = _split(text)
    anchors = _find_outer_comments(lines, outer_order)
    inserts: List[Tuple[int, List[str]]] = [
        (anchors[k], pause_block(k, spec)) for k in range(1, len(anchors))]
    if after_last_part and anchors:
        last_motion = max((i for i in range(anchors[-1], len(lines)) if _is_motion(lines[i])), default=None)
        if last_motion is None:
            raise PauseError("the last outline op has no motion")
        inserts.append((last_motion + 1, pause_block(len(anchors), spec, restart=False)))
    for index, block in sorted(inserts, key=lambda item: item[0], reverse=True):
        lines[index:index] = block
    return newline.join(lines)


def remove(text: str, outer_order: Sequence[str], spec: PauseSpec,
           after_last_part: bool = False) -> str:
    """The exact inverse of `insert`: the program as it was before the pauses went in (a sheet that runs
    straight through). Raises PauseError unless every block is exactly where and what `insert` makes, and
    putting the pauses back gives the same text, so nothing else can have changed."""
    lines, newline = _split(text)
    anchors = _find_outer_comments(lines, outer_order)
    cuts: List[Tuple[int, int]] = []
    for k in range(1, len(anchors)):
        block = pause_block(k, spec)
        start = anchors[k] - len(block)
        if start < 0 or lines[start:anchors[k]] != block:
            raise PauseError(f"no pause block right before part {outer_order[k]}")
        cuts.append((start, anchors[k]))
    if after_last_part and anchors:
        block = pause_block(len(anchors), spec, restart=False)
        hits = [i for i in range(anchors[-1], len(lines)) if lines[i:i + len(block)] == block]
        if len(hits) != 1:
            raise PauseError("no pause block after the last part")
        cuts.append((hits[0], hits[0] + len(block)))
    for start, stop in sorted(cuts, reverse=True):
        del lines[start:stop]
    out = newline.join(lines)
    if insert(out, outer_order, spec, after_last_part) != text:
        raise PauseError("taking the pauses out would change more than the pauses")
    return out


def verify(text: str, outer_order: Sequence[str], spec: PauseSpec, *,
           after_last_part: bool = False, safe_z_in: Optional[float] = None) -> PauseCheck:
    """Check every pause on the final program text, whichever way it got there.

    Between the end of one part's outline and the first move of the next, the code lines must be,
    in order: G53 Z, M5, [M12 C8], park, G4 (the stop), [M11 C8], S<rpm>, M3, G4 X<dwell>. After the restart,
    the first sideways move must happen at or above `safe_z_in` (the move comes down from the top).
    """
    lines, _ = _split(text)
    expected = max(len(outer_order) - 1, 0) + (1 if after_last_part and outer_order else 0)
    problems: List[str] = []
    entries: List[PauseEntry] = []
    stops = [i for i, line in enumerate(lines) if _is_stop(line)]
    try:
        anchors = _find_outer_comments(lines, outer_order)
    except PauseError as e:
        return PauseCheck(False, expected, len(stops), (), (str(e),))

    stop_words = [parse_code(l) for l in stop_lines(1, spec) if parse_code(l)]
    restart_words = [parse_code(l) for l in restart_lines(spec)]
    full = stop_words + restart_words

    for k in range(1, len(anchors)):
        prev_motion = max((i for i in range(anchors[k - 1], anchors[k]) if _is_motion(lines[i])), default=None)
        if prev_motion is None:
            problems.append(f"outline op for {outer_order[k - 1]} has no motion")
            continue
        next_motion = next((i for i in range(anchors[k], len(lines)) if _is_motion(lines[i])), len(lines))
        code = [(i, _words(lines[i])) for i in range(prev_motion + 1, next_motion)]
        if any(w is None for _, w in code):
            problems.append(f"unreadable line in the pause after {outer_order[k - 1]}")
            continue
        code = [(i, w) for i, w in code if w and not _ignorable(w)]
        if [w for _, w in code] != full:
            problems.append(f"pause after {outer_order[k - 1]} (before line {anchors[k] + 1}) is not the "
                            f"expected stop/restart block: {[_show(w) for _, w in code]}")
            continue
        stop_index = next(i for i, w in code if w == STOP_WORDS)
        entries.append(PauseEntry(after=outer_order[k - 1], line=stop_index + 1))
        if safe_z_in is not None:
            problem = _first_move_problem(lines, code[-1][0] + 1, safe_z_in)
            if problem:
                problems.append(f"after the pause for {outer_order[k - 1]}: {problem}")

    if after_last_part and anchors:
        last_motion = max((i for i in range(anchors[-1], len(lines)) if _is_motion(lines[i])), default=None)
        tail = [_words(l) for l in lines[last_motion + 1:]] if last_motion is not None else []
        tail = [w for w in tail if w and not _ignorable(w)]
        if tail[:len(stop_words)] != stop_words:
            problems.append(f"no stop block after the last part ({outer_order[-1]})")
        else:
            stop_line = next(i for i in range(last_motion + 1, len(lines)) if _is_stop(lines[i]))
            entries.append(PauseEntry(after=outer_order[-1], line=stop_line + 1))

    if len(stops) != expected:
        problems.append(f"expected {expected} stops, found {len(stops)}")
    if anchors and any(i < anchors[0] for i in stops):
        problems.append("a stop before the first part outline")
    return PauseCheck(ok=not problems, expected=expected, found=len(stops),
                      entries=tuple(entries), problems=tuple(problems))


def _ignorable(words: Words) -> bool:
    # Modal noise the post may repeat at an op start; harmless anywhere in the block.
    return all(w in (("G", 90.0),) or w[0] == "F" for w in words)


def _words(line: str) -> Optional[Words]:
    try:
        return parse_code(line)
    except ValueError:
        return None


def _is_stop(line: str) -> bool:
    return _words(line) == STOP_WORDS


def _show(words: Words) -> str:
    return " ".join(f"{l}{'' if v is None else _num(v)}" for l, v in words)


def _first_move_problem(lines: List[str], start: int, safe_z_in: float, z: float = TOP) -> Optional[str]:
    # z: where the cutter is at the stop (AutoCAM's stop block retracts to machine top)
    for i in range(start, len(lines)):
        try:
            words = parse_code(lines[i])
        except ValueError:
            return f"line {i + 1} is unreadable"
        if not words:
            continue
        values = {l: v for l, v in words if l not in ("G", "M")}
        if "Z" in values and values["Z"] is not None:
            z = values["Z"]
            if z < safe_z_in:
                return f"line {i + 1}: first move after resume drops to Z{_num(z)} before moving sideways"
            if "X" in values or "Y" in values:
                return None
            continue
        if "X" in values or "Y" in values:
            return None if z >= safe_z_in else f"line {i + 1}: first sideways move is at Z{_num(z)}"
    return None


_CUT_G = frozenset({1.0, 2.0, 3.0, 73.0, 81.0, 82.0, 83.0})
_MOTION_G = frozenset({0.0}) | _CUT_G


def stop_problems(text: str, safe_z_in: float, mist: bool) -> List[str]:
    """A hand-made program's own stops (bare G4s), held to what AutoCAM's stop blocks do (2026-11-04): the cutter
    is up at or above safe_z_in (the clamps' height) with the mist off when it stops, after ENTER the first move comes
    down from there, and the mist is back on before the next cut when the material uses it. A stop with the spindle
    running, or a cut with it stopped, already fails the guard."""
    lines, _ = _split(text)
    problems: List[str] = []
    z: Optional[float] = None            # None: not known yet (the program's start)
    motion: Optional[float] = None       # the modal motion code
    mist_on = False
    stopped_at: Optional[int] = None     # the last stop's line, until the mist is on again or a cut shows it isn't
    for i, line in enumerate(lines):
        words = _words(line)
        if not words:
            continue
        gs = [v for l, v in words if l == "G"]
        ms = [v for l, v in words if l == "M"]
        values = {l: v for l, v in words if l not in ("G", "M")}
        if words == STOP_WORDS:
            where = f"line {i + 1}"
            if z is None or z < safe_z_in:
                problems.append(f"{where}: stops with the cutter at "
                                f"{'an unknown height' if z is None else 'Z' + _num(z)}, below Z{_num(safe_z_in)} "
                                "(the clamps' height): retract first (G53 Z)")
            if mist_on:
                problems.append(f"{where}: stops with the mist on (M12 C8 first)")
            after = _first_move_problem(lines, i + 1, safe_z_in, TOP if z is None else z)
            if after:
                problems.append(f"after the stop at {where}: {after}")
            stopped_at = i + 1 if mist else None
            continue
        if 53.0 in gs:
            if "Z" in values:
                z = TOP
            continue
        if 4.0 in gs:                     # a dwell
            continue
        if 11.0 in ms:
            mist_on = True
            stopped_at = None
        if 12.0 in ms:
            mist_on = False
        if 80.0 in gs:
            motion = None
        for g in gs:
            if g in _MOTION_G:
                motion = g
        moves = any(l in values for l in ("X", "Y", "Z"))
        if moves and motion in _CUT_G and stopped_at is not None and not mist_on:
            problems.append(f"line {i + 1}: cuts after the stop at line {stopped_at} with the mist still off "
                            "(M11 C8 after the stop)")
            stopped_at = None
        if "Z" in values and values["Z"] is not None:
            z = max(values["Z"], values.get("R") or values["Z"]) if motion in _CUT_G - {1.0, 2.0, 3.0} else values["Z"]
    return problems


def air_test_program(spec: PauseSpec) -> str:
    """The machine air test: two parts, two pauses, every cutting-area move 3 in above work zero."""
    lines = list(AIR_TEST_HEADER)
    if spec.mist:
        lines.append("M11 C8")
    lines += ["G90", "G20", "G53 Z", f"S{spec.spindle_rpm}", "M3", f"G4 X{_num(spec.dwell_s)}"]
    lines += ["G0 X2. Y2.", "G0 Z3.", "G0 X6. Y2."]
    lines += pause_block(1, spec)
    lines += ["G0 X6. Y6.", "G0 Z3.", "G0 X2. Y6."]
    lines += pause_block(2, spec)
    lines += ["G0 X2. Y2.", "G0 Z3.", "[END]"]
    lines += (["M12 C8"] if spec.mist else []) + ["G53 Z", "M5", spec.park]
    return "\r\n".join(lines) + "\r\n"
