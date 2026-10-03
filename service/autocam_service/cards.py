"""Part cards (Trello Free has no custom fields, so everything is in the title, description and labels).

- Title: the part's name as in Onshape (case and extra spaces don't matter; a Part Studio with one part needs
  no name at all). A quantity may end the title: `P-2011 x4`.
- Description: a link to the Part Studio (its workspace or a version) and the quantity: `Qty: 4`, `Qty 4`,
  `Quantity: 4`, `x4` or `4x` on a line of its own. The quantity is required.
- Labels: `Smoked` (smoked polycarbonate), `Tool 1/8` (force the 1/8 in endmill).
- Fallback: a .step attachment instead of a link, plus a `Material: ...` line.
Anything wrong sends the card to Needs fixing with a short comment saying what to fix.
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple, Union

from .onshape.urls import LinkError, OnshapeLink, first_onshape_url, parse_link
from .tracker.base import Attachment, Card

MAX_QTY = 100
QTY_LINE_RES = (
    re.compile(r"^[ \t>\-]*(?:qty|quantity)[ \t]*[:=]?[ \t]*(\S+)[ \t]*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^[ \t>\-]*[x\u00d7][ \t]*(\d+)[ \t]*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^[ \t>\-]*(\d+)[ \t]*[x\u00d7][ \t]*$", re.IGNORECASE | re.MULTILINE),
)
TITLE_QTY_RE = re.compile(r"^(?P<name>.*?)[ \t]*\(?[x\u00d7][ \t]*(?P<qty>\d+)\)?[ \t]*$", re.IGNORECASE)
MATERIAL_RE = re.compile(r"^[ \t>\-]*material[ \t]*[:=][ \t]*(.*?)[ \t]*$", re.IGNORECASE | re.MULTILINE)
STEP_SUFFIXES = (".step", ".stp")
README_CARD = "How to add a part (read me)"

FORMAT_HELP = """1. In Onshape, open the Part Studio with your part and copy the address bar.
2. Make a card in Drafts (or use the New part template).
   Title: the part's name.
   Description: the link, and a line like Qty: 2
3. Move the card to Ready for CAM.

Labels, if needed: {smoked} for smoked polycarbonate, {tool} to force the 1/8 in endmill.
No Onshape? Attach a .step file and add a line like Material: 6061 (or 5052, PC).
If something is wrong, the card comes back in Needs fixing with a comment saying what to fix."""


def _plain(desc: str) -> str:
    """The description without Markdown emphasis or code marks, for the Qty/Material lines only.

    Trello keeps what students type as Markdown: copying `Qty: 2` from the read-me card stores the backticks
    (seen 2026-10-02). Links are read from the raw text instead (URLs may contain these characters)."""
    return re.sub(r"[`*]", "", (desc or "").replace("\u00a0", " "))


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
        return problem_comment("Not queued", self.problems)


def problem_comment(headline: str, problems) -> str:
    lines = [f"{headline}:"] + [f"- {p}" for p in problems]
    return "\n".join(lines) + f"\n\nFix it, then move this card back to Ready for CAM. (Card format: see the " \
                              f"\"{README_CARD}\" card in Drafts.)"


def parse_card(card: Card, smoked_label: str = "Smoked", tool_label: str = "Tool 1/8") -> Union[PartRequest, CardProblem]:
    problems = []
    name = " ".join(card.name.split())
    title_qty = None
    m = TITLE_QTY_RE.match(name)
    if m and m["name"]:
        name, title_qty = m["name"], m["qty"]
    if not name:
        problems.append("The title is empty. Make it the part's name in Onshape.")

    qty = 0
    plain = _plain(card.desc)
    found = [v.strip() for rx in QTY_LINE_RES for v in rx.findall(plain)]
    if title_qty is not None:
        found.append(title_qty)
    if not found:
        problems.append("No quantity. Add a line like Qty: 2 to the description.")
    elif len(set(found)) > 1:
        problems.append(f"Two different quantities ({', '.join(sorted(set(found)))}). Keep one.")
    elif not found[0].isdigit() or not 1 <= int(found[0]) <= MAX_QTY:
        problems.append(f"Quantity {found[0]} isn't a whole number from 1 to {MAX_QTY}.")
    else:
        qty = int(found[0])

    link = None
    url = first_onshape_url(card.desc or "")
    if url:
        try:
            link = parse_link(url)
        except LinkError as e:
            problems.append(f"The Onshape link {e}.")

    steps = [a for a in card.attachments if a.name.lower().endswith(STEP_SUFFIXES)]
    step = None
    hints = [v.strip() for v in MATERIAL_RE.findall(plain) if v.strip()]
    hint = hints[0] if hints else None
    if not url:
        if not steps:
            problems.append("No Onshape link. Paste the Part Studio's address into the description.")
        elif len(steps) > 1:
            problems.append("More than one .step file attached. Keep one.")
        else:
            step = steps[0]
            if not hint:
                problems.append("A .step file needs a material. Add a line like Material: 6061.")

    labels = {l.strip().lower() for l in card.labels}
    if problems:
        return CardProblem(card, tuple(problems))
    return PartRequest(card=card, name=name, qty=qty, link=link, step_attachment=step, material_hint=hint,
                       smoked=smoked_label.lower() in labels, force_small_tool=tool_label.lower() in labels)
