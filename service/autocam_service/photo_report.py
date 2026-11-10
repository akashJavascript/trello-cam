"""How well the offcut photo finding does on the team's own photos (`python -m autocam_service offcut-photo-report`).

Each photo saved on the shop page is kept with the corners and boxes AutoCAM suggested and the ones people saved
(offcut_photos.py, state/offcut_photos/kept/). This compares the two, per way of finding (photo_find: "look" where the
floor and the piece look different, "outline" where they don't):
- a hole is a box people saved; it was **found** when suggested boxes cover at least half of it, **missed** if not;
- an **extra** box is a suggested one people's boxes cover less than half of (deleted, or far off);
- corners were **right** when every suggested corner is within 1/2 in of where people left it.
Missed holes are what matter (a part can end up over one); extra boxes only waste some metal.
"""

import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

GRID = 8                # cells per inch for the overlaps
FOUND_SHARE = 0.5
CORNER_OK_IN = 0.5
HOW = {"look": "by look (film, wood)", "outline": "by outline (bare metal)", "": "nothing found to go on"}


def _cells(box: Sequence[float]) -> Set[Tuple[int, int]]:
    x0, y0, x1, y1 = (float(v) * GRID for v in box)
    xs = range(math.floor(x0), max(math.floor(x0) + 1, math.ceil(x1)))
    ys = range(math.floor(y0), max(math.floor(y0) + 1, math.ceil(y1)))
    return {(i, j) for i in xs for j in ys}


def _covered(box: Sequence[float], cover: Set[Tuple[int, int]]) -> float:
    c = _cells(box)
    return len(c & cover) / len(c)


def _union(boxes: Iterable[Sequence[float]]) -> Set[Tuple[int, int]]:
    out: Set[Tuple[int, int]] = set()
    for b in boxes:
        out |= _cells(b)
    return out


def corner_miss_in(suggested: Optional[Sequence], final: Sequence, width: float, length: float) -> Optional[float]:
    """How far (inches) the worst suggested corner was from where people left it; None when none was suggested.
    Turning or flipping the order (the front swapped) doesn't count as a miss."""
    if not suggested or not final or len(suggested) != 4 or len(final) != 4:
        return None
    if width <= 0 or length <= 0:
        return None
    f = [(float(x), float(y)) for x, y in final]
    px_per_in = sum(math.dist(f[k], f[(k + 1) % 4]) for k in range(4)) / (2 * (width + length))   # the photo's scale
    if px_per_in <= 0:
        return None
    s = [(float(x), float(y)) for x, y in suggested]
    best = min(max(math.dist(o[k], f[k]) for k in range(4))
               for o in [s[r:] + s[:r] for r in range(4)] + [s[::-1][r:] + s[::-1][:r] for r in range(4)])
    return best / px_per_in


def score(record: Dict[str, Any]) -> Dict[str, Any]:
    saved = [b for b in record.get("saved", []) if len(b) == 4]
    suggested = [b for b in record.get("suggested", []) if len(b) == 4]
    got, people = _union(suggested), _union(saved)
    found = sum(1 for b in saved if _covered(b, got) >= FOUND_SHARE)
    extra = sum(1 for b in suggested if _covered(b, people) < FOUND_SHARE)
    miss = corner_miss_in(record.get("suggested_corners"), record.get("corners") or [],
                          float(record.get("width") or 0), float(record.get("length") or 0))
    return {"holes": len(saved), "found": found, "missed": len(saved) - found, "extra": extra,
            "corners": "none" if miss is None else ("right" if miss <= CORNER_OK_IN else "moved"), "corner_in": miss}


def load(folder: Path) -> List[Dict[str, Any]]:
    out = []
    if not folder.is_dir():
        return out
    for d in sorted(p for p in folder.iterdir() if p.is_dir()):
        try:
            r = json.loads((d / "record.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        r["_folder"] = d.name
        out.append(r)
    return out


def _row(name: str, scores: List[Dict[str, Any]]) -> str:
    holes = sum(s["holes"] for s in scores)
    found = sum(s["found"] for s in scores)
    right = sum(1 for s in scores if s["corners"] == "right")
    share = f"{100 * found / holes:.0f}%" if holes else "-"
    return (f"{name:<26}{len(scores):>7}{holes:>7}{found:>7}{holes - found:>8}{share:>7}"
            f"{sum(s['extra'] for s in scores):>7}   {right} of {len(scores)}")


def report(folder: Path, worst: int = 5) -> str:
    records = load(folder)
    if not records:
        return (f"No offcut photos kept yet ({folder}). Each photo saved on the shop page's Offcuts tab is kept here "
                "([offcut_photos] keep).")
    scored = [(r, score(r)) for r in records]
    lines = [f"Offcut photos kept: {len(records)} ({folder})", "",
             f"{'':<26}{'photos':>7}{'holes':>7}{'found':>7}{'missed':>8}{'':>7}{'extra':>7}   corners right"]
    for how in sorted({r.get("how", "") for r in records}, key=lambda h: list(HOW).index(h) if h in HOW else 9):
        lines.append(_row(HOW.get(how, how), [s for r, s in scored if r.get("how", "") == how]))
    lines.append(_row("all", [s for _, s in scored]))
    bad = sorted((x for x in scored if x[1]["missed"]), key=lambda x: (-x[1]["missed"], x[0]["_folder"]))[:worst]
    if bad:
        lines += ["", "Most holes missed:"]
        for r, s in bad:
            what = f"{r.get('material', '?')} {r.get('thickness', '')} {float(r.get('length') or 0):g} in".strip()
            lines.append(f"  {r['_folder']}  {what}, {r.get('kind', '?')}, {r.get('how') or '-'}: "
                         f"{s['missed']} of {s['holes']} missed, {s['extra']} extra")
    qs = [r.get("quality") or {} for r in records]
    cam, app = sum(1 for q in qs if q.get("via") == "camera"), sum(1 for q in qs if q.get("via") == "app")
    if cam or app:
        flagged = [q for q in qs if q.get("problem")]
        lines += ["", f"Photo check: {cam} from the page's camera, {app} from the camera app; "
                  f"{sum(1 for q in flagged if q['problem'] == 'blurry')} blurry and "
                  f"{sum(1 for q in flagged if q['problem'] == 'dark')} dark ones used anyway."]
        tilts = [q["tilt"] for q in qs if "tilt" in q]
        if tilts:
            lines.append(f"  Held within {sorted(tilts)[len(tilts) // 2]:g} deg of flat on half of them.")
    lines += ["", "found = suggested boxes cover at least half of a saved box; extra = a suggested box people's boxes "
              "don't; corners right = all four within 1/2 in."]
    return "\n".join(lines)
