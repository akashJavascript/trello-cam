# Status

_Last updated 2026-11-05 (branch `main`)._

## Where things are

| Milestone | State |
|---|---|
| M0 scaffolding, config, `.env` | Done, on `main` |
| M1.0 dump_params / api_probe / smoke test / probes | Done: api_probe, dump_params, the smoke test (stopped at Arrange), `pipeline_probe` and three `pipeline_probe2` runs. Every call the through-cut pipeline needs has been seen working. Results: `docs/fusion-api-status.md` |
| M1.1 pure core | Done and unit-tested |
| M1.2-M1.7 Fusion adapters + pipeline | **Working in Fusion for through-cut aluminum plates** (2026-10-01): drilled, bored and bearing holes, cutouts, several parts per sheet, pauses, the guard and a check that the tool never enters a part. Still to run in Fusion: several sheets, the 1/8 in tool, polycarbonate. Pockets work in Fusion (r024). Fusion Team save (M1.7) **works**: jobs save to 5940 BREAD / AutoCAM test with a file link |
| M2 add-in hot-folder worker | Written: `fusion/autocam_addin/` (job loop in `autocam_worker/worker.py`, tested offline, including a full service run through it); **works in Fusion** (2026-10-01: a queued job ran by itself into `done/`). Still worth doing once on the shop PC: kill Fusion mid-job and check the retry (`docs/manual-tests.md` → M2, checks 4-5) |
| M3 service: Trello read + Onshape export + jobs | **First real run worked** (2026-10-02, board https://trello.com/b/fsuTYbO3/5940-autocam): a real card -> Onshape STEP export -> job. It found and fixed: Markdown around `Qty:`, and an Onshape 400 crashing the pass (now that card goes to Needs fixing) |
| M4 service: results back to Trello | **First real run worked** (2026-10-02): the sheet card with the program came back to Sheet review |
| Board flow (2026-10-02) | Runs start by themselves 2 min after cards land in Ready for CAM; "Nest this part" box; new parts fill sheets nobody has started reviewing (cards updated in place). Tested offline; first real run (r005) published fine. **Options** on sheet cards since 2026-10-02: cut without stopping, and an air test (a raised copy that cuts nothing). Tested offline and on r005's real program; the service applies them on the real board. The service restarts itself into new code (seen working twice) |
| M5 ops | **Built 2026-10-02:** the System card's description is a live status (Fusion alive, run, waiting cards, Onshape budget, last error), with one comment when jobs wait for a closed Fusion; one service at a time (a lock); `ops/install-autostart.ps1` starts the service and Fusion at logon (dry run checked; not installed yet). Checks in `docs/manual-tests.md` → M5 |
| M6 tabs | **Built 2026-10-03** (tape alone didn't hold parts in the team's trials): a "Hold it in with tabs" box on part cards, ticked by default (`[tabs] default`); the template's tabs on that part's outline and every cutout, 2 to 6 per contour by its length, triangular (from the template), placed on straight edges clear of corners where possible, at points. Works in Fusion (test jobs); not cut yet |
| Shaft list (2026-11-02) | **Built, tested offline and in a local browser**: library shafts sent from the Onshape panel go on a phone page (`/shop`) instead of Trello. `docs/shafts.md`. Read right off the Shooter assembly's real BOM (2026-11-02) |
| Watched assemblies (2026-11-02) | **Built, tested offline**: assemblies with things on the shop's lists, or with cards for parts from another document's saved version, are read again in the background after Onshape says they changed (at most hourly): "Changed in CAD" and "CAD has 6" without opening the panel, and version cards follow their assembly to newer versions (CAD changed on an uncut sheet). `panel/assemblies.py`, `docs/panel.md` |
| Shop sign-in (2026-11-04) | **Built, tested offline and in a local browser with a pretend Slack**: the phone page is opened by signing in with Slack (team workspace only, the name not the email); the Onshape panel keeps its key. Waiting for the Slack app's id and secret in the laptop's `.env` (`docs/panel.md`); until then the key link works as before, and for a week after the first sign-in. `panel/signin.py` |
| Clamps in Fusion's simulation (2026-11-04) | **Works in Fusion** (r086-r091, core 0.25.0): each sheet's setup (and its FACE, SMALL, SCRIBE, fit-lap setups) has the clamp strips as fixtures, blocks the clamp height on the plate, so a simulation shows a cutter hitting a clamp; measured after Fusion makes them; the programs are byte-identical with and without (r088 vs r089). Not on parts placed by hand. `[clamps] in_fusion`. Left: see it in a simulation (`docs/manual-tests.md` section 22) |
| Hand-made programs checked (2026-11-04) | **Built, tested offline**: every hand program (own programs AutoCAM places, "Place it myself", and the new **Check** label, which only checks) gets AutoCAM's guard plus the mist for its material and safe stops (`program_checks.py`, `pauses.stop_problems`). A Check card gets a comment saying it passes or why not; nothing is placed. The Check label is on the board. Left: manual-tests section 23 |
| Fits, outlines last (2026-11-04) | **Works in Fusion** (r092, core 0.26.0): a hole fit's part stays solid in the sheet while its hole is lapped; `<sheet>_OUTLINES` cuts it out after the laps (the card's LAST line). `[fit] outlines_last`. Left: a real cut (manual-tests section 24) |
| Machine screen (2026-11-05) | **Built, tested offline and clicked through in a local browser**: the operator's screen on the ShopSabre's PC (`/machine`, `docs/machine.md`): the sheets to cut, then one step at a time; every button is a box on the sheet card or the card moving. Left: open it on the ShopSabre's PC (`machine-link`), manual-tests section 25 |
| Straight rows (2026-11-05) | **Works in Fusion** (r099 for a part drawn square, core 0.27.0; r103 for one drawn at 11 deg, core 0.28.0): after Fusion nests a sheet, its parts are also laid in straight rows (each row placed by Arrange in an area as deep as its deepest part), and the layout that leaves the better offcut is kept. A part lying off square is laid along its longest straight edge and turned on until its smallest rectangle is square (`setRotationUsingEdge` + `rotation`; Arrange won't turn it by itself, r098). `rows.py`, `[nest] straight_max`. Left: manual-tests section 26 on a real sheet |
| Parts laid square (2026-11-05) | **Built, tested offline** (core 0.29.0): a probe before nesting finds each part drawn off square in CAD and how to lay it square (its longest straight edge along X, then turned on, checked both ways), and every Arrange is told. `[nest] square_parts`. Left: see it in Fusion (manual-tests section 28) |
| Offcuts on the rack (2026-11-05) | **Built, tested offline** (core 0.28.0): only the offcut from today's last cut, with no other sheet loaded on the machine screen since, counts as on the bed; an offcut on the rack is loaded whichever way round gives more room, and ties go to the smallest offcut. Left: manual-tests section 27 |
| Panel options explained (2026-11-04) | **Live**: one plain line under each option in the Onshape panel (Rush, Fit, tabs, nesting, Drafts...) |
| Bought-parts list (2026-11-02) | **Built, tested offline and in a local browser**: an assembly's bought parts (no screws or nuts) sent from the panel go on the phone page's Parts tab: In the bin, or Order (opens the team's order sheet; AutoCAM never writes to it). `docs/bought-parts.md`. Counts (some in the bin, order the rest), "CAD has 6", order age and notes added the same day; the sheet's link is in the laptop's `.env` |

M1.1 pure core covers:
- the `.tap` guard and pause insertion/verification;
- holes, plate rules and tool choice;
- the fixture, layout and ordering;
- the job/result schemas and the hot folder.

## Independent safety review (overnight)

A separate review pass tried to break the safety rules: no Z below 0 reaches Trello, nothing auto-moves to
Ready to cut, pauses are safe, the Onshape budget can't be undercounted, and restarts don't duplicate writes.
- **What held:** it found no way to upload a below-floor program and no automated path to Ready to cut.
- **What it found:** 13 other defects, including a crash loop that could have burned the Onshape budget,
  the service trusting the worker's cut plan, line-ending tricks, and the G-code allowlist acting as a
  denylist.
- **Status:** all 13 are fixed, each with a regression test (commit `edf3b46`). The rules that came out of it
  are listed in `docs/decisions.md`.
- **Re-check:** a second pass confirmed the fixes and found 3 new problems the fixes introduced (run-id reuse
  after a wiped `state/`, a run stuck behind a failing Trello write, a zero-job run losing its comment). Those
  are fixed and tested too.
- **One rule to confirm:** "no sideways rapid below the stock top" assumes templates use full retraction.
  Check it on real Fusion output in M1.2.

**2026-10-03: sheets are now laid out in the machine's axes** (X across, Y front to back). Programs from
r005-r007 were turned 90 degrees for the ShopSabre: don't cut them (`docs/decisions.md`).

## What's left (2026-10-31)

### Before the first real cut
1. **Z:** settled (2026-10-31): the ShopSabre's fixed tool setter zeros to the top of the spoilboard, which is
   every program's Z0 (decision 22). Recalibrate it after resurfacing the spoilboard: the programs stop at Z0, so
   a Z0 above the new surface leaves parts uncut. Never zero on top of a sheet.
2. **Tabs trial:** cut a sheet with tabbed parts (triangular, 0.157 in wide, 1 mm high) and check the parts
   and slugs stay put and the tabs break out cleanly (tape alone didn't hold parts in the team's trials).
3. **Pause test** on a sheet with 2+ parts: tick "Add an air test program" under Options and run it
   (`docs/manual-tests.md` → M1.6). WinCNC has no M0 (found 2026-11-03): the stop is a bare G4, which waits
   for ENTER by the manual. Check that it stops, and that ENTER resumes.
4. **Confirm the clamp values** and the other `[assumed]` entries in `config/autocam.toml` (`autocam
   config-check` lists them): clamp reach, clearance and height decide where parts may go.
5. A **corner stop** on the bed (the user's idea): every sheet and offcut square, in reach and at one zero.

### Still to build
- **Placed by hand** (built 2026-10-03, tested offline, untested in Fusion): "Nest this part" unticked gives a
  part its own program, long side along X, zeroed at its box's front-left corner, run once per copy. Its first
  real card (P-032, renamed P-2015) waits on pockets.
- **Templates** (2026-10-31): all four exist. `alu_eighth`, `poly_4mm` and `poly_eighth` are made from `alu_4mm`
  (`fusion/tools/make_templates/`) and work in Fusion: r025 (poly 4 mm), r027 (both 1/8), every check passed.
  Polycarbonate and the 1/8 in endmill are no longer held back; none has been cut yet.
- **Pockets** (2026-10-31): `[pocket]` (2D Adaptive) clears each floor, `[pocketfinish]` (2D Contour) finishes its
  walls, both before the holes; `alu_4mm` has both ops. **Work in Fusion** (r024, P-2015): the pocket selection
  took, the adaptive stayed in the pocket, the finish pass ran inside the wall, every check passed. Not yet
  simulated by eye or cut (`docs/manual-tests.md` → M3 section 14, row 3). The other three templates have both ops. Islands in pockets still go to manual CAM.
- **0.3 in tabs** (2026-11-02, test runs r054-r066 on tsize1's plates): they work when every tab on the sheet is on a
  straight edge (r060, r063, r064); a tab on a curve crashes Fusion's stock simulation at either width (r059, r061,
  r062, r065). Built: wide tabs only where their ramp fits on one straight run, and a per-setup rule (0.3 in only if
  every tabbed contour fits on straight edges). Small curvy cutouts (up to 2 in) are cut free so the rest of the sheet
  keeps 0.3 in (the user's pick); on since r067 passed. Not cut yet with 0.3 in tabs. After a crash, Fusion's
  "Recovered Documents" window blocked every later generation; the add-in now closes it (seen working, r066).
- **Adapters:** the user's, later.
- **M5, on the scouting laptop** (the shop PC since 2026-10-30, reached over Tailscale): autostart tasks
  installed (the service in the background, no window, since 2026-10-31), the add-in on Run on Startup, Windows
  signs in by itself. Left: the reboot test
  (`docs/manual-tests.md` → M5). The laptop's Fusion runs on the user's Autodesk account, so Fusion opened
  elsewhere suspends it (one session per account); a separate account for the laptop would end that.

### Built, not yet seen on the machine or the real board
- **Bent (sheet metal) parts** (core 0.17.0, 2026-11-01): a run reads the Part Studio's parts with
  `includeFlatParts`; a part with a flat pattern is exported as its flat (the bent part's id stays on record) and its
  bend table (`.../sheetmetal/bendtable`) goes in the job, so the sheet card has a "BEND AFTER CUTTING" section and the
  cut order marks it bent. A flat's top and bottom are split along its bends (seen on a real one): any top split into
  faces in one plane is now cut as one outline (fx_geometry.outline_coedges, a chain cut outside, like a cutout's loop
  the other way round), with tabs and labels on that outline; before, it went to manual CAM. No 3/32 stock (the user).
- **Scribed bend lines** (core 0.18.0, 2026-11-01): a bent part's card can say `Scribe: endmill` or `Scribe: chamfer
  mill` (the panel's "Scribe the bend lines"; off unless asked). Each bend's centerline is found on the flat's split
  top (`core/autocam_core/scribe.py`) and scribed 0.012 in deep (`[scribe]`; the user's 10 to 15 thou): with the
  sheet's endmill as a `[scribe]` op in the sheet's program, before the outlines, or with the 45 degree chamfer mill
  (T16) in a `<sheet>_SCRIBE` program run before the sheet's. Seen in Fusion on Part 13: r044 (endmill, within
  0.0009 in of the line) and r047 (chamfer, within 0.0001 in, guard passed, attached and in RUN). Never holds a sheet
  up: a part whose lines can't be found or scribed gets "mark them by hand". Not cut yet. Beside each line a U or D
  says which way the flange goes from the scribed face (r050: Part 13 lay bottom-up, so its "down" Bend D got a U),
  where a letter fits clear of holes, edges and bends.
- **The Onshape panel, Phase 1** (2026-11-01, `docs/panel.md`): "Send to AutoCAM" in a Part Studio's right panel
  makes part cards in AutoCAM's format (Ready for CAM, or Drafts), with the quantity counted from the document's
  assemblies, the options in one list, the material set in Onshape when it has none, and each part's status
  (waiting, on a sheet with its preview, cut, sent back and why). Served by the service on 127.0.0.1:8790, public as
  https://autocam.team5940.org through its own Cloudflare tunnel; its own Onshape call limits. Tested offline (an
  e2e test sends a card through a whole run) and in a browser with fakes. Registered in Onshape 2026-11-01 (app
  `AutoCAM (Team 5940)`, Part Studio and assembly extensions, private App Store entry for all enterprise users;
  `docs/panel.md`). Seen working 2026-11-01 on the shooter document: selection (parts, and faces/edges of parts
  opened before), design checks, quantities, the Shooter assembly (20 plates with BOM counts). Left: the material write. A `Part ID:` line (new, written by the panel, works on typed cards too) finds a part by its id
  before its name.
- **Panel design checks** (2026-11-01): opening a part runs AutoCAM's own plate rules on its shape from Onshape
  (one `bodydetails` call per part per Part Studio state) and says "AutoCAM can cut this" or what would send it back,
  with a fix. The 1/8 in endmill is a fact, never a problem. Matches Fusion on P-2011's real version.
- **Panel, second batch** (2026-11-01, `docs/panel.md`): changed since it was CAM'd (each job's record of what it cut
  is the manufacturing version; a changed part's uncut sheets get a comment, and the panel can nest the new
  version), sending a whole assembly from its BOM, marks in the parts list, the machine queue, cost and sheet use
  (WCP's sheet prices in `[costs]`, 70% of a sheet used), and fits by clicking a hole (waits on Onshape's selection
  messages).
- **Panel, third batch** (2026-11-01): sheets in Ready to cut are checked by the service for parts changed since they
  were CAM'd (one call per document when nothing changed; a comment on the sheet when a part did), and "I CAM'd it
  myself" sends a part's own program from the panel (guard-checked, on a card linked to the part; an "Own
  program:" line marks it for own_programs).
- **Onshape webhooks and part pictures** (2026-11-01, the user is an enterprise admin): a webhook per document with
  parts on a sheet on the board tells AutoCAM within minutes when a part changed (CAD changed); each part card gets
  Onshape's picture of the part as its cover. Both seen working (registration, a ping through the tunnel, Part
  13's picture); a real edit's event still to be seen. `AUTOCAM_WEBHOOK_SECRET` in the laptop's `.env`.
- **Configured parts** (2026-11-01, from the old panel): the configuration rides in the card's link and goes with
  every Onshape read and export; each configuration is its own part and card; the panel reads `{$configuration}`
  (its Action URL needs `&configuration={$configuration}`), and assemblies send configured parts. Not yet seen on a
  real configured plate.
- **Faster refresh** (2026-11-01, the user asked): the service reads Trello every 15 s (was 60), runs start 1 min
  after the last card (was 2), the open panel refreshes its statuses every 15 s without Onshape calls, and CAD
  changed checks run 2 min after an edit stops (was 5). The System card shows the whole team's Onshape API count from
  the admin Developer page's endpoint, once a week (AutoCAM's keys can read it).
- **More than one cutter per part** (core 0.19.0, 2026-11-01): small through holes (an 8-32's 0.136) and cutouts
  with corners too tight for the 4 mm go in a short `<sheet>_SMALL` program with the 1/8 in, run first; the sheet
  stays on the 4 mm, outlines last. Only a tight outline or pocket corner (or the Tool 1/8 label) still puts a
  sheet on the 1/8. **Works in Fusion** (r052: P-2011 with 0.136 holes, the 1/8 drilled just those); not cut yet.
- **CAD changed** (2026-11-01, from the teammate's panel): a part that changed in Onshape after it was CAM'd, on a
  sheet not cut yet, gets the **CAD changed** label on its card and its sheet card, and on its card a box "Cut it as
  it was CAM'd" (or the panel's "Cut it as it is"). Ticking it, or nesting the part again, takes the labels off; a part
  cut with it unsettled keeps its label (what was cut is the old version). Found by the panel, by sheets reaching
  Ready to cut, and now by sheets in Sheet review every 12 h (`[panel] change_check_hours`; one call per document
  state). Tested offline.
- **Own programs placed by hand** (2026-11-01): a "Place it myself" line (or the panel's "I'll place it myself") puts
  someone's own program in Sheet review as it is, guard-checked for what doesn't depend on where it's cut, with the area
  its cutter covers; nothing is recorded on an offcut at Cut. Tested offline.
- **Fit series** (2026-11-01): a part card's `Fit: 1.125 hole` or `Fit: outline` line gets that feature cut 0.003 tight,
  with lap programs taking 0.001 more each, run one by one at the machine; the run that fit is ticked on the sheet card
  and the next series starts there. Holes over 1.5 in too (cut as cutouts: the slug comes out by hand, then the laps,
  which have no tabs). **Works in Fusion** (r034, r036, r038; no dialogs since r036); not cut yet
  (`docs/manual-tests.md` → M3 section 17). `[fit] enabled` switches it off. **Outlines last** (2026-11-04, core
  0.26.0, works in Fusion r092): a hole fit's part stays solid in the sheet while its hole is lapped, and
  `<sheet>_OUTLINES` cuts it out after (`[fit] outlines_last`; manual-tests section 24). Fits on parts placed by hand:
  not yet.
- **Bearing holes** (2026-11-01): their own `[bearing]` op in every template, the bore with a 0.005 in finishing lap;
  press fits tuned in config (`holes.bearing_stock_in`, radial: + tighter, - looser). **Work in Fusion** (r030, r031:
  0.002 in made the hole 0.004 smaller across); none cut yet. Tune it from the first real bearing's fit.
- **Faced parts** (2026-11-01): a **Face** label on a part card skims its top 0.005 in clean in a FACE program of the
  sheet's own, run first with the 6 mm 3-flute ALU (T2), then the sheet's program. Checked both sides: no deeper
  than the face depth, the cutter over faced parts only. **Works in Fusion** (r029: Z0.1825 on 3/16, the whole
  top covered, every check passed); not simulated by eye or cut yet (`docs/manual-tests.md` → M3 section 15).
- **Part labels on the preview** (2026-10-04): numbered badges and names, in the middle of parts that are
  mostly cutouts. Work in Fusion (test jobs lbl1, lbl2); not on a real sheet card yet (M3 section 13).
- **Duplicate part cards and "did you mean"** for a wrong part name: tested offline (M3 section 12).
- **Fewer Onshape calls** (2026-10-04): a run starts all its STEP exports before it waits on any, status
  checks are timed from each export's start (15, 60, 120, 180 s since 2026-11-01; was 5, 20, 65, 125), and an export left from an earlier run gets one
  check. Two parts that each take 30 s: 4 checks instead of 8. Tested with recorded responses only.
- **Better nests** (core 0.23.0, 2026-11-04): one Arrange per thickness with big parts given priority, then every
  sheet that ends in a free stretch squeezed toward its front (6 tries, to 0.25 in); parts drawn at an angle are
  measured with tight boxes. Work in Fusion (r076 to r081; earlier r006, r014).
- **Spares** (core 0.24.0, 2026-11-04, decisions): a count per plate per send, from the Onshape panel (a Spares
  line in a Part Studio, a Robot | Spares switch in an assembly) or the phone's Send spares; a card of their own with
  the Spare label, nested only in the room the robot parts leave, marked "spare" on the sheet card. Tested offline
  and in a browser with fakes. **Works in Fusion** (r085: the robot plate and 6 spares on S1, squeezed 14.5 -> 9.6 in;
  the big spare that didn't fit beside it on S2; r084 found Fusion's no-room error, fixed in 0.24.1); none cut yet
  (`docs/manual-tests.md` → M3 section 19).
- **Pictures drawn from the programs** (2026-11-04, decisions): a toolpath picture on each sheet card (what the
  program cuts, numbered in cut order, cuts at the cutter's width, tabs orange) and a cover on each offcut card
  showing what the piece should look like (every program cut on it, the room left). Tested offline; drawn from real
  data for offcuts #3 and #4 before going live.
- **The air test ticked on risky sheets** (2026-11-04): on an offcut, a piece added by hand, or with a part placed
  by hand; the comment says why. Tested offline.
- **Offcuts**, with **room beside** earlier cuts: work in Fusion (r013 to r016, a test job); none cut yet
  (manual tests M3 5c).
- **Rush**, freed offcuts, smallest offcut first, the season's stock tally: tested offline (M3 section 8).
- **Finding offcuts:** numbers, "The offcut isn't on the rack", scraps added by hand: tested offline (M3 section 9).
- **Parts cut out of offcuts by hand** (2026-10-31): a "Cut by hand: 12 to 18 along, 5 to 10 across" line on
  the offcut card takes that rectangle out (the room either side kept), and "Something's already cut where this
  program goes" on a sheet card moves it like "isn't on the rack" but keeps the offcut. Tested offline (M3 section 9).
  Since 2026-11-01, optionally: the .tap that cut it attached, with "Zeroed at: 10 along, 5 across"; the cut is
  worked out from the program. Tested offline.
- **Own programs placed by AutoCAM** (2026-11-01, option 3): a card in Ready for CAM with someone's .tap (Material,
  Thickness lines) gets a spot on an offcut or a new sheet, a copy of the program moved there and checked, and a card
  in Sheet review; its cut is recorded at Cut. Tested offline (`docs/manual-tests.md` → M3 section 9, row 11).
- **Tabs:** on by default, counts per contour, placed at points on straight edges clear of corners,
  triangular from the template. Work in Fusion (test jobs, r018 S1); none cut yet (M3 section 10).
- **Board:** "Cut the whole sheet without stopping" on a 2+ part sheet.
- **Fusion:** several sheets in one run, the 1/8 in tool, polycarbonate. (Fusion dying mid-job is handled:
  M2 checks 4-5 passed by accident on 2026-10-03.)
- **Self-updating** works (the service and the add-in, most recently into core 0.11.0).

### Stretch ideas (not planned; rough size)
- **Board upkeep** (small): cards in Cut archive themselves after ~14 days; old job folders and exports pruned
  (keeping what open sheets need); a machine queue on the System card ("Ready to cut: 3 sheets, about 2 h 40").
- **Brief leftovers** (small each; the clamp blocks in the Fusion setup were built 2026-11-04): the
  "stock/cutter loaded" and "clamps as shown" items back in the required Review checklist; lowest Z per sheet
  in the run summary; a parts checklist on sheet cards.
- **Even fewer Onshape calls:** one export per Part Studio (the add-in splits the bodies), and not
  exporting parts again after an edit elsewhere in the document (needs a geometry fingerprint from Onshape,
  confirmed with real calls first). Decisions, 2026-10-04.
- **Part-in-part nesting** (large): small parts placed inside big cutouts; needs a per-part cut order and
  tabs on the slugs that hold them.
- **Splitting a 1/8 in aluminum sheet** into two programs (out of scope for v1 in the brief).
- **Tracker adapters** (Notion, GitHub Projects): possible, not worth it on Trello Free (decisions).

More stretch ideas (2026-10-04, saved for later; 1 and 2 were built the same day, below):
- **Cutter hours on the System card** (small): each sheet card moved to Cut adds its machining time (times its
  repeat) to its cutter, per tool (the two 4 mm cutters share T1 but are counted apart); air tests don't
  count. A "Cutter changed" checklist on the System card, one box per cutter: tick it when you put in a new
  one, and the service resets that count, unticks the box and comments how long the old one ran. Optional: a
  warning point per cutter in config ("check or change it"), inches of cut from the programs, and a way to add
  time for hand-CAM'd jobs.
- **Automatic air test on risky loads**: built 2026-11-04 (every sheet on an offcut or a piece added by hand, or
  with a part placed by hand).
- **Feedback after a cut** (small): an "After the cut" checklist (a part came loose, tabs too hard to break,
  didn't cut through), tallied on the System card, to tune the tabs.
- **Part names on the preview:** built 2026-10-04, drawn on the picture by the service instead (decisions).
- **Sheets on the shelf** (small-medium): a Stock card with the full sheets of each material; each new sheet
  cut counts one off; the System card warns below a threshold.
- **Spares list**: built 2026-11-04 as spares sent with a count each time (decisions); this was the first idea:
  spare part cards with quantities; one tick sends every spare of a material to
  Ready for CAM together, nested from cache (no Onshape calls).
- **Team chat ping** (small, needs a webhook URL): Discord or Slack messages when a sheet is ready for review, a
  run fails or Fusion goes down.
- **Changed-part alerts:** built 2026-11-01 (the panel's checks, Ready to cut and Sheet review, the CAD changed label).

## Not verified yet (don't rely on these until they are)

- **Fusion:** what has run is in `docs/fusion-api-status.md`; everything else in `fx_*.py` is untested.
- **The machine:** nothing AutoCAM made has run on it yet. r068 S1 (2026-11-03) failed at its first stop: WinCNC
  has no M0. The stop is a bare G4 now (decisions, 2026-11-03), not tried at the machine yet.
- **Onshape and Trello** have worked for real (runs r001-r016, `.step` attachments included); version links,
  Smoked labels and 5052 haven't been through a real run.
