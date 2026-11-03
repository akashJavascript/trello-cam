# The shaft list (2026-11-02)

Shafts are configured parts from the team's common parts library
(document `dee14b205b4629c493a3df74`): **Hex Shaft** (1/2 hex), **3/8 Hex shaft**, **3/8 hex lite** (3/8 hex
with a 0.159 hole through it) and **Standoff** (round: OD 0.375 is 3/8 round, OD 0.5 is 1/2 round). They're cut on
the horizontal bandsaw, sometimes turned and grooved on the lathe, and tapped with a drill at a separate station.
Spline XL, Hex Spacer, the spline spacers and "3d printed standoff" are printed, so they aren't on this list.

No Trello: the Free plan stops at 10 members, and everyone who makes shafts would need a seat. Shafts go on a phone
page instead, at `https://autocam.team5940.org/shop`.

## Sending them (the Onshape panel, Assembly tab)

- An assembly's library shafts are listed under **Shafts**, apart from its plates (they're 6061, so before this they
  showed up as plates). Each row is the stock and finished length, like `1/2 hex, 18.250 in`; shafts are never
  named. Identical ones (same tab, same length) are one row with the assembly's count.
- The pencil opens a shaft: quantity, **Tap** for each end (10-32, 1/4-20, Other with a size typed in, None;
  hex shafts start at 10-32, standoffs start untapped: they're bought-style spacers, the user), **Lathe** steps:
  Turn down (End 1 or 2, Diameter, Length) and Groove (distance from the end; 0.020 per side, fixed), and a
  **Note** that goes on the card. A Standoff with an OD that isn't a bar size gets "Turn down to Ø0.313 from 3/8
  round" from the model (the next bar up).
- **Click a part in the assembly** and its row in the list lights up (plates and shafts). Onshape's SELECTION
  message gives the occurrence path; its last id is the part's instance, looked up in the assembly's definition
  (one call, kept 30 minutes; read again once if the part is new).
- Send sends ticked plates and shafts together. **Rush** applies to both; **Send plates to Drafts** only to plates.
  After sending, a shaft row shows nothing while it waits, then **In progress** or **Done**.
- A waiting shaft can still be changed (the pencil) or taken off the list (Take it off the list). Once someone
  starts it, neither.
- **What can't be made** stops Send until it's fixed: a turn-down that isn't smaller than the stock (1/2 hex to
  0.500 is fine: that takes the corners off), a turn-down or groove past the end, turn-downs on both ends that
  overlap, 3/8 hex lite turned into its hole, a Standoff wider than 1/2 in.
- **Changed in CAD:** when the assembly is opened again and a shaft on the list isn't in it any more (its length
  changed, or it was taken out), that shaft gets "Changed in CAD" on the phone and in the panel. No extra Onshape
  call: it's the assembly's parts list the panel reads anyway.

## Making them (the phone page)

- One card per shaft: **Length** (finished, three decimals like the calipers; people add their own saw allowance),
  the assembly, quantity, and one short line per step ("Grooves 0.600 from both ends, 0.020 per side",
  "Tap 10-32 both ends"). The icon at the top opens the assembly in Onshape.
- **Start** marks it in progress, **Stop** puts it back, **Done** finishes it (Undo, the same day, from the phone
  that did it).
- Order: **Yours** (started on this phone) at the top, then **Rush**, then each stock (1/2 hex, 3/8 hex, 3/8 hex
  lite, 1/2 round, 3/8 round), then **In progress** (started on other phones), then **Done today**.
- No names: each phone keeps a random id, and "Yours" is what that phone started. If two phones tap Start at once,
  the second is told "Someone already started this".
- It refreshes every 10 seconds, reading only the list on the laptop (no Onshape or Trello calls).
- Done shafts drop off after 14 days (`[shafts] keep_done_days`).

## The link and its key

The page's key is worked out from the panel's key (`shop_key()` in `panel/api.py`), so there's nothing new in
`.env`, and the shop's link can only start and finish shafts: it can't send cards or read anything else. To make
the page to print for the saw and the lathe, on the laptop:

    pip install segno          (once)
    autocam shop-qr            writes state/shop-qr.html (the link goes only into that file)

## Where it is

- `service/autocam_service/shafts.py`: reading a shaft off the assembly's BOM row (its configuration string has the
  Length and OD), the card's wording, what can't be made, and the list (`state/shafts.json`).
- `panel/api.py`: the Shafts rows, sending, `/api/shaft`, `/api/shaft-remove`, and `/shop` + `/api/shop/*`.
- `panel/static/shop.html`, `shop.js`, `shop.css`: the phone page. `panel.js`: the Shafts rows and the editor.
- `[shafts]` in `config/autocam.toml`: the library document, its shaft tabs, the default tap, the groove depth.
- Tests: `tests/service/test_shafts.py`.

## Checked on a real assembly (2026-11-02)

The Shooter assembly's BOM (one call): 23 library rows. Lengths come in meters with float noise
(`Length=0.19843750000000004+meter`, HEX-0.5-7.8125), and 3/8 hex lite's with no unit (`Length=3.5`, inches). Every
Hex Shaft, 3/8 Hex shaft, 3/8 hex lite and Standoff was read right; Hex Spacers, Spline XS spacers, pulleys and the
3d printed standoff were left out. Lengths are rounded halves up (7.8125 is 7.813, the same on the phone page).

**Standoffs start untapped** (the user), whatever their hole (the Shooter has 0.156, 0.196-0.197 and 0.203). The
hole comes with the bar ("no one drills it"), so it's no step on the card.

**Bought parts and the library's prints aren't plates.** The Shooter's wheels and sushi rollers (Polycarbonate),
nut strip and spline adapter (6061) come from vendor libraries: public documents owned by the MKCad account, not
by Team 5940 BREAD (`[panel] company_id`). A part from a document the team doesn't own is a "Bought part"; a part
from the common parts library that isn't a shaft (PLA-STNDF, set to 6061) is a "Library part". Neither is offered
or sent. Each document's owner is one call, read once and kept in `cache/document_owners.json`.
