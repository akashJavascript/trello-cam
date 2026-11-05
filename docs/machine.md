# The machine screen

The operator's screen on the ShopSabre's own PC (`/machine`, `service/autocam_service/machine.py`,
`panel/static/machine.js`). It walks one sheet at a time through what the Trello sheet card says, one card per step,
built from the shop page's own pieces.

## Opening it

No sign-in: it has its own key, worked out from `AUTOCAM_PANEL_KEY` like the shop page's. On the laptop run

    python -m autocam_service machine-link

It writes `state/machine-link.html` (the link carries the key, so it never goes to the terminal or a log). Open
that file on the ShopSabre's PC and bookmark the screen it opens. `[panel] machine = false` turns the screen off.

## What it shows

- **In progress**: sheets someone started (it remembers the step, `state/machine.json`), with Continue.
- **Ready to cut**: AutoCAM's sheet cards in Ready to cut, Rush first, own programs AutoCAM placed included.
- **Placed by hand**: parts and own programs zeroed at their own corner on any piece.
- **Checked programs**: Check-label cards in Drafts whose program passed (`state/checked_programs.json`).
- **Cut today**: with Undo while the move to Cut is still waiting.

A started sheet shows only the step to do now: find the offcut, load, change the cutter, the air test (only when
ticked), each program (Download, then Done), try the fit (Yes / Not yet, with a lap in between), the outlines cut
last, then what's left of the sheet.

**The tool height is insisted on** (the user: a missed tool height "is one of the most common ways we lose parts"):
Load (and Load for a part placed by hand) and every Change the cutter have boxes to tick (the cutter in the spindle,
"Tool height measured on the tool setter", and at Load the zero), and their button stays off until every box is
ticked. Back a step clears them. The sheet card says it too: "its height measured on the tool setter" at LOAD and
"change to ... and measure its height" at every cutter change in RUN.

## What the buttons do on Trello

Every button is something the board already understands; the service's own passes do the rest.

| Button | On the sheet card |
|---|---|
| Loaded | ticks the At the machine boxes |
| Stop after each part (switch) | Options: "Cut the whole sheet without stopping" (off = ticked); the `_NOSTOP` program arrives a pass later |
| Yes / Still tight (a fit) | ticks that run in the Fit checklist (AutoCAM learns it) |
| Can't find it (an offcut) | Stock: "The offcut isn't on the rack"; another piece the same program fits, or Nest it again / Cut it on a new sheet |
| Scrap it instead / Keep it after all | Offcut: "Keep the rest of the sheet" |
| Move to Cut | moves the card to Cut 3 minutes later (Undo until then; automation never moves a card into Ready to cut) |
| Something went wrong | moves the card to Sheet review with what happened in a comment |

Download sends the program from the card (`Content-Disposition: attachment`): it lands in the PC's Downloads, where
WinCNC's yellow folder button finds it.
