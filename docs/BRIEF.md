# FRC auto-CAM + auto-nest pipeline: planning brief

## Goal

Students put a part on a Trello card (Onshape link + quantity). When we run a nest, the system pulls the
geometry from Onshape, validates it, nests all waiting parts of the same stock type onto 24" x 48" sheets
in Fusion, applies our CAM template, posts one WinCNC `.tap` per sheet, and puts the `.tap`, a nest preview,
and a link to the Fusion file back on Trello. Trello is how files reach the machine: operators download
`.tap` files from Trello on the WinCNC PC. A human reviews every sheet before it's cut.

## Shop facts (don't re-ask these)

- **Parts:** FRC robot plates. Mostly 2D through-cut (holes, cutouts, outline); some have **pockets**, which are in scope.
- **Stock:** 24" x 48" sheets for every material. The sheet is longer than the bed's 40" cut length, so the
  team lets the extra ~8" hang off the end of the bed.
- **Materials (material, thicknesses):** 6061 aluminum 1/16, 1/8, 3/16, 1/4; 5052 aluminum 1/16, 1/8;
  clear polycarbonate 1/16, 1/8, 3/16, 1/4; smoked polycarbonate 1/16, 1/8, 3/16, 1/4. That's 14 stock types.
- **Tools:** only these three, from our Fusion library "5940 Tool Library" (copy pinned at
  `fusion/tools/5940_Tool_Library.tools`). All single-flute carbide, 18,000 rpm, stepdowns not set in presets
  (they live in the templates). Flute lengths all cover 1/4" plate plus overcut.

  | # | Library description | GUID | Flute | Feed / plunge | Use for |
  |---|---|---|---|---|---|
  | T1 | `4mm 0 flute Aluminum` | `7b77ef53-1ace-4e3b-ac2d-380b01a638bd` | 12 mm | 60 / 20 ipm | 6061, 5052 |
  | T1 | `4mm 0 flute Poly` | `ec14e4ef-aa38-4a96-af26-8885accae4a3` | 18 mm | 100 / 20 ipm | polycarbonate |
  | T12 | `1/8` | `7e0681ca-7664-4d53-ae3a-7457a77fd00a` | 0.472" | 48 / 10 ipm | **aluminum only** |
  | TBD | 1/8" poly (being imported from another library) | TBD | TBD | TBD | polycarbonate |

  The library holds other tools, including another 4 mm endmill ("Onsrud Alu", T2) and duplicate tool numbers.
  **Never use them, and never pick a tool by diameter or number; identify tools by GUID.** Both 4 mm tools are
  T1, so the `.tap` alone doesn't say which cutter to load (see decision 17).
  Small holes are made with the endmills, no separate drill. With the **4 mm** endmill:
  **0.156" and 0.159" holes are drilled** (straight plunge; 4 mm = 0.1575"), and **every round hole from
  5 mm / 0.197" up to 1.5" is bored** (helical). The **1/8" endmill plunges 8-32 tap holes** (aluminum), which get tapped by hand afterwards.
- **CAD:** Onshape, **Education Enterprise** plan. Students reliably assign Onshape materials to parts.
  Plates live in **separate Part Studios per mechanism**.
- **Router:** ShopSabre 23, **30" x 40" cutting area**, manual tool change, tool-length switch. Controller is **WinCNC**.
- **Zero:** XY zero is the **front-left corner of the sheet** (the corner facing the operator), on the bed end.
  **Z zero is the bottom of the stock.** The sheet runs +X from there and its far end overhangs the bed. The 40"
  reach spec is correct. Manual setups use WCS origin = "Stock box point" and inch units.
- **CAM:** Fusion. Our post is a customized Autodesk ShopSabre (WinCNC) post, description
  **"ShopSabre with automatic mist"**, `.tap` output, in our **local** Fusion post library and used for manual CAM.
  A copy is pinned at `fusion/posts/shopsabre_automatic_mist.cps` (see "Post processor" below).
- **Workholding:** ShopSabre T-slot-style clamps screwed into a **threaded-insert pattern on a 2" x 2" grid** in
  the spoilboard (no T-slots), plus **double-sided tape** under the plate. The grid's offset from the bed edge
  isn't measured, and clamp positions relative to the sheet aren't fixed.
- **Compute:** a spare **Windows PC that stays on** runs Fusion and the service. It is *not* the WinCNC controller PC.
  **There is no shared folder**: Trello is the only way files reach the WinCNC PC.
- **Tracking:** Trello, **Free plan**: 10 MB per attachment, 10 collaborators per workspace (a separate service
  account would use one of those seats).
- **Approvals:** anyone on the team may move a sheet to "Ready to cut".

## Decisions already made

1. **Two processes joined by a hot folder.**
   - `service/`: plain Python (can use `requests` etc.). Handles Trello polling, Onshape exports, job batching,
     writing `job.json`, reading `result.json`, uploading results, moving cards.
   - `fusion/`: a Fusion **add-in** that watches the hot folder, runs `process_job()` from the existing script,
     writes `result.json`. One job at a time.
   - Why: the service can restart, log, and retry without touching Fusion; Fusion updates and restarts itself
     and nothing queued is lost because jobs are files.
2. **Trello is polled; Onshape is never polled.** Onshape is called only when a nest job actually runs.
3. **Cards link to Onshape *versions*, not workspaces.** Versions are immutable, so what gets cut is what was
   reviewed, and exports can be cached forever keyed by (document, version, element, part).
4. **Batch by stock type** (decision 10). One nest run can produce several sheets. Each sheet gets its own
   **sheet card** holding the `.tap`, the nest preview, and a link to the `.f3d`, with a checklist of its parts.
   Each part card gets a comment plus a link attachment to its sheet card. Operators are used to grabbing
   `.tap` files from part cards, so a part card must lead straight to its sheet's `.tap`.
5. **One standard sheet fixture with clamp keep-out strips (v1).** Every sheet is 24" x 48" (decision 15).
   Because clamps can land on any 2" grid position relative to the sheet, v1 doesn't model individual clamps:
   each **clamped edge** gets a keep-out strip along its full length, width = clamp reach onto the plate +
   clearance. The nest goes into the reachable area minus those strips (Fusion Arrange supports sketch/face
   envelopes, not just rectangles). Strip-shaped "clamp zone" bodies at clamp height are added to the setup as
   fixtures so clearance/retract heights stay above the clamps. All of it (clamped edges, reach, clearance,
   height) is config.
   *Later upgrade:* if sheets get placed against a fixed stop and the grid offset is measured, replace strips
   with exact clamp boxes to reclaim material. That's a config/fixture change, not a redesign.
6. **Tabs off by default** because plates are taped. Tab placement is an optional module behind a flag
   (see "Tabs" below), built only if a taped no-tab trial fails.
7. **CAM template convention:** operations in our Fusion CAM templates are tagged by name:
   - `[drill]`: holes matching the tool's **drill sizes**. 4 mm tool: 0.156" and 0.159". 1/8" tool (ASSUMED,
     correct later): 0.125" and **0.136"** (#29, the 8-32 tap drill Onshape's tapped-hole feature uses).
     Straight plunge. Note: plunging a 0.125" endmill leaves a 0.125" hole (~96% thread for 8-32) even where the
     model says 0.136" (~69%); that's the current shop practice, not something the automation changes.
   - `[bore]`: **every round through-hole from 0.197" (5 mm) up to 1.5"**, with either tool, except bearing
     sizes (below). Helical bore. A range is safe here because a bore cuts to the modeled diameter; a drill
     always cuts at the tool's diameter, which is why drill sizes stay an exact list.
   - `[bearing]`: **bearing holes** for 1/2" hex and 3/8" hex bearings, bored with either tool. Takes
     precedence over `[bore]`, since these sizes fall inside the bore range. Sizes:
     **1.125"** (1/2" hex bearings, and the large-OD 3/8" hex bearings) and **0.875"** (standard 3/8" hex
     bearings), tolerance +/-0.010" to catch holes modeled slightly under/over for fit. A separate op from
     `[bore]` so press fit (radial stock to leave) and a finishing pass can be tuned per material in each
     template without touching bolt holes. Any round hole at these sizes gets this op, bearing or not.
   - `[pocket]`: pocket/counterbore floors (pocket selections on floor faces).
   - `[inner]`: every other **through** hole and cutout. Must exclude drilled/bored/bearing holes and pocket
     openings, so build it from per-loop chain selections, not a whole-face "inside loops" selection (that
     would also cut every pocket outline straight through).
   - `[outer]`: every part outline.
   Config: drill sizes as an exact list (+/-0.002") so an odd size never gets drilled at the wrong diameter;
   bore as a range (0.197" -0.002" up to 1.5" inclusive); bearing sizes as a list (+/-0.010").
   Drilled holes skip the "radius >= tool radius" check. A hole between the largest drill size and 5 mm is
   contoured with a warning so someone checks the fit. Round holes larger than 1.5" go to `[inner]`.
   "Round hole" = a full circular through-hole; STEP exports often split one hole into two half-cylinder
   faces, so group faces by shared axis and radius before classifying.
   Template handles tools, feeds, depths, and order: drill, bore, bearing, pockets, inner, outer. **Z zero is the
   bottom of the stock**, so through-cuts are referenced to the bottom and still go through if a sheet is a bit
   thicker or thinner than nominal. **Nothing may go below Z0** (decision 22).
8. **Human review gate.** Automation moves things to "Needs review" at most. Never to "Ready to cut".
9. **Manual nest trigger for v1** (e.g., moving a control card or pressing a Trello board button). Auto-trigger
   by accumulated area is out of scope for v1.
10. **Batch key = stock type** (material, color for polycarbonate, thickness). Clear and smoked polycarbonate
    never share a sheet; neither do 6061 and 5052.
11. **Material comes from Onshape**, mapped to our stock types through a table in config. Unmapped or missing
    material sends the part card to "Needs review". **Trello labels override**: a `Smoked` label makes a
    polycarbonate part smoked (Onshape can't tell clear from smoked). Label names confirmed.
12. **Templates are per material family and tool, not per thickness.** Heights are relative to model/stock
    top and bottom, so one template covers every thickness. **Polycarbonate-specific templates are required**
    (in scope). **Four templates:** aluminum 4 mm (T1 alu tool; used for 6061 and 5052), aluminum 1/8"
    (T12), polycarbonate 4 mm (T1 poly tool), and polycarbonate 1/8" once the 1/8" poly tool is imported into
    the library (open question 6). Until that tool exists, there is no poly 1/8" template. Each template has `[drill]`, `[bore]`, `[bearing]`, `[pocket]`, `[inner]`, `[outer]` operations,
    all using the template's one tool.
13. **Tool choice per sheet (confirmed):** 4 mm by default; 1/8" if any part on the sheet has a feature the
    4 mm can't make. A hole is fine for 4 mm if it matches a 4 mm drill or bore size or is big enough to contour; inside
    radii must be >= 2 mm. Validation computes this per part, so it's automatic. A `Tool 1/8` Trello label
    forces the 1/8" tool for that part (label name confirmed). **Polycarbonate never uses the aluminum 1/8" (T12).**
    A poly part that needs the 1/8" (feature too small for 4 mm, or a `Tool 1/8` label) uses the poly 1/8"
    template. If that template isn't configured yet, the part goes to "Needs review" with the comment
    "needs manual CAM: no poly feeds for the 1/8 in endmill". The template list lives in config, so adding it
    later is a config change, not a code change.
14. **Where outputs go:** no shared folder. The sheet card gets the `.tap` and the nest `.png` as attachments
    (Trello Free: 10 MB each; fail loudly if a `.tap` exceeds it). The Fusion document is saved to a Fusion Team
    project folder and linked from the sheet card (also attach the `.f3d` if it's under 10 MB).
15. **Overhanging sheet:** the Fusion fixture is the full 24" x 48" sheet (used as setup stock, so simulation and
    the preview match reality), but the **nest envelope is only the reachable ~24" x 40" on the bed**, minus
    edge margin and clamp keep-outs. The overhanging ~8" is off-limits. The WCS origin is the **bottom front-left
    corner of the stock** (facing the operator, bed end), matching how the team zeros today. WCS orientation =
    model orientation (the nest is built flat in XY with X along the sheet's 48" length), origin = stock box point. Reusing the overhang strip later is remnant nesting (out of scope v1).
16. **Review checklist guard (confirmed):** since anyone can approve, each sheet card gets a checklist
    (simulated, correct stock loaded, clamps placed as shown). If a sheet card reaches "Ready to cut" with the
    checklist incomplete, the service moves it back with a comment.

22. **Never cut the spoilboard.** Z0 is the stock bottom, which is the spoilboard surface with the tape in
    between (ASSUMED: Z is touched off on the spoilboard next to the sheet; correct if not). So:
    - **Templates:** every operation's bottom height is "stock bottom" with **offset 0**, never negative. Drill
      ops don't use "drill tip through bottom" or a break-through depth. Multiple-depth passes end at Z0.
      Cutting to exactly Z0 goes through the plate and the tape but stops at the spoilboard surface.
    - **Guard on every posted program (implemented in the script):** after posting, the worker scans the `.tap`
      and finds the lowest Z on every motion line (comments and `G53` machine-coordinate lines skipped; inch and
      metric handled). If anything is below `z_floor_in` (default `0.0`), the file is renamed
      `*.REJECTED.tap`, the sheet gets an error listing the offending lines, and it is **not** offered for
      cutting. The lowest Z is noted on every sheet so a reviewer can see it.
    - The guard checks the exact code the machine will run, so it also catches template mistakes (e.g. a copied
      manual op with a -0.02" bottom offset).
    - Unit-test the scanner offline with sample programs (the logic is plain Python).

## Post processor (`fusion/posts/shopsabre_automatic_mist.cps`)

This is the shop's machine-proven post. **Do not edit it.** What it means for the automation:
- **One tool per program, enforced.** With no tool changer it raises an error if a program uses more than one
  tool. This matches decision 13 (one tool per sheet): every op in a template must use the same tool *number*,
  and a `Tool 1/8` label moves the whole sheet to the 1/8" template.
- **One work offset.** WCS range is 1..1, so one setup per NC program. Matches one program per sheet.
- **Mist:** writes `M11 C8` (mist on) at program start and `M12 C8` at the end when the `useMist` property is
  true (default). Expose `useMist` per material in config via `post_params` in case poly shouldn't be misted.
- **Helical bores post as real arcs.** Arcs are limited to 180 degrees and helical G2/G3 with Z is allowed, so
  `[bore]`/`[bearing]` ops come out as arcs, not thousands of line segments.
- **Drilling posts as G81 canned cycles** (G80 to cancel), same as the manual workflow.
- **Retracts use `G53 Z`** (machine Z top) between ops and at the end (`G53 P10`), so moves between ops clear
  the clamps. Inside an op, template clearance/retract heights must clear clamp height.
- **Units:** emits G20 for inch programs. New Fusion documents may default to mm, so the worker must set
  document/CAM units explicitly to match the manual jobs.
- **Comments** only keep letters, digits, and ` .,=_-:()`. Keep job and program names to letters, digits,
  `-` and `_` (e.g. `6061_0p125_S1`, not `6061 1/8`).
- **Finding it:** the script matches the post by description ("ShopSabre with automatic mist") in the local
  library. The uploaded file name ended in "(3)", so several copies probably exist; the pinned copy in the repo is
  the reference. Preferred: have the worker use the pinned file directly (by path, if the API allows it) or verify
  at startup that the library copy is identical to the pinned one, and fail loudly if not.

17. **Say which cutter to load.** Both 4 mm tools are T1, and the library's tool comment fields are empty, so the
    `.tap` can't tell the operator "alu" vs "poly". The sheet card title and description state the physical
    cutter in plain words (e.g. "LOAD: 4 mm O-flute POLY"), and the review checklist (decision 16) includes
    "correct cutter loaded". Optional for the team: fill in each tool's post-process comment in the library;
    the post prints it in the `.tap` at tool load.
18. **Coolant comes only from the post's `useMist` property.** The post's `useCoolant` property is off, so the
    tool presets' coolant settings (alu "disabled", poly "air", 1/8" "mist") never reach the `.tap`. `M11 C8` mist
    is written whenever `useMist` is on. The poly preset says "air", which suggests poly shouldn't be misted:
    configure `useMist` per material (open question 3).
19. **Pause after every part so the operator can pull it.** On by default, switchable per job in config.
    - **Cut order:** holes, bores, bearings, pockets and inner cutouts stay sheet-wide (efficient). The `[outer]`
      contour is split into **one operation per part instance**, ordered to keep travel short. A part is finished
      the moment its outline finishes (its holes were cut earlier).
    - **After each part's outline**, a pause block: retract (`G53 Z`), spindle off (`M5`), mist off (`M12 C8`, only
      if mist is on), move to the park spot, then **`M0` program stop: the machine waits for the operator to press
      start.** On resume: mist back on if used (`M11 C8`), spindle on (`S18000 M3`), and the post's usual 4-second
      spin-up dwell (`G4 X4`) before cutting continues.
    - **Not a timed dwell.** A dwell resumes on its own; if nobody hits pause in time, the machine starts moving
      with hands near the spindle. `M0` plus spindle off means nothing moves until someone presses start.
    - **Implementation:** preferred is Fusion Manual NC entries (Pass Through for park/spindle/mist, Stop for `M0`)
      between the per-part outline ops, if the API can create them; the post already turns Stop into `M0` and
      forces a spindle restart afterwards. Fallback if the API can't: insert the pause block into the posted
      `.tap` text after each part's outline, keyed on the operation comment the post writes at the start of each
      operation. Either way, **do not edit the post.** Note Fusion's known quirk: Manual NC entries can post before
      the previous op's retract, so the pause block always starts with its own `G53 Z`.
    - **Park spot:** `G53 P10`, the same park the program end uses (confirmed). Kept in config.
    - **Resume:** the operator resumes with WinCNC's start/continue control (exact key confirmed with
      `fusion/tests/pause_air_test.tap`, open question 7). **Never Esc at a pause**: Esc aborts the job
      (recovery is WinCNC's Restart, Ctrl+R). The sheet card states the resume key.
    - **Must be proven on the machine with an air cut before real use** (add to `docs/manual-tests.md`): stops at
      each part, spindle actually stops and restarts, first move after resume comes down from the top, not
      sideways at cutting depth.
    - The sheet card says how many pauses the program has.
20. **Assumed defaults (correct later, all config):** programs post in **inches**; mist **on for aluminum,
    off for polycarbonate** (the poly tool preset says "air").
    Clamp keep-outs (ASSUMED): clamps on the **front and back long edges** only (the far end hangs off the bed);
    clamp reach onto the plate **1.0"**, plus **0.25"** clearance, so a **1.25" strip** along each long edge;
    clamp height **1.5"** above the plate, so template clearance height must be at least **2.0"** above stock top
    (thickness + 2.0" above Z0, since Z0 is the stock bottom).
    Plus the general **0.5"** edge margin on the zero-corner end.
21. **8-32 holes push aluminum sheets to the 1/8".** A 0.136" hole is smaller than the 4 mm tool, so any
    aluminum part with an 8-32 tapped hole moves its whole sheet to the slower 1/8" template (decision 13).
    *Later option if this is common:* split such a sheet into two programs run back-to-back without moving the
    sheet (1/8" program for the small holes, then 4 mm program for everything else). The post forbids two tools
    in one program, so it has to be two files. Out of scope for v1.

## Existing code

`fusion/autocam_nest/autocam_nest.py` (Phase 1). Standalone Fusion script: `job.json` in, `.f3d` + `.tap` per sheet
+ nest `.png` + `result.json` out. `process_job(job)` is the entry point the add-in should call.

**It was written from Autodesk's API docs and has never been run in Fusion.** Known uncertain spots:
- Stock from solid (`Setup.stockSolids`, `SetupStockModes.SolidStock`).
- How Arrange represents quantity > 1 copies (script bins bodies to sheets by position because the Arrange API
  preview has a known issue reporting which occurrences landed in which envelope).
- `frameWidth` / `isCreateCopies` / `isPartialArrangeAllowed` on the Arrange inputs.
- `adsk.cam.LoopTypes` enum names; `CAMTemplate.createFromFile`; op `hasError`/`warning` attributes.

**How to treat it: restructure, don't patch, and don't rewrite blind.** The script predates most decisions in
this brief, so extending it in place isn't worth it. But it encodes API details that a from-scratch rewrite would
have to rediscover. Plan:
1. **Smoke test first (M1 step 0):** the team runs it as-is in Fusion on 2-3 plain through-cut plates (no
   pockets), with a simple CAM template tagged `[inner]` / `[outer]`. Record exactly which API calls fail. A
   rewrite doesn't make untested Fusion calls correct; only running them does.
2. **Then restructure** into pure-Python modules (hole classification, keep-outs, tool choice, pause insertion,
   `.tap` Z-floor guard, job/result schemas; all unit-testable offline) plus thin Fusion adapters (import,
   arrange, setup, template, selections, post, save), inside the add-in folder.
3. **Carry over, don't reinvent:** explicit-unit `ValueInput` strings for Arrange; parking the nest envelope
   away from imported parts; position-based binning of bodies to sheets; the plate checks (oriented bounding box
   thickness, face classification, concave-cylinder test, sharp-inside-corner probe); post lookup by
   description; the NC program / post sequence; the Z-floor guard (`check_tap_floor`, already tested offline).
4. **Replace:** plain-rectangle stock/nest (fixture, decisions 5/15), floor rejection (pockets are in scope),
   single `tool_diameter_in` (per-sheet tool choice), face-contour `[inner]` (through-loops only), local `.f3d`
   export (Fusion Team), single-op `[outer]` (per-part ops + pauses), the file-dialog entry point (hot folder).

**Not implemented yet (needed for v1):**
- The fixture from decisions 5 and 15 (full-sheet stock, smaller nest envelope, clamp keep-outs, clamp bodies
  as fixtures). It currently nests into a plain rectangle and uses that rectangle as the stock.
- Pockets. Validation currently **rejects** any floor; it must instead accept floors that open to one side,
  reject parts with features from both sides, and pick the pocket side as the "up" face for nesting (today it
  uses the largest flat face, which on a pocketed plate is usually the *bottom*).
- `[drill]`, `[bore]`, `[bearing]` and `[pocket]` operations, hole classification by size list, and `[inner]` built from
  through-loops only (decision 7). Geometry parameter names for drill, bore and pocket ops (bearing is a bore op): I'll supply them
  via Export Parameters.
- Automatic 4 mm / 1/8" choice per sheet (decision 13); today the tool diameter is a single job setting.
- Saving to Fusion Team and returning the link (decision 14); today it exports `.f3d` to a local folder.

Current validation (keep, except the floor rule above): exactly one solid body; thickness matches; no
chamfers/countersinks/edge fillets; concave radii and holes >= tool radius; warns on sharp inside corners.

## Hot folder contract

```
queue/
  incoming/<job_id>.json     service writes (tmp file + atomic rename)
  processing/<job_id>.json   add-in moves it here while running
  done/<job_id>/             result.json, <job>.f3d, <job>_S<n>.tap, <job>_nest.png
  failed/<job_id>/           job.json + error.txt
```
- `job.json` schema: see `fusion/autocam_nest/job.example.json`, plus `job_id`, `blank_id`, and per-part
  `card_id` + `onshape` reference. `step` paths point at the service's local STEP cache.
- `result.json` schema: whatever `summarize()` in the script returns (status, sheets with tap paths and part counts,
  per-part errors/warnings, notes).
- On add-in startup, anything left in `processing/` is re-queued.

## Trello model (new dedicated board, Free plan)

Free-plan constraints that shape this: **no custom fields**, 10 MB per attachment, 10 collaborators and 10 boards
per workspace, 250 automation (Butler) runs per month. The design uses **no Butler automation at all**; the
service does everything through the API.

- **Lists (proposal):** `Inbox` (drafting) -> `Ready for CAM` (queued) -> `Needs fixing` (validation failed,
  service comments why) -> `Nested` (assigned to a sheet) ... and for sheet cards: `Sheet review` ->
  `Ready to cut` -> `Cut`. Plus `Control` and `Run nest` lists for the trigger.
- **Part card format** (no custom fields on Free):
  - Title = the part name **exactly** as in Onshape.
  - Description contains the Onshape **version** URL (service takes the first `cad.onshape.com` URL) and a line
    `Qty: N`. Missing/unparseable qty or a workspace URL -> `Needs fixing` with a comment showing the format.
  - Labels as overrides: `Smoked`, `Tool 1/8`.
  - Fallback: a `.step` attachment instead of an Onshape URL.
- **Trigger:** a "Run nest" control card lives in `Control`. Dragging it into `Run nest` starts a run; the
  service moves it back and comments a summary (sheets made, parts rejected, lowest Z per sheet).
- **Sheet card:** created by the service in `Sheet review`, titled with stock + tool + cutter, e.g.
  `6061 0.125 - 4 mm ALU - S1 (5 pauses)`. Attachments: `.tap` and nest `.png` (<= 10 MB each; fail loudly
  otherwise). Description: Fusion Team link, resume key, part list. Checklist = the review checklist
  (decision 16). Each part card gets a comment and a link attachment to its sheet card and moves to `Nested`.
- **Guard:** a sheet card found in `Ready to cut` with its checklist incomplete gets moved back to
  `Sheet review` with a comment (decision 16).
- **Credentials:** the service needs a Trello API key + token. Either a bot account (uses one of the 10
  collaborator seats) or a mentor's token.

## Tracker choice: **Trello (decided)**; GitHub Projects or Notion possible later

**Decision: Trello Free, on a new dedicated board.** Keep the interface below anyway so a switch later is an
adapter, not a rewrite. Build the service against a small **tracker interface** (list queued parts, read part fields and labels, create
sheet items, attach files, link parts to sheets, comment, move status, read the review checklist), with a
Trello adapter first. A Notion adapter should be possible without touching the rest of the service.
- **Notion fit:** a "Parts" database (properties: Status, Qty, Onshape URL, Material override, multi-select
  labels `Smoked` / `Tool 1/8`) and a "Sheets" database, joined by a **relation** property instead of Trello
  link attachments; review checklist = checkbox properties; board view grouped by Status. REST API at
  `api.notion.com` with file uploads.
- **Notion limits that matter:** Free workspaces with more than one member are capped at **1,000 lifetime
  blocks**, and since September 2026 the API enforces it (writes fail with HTTP 403 `restricted_resource`).
  Every page is a block, so a multi-member Free workspace would stop working within weeks. Paid plans are
  unlimited. Free file uploads are capped at 5 MiB (5 GiB paid): fine for `.tap` and `.png`. Rate limit is
  180 requests/minute per connection on non-Business plans, plus a shared per-workspace limit: plenty.
- **Trello limits that matter:** Free plan, 10 MB per attachment, 10 collaborators per workspace, no content cap.
- Rule: use Notion only if the team workspace is on a paid (or otherwise unlimited-block) plan.
- **GitHub Projects option** (if more than ~10 people need access, or the team already lives in GitHub):
  issues = part/sheet items, a Projects board with a Status field, custom fields (Qty, Onshape URL), labels
  (`Smoked`, `Tool 1/8`), comments, issue links for part-to-sheet. Free organizations have unlimited
  collaborators; the API is generous. **The API can't attach files to issues**, so the service uploads the
  `.tap` and preview as release assets (or commits them to a `cnc-output` repo) and links them from the sheet
  issue; the operator clicks the link on the WinCNC PC.
- Rejected: Airtable Free (1,000 API calls per month, 5 editors).
- No mainstream tracker has a free K-12 education plan (Trello offers academic discounts, Notion's free
  education plan is higher-ed only). Free-for-school alternatives: the school's Microsoft 365 (Planner) or
  Google Workspace (Sheets + Drive), but their APIs usually need school IT approval; not recommended for v1.
- Cheap Trello path if >10 people need access: a paid workspace with only mentors as members and students as
  single-board guests on the CNC board (single-board guests are free on paid plans).
- The team says it's on "Notion's education plan". Notion's free Education Plus plan is for accredited
  colleges/universities (not K-12) and is a **one-member** workspace; a separate program gives verified
  higher-ed student organizations unlimited members. A high-school FRC team's multi-member workspace is most
  likely on **Free** (1,000-block cap). Check Settings -> Billing/Plans for the plan name, and Settings -> Members
  for members vs guests, before choosing Notion. A single-member Education Plus workspace with teammates as
  guests would have unlimited blocks.

## Onshape

- Parse version URLs (`/documents/{did}/v/{vid}/e/{eid}`); reject `/w/` workspace URLs.
- Find the part in the Part Studio by name, export STEP via the translation API, poll with exponential backoff,
  download. Budget roughly 5 to 7 calls per part; batching several parts from one Part Studio is a later optimization.
- Material: if the parts list response includes the Onshape material, prefer it over a Trello field. Verify
  in the docs before relying on it.
- **Budget:** 10,000 calls/year shared across the enterprise; only successful (2xx/3xx) calls count; 402 when
  exhausted; 429 = back off. Keep a persistent call ledger with a monthly soft budget and a warning (e.g., a
  comment on a pinned "System" card).
- Auth: API keys for a dedicated team account inside the enterprise. Look up the current auth scheme in
  Onshape's docs; keys in `.env`.

## Tabs (optional module, only if needed)

Pure-Python function, unit-tested outside Fusion: input = one outer loop as 2D segments (lines/arcs),
tool diameter, tab width, spacing; output = tab points.
- Tabs only on straight segments, never arcs.
- Keep each tab >= one tool diameter from any corner; skip segments too short for that.
- Count scales with perimeter: min 2, max 6.
- Spread evenly by arc length, snap to nearest allowed spot, prefer roughly opposing positions across the centroid.
- Only on `[outer]` operations.
Fusion adapter: convert the BRep outer loop to segments, create sketch points, set the operation's
"At points" tab positions. **The At-points parameter names are unknown**; I'll supply them from
CAM_API_Utilities > Export Parameters.

## Testing

- `service/`: pytest, fully offline. Trello and Onshape via recorded JSON fixtures/fakes. No network in tests.
- Pure logic (URL parsing, batching, blank selection, call ledger, tab placement, job/result schemas) lives in
  plain modules with unit tests.
- A **fake Fusion worker** that consumes `job.json` and writes a canned `result.json`, so the whole service
  loop can be tested end to end without Fusion.
- Fusion-side: thin adapters + `docs/manual-tests.md` checklist using a few sample STEP files.

## Out of scope for v1

Remnant/scrap nesting (including the overhang strip), auto-trigger by area, Fusion cloud Automation API,
part-in-part nesting, two-sided parts (features machined from both faces), countersinks and chamfers.
**In scope:** pocketed parts and polycarbonate-specific templates.

## Open questions (ask me; don't guess)

Nothing here blocks planning. Grouped by when it's actually needed:

**Before M1 (Fusion script on real parts):**
1. **Parameter dump:** run `dump_params.py` (first M1 task) on a finished manual setup and each template; this
   confirms the assumed parameter names (see "Working without the parameter exports").
2. **Clamps:** confirm or correct the assumed values in decision 20.
3. **1/8" drill sizes:** confirm or correct the assumed list in decision 7.
4. **Z touch-off:** confirm Z is zeroed on the spoilboard next to the sheet (decision 22).
5. **Units and mist:** confirm inches, mist on for aluminum, off for polycarbonate (decision 20).

**Before M3/M4 (Trello + Onshape service):**
6. **Trello:** the new board's name, OK with the proposed lists/card format/trigger (see "Trello model"),
   and whether the service gets its own account (one of the 10 free collaborator seats) or a mentor's token.
7. **Fusion Team** project/folder for saved sheet files.
8. **Onshape naming:** OK that the part card title must match the Onshape part name exactly? And the exact
   Onshape material names students use (for the material mapping table; the service can list them once).

**Before the first real cut:**
9. **Resume key after M0:** run `fusion/tests/pause_air_test.tap` (air moves only, 3" above work zero) and note
   which key/button resumes after each stop, and that the spindle stops at each pause and restarts after.
10. **No-tab trial:** cut one taped sheet without tabs to confirm tape holds every part size (decision 6).
11. **1/8" poly tool:** GUID, tool number, flute length and feeds once imported (only needed for poly parts
    with features smaller than 4 mm; until then those go to review).

## Working without the parameter exports

Fusion stores CAM settings as named parameters. Some names are documented (`contours`, `pockets`,
`group_tabs`, `tabWidth`, `wcs_origin_boxPoint`, `job_stockFixedX`...); others aren't (drill/bore hole
selections, At-points tabs). Plan for this instead of waiting:
- Put every undocumented parameter name and every setup value in **config**, with placeholders.
- First task in M1: a tiny Fusion script, `fusion/tools/dump_params.py`, that the team runs once on a manual
  setup and each template. It writes every setup/operation parameter (name, type, value, expression) to JSON.
  The team sends that JSON back and the placeholders get filled in.
- **Assumed names until the dump confirms them:**
  - `[drill]` = Drill operation: `holeMode` = `'selection-faces'`, `holeFaces` = list of the hole's cylindrical
    `BRepFace`s (a `CadObjectParameterValue`, not a contour selection). Seen working in Autodesk forum code.
  - `[bore]` / `[bearing]` = Bore operations; face-selection parameter name **unknown**. Keep a candidate list
    in config (e.g. `circularFaces`), use the first one that exists on the op, and log which. Fallback if none
    works: make `[bore]` a Drill operation with the "bore milling" cycle, which uses the known `holeFaces`. The
    post expands cycles it doesn't handle natively into helical arcs.
  - Always pass **every face of a hole**: STEP often splits one hole into two half-cylinders.
  - Setup: WCS origin mode = stock box point, box point = the bottom front-left corner (assumed value
    `'bottom 1'`; confirm with the dump and by checking the origin in the nest preview).
- Everything else proceeds in parallel: M0, M2 through M5, the pure-Python logic (hole classification, keep-outs,
  batching, tool choice, pause insertion, tabs), and all Fusion code except the few lines that set those
  parameters. Those lines are clearly marked and tested last.

## What I want from plan mode

Don't write code yet. Produce:
1. Your questions for me (anything above that blocks the plan).
2. Repo layout and module list with responsibilities.
3. Data models: job.json, result.json, config file (blanks, materials, templates, Trello IDs), ledger.
4. Milestones with acceptance criteria and how each is tested. Suggested order:
   - M0 scaffolding, config, `.env` handling
   - M1 Fusion script hardening: smoke-test Phase 1 as-is (see "How to treat it"), restructure, then add the sheet/clamp
     fixture, pockets, drill/bore hole sizes, per-sheet tool choice, and Fusion Team save
   - M2 Fusion add-in hot-folder worker
   - M3 service: Trello queue read + Onshape export with cache and ledger + job.json writer (dry-run mode)
   - M4 service: results back to Trello (sheet cards, attachments, part-card links, comments, moves)
   - M5 ops: logging, Windows auto-start, heartbeat/health card
   - M6 tabs module (only if the no-tab trial fails)
5. Risks and how the plan handles them (especially the untested Fusion API calls and the Onshape budget).
