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

1. **Photo.** The piece flat on the floor, all of it in the photo with floor round it. Pick the material and thickness
   and type the **length from a tape**. The width is always the sheet's (full width only: the clamps hold the long
   edges). Re-photo already knows all of it.
2. **Corners.** Suggested when they can be found; drag them onto the piece's corners (a magnifier shows under the
   finger). **The end at the bottom of the photo is the front** (the user, 2026-11-09); "Other end is the front" swaps.
   For a re-photo, line the hatched areas up with what's on the record.
3. **Used areas.** The piece straightened to scale, a box over each hole found (0.25 in added round each). Move, resize
   (the corner dot), delete (×), drag on the piece to add one. What's already on record is hatched. **Every hole needs a
   box**: a missed hole is how a part ends up over one; an extra box only wastes some metal.

## How the finding works (tuned on the team's photos)

- **Corners:** two tries (bright smooth film, or anything unlike the photo's own border); each outline is snapped onto
  the photo's straight edges, and one is kept only if its shape fits the size and all four sides run along edges. On
  bare aluminum on concrete none is: people place them.
- **Holes "by look"** when the floor and the piece look different (film-covered poly on concrete, bare aluminum on a
  wood table): the photo teaches itself what each looks like, from the margin and the piece's rim.
- **Holes "by outline"** when they don't (bare aluminum mirrors concrete: same brightness, colour and grain): only the
  long cut edges tell, so only sure holes get a box. Expect to add many by hand; **aluminum on the plywood table** is
  found well.
- Misses: holes still full of crumpled clear film and chips read as the piece (pull the film out first), bare clear
  poly with no film is see-through, print and marker are ignored on purpose.

Needs OpenCV (`pip install -e ".[photos]"` on the laptop). Without it the tab still works with nothing suggested.
Photos are kept in `state/offcut_photos/` for a day. `[offcut_photos] enabled` switches the tab off.
