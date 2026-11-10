# Offcuts from a phone photo (2026-11-09)

The shop page's **Offcuts** tab (`/shop#offcuts`): the rack, and offcuts added or updated from a photo. The mockup the
user approved: https://claude.ai/artifact/JVPafrb9zAH6fbBk1i5Ds6. Code: `service/autocam_service/offcut_photos.py` (the
tab and the cards), `photo_find.py` (corners and holes), `panel/static/offcuts.js` (the page).

## What it records

Nothing new: a photo ends up as the card a person would write by hand, and the service's own offcut pass
(`runner.scan_offcuts`) does the rest, as it always has.

- **A new piece:** a card in Offcuts with `Material`, `Thickness` and `Length` lines and a `Cut by hand` line per box,
  plus the photo. The pass adds it, numbers it and says so on the card; the tab shows "Write #7 on the front end".
- **Re-photo** (someone cut more out of an offcut): only its new boxes, added to its card as `Cut by hand` lines.
- **Gone:** its card archived (an offcut whose card leaves the list is forgotten). Undo for 5 seconds; an offcut a sheet
  waiting to be cut is on can't go.

Boxes are in the piece's own inches: across from the left edge, along from the front end, with the front end toward you.

## The three steps

1. **Photo.** The piece flat on the floor, all of it in the photo with floor round it. "Take a photo" opens the page's
   own camera (2026-11-09): a frame shaped like the piece (front at the bottom) to line it up with, a level that goes
   green when the phone is within 5 deg of flat, and a shutter that waits (up to 2 s) until the phone is still (under
   4 deg/s). Every photo, from there or from "Use the camera app", is checked first: **Too dark** (mean brightness
   under 40 of 255) or **Too blurry** (how sharp its sharpest edges are, under 0.5) asks for it again, with "Use it
   anyway". On the team's 5 photos: sharp ones score 0.75-0.90 and a 1080p video frame about 0.7; hole finding starts
   losing holes (film-filled and small ones first) at about 0.5, which 1 px of blur or a 5 px shake reaches. What the check measured goes to the
   laptop with the photo and into the kept record, so the numbers can be tuned on real photos. No camera access
   (blocked, or an old browser): the camera app, as before. Pick the material and thickness
   and type the **length from a tape**. The width is always the sheet's (full width only: the clamps hold the long
   edges). Re-photo already knows all of it.
2. **Corners.** Suggested when they can be found; drag them onto the piece's corners (a magnifier shows under the
   finger). **The end at the bottom of the photo is the front** (the user, 2026-11-09); "Other end is the front" swaps.
   For a re-photo, line the hatched areas up with what's on the record. When none are found, they start on the camera
   frame the piece was lined up with (photos from the page's camera; 2026-11-09).
3. **Used areas.** The piece straightened to scale, a box over each hole found (0.25 in added round each). Move, resize
   (the corner dot), delete (×), drag on the piece to add one. What's already on record is hatched. **Every hole needs a
   box**: a missed hole is how a part ends up over one; an extra box only wastes some metal.
   - **Dotted "?" boxes are maybes** (2026-11-09): what nearly made it (a hole just under 0.6 in, an outline nearly
     sure, film nearly busy enough). A tap keeps one; the rest are ignored. Not beside a box or at the piece's edge.
   - **Tap a hole it missed** and the laptop boxes it (`photo_find.grow_at`, about half a second): by look where the
     photo has it; else the hole grown from the tapped spot, less keenly each step, kept from just before it floods the
     metal (a hole fills to its edges and holds; the metal floods at once). A tap on the piece itself, or where no hole
     can be told, says so: drag a box. On the team's photos it boxed the film-filled holes on the table, the poly's
     small holes and most of the house holes on bare aluminum on concrete (some boxes short or covering two holes).
   - Where each saved box came from (found, maybe, tapped, drawn) is kept with the photo; the report counts them.

## How the finding works (tuned on the team's photos)

- **Corners:** two tries (bright smooth film, or anything unlike the photo's own border); each outline is snapped onto
  the photo's straight edges, and one is kept only if its shape fits the size and all four sides run along edges. On
  bare aluminum on concrete none is: people place them.
- **Holes "by look"** when the floor and the piece look different (film-covered poly on concrete, bare aluminum on a
  wood table): the photo teaches itself what each looks like, from the margin and the piece's rim.
- **Holes "by outline"** when they don't (bare aluminum mirrors concrete: same brightness, colour and grain): only the
  long cut edges tell, so only sure holes get a box. Expect to add many by hand; **aluminum on the plywood table** is
  found well.
- **Holes still full of crumpled film and chips** (2026-11-09, the user's aluminum-on-the-table photo: 11 of them were
  missed): they look like the metal in colour but are busy (creases, chips) where bare metal and wood are smooth, so
  busy patches clearly busier than the floor also get a box ("by look" photos only; not within 1/2 in of the edges, not
  paper-white). That photo: 8 of the 11 boxed, 2 partly, 1 missed (smooth clear film over wood). Pulling the film out
  first still works best.
- Misses: smooth clear film over a hole, bare clear poly with no film (see-through); print and marker are ignored on purpose.

## Kept to measure and tune the finding (2026-11-09)

Each photo that's saved is kept on the laptop in `state/offcut_photos/kept/<when>-<id>/`: the photo, and
`record.json` with the stock, the corners and boxes AutoCAM suggested and the ones people saved (no names). The
newest 500 (`[offcut_photos] keep`; 0 keeps none, about 0.5 MB each). `python -m autocam_service offcut-photo-report`
compares them, per way of finding:

- **found / missed:** a box people saved is found when the suggested boxes cover at least half of it. Missed holes are
  the number to bring down.
- **extra:** a suggested box people deleted (or moved far).
- **corners right:** all four suggested corners within 1/2 in of where people left them.

The report shows which photos missed most, so tuning works on the team's photos instead of a handful, and the saved
boxes are labelled examples if a trained finder is ever worth it (after about 100 photos).

Needs OpenCV (`pip install -e ".[photos]"` on the laptop). Without it the tab still works with nothing suggested.
Photos are kept in `state/offcut_photos/` for a day. `[offcut_photos] enabled` switches the tab off.
