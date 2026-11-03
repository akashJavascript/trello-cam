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
  10-32 by default), and **Lathe** steps: Turn down (End 1 or 2, Diameter, Length) and Groove (distance from the
  end; 0.020 per side, fixed). A Standoff with an OD that isn't a bar size gets "Turn to Ø..." from the model.
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

## Not confirmed yet

- **How a real assembly's parts list writes a library shaft's configuration.** The code reads `Length=...` and
  `OD=...` from the row's configuration string, in any unit Onshape uses (meters or inches). Check it on one real
  assembly with library shafts before relying on it.
