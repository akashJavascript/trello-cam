"""Part cards (Trello Free has no custom fields, so everything is in the title, description and labels).

- Title: the part's name as in Onshape (case and extra spaces don't matter; a Part Studio with one part needs
  no name at all). A quantity may end the title: `P-2011 x4`.
- Description: a link to the Part Studio (its workspace or a version) and the quantity: `Qty: 4`, `Qty 4`,
  `Quantity: 4`, `x4` or `4x` on a line of its own. The quantity is required.
- Labels: `Smoked` (smoked polycarbonate), `Tool 1/8` (force the 1/8 in endmill), `Face` (skim its top clean
  first, in a program of its own), `Spare` (spares for the spares box: they only get the room the robot parts
  leave; the Onshape panel and the phone's Send spares make these cards).
- Fallback: a .step attachment instead of a link, plus a `Material: ...` line.
- Optional: `Part ID: JHD`, Onshape's id for the part (the Onshape panel writes it), so a part renamed after the
  card was made is still found; a `Material:` line for a part with no material in Onshape.
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
SCRIBE_RE = re.compile(r"^[ \t>\-]*scribe(?:[ \t]+(?:the[ \t]+)?bend[ \t]*lines?)?[ \t]*[:=][ \t]*(.*?)[ \t]*$",
                       re.IGNORECASE | re.MULTILINE)
PART_ID_RE = re.compile(r"^[ \t>\-]*part[ \t]*id[ \t]*[:=][ \t]*([A-Za-z0-9_.+/=-]+)[ \t]*$", re.IGNORECASE | re.MULTILINE)
STEP_SUFFIXES = (".step", ".stp")
README_CARD = "How to add a part (read me)"
TABS_HELP = {
    True: ("\"Hold it in with tabs\" starts ticked: its outline gets small tabs, and you break the part out "
           "after the cut. Untick it for a part tape holds on its own."),
    False: ("Tick \"Hold it in with tabs\" if tape alone won't hold it: its outline gets small tabs, and you "
            "break the part out after the cut."),
}

FORMAT_HELP = """1. In Onshape, open the Part Studio with your part and copy the address bar. A configured part: pick its
   configuration first; the address then has it, and that configuration is what's cut.
2. Make a card in Drafts (or use the New part template).
   Title: the part's name.
   Description: the link, and a line like Qty: 2
3. Move the card to Ready for CAM. About 2 minutes later it's nested, together with whatever else is waiting
   and any sheet still in Sheet review that has room.

Untick "Nest this part" to place it yourself: it gets a program of its own, zeroed at the front-left corner of
the part's box, to cut from any piece anywhere on the bed (one copy; the card says how many times to run it).
{tabs} (Already on a sheet? Change it and move the card back to Ready for CAM.)
Need one part now? Add the {rush} label before moving it: it's nested at once (within a minute, after any run
already going), on its own: an offcut or a new sheet, not a sheet waiting in Sheet review.
Need a press fit or a slip fit you'll tune at the machine? Add a line like Fit: 1.125 hole (that hole's size: 0.5,
1/2 and 22 mm all work; any round hole that isn't drilled. A hole over 1.5 in is cut with its slug tabbed: break the
slug out before the laps) or Fit: outline. It's cut a little tight, and the sheet card gets short programs that each take a little more off,
to run one at a time until it fits; tick the one that did, and the next time starts there.
Labels, if needed: {smoked} for smoked polycarbonate, {tool} to force the 1/8 in endmill, {face} for a clean
top (it's skimmed {face_depth} in off the top, in a program run before the sheet's).
Spares for the spares box? Set Spares in the Onshape panel, or use Send spares on the shop page's Plates tab. They
get a card of their own with the {spare} label, only take the room the robot parts leave, and the sheet card marks
them.
SRPP? Set the part to Polycarbonate in Onshape and add a line Material: SRPP Twill (or SRPP MAXComposite, or SRPP
SWYFT): it's cut like polycarbonate, from that kind of SRPP sheet.
Bent (sheet metal) part? It's cut flat and the sheet card lists its bends. Add a line Scribe: endmill to have each
bend line scribed on top with the sheet's own cutter (a shallow line as wide as it), or Scribe: chamfer mill for a fine
line with the 45 degree chamfer mill (a short program of its own, run before the sheet's).
No Onshape? Attach a .step file and add a line like Material: 6061 (or 5052, PC, SRPP Twill).
CAM'd it yourself? Attach your .tap instead, with lines like Material: 6061 and Thickness: 3/16 (and Cutter: 1/2
if it's over 1/4 in), or use "I CAM'd it myself" in the Onshape panel. Add a line Place it myself to cut it wherever
you like instead: its card goes to Sheet review with your program as it is. AutoCAM picks it a spot on an offcut or a new sheet, moves the program there and puts it in
Sheet review: zero X/Y at the piece's front-left corner like any sheet. Its Z0 must be the spoilboard.
Running a program yourself (from a USB stick)? Check it first: a card with the {check} label, the .tap attached and
lines like Material: 6061 and Thickness: 3/16. Within a minute a comment says whether it passes AutoCAM's checks
(spoilboard, clamps, reach, rapids, mist, stops) and nothing is placed. They're checked as zeroed at a full sheet's
front-left corner; add a line Place it myself if you zero it somewhere else.

Offcuts: each one has a number (#4); write it on the piece when it goes on the rack. If a sheet card names an
offcut that isn't there, tick "The offcut isn't on the rack" under Stock on that card. To add a full-width
piece the system doesn't know about, make a card in Offcuts with lines like Material: 6061, Thickness: 3/16
and Length: 20.
Cut a part out of an offcut by hand? Add a line to its card like Cut by hand: 12 to 18 along, 5 to 10 across
(inches; the card says where to measure from) and runs keep clear of it. Cut it with your own program? You can
attach the .tap instead, with a line like Zeroed at: 10 along, 5 across (where you zeroed X/Y). If a sheet card's offcut turns out
to have something cut where the program goes, tick "Something's already cut where this program goes".
If something is wrong, the card comes back in Needs fixing with a comment saying what to fix."""


def scribe_value(text: str) -> Optional[str]:
    """What a Scribe: line says: "endmill", "chamfer", "" (no), or None if it can't be read."""
    t = " ".join(text.lower().replace("-", " ").split())
    if t in ("", "no", "off", "none", "false"):
        return ""
    if "chamfer" in t or "45" in t or t in ("v bit", "vbit", "t16"):
        return "chamfer"
    if t in ("yes", "on", "true", "endmill", "end mill", "4mm", "4 mm", "1/8", "the endmill", "the end mill") \
            or "end mill" in t or "endmill" in t:
        return "endmill"
    return None


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
    tabs: bool = False                   # the card's "Hold it in with tabs" box (set by the runner)
    by_hand: bool = False                # "Nest this part" unticked: placed by hand (set by the runner)
    face: bool = False                   # the Face label: its top skimmed clean first
    fit: Optional[Tuple[str, float]] = None   # its Fit: line, ("hole", diameter) or ("outline", 0) (set by the runner)
    part_id: Optional[str] = None        # a Part ID: line: Onshape's id for the part, found by it before its name
    scribe: str = ""                     # a Scribe: line: a bent part's bend lines scribed, "endmill" or "chamfer"
    spare: bool = False                  # the Spare label: spares, nested after the robot parts (2026-11-04)


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


def parse_card(card: Card, smoked_label: str = "Smoked", tool_label: str = "Tool 1/8",
               face_label: str = "Face", spare_label: str = "Spare") -> Union[PartRequest, CardProblem]:
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

    ids = sorted({v.strip() for v in PART_ID_RE.findall(plain)})
    if len(ids) > 1:
        problems.append(f"Two different Part ID lines ({', '.join(ids)}). Keep one.")

    scribes = {scribe_value(v) for v in SCRIBE_RE.findall(plain)}
    if None in scribes:
        problems.append("The Scribe: line says something else. Write Scribe: endmill (the sheet's own cutter) or "
                        "Scribe: chamfer mill.")
    elif len(scribes) > 1:
        problems.append("Two different Scribe: lines. Keep one.")

    labels = {l.strip().lower() for l in card.labels}
    if problems:
        return CardProblem(card, tuple(problems))
    return PartRequest(card=card, name=name, qty=qty, link=link, step_attachment=step, material_hint=hint,
                       smoked=smoked_label.lower() in labels, force_small_tool=tool_label.lower() in labels,
                       face=face_label.strip().lower() in labels, part_id=ids[0] if ids and link else None,
                       scribe=next(iter(scribes)) if scribes else "",
                       spare=" ".join(spare_label.split()).lower() in {" ".join(l.split()) for l in labels})
