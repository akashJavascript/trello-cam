# The Onshape panel ("Send to AutoCAM")

_Phase 1, built 2026-11-01. Code: `service/autocam_service/panel/`. Tests: `tests/service/test_panel.py`._

A page in a Part Studio's right panel in Onshape. A student picks a part (in the panel's list, or by selecting it
in the CAD once Onshape's selection messages are confirmed), sets the quantity and options, and presses Send. A part
card in AutoCAM's own format lands in Ready for CAM (or Drafts), exactly as if it had been typed, and the run
treats it like any other. The panel also shows where each part already is: waiting, on a sheet (with the sheet's
preview), cut, or sent back with the reason.

## What a student sees

- **The parts list:** every solid part in the Part Studio, each with its status (Not sent, In Drafts, Waiting to
  nest, On r034 S2, Cut, Sent back). Parts in a material AutoCAM doesn't cut (belts, PLA, steel) wait behind a
  "more" link.
- **A part with no card yet (or one that was cut):** its material, the quantity, the options and Send.
  - **Quantity** starts at how many of that part the document's assemblies use (the biggest count; a dropdown when
    there's more than one assembly), and can be changed. Only assemblies in the same document are counted.
  - **Options**, in one list: Hold it in with tabs, Nest it with other parts, Face the top, Use the 1/8 in endmill
    (the Tool 1/8 label), Fit (a hole size or the outline), Rush, Smoked (for polycarbonate), Send to Drafts, I CAM'd
    it myself, and notes.
  - **No material in Onshape:** the student picks one, and AutoCAM sets it on the part in Onshape. If Onshape
    refuses (or the tab is a version), the card gets a `Material:` line instead, which the run reads.
  - **Who:** each student types their name once. It's kept for their Onshape user id (`state/panel_users.json`)
    and goes on every card ("Sent from the Onshape panel by ..."). There's no Onshape sign-in (the user's choice).
- **Design checks** (built 2026-11-01): opening a part checks it with AutoCAM's own plate rules (`plate.py`,
  `tooling.py`), the ones a run applies after the STEP export, on its shape from Onshape (`panel/geometry.py`, one
  `bodydetails` call per part per state of the Part Studio, kept on disk). It says either "AutoCAM can cut this" with
  the stock thickness, cutter and features (2 drilled, 20 bored, ...), or what would send it back, each with a fix:
  a thickness that isn't stock (and the nearest that is), chamfers, rounded top edges, 3D faces, pockets from both
  sides, a hole or inside corner too small for any cutter, a pocket with an island, a split top. Notes: sharp inside
  corners, contoured holes (check the fit). A feature that only needs the 1/8 in endmill isn't a problem: it says
  "1/8 in endmill (a 0.130 in hole needs it)" and never asks for a change (the user's choice). Without a material,
  the shape is checked against every stock size until one is picked. Checked against Fusion on P-2011's real
  version: the same thickness, cutter and 2 drilled, 20 bored, 2 bearing, 25 cut out.
- **Cost and sheet use** (2026-11-01): under the check, "Cost: about $21.24 for 4 · 18% of a sheet": the part's
  outline area, plus its share of the gaps (`[costs] nest_efficiency`, 0.7), at WCP's price for the sheet
  (`[costs.sheet_prices]`, read 2026-11-01), or another vendor's where WCP has none (EasyRuler Plastic for 1/16 in
  clear and smoked and 3/16 in clear polycarbonate). A size with no price (1/8 in 5052, 3/16 in smoked) shows only
  the share of a sheet.
- **Marks in the parts list:** a part already checked in this state of the Part Studio shows a tick or a warning
  sign beside its name (no Onshape call).
- **The machine queue** at the top: AutoCAM's sheets in Sheet review and Ready to cut, their cutting time (from the
  sheet cards' titles) and the cards waiting to be nested.
- **Changed since it was CAM'd (the manufacturing version):** every job records exactly what it CAM'd for each card
  (the document, the version or the pinned workspace state, the Part Studio and the part; `state/jobs`). For a part
  on a sheet or cut, the panel compares its shape then (read once and kept for good) with its shape now: "Changed in
  Onshape since it was CAM'd (r034): 2 holes added", or "Onshape still matches what was CAM'd in r034". A sheet not
  cut yet gets one comment saying so, and the panel offers "Nest the new version" (the card goes back to Ready for
  CAM). A moved part isn't a change; a moved hole is.
- **Checked again before it's cut** (2026-11-01, `panel/precut.py`): when one of AutoCAM's sheets reaches Ready to
  cut, the service itself checks each part on it that was CAM'd from a workspace, once per sheet program: one call
  asks the workspace's state; the same state as when it was CAM'd means nothing in that document changed. Otherwise
  the shapes then and now are compared, and a part that really changed gets one comment on the sheet card. The
  panel's calls; out of calls, it's tried again later.
- **"I CAM'd it myself"** (a switch in the options): pick the .tap, the plate's thickness (filled in from the check)
  and the cutter if it's over 1/4 in. The guard checks the program first for what doesn't depend on where it goes
  (codes, inch units, nothing below Z0, the park), so a bad one never reaches Trello. It goes on a card linked to the
  part with Material, Thickness and an "Own program:" line, and AutoCAM places it like any own program (a spot,
  the program moved there, the full check, a card in Sheet review). The card follows the part in the panel. With
  **"I'll place it myself"** AutoCAM doesn't pick a spot: the program goes in Sheet review as it is, with the area
  its cutter covers, for the operator to zero on any piece big enough (a "Place it myself" line on a Trello card
  does the same).
- **CAD changed** (2026-11-01): a part that changed after it was CAM'd shows "CAD changed" in the list and on its
  status, with **Nest the new version** and **Cut it as it is** (the same as ticking the box on its card; either
  takes the CAD changed labels off its card and sheet card). See `docs/decisions.md`.
- **Bent (sheet metal) parts** (2026-11-01): a bent part is listed with "Bent"; its check runs on its flat pattern
  (what's cut) and says how it bends ("Cut flat, then 1 bend: Bend A 90 up (R 0.09 in)", plus a word on 6061).
  Its options add **Scribe the bend lines** (No / With the endmill / With the chamfer mill), written on the card as
  `Scribe: endmill` or `Scribe: chamfer mill` (core 0.18.0; `docs/decisions.md`).
- **Fits by clicking:** clicking a round hole that isn't drilled, or a wall of the outline, in the CAD sets the Fit
  (Onshape face ids from the shape read for the check). Waits on what Onshape's SELECTION messages really hold.
- **A whole assembly:** the second extension (context Assembly, `&kind=assembly` on its link) lists the assembly's
  parts from its bill of materials (one call, subassemblies counted in): this document's parts AutoCAM cuts first
  and ticked (parts from other documents are often bought ones, so they start unticked), with their
  counts, the rest saying why not (no material, not a material AutoCAM cuts, configured, already has a card).
  Send makes a card per ticked part, linking its own Part Studio (a version for parts from other documents), with
  Tabs, Nest, Face and Rush for all of them; a fit is set on one part, from its Part Studio.
- **A part that already has a card:** its status. A second card is refused; the quantity can be changed instead
  (a card already on a sheet goes back to Ready for CAM and is nested again). A card in Drafts can be edited and
  sent; a sent-back card shows why and can be sent again (the same card, made over). Made over, a card starts the
  form from what it says (quantity, labels, Fit, the boxes, notes), and lines someone typed that the panel doesn't
  write are kept below the panel's.

## The card it writes

```
https://cad.onshape.com/documents/<did>/w/<wid>/e/<eid>
Qty: 4
Part ID: JHD
Material: 6061            (only when Onshape doesn't have the material)
Fit: 1.125 hole           (only with a fit)
Sent from the Onshape panel by Akash
Note: deburr it           (notes, on one line)
```

Title: the part's name in Onshape. Labels: Face, Rush, Smoked as chosen. Boxes: Nest and Tabs as chosen. The
`Part ID:` line is new and works on typed cards too: the run finds the part by its id first, so renaming it in
Onshape after sending doesn't break the card.

## Onshape calls

Every call goes through the ledger (run id `panel`) and is counted apart from runs': `[panel]
onshape_calls_per_day` (60) and `onshape_calls_per_year` (1500), beside runs' `yearly_cap_calls` (1500). Both
are shares of the enterprise's 10,000 a year; config-check refuses more than that together.

| What | Calls | Kept |
|---|---|---|
| A Part Studio's parts (opening the panel) | 1 | 10 min (`parts_cache_s`), or until Refresh |
| Quantities: the document's assemblies | 1 + up to 4 (`max_assemblies`) | 30 min, for every part of the document |
| A part's shape, for the design checks | 1 | on disk, until the Part Studio changes |
| Changed since it was CAM'd | 1 for the CAM'd state (kept for good), plus the check's | |
| A whole assembly (its bill of materials) | 1 (+1 for the document's tabs) | 10 min |
| Setting a material | 1 | |
| Status, sending, changing a quantity | 0 (Trello only) | |

When the panel's calls for the day are used up, it says so and students make cards on Trello as before.
`python -m autocam_service ledger` shows the panel's count.

## How it's hosted

- The AutoCAM service serves the panel on `127.0.0.1:8770` (threads of its own process; `[panel] port`).
- A Cloudflare tunnel named `autocam` makes it `https://autocam.team5940.org`. It's separate from
  BreadCompetition's `bread-comp` tunnel (which runs in the laptop's WSL) so neither one's restarts touch the other.
- The tunnel's files are in `C:\dev\autocam-tunnel` on the laptop (not in the repo): `cloudflared.exe`
  (2026.10.3), `config.yml` and the tunnel's credentials. The service starts it with no window, leaves it running
  across its own restarts (`state/panel_tunnel.pid`) and starts it again within a minute if it stops. Its log is
  `logs\cloudflared.log`.
- The page's link carries a secret key: `AUTOCAM_PANEL_KEY` in `.env`. Every request without it is refused. The
  page only loads inside Onshape (or opened directly), never framed by another site.

Made 2026-11-01 from the laptop's existing Cloudflare login (WSL's `~/.cloudflared/cert.pem`):

```
cloudflared tunnel create autocam
cloudflared --config <empty file> tunnel --origincert ~/.cloudflared/cert.pem route dns --overwrite-dns <tunnel id> autocam.team5940.org
```

(Without `--config`, `route dns` uses `config.yml`'s tunnel, BreadCompetition's, whatever tunnel is named.)

## Registering it in Onshape (once, by someone signed in to the team's Onshape)

Onshape's docs: an extension belongs to an OAuth application, even one that never uses OAuth.

1. Go to https://dev-portal.onshape.com and sign in with an account in the 5940 enterprise (an admin, so it can be
   given to the whole team).
2. **OAuth applications > Create new OAuth application.** Name `AutoCAM`; primary format `com.team5940.autocam`;
   summary "Send parts to AutoCAM"; redirect URL `https://autocam.team5940.org/`; the least permission it offers
   (read). The panel never uses the OAuth tokens.
3. In the application: **Extensions > Add extension.** Name `AutoCAM`; location **Element right panel**; context
   **Part Studio**; icon: any square image; Action URL (with the key from the laptop's `.env`):

   ```
   https://autocam.team5940.org/panel?key=<AUTOCAM_PANEL_KEY>&documentId={$documentId}&workspaceOrVersion={$workspaceOrVersion}&workspaceOrVersionId={$workspaceOrVersionId}&elementId={$elementId}
   ```

   Onshape adds `server`, `userId` and `companyId` to it by itself.
4. Make it available to the team: an enterprise admin can assign the app to users or teams in Developer Settings
   (Onshape's docs), or create a private store entry for it and subscribe.
5. For whole assemblies, add a second extension to the same application: the same fields, but context
   **Assembly** and the same Action URL with `&kind=assembly` on the end.
6. Open a Part Studio (or an assembly), open the right panel, and pick AutoCAM.

Changing the key later: change `.env` (the service restarts by itself) and the Action URL together.

## Not confirmed yet

- **Selecting a part in the CAD:** Onshape's docs name the `SELECTION` message but not what's in it. The panel
  takes any id in it that's one of the Part Studio's part ids, and logs the first few messages a day (service.log,
  "Onshape selection message"). Until that's checked, students pick the part from the panel's list.
- **Setting a material:** the request follows Onshape's metadata docs with a material object copied from a part
  that has it. Not yet tried on a live part; if it fails, the card's `Material:` line covers it and the panel says so.
- **Assembly counts** follow Onshape's documented assembly definition (occurrences, subassemblies). Seen working on
  the P-2011 document (2026-11-01: P-2011 x1 in Shooter, 3 calls); not yet on a big robot assembly.
