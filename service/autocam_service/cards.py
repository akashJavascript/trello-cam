"""Part cards (Trello Free has no custom fields, so everything is in the title, description and labels).

- Title: the part's name exactly as in Onshape.
- Description: the first Onshape link (must be a version) and a line `Qty: N`.
- Labels: `Smoked` (smoked polycarbonate), `Tool 1/8` (force the 1/8 in endmill).
- Fallback: a .step attachment instead of a link, plus a `Material: ...` line.
Anything wrong sends the card to Needs fixing with a comment that shows the format.
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple, Union

from .onshape.urls import LinkError, OnshapeLink, first_onshape_url, parse_link
from .tracker.base import Attachment, Card

MAX_QTY = 100
QTY_RE = re.compile(r"^[ \t>\-]*qty[ \t]*[:=][ \t]*(\S*)[ \t]*$", re.IGNORECASE | re.MULTILINE)
MATERIAL_RE = re.compile(r"^[ \t>\-]*material[ \t]*[:=][ \t]*(.*?)[ \t]*$", re.IGNORECASE | re.MULTILINE)


def _plain(desc: str) -> str:
    """The description without Markdown emphasis or code marks, for the Qty/Material lines only.

    Trello keeps what students type as Markdown: copying `Qty: 2` from the read-me card stores the backticks
    (seen 2026-10-02). Links are read from the raw text instead (URLs may contain these characters)."""
    return re.sub(r"[`*]", "", (desc or "").replace("\u00a0", " "))
STEP_SUFFIXES = (".step", ".stp")

FORMAT_HELP = """How a part card should look:
- Title: the part's name, exactly as in Onshape.
- Description: a link to an Onshape *version* of the Part Studio (not the workspace), and a line `Qty: 2`.
- Labels (optional): `{smoked}` for smoked polycarbonate, `{tool}` to force the 1/8 in endmill.
- No Onshape link? Attach a .step file and add a line `Material: 6061` (or 5052, PC)."""


@dataclass(frozen=True)
class PartRequest:
    card: Card
    name: str
    qty: int
    link: Optional[OnshapeLink]
    step_attachment: Optional[Attachment]
    material_hint: Optional[str]
    smoked: bool
    force_small_tool: bool


@dataclass(frozen=True)
class CardProblem:
    card: Card
    problems: Tuple[str, ...]

    def comment(self, smoked_label: str = "Smoked", tool_label: str = "Tool 1/8") -> str:
        lines = ["This part wasn't queued for CAM:"] + [f"- {p}" for p in self.problems]
        return "\n".join(lines) + "\n\n" + FORMAT_HELP.format(smoked=smoked_label, tool=tool_label)


def parse_card(card: Card, smoked_label: str = "Smoked", tool_label: str = "Tool 1/8") -> Union[PartRequest, CardProblem]:
    problems = []
    name = card.name.strip()
    if not name:
        problems.append("the card title must be the part's name exactly as in Onshape")

    qty = 0
    found = [m.strip() for m in QTY_RE.findall(_plain(card.desc))]
    if not found:
        problems.append("no `Qty: N` line in the description")
    elif len(set(found)) > 1:
        problems.append("more than one `Qty:` line")
    elif not found[0].isdigit() or not 1 <= int(found[0]) <= MAX_QTY:
        problems.append(f"`Qty: {found[0]}` isn't a whole number from 1 to {MAX_QTY}")
    else:
        qty = int(found[0])

    link = None
    url = first_onshape_url(card.desc or "")
    if url:
        try:
            link = parse_link(url)
        except LinkError as e:
            problems.append(f"the Onshape link {e}")

    steps = [a for a in card.attachments if a.name.lower().endswith(STEP_SUFFIXES)]
    step = None
    hints = [m.strip() for m in MATERIAL_RE.findall(_plain(card.desc)) if m.strip()]
    hint = hints[0] if hints else None
    if not url:
        if not steps:
            problems.append("no Onshape version link (or .step attachment) in the card")
        elif len(steps) > 1:
            problems.append("more than one .step attachment; keep one")
        else:
            step = steps[0]
            if not hint:
                problems.append("a card with a .step file instead of an Onshape link needs a `Material: ...` line")

    labels = {l.strip().lower() for l in card.labels}
    if problems:
        return CardProblem(card, tuple(problems))
    return PartRequest(card=card, name=name, qty=qty, link=link, step_attachment=step, material_hint=hint,
                       smoked=smoked_label.lower() in labels, force_small_tool=tool_label.lower() in labels)
