# The bought-parts list (2026-11-02)

The same idea as the shaft list (`docs/shafts.md`), for parts the team buys: wheels, rollers, bearings, motors,
belts. They're sent from the Onshape panel with an assembly's plates and shafts, and checked off on the phone page's
**Parts** tab: **In the bin** when the team has one (it goes in that subsystem's bin), **Order** when it has to be
bought.

**AutoCAM never writes to the order sheet** (the user: people write the row themselves, so everything is checked
before money is spent). Order opens the team's Google Sheet in a new tab and copies the part's name; the card then
asks "Added it?", and only **It's on the sheet** moves it to **On the order sheet**.

## What counts as a bought part

The same rules the panel already used to keep these out of the plates (`panel/api.py` `_not_ours`, `_why_not_cut`):

- a part from a document the team doesn't own (a vendor's library, like MKCad's; `[panel] company_id`),
- a McMaster number as its name (`91259A496 ...`, `2766T21 ...`),
- rubber (belts: Viton, Neoprene, urethane...).

**Screws and nuts aren't on the list** (the user). Onshape's standard content never is, and a bought part named like
a screw, bolt or nut (`bought.HARDWARE_RE`: screw, setscrew, bolt, nut, locknut, nylock; so a nut strip too) is
left out of the panel altogether. The common parts library's prints stay "Library part", and brass, steel and other
metals stay "AutoCAM doesn't cut": neither is on this list.

## Sending them (the Onshape panel, Assembly tab)

- An assembly's bought parts are listed under **Bought parts**, under Shafts. Each row is the part's name (it wraps:
  vendors' names are long) and the assembly's count. They start **unticked** (the team may have most of them).
- Send sends ticked plates, shafts and bought parts together ("Send 3 parts and 2 bought parts").
- After sending, a row shows a check and its count while it's needed, with an × to take it off the list; **Ordered**
  once someone says it's on the order sheet; "In the bin" under its name once it's in the bin, ticked off and
  sendable again (for when more are needed).
- A part taken out of the assembly gets **Changed in CAD**, in the panel and on the phone, like a shaft.
- Clicking a row lights the part up in Onshape, and clicking the part in Onshape lights up its row (the same as
  plates and shafts).

## The phone page (`/shop`, Parts tab)

- **Shafts | Parts** at the top. The last tab used is remembered on the phone; a link ending in `#parts` opens on
  Parts (for a QR code at the bins later).
- **<Assembly> bin:** what each subsystem still needs, one card per part with its count: **Order** (only if
  `[parts] order_sheet_url` is set) and **In the bin**.
- **On the order sheet:** ordered and waiting to come in. **In the bin** when it does.
- **In the bin today**, with **Undo** on the phone that did it (the same day).
- The vendor shows under the name when the BOM has a Vendor column, or "McMaster" for a McMaster number.
- Parts in the bin drop off after 14 days (`[parts] keep_days`).

## Setup

    [parts]
    enabled = true
    order_sheet_url = "https://docs.google.com/spreadsheets/d/.../edit"   # the team's order sheet (only a Google Sheet)
    keep_days = 14

With no `order_sheet_url` there's no Order button (In the bin only). The link is only opened on the phone; AutoCAM
doesn't read the sheet or need access to it. The shop link and its key are the shaft list's (`docs/shafts.md`).

## Where it is

- `service/autocam_service/bought.py`: the list (`state/bought_parts.json`), what's hardware, the panel's tags.
- `service/autocam_service/shoplist.py`: what the shaft list and this one share (the file, dropping finished ones,
  "Changed in CAD").
- `panel/api.py`: the Bought parts rows, sending, `/api/part-remove`, and `/api/shop/part-ordered`, `part-bin`,
  `part-undo`. `panel/static/panel.js` (the rows), `shop.js` / `shop.css` / `shop.html` (the tab).
- Tests: `tests/service/test_bought.py`.
