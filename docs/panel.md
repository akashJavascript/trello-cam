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
  - **Options**, in one list: Hold it in with tabs, Nest it with other parts, Face the top, Fit (a hole size or the
    outline), Rush, Send to Drafts, Smoked (for polycarbonate), and notes.
  - **No material in Onshape:** the student picks one, and AutoCAM sets it on the part in Onshape. If Onshape
    refuses (or the tab is a version), the card gets a `Material:` line instead, which the run reads.
  - **Who:** each student types their name once. It's kept for their Onshape user id (`state/panel_users.json`)
    and goes on every card ("Sent from the Onshape panel by ..."). There's no Onshape sign-in (the user's choice).
- **A part that already has a card:** its status. A second card is refused; the quantity can be changed instead
  (a card already on a sheet goes back to Ready for CAM and is nested again). A card in Drafts can be edited and
  sent; a sent-back card shows why and can be sent again (the same card, made over).

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
onshape_calls_per_day` (40) and `onshape_calls_per_year` (1500), beside runs' `yearly_cap_calls` (1500). Both
are shares of the enterprise's 10,000 a year; config-check refuses more than that together.

| What | Calls | Kept |
|---|---|---|
| A Part Studio's parts (opening the panel) | 1 | 10 min (`parts_cache_s`), or until Refresh |
| Quantities: the document's assemblies | 1 + up to 4 (`max_assemblies`) | 30 min, for every part of the document |
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
5. Open a Part Studio, open the right panel, and pick AutoCAM.

Changing the key later: change `.env` (the service restarts by itself) and the Action URL together.

## Not confirmed yet

- **Selecting a part in the CAD:** Onshape's docs name the `SELECTION` message but not what's in it. The panel
  takes any id in it that's one of the Part Studio's part ids, and logs the first few messages a day (service.log,
  "Onshape selection message"). Until that's checked, students pick the part from the panel's list.
- **Setting a material:** the request follows Onshape's metadata docs with a material object copied from a part
  that has it. Not yet tried on a live part; if it fails, the card's `Material:` line covers it and the panel says so.
- **Assembly counts** follow Onshape's documented assembly definition (occurrences, subassemblies). Seen working on
  the P-2011 document (2026-11-01: P-2011 x1 in Shooter, 3 calls); not yet on a big robot assembly.
