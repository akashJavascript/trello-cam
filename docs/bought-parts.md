# The bought-parts list (2026-11-02)

The same idea as the shaft list (`docs/shafts.md`), for parts the team buys: wheels, rollers, bearings, motors,
belts. They're sent from the Onshape panel with an assembly's plates and shafts, and checked off on the phone page's
**Parts** tab: **In the bin** when the team has one (it goes in that subsystem's bin), **Order** when it has to be
bought.

**AutoCAM never writes to the order sheet** (the user: people write the row themselves, so everything is checked
before money is spent). Order opens the team's Google Sheet in a new tab and copies the part's name (the sheet's
Description column; its Name column is who asks); the card then asks "Added 2 to it?", and only **It's on the
sheet** counts them as ordered.

**Counts, not just a state** (the user, 2026-11-02): a part needs a quantity; some of it can be in the bin, some on
the order sheet, the rest still needed. "2 of 4 in the bin", then Order orders the other 2.

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
- After sending, a row shows a check and where it's at: ×4 (all still needed), **2 of 4 in bin**, **Ordered** (the
  rest are on the sheet); "In the bin" under its name once all of it is, ticked off and sendable again.
- **The pencil:** the quantity and a **note** shown on the phone's card ("The flanged ones, not the plain ones").
  Before sending, it sets what Send sends; after, it changes the part on the list (never fewer than are in the bin),
  and **Take it off the list** while none are in the bin or ordered.
- **CAD's count changed:** when the assembly's count isn't the one the part was sent with (4 wheels became 6), the
  row says "CAD has 6 now · **Make it 6**" and the phone card has a red "CAD has 6". The list never changes by
  itself (someone may have sent a different number on purpose); Make it 6, or the pencil, settles it. A part all in
  the bin gets it too, since more may be needed now.
- A part taken out of the assembly gets **Changed in CAD**, in the panel and on the phone, like a shaft.
- Clicking a row lights the part up in Onshape, and clicking the part in Onshape lights up its row (the same as
  plates and shafts).

## The phone page (`/shop`, Parts tab)

- **Shafts | Parts** at the top. The last tab used is remembered on the phone; a link ending in `#parts` opens on
  Parts (for a QR code at the bins later).
- **<Assembly> bin:** what each subsystem still needs (the assembly it was sent from; the user sends from
  subassemblies), one card per part with how many are still needed: **Order** (only with an order sheet link) and
  **In the bin**. More than one: In the bin asks "How many went in the bin?" (all of them to start).
- **On the order sheet:** ordered and waiting to come in, with "ordered 9 days ago" (orange from 14 days).
- **In the bin today:** each In the bin (×2 of 4), with **Undo** on the phone that did the part's latest one, the
  same day.
- The vendor shows under the name when the BOM has a Vendor column, or "McMaster" for a McMaster number.
- Parts in the bin drop off after 14 days (`[parts] keep_days`).

## Setup

    [parts]                       config/autocam.toml
    enabled = true
    keep_days = 14

    AUTOCAM_ORDER_SHEET_URL=...   the laptop's .env (set 2026-11-02)

The order sheet's link goes in `.env`, not the config: it isn't a secret, but the repo is public and the sheet has
students' names. (`[parts] order_sheet_url` works too and wins.) Only a Google Sheets link is taken; "/u/3/" (one
person's third Google account) and "#gid=" are taken off so it opens right on every phone. With no link there's no
Order button. The link is only opened on the phone; AutoCAM doesn't read the sheet or need access to it. The shop
link and its key are the shaft list's (`docs/shafts.md`).

## Where it is

- `service/autocam_service/bought.py`: the list (`state/bought_parts.json`), what's hardware, the panel's tags.
- `service/autocam_service/shoplist.py`: what the shaft list and this one share (the file, dropping finished ones,
  "Changed in CAD").
- `panel/api.py`: the Bought parts rows, sending, `/api/part-remove`, and `/api/shop/part-ordered`, `part-bin`,
  `part-undo`, `/api/part` (the pencil, Make it 6). `panel/static/panel.js` (the rows, the pencil), `shop.js` /
  `shop.css` / `shop.html` (the tab).
- Tests: `tests/service/test_bought.py`.
