"""Part labels drawn on a sheet's preview before it goes on the card, so whoever unloads the sheet can tell the
parts apart: each part's cut-order number in a yellow badge (the CUT ORDER list on the card uses the same
numbers), its name (and copy, "2/3") in bold white with a black outline under it (or over, right or left of
it, whichever covers the least of the other labels), and a line along the top saying what the numbers mean.

The Fusion side works out where each label goes (autocam_core/preview.py: on the part's material, as far from
any edge as possible, or in the middle of a part that's mostly cutouts) and writes <sheet>.labels.json next to
the picture. Drawing needs Pillow; without it, or
if anything goes wrong, the card gets the picture as Fusion made it.
"""

import io
import logging
from typing import Any, Dict, Optional

from autocam_core.preview import BADGE_MIN_PX

log = logging.getLogger("autocam.labels")

BADGE = (255, 212, 0)          # yellow, readable on the dark background and the grey parts
INK = (0, 0, 0)
NAME = (255, 255, 255)
FONTS = ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf")


def _font(size: int):
    from PIL import ImageFont
    for name in FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                           # Pillow before 10.1
        return ImageFont.load_default()


def draw(png: bytes, labels: Dict[str, Any]) -> bytes:
    """The picture with its labels on (raises on anything unexpected; see labelled)."""
    from PIL import Image, ImageDraw
    image = Image.open(io.BytesIO(png)).convert("RGB")
    d = ImageDraw.Draw(image)
    w, h = image.size
    sx, sy = w / labels["image"][0], h / labels["image"][1]     # in case the picture came out another size
    badges = []
    for item in labels["labels"]:
        room = item.get("room", 0) * min(sx, sy)
        badges.append((item["x"] * sx, item["y"] * sy, int(max(BADGE_MIN_PX, min(30, room * 0.9)))))
    taken = [(x - r, y - r, x + r, y + r) for x, y, r in badges]
    for item, (x, y, r) in zip(labels["labels"], badges):
        d.ellipse((x - r, y - r, x + r, y + r), fill=BADGE, outline=INK, width=3)
        d.text((x, y), str(item["n"]), font=_font(int(r * 1.15)), fill=INK, anchor="mm")
    for item, (x, y, r) in zip(labels["labels"], badges):
        name = item["name"] + (f"  {item['copy']}" if item.get("copy") else "")
        font = _font(max(16, min(28, int(r * 0.9))))
        # under the badge, else over it, right or left: wherever it covers the least of the other labels
        spots = [((x, y + r + 4), "mt"), ((x, y - r - 4), "mb"), ((x + r + 6, y), "lm"), ((x - r - 6, y), "rm")]
        boxes = [d.textbbox(at, name, font=font, anchor=anchor, stroke_width=3) for at, anchor in spots]
        own = (x - r, y - r, x + r, y + r)
        costs = [sum(_overlap(b, t) for t in taken if t != own) + _outside(b, w, h) for b in boxes]
        best = min(range(len(spots)), key=lambda i: (costs[i], i))
        at, anchor = spots[best]
        d.text(at, name, font=font, fill=NAME, anchor=anchor, stroke_width=3, stroke_fill=INK)
        taken.append(boxes[best])
    title = f"{labels.get('sheet', '')}: the numbers are the cut order".lstrip(": ")
    d.text((w / 2, 12), title, font=_font(26), fill=NAME, anchor="mt", stroke_width=3, stroke_fill=INK)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _overlap(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _outside(box, w: int, h: int) -> float:
    """How much of a box is off the picture (counted like an overlap, so it's avoided too)."""
    inside = _overlap(box, (0, 0, w, h))
    return (box[2] - box[0]) * (box[3] - box[1]) - inside


def labelled(png: bytes, labels: Optional[Dict[str, Any]]) -> bytes:
    """The picture with labels, or as it was if there are none or they can't be drawn."""
    if not labels or not labels.get("labels"):
        return png
    try:
        return draw(png, labels)
    except ImportError:
        log.warning("Pillow isn't installed: previews go on the cards without part labels")
    except Exception as e:  # noqa: BLE001 - a picture without labels beats no picture
        log.warning("couldn't draw the part labels: %s", e)
    return png
