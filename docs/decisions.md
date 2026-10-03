# Decisions log

Decisions made after `docs/BRIEF.md`, newest last. Answers to the brief's open questions go here too.

## 2026-10-01: planning (see `docs/PLAN.md`)

- **Repo root** is `C:\dev\frc-autocam`, remote `github.com/akashJavascript/trello-cam`.
- **Thickness comes from Fusion.**
  - The service batches by material + color only.
  - Fusion measures each part's thickness, snaps it to the material's stock thicknesses, and nests each
    thickness separately, so every sheet is still one stock type.
  - A non-stock thickness goes to `Needs fixing`.
- **Config is TOML** (`config/autocam.toml`). Fusion never reads it; jobs are self-contained JSON.
- **Pauses** default to `.tap` text insertion (pure Python, tested). Manual NC becomes an option only if the API
  probe shows it can be created. No pause after the last part on a sheet.
- **Short quantity:** if a card's full qty doesn't fit, none of its copies are cut this run, and the card stays
  in `Ready for CAM`.
- **Post:** use the pinned `.cps` by path if the API allows. Otherwise use the library post after verifying it
  against the pinned sha256. Every post property is set explicitly on every post.
- **Program names** carry a run number (`6061_0p125_r017_S1`).

## 2026-10-01: M0

- **`.gitattributes` fixed.** The catch-all `* text=auto` was last, which overrode `*.cps -text` /
  `*.tap -text`. It now comes first, and a test enforces the order.
- **Post properties.** The post also defines `writeMachine` and `writeTools` (both false); config sets them.

## 2026-10-01: overnight work on `m1-offline` (defaults; say if any is wrong)

- **Cards with a `.step` file instead of an Onshape link** need a `Material: 6061` line, since there's no
  Onshape material to read. `PC` means clear; add the `Smoked` label for smoked.
- **The service checks programs against its own copy of each job** (`state/jobs/<job_id>.json`), never the
  copy in Fusion's output folder.
- **Re-check before upload.** The `.tap` guard and pause verification (`autocam_core.sheetcheck`) run in
  Fusion and again in the service on the exact bytes before upload. A `.tap` can only be uploaded through
  `Tracker.attach_program`, which needs a passing guard report with the same sha256.
- **The guard also checks clamp clearance.** Any move over a clamp strip must be at or above stock top + clamp
  height + `tapguard.clamp_margin_in` (default 0.25 in). The post doesn't retract between ops, so this checks
  the real file.
- **Unfinished Onshape exports are resumed, not repaid.**
  - A STEP translation that doesn't finish within the poll budget is remembered.
  - The next run polls it instead of starting a new one.
  - Parts that can't be exported yet (offline, or translation still running) leave their card in Ready for
    CAM rather than sending it to Needs fixing.
- **When a sheet's program is rejected**, its part cards go to Needs fixing with an explanation, so the next
  run doesn't nest them again before a human looks.
- **Shared Part Studio lookups.** The parts list of a Part Studio version is fetched once and shared by every
  card that links to it. Card titles must match exactly one part name, and duplicate names are rejected.
- **`make-job`** builds a job from local STEP files (part source `local`) for manual Fusion runs in M1.2.
- **Onshape API version and auth.** Requests use `/api/v10` and HMAC request signing. The response field
  names (`partId`, `bodyType`, `material.displayName`, `requestState`, `resultExternalDataIds`) follow the
  docs and still need confirming on the first supervised run (M3).

## 2026-10-01: rules added after the independent safety review

- **Bytes.** A program must be printable ASCII with one consistent line ending (CRLF or LF). A lone CR, tab or
  other control byte fails, because the controller might split lines differently from the guard.
- **Fixed G/M sets.** The guard only accepts G0-4, G20, G53, G73, G80-83, G90 and M0, M3, M5, M11, M12, whatever
  config says. Config can narrow this, never widen it.
- **No sideways rapid below the stock top.** A `G0` that moves in X/Y below the stock top fails, and so does
  drilling travel between holes below it. Vertical rapids below the top (e.g. re-entering a pocket) are
  allowed. **Watch for this in M1.2:** if Fusion's real output trips it on a good template, we'll look at the
  actual pattern before loosening it.
- **The service re-derives the cut plan.**
  - The sheet thickness must be one of the job material's stock thicknesses.
  - The tool must be one of the job's tools.
  - The program's `[outer ...]` ops must be exactly the planned order, covering every placed copy.
  - Card pause counts come from the service's own check.
- **A run is saved before it does anything.** A crash or Trello failure mid-start resumes the same run; it
  never starts a new one and never re-exports.
- **Dry runs are separate.** They keep their own state folder (`state/dryrun/`) and `d001`-style run ids.
- **What can sit in Ready to cut.**
  - Rejected sheet cards are always moved out.
  - A sheet card the service made must have its full review checklist ticked.
  - Cards the service didn't make are left alone.
- **Second review pass** (verifying the fixes) found and fixed three more:
  - **Run ids:** a new run never reuses a run id the hot folder already holds, even if `state/` was wiped.
    A fresh run's job ids must be new, or the submit fails loudly.
  - **Stuck writes:** a Trello write that keeps failing (e.g. a deleted card) is given up after 3 ticks and
    noted, so it can't keep a run active forever.
  - **Zero-job runs:** such a run is only marked done after its control-card comment and move land.
- **Templates use full retraction.** The worker reports nominal stock thickness in `result.json`.

## 2026-10-01: what the team's manual CAM actually does (from `dump_params`)

These are inputs for the templates in M1.2-M1.5, not decisions yet. One aluminum job (3/16 in plate, 4 mm alu tool):
- **Work origin:** Stock box point, `'bottom 1'`, with the WCS Z and X axes picked explicitly.
- **Stock:** the part bounding box plus 0.04 in on the sides and top, 0 on the bottom. The automation will use
  the full sheet as stock instead.
- **Heights:**
  - Retract and feed are 0.2 in above the stock top.
  - Clearance is 0.4 in above retract, about 0.6 in above the stock top. The brief assumed 2.0 in
    (`clamps.min_clear_above_stock_in`, still ASSUMED). The guard only rejects moves that cross a clamp strip,
    so the lower value may be fine; decide when the clamp numbers are confirmed.
- **Bottoms:**
  - Drill and bore: "from hole bottom", offset 0, no drill-tip-through, no break-through.
  - Contours: "from contour", offset 0.

  These already match the never-below-Z0 rule.
- **Linking:** "keep tool down" off, high feedrate mode disabled, single depth with a 2° profile ramp.
- **Tabs are on** in this job (2.5 in spacing; one contour uses manual tabs). The brief assumes tape and no
  tabs, so the no-tab trial (open question 10) matters.
- **Pockets** are cut with 2D Adaptive Clearing, not the Pocket strategy. The `[pocket]` op in the templates
  should be a 2D Adaptive op.

## 2026-10-01: tool library switched to the team's current one

- **Which library.** The pinned `fusion/tools/5940_Tool_Library.tools` is now the team's library as saved in
  March 2026. It replaces the older copy that came with the brief.
- **What changed.** The same tools have **new GUIDs**:
  - 4 mm alu `e5dd75b2-…`
  - 4 mm poly `b4521723-…`
  - 1/8 alu `dc1f12bd-…`

  It adds **1/8 polycarbonate** (`7a26b9db-…`, T12, 96 ipm / 10 ipm plunge, mist), which closes open
  question 11. It also adds a 3 mm poly (T1) and a 6 mm chamfer (T16). The Onsrud and wood spiral bits are gone.
- **Duplicate tool numbers.** T1 is now shared by three tools and T12 by two. The `.tap` can't say which cutter
  to load, so the card's `LOAD:` line and the GUID check matter more.
- **Lesson.** GUIDs are stable within one library but change when a library is re-saved or copied. Config, the
  pinned file and the templates must come from the same library. `config-check` catches config vs pinned file;
  the worker's GUID check catches templates. After importing on a new machine, confirm with `dump_params`.
- **Poly 1/8 template.** `poly_eighth` is configured. Until its template file is exported, poly parts that need
  it still go to Needs fixing with the decision-13 comment.

## 2026-10-01: from the `pipeline_probe` results

- **Every outline must reach the stock bottom.** The probe's programs passed the Z-floor check but never went
  below Z 0.125 (the plate top), because the smoke template used the team's "From contour" bottom height and the
  automation selects the top face. The sheet check (both in Fusion and in the service) now rejects any
  `[outer ...]` op whose lowest Z isn't the floor (within 0.0005 in, the posting precision). The card names the
  op and says to set the template's bottom height to stock bottom, offset 0.
- **A rejected sheet's `result.json` carries every reason.** The worker reports each `SheetCheck.problems()`
  line as a `TAP_REJECTED` error, not just the guard summary, so the card says why (the fake worker does this
  too).
- **Arrange: occurrences, one Arrange per sheet, our own offsets.** Faces can't be added; quantity and
  multiple envelopes need the Manufacturing Extension; `resultEnvelopes` doesn't include the offset. Details and
  the open questions are in `docs/fusion-api-status.md`.

## 2026-10-01: from the second `pipeline_probe2` run

- **The worker sets the bottom height of `[outer]` and `[inner]` ops to stock bottom, offset 0** (default; say if
  wrong). The automation selects each part's top face, and with the team's usual "From contour" that cuts
  nothing (Z 0.125 on a 0.125 in plate). Setting it from the API works. Other ops (`[drill]`, `[bore]`,
  `[pocket]`) keep the template's heights. The sheet check stays as the backstop.
- **Pauses stay as text inserted into the `.tap`.** A Manual NC Stop posts only `M0` (no retract, spindle stop,
  mist off or park), and the guard rejects it.

## 2026-10-01: how the worker drives Arrange (from `pipeline_probe2` version 3)

Quantity and multiple envelopes need the Manufacturing Extension, so the worker builds what it needs from the
parts that work without it (details in `docs/fusion-api-status.md`):
- one occurrence per physical copy (`addExistingComponent`), with `isGroundToParent` cleared;
- one Arrange per sheet, moving the occurrences, partial arrange on; whatever didn't land goes to the next
  sheet's Arrange, up to `max_sheets_per_group`;
- `isDirectionFlipped` set per part so a pocket's open side faces up (Arrange otherwise lands plates upside
  down).

## 2026-10-01: M1.2 pipeline structure

- **Pure flow, thin Fusion adapter.** `fusion/autocam_addin/autocam_worker/pipeline.py` is the whole job flow
  in standard-library Python. It talks to Fusion only through `adapter.py`, so it runs offline against a fake
  (`tests/fusion/fakeadapter.py`), and its output passes through the service's own ingest checks in the tests.
  The `fx_*.py` modules are the real adapter (untested in Fusion). The package is `autocam_worker` rather than the
  plan's `worker`, because every add-in shares one Python process and `worker` could collide.
- **The pipeline does M1.2-M1.6 in one pass**, because the core already had the logic:
  - per-part tool need and one tool per sheet;
  - feature plans (drill, bore, bearing, inner);
  - per-part outline ops in cut order;
  - text-inserted pauses;
  - the sheet check.
  Fusion Team saving (M1.7) isn't done; the `.f3d` is exported locally.
- **Arrange envelope = nest region shrunk by `part_spacing_in`.** Arrange packs parts against the envelope
  edge; the outline's tool path (tool radius + lead-in, about 0.2 in) would otherwise reach into the clamp
  strips, and the guard rejects the sheet. The tests catch it if the inset is removed.
- **Job-level problems raise, part problems are reported.** A bad job (core version, pause mode, post sha256,
  missing template) raises `JobFailed` before Fusion is touched. Everything else ends in a `result.json`.
- **`autocam_run` can build a job from STEP files** with the service's job builder (standard library only).
  That is the one place Fusion-side code imports `autocam_service`, and only for manual runs.

## 2026-10-01: rules added after reviewing the M1.2 pipeline

An independent review of the new Fusion-side code found these; each is fixed and tested offline.
- **Stay on the sheet.** The guard now rejects any move below the stock top that takes the tool off the
  sheet (`GuardSpec.sheet_in`; both sides run it through `sheetcheck`). Before, a part that moved after
  nesting would have been cut wherever it ended up, and every check still passed.
- **Positions are re-read before CAM.** A copy more than 0.001 in from where Arrange put it stops its sheet.
- **Copies taken out of the job are hidden, not deleted.** Deleting an occurrence that an Arrange moved could
  make Fusion solve that Arrange again.
- **One part's trouble stays with that part:**
  - A part Arrange can't lay flat is refused on its own.
  - An Arrange that fails rejects the parts it held, with an error rather than a silent deferral.
  - A part that doesn't fit even alone on an empty sheet is an error, not deferred run after run.
  - Any Fusion exception in the adapter becomes an error for that part or sheet, not a crashed job.
- **Arrange results are matched by occurrence name**, not entity-token strings (tokens of one entity can
  differ between reads).
- **The envelope-edge tolerance is 0.01 in**: body boxes can be a little loose, and the envelope is already
  0.25 in inside the nest region.
- **A part whose top is split into several faces needs manual CAM**, because there's no single outline.
- **Single inner loops are chained from every edge of the loop**; the cutting side is a manual check.

## 2026-10-01: M2 add-in

- **Thin add-in, tested loop.** `autocam_addin.py` only wires Fusion up. A background thread updates the
  heartbeat every 10 s and fires a custom event when a job waits; the handler calls `Worker.tick()` on the
  main thread. The loop is `autocam_worker/worker.py`: recover on the first tick, one job per tick, done or
  failed, and the heartbeat.
- **Failures.** A job that can't run, or hits a worker bug, goes straight to `failed/` with the reason or the
  traceback; retrying wouldn't change it. Only a Fusion crash leaves the job in `processing/` for a retry, up to
  `fusion.max_attempts`.
- **One job per Fusion process.** A flag on `sys` (not in an `autocam_*` module, which `autocam_run` reloads)
  stops the add-in from starting a queued job inside a manual `autocam_run` job while Fusion pumps events.
- **The add-in reads three config values** (queue folder, logs folder, max attempts) from
  `config/autocam.toml`. That is an exception to "Fusion never reads config": jobs still carry everything a
  job needs.
- **No job timeout inside Fusion.** A job runs on the main thread and can't be interrupted. The heartbeat
  reports the job and when it started, so the service can flag a stuck one.

## 2026-10-01: arcs in the guard are bounded by what they sweep

Run t184402 (two C-shaped 1/4 in plates) was rejected for "too low over a clamp zone" and "cuts off the
sheet" on a G3 of about 28 in radius in the middle of the sheet. The guard bounded every arc by its whole
circle, which for that radius covers most of the machine. Now an arc is bounded by its endpoints plus the
0/90/180/270 degree points it actually passes (G2 clockwise, G3 counterclockwise; start = end is a full circle).
That's still conservative (a box, not the curve). Tests cover direction, full circles, the 28 in case, and an
arc that really dips into a clamp strip.

## 2026-10-01: the tool center must never be inside a part

Run t184808 posted and passed every check, but in the simulation some round cutouts were cut on the outside of
their line (a tool width too big); the triangles were right. Those loops are selected one by one as edge chains
(the face also has drilled/bored holes), and Fusion takes a chain's cut side from its direction, which comes from
the edges, not the loop. Two changes:
- **Fix:** use the chain's explicit inside side setting if this Fusion has one (`sideType`). Otherwise run each
  chain the way its loop runs (`isReverted` from the first co-edge). The run notes record what `ChainSelection`
  offers.
- **Safety net, whatever the cause:** after the guard passes, every point where the tool center goes below the
  stock top (`autocam_core/toolpoints.py`: feed-move ends and midpoints, arc midpoints, drill points) is tested
  against the sheet's part bodies (`pointContainment`, just above the cut). A point inside a part rejects the
  sheet. This catches a wrong-side cutout or outline, a template mistake, anything that would cut into a part. The
  service can't repeat it (no geometry), so it runs in Fusion only.

## 2026-10-02: the board made simpler (the user's choices)

- **Trigger:** unchanged. Drag the Run nest card into the Run nest list.
- **Links:** workspace links (`/w/`) work as well as version links. A run pins each workspace to its current
  microversion: 1 Onshape call per Part Studio per run. The cache is keyed by that microversion, so an edited
  Part Studio is exported again and an unchanged one costs nothing more. The job records the microversion
  (`OnshapeRef.microversion`; core version 0.2.0, so a stale add-in refuses new jobs instead of misreading them).
  The translation API takes a workspace, not a microversion, so a Part Studio edited in the seconds between the
  pin and the export would be exported as edited; that window is accepted.
- **Quantity:** still required, but written any common way: `Qty: 4`, `Qty 4`, `Quantity: 4`, `x4`, `4x`, or at
  the end of the title (`P-2011 x4`). Markdown around it is ignored.
- **Part names:** matched ignoring case and extra spaces. A Part Studio with one solid part needs no name match.
- **Plain text everywhere**, as short as possible:
  - **Problem comments** say only what's wrong and how to fix it.
  - **Sheet cards** read as LOAD / RUN / CUT ORDER, with the title leading with what stock to grab
    (`6061 1/8in - 4 mm O-flute ALU - 5 parts - 23 min - r004 S1`).
- **Board:**
  - Inbox becomes Drafts and Nested becomes On a sheet (renamed in place; same IDs).
  - A "New part" card template, with the quantity left blank on purpose.
  - The nest preview is the sheet card's cover.
  - Two checklists: Review (required for Ready to cut) and At the machine (for the operator).
  - No `.f3d` attachment when the Fusion Team link exists.
- **Parts follow their sheets to Cut:** once every sheet a part is on is in Cut, its card moves there, but only
  from On a sheet, so a card someone moved by hand is left alone.
- **No "In CAM" list:** the user wanted the fewest lists.

## 2026-10-02: runs start by themselves, and new parts fill open sheets (the user's choices)

- **Trigger:** a card arriving in Ready for CAM. A run starts 2 minutes (`trello.start_delay_s`) after the last
  card arrived, so dragging several cards makes one run. The Run nest list and card are archived. Run comments
  go on the System card. `autocam tick --now` and `dry-run` don't wait.
  - A run takes every card in Ready for CAM whose box is ticked, including ones left from earlier runs.
  - A card the run took doesn't start another run until it changes (text, labels, attachments) or leaves Ready
    for CAM and comes back. Comments don't count, so a part that didn't fit doesn't restart runs every 2 minutes.
  - Cards past the per-run Onshape limit wait and start the next run as soon as this one is done. That's
    needed now that nobody chooses what goes in a run (15 calls is about 2 uncached parts).
- **"Nest this part" box:** a checklist named `Nest` with one item, ticked by default. The service adds it,
  ticked, to any part card in Drafts or Ready for CAM without one (within a minute). It isn't on the New part
  template: Trello unticks checklist items when it copies a card (checked on the real board 2026-10-02), so
  every card made from the template would have come out "don't nest". An unticked card stays in Ready for
  CAM and runs skip it; ticking it counts as a change. No box at all means nest.
- **Open sheets get the new parts:** a sheet card is open while it's in Sheet review, cuttable, and nobody has
  ticked a Review item (the user's choice; once someone starts reviewing, the sheet is frozen).
  - A run nests the open sheets of a material again together with that material's new parts. The parts already
    on them come from the job that made the sheet (cached STEP: no Onshape calls) and keep their card and quantity.
  - The sheet card is updated in place (same card, same link): files deleted first, then the new title,
    description, program and preview, fresh checklists, and a "Rebuilt with new parts" comment. A card the new
    nest doesn't need is emptied and archived.
  - Only these cases change a card. Otherwise it's left exactly as it was:
    - Nothing new joined that thickness: the card keeps its program.
    - Someone ticked a Review item, or moved the card, while the run was going: the new parts going on it wait
      for the next run.
    - The rebuilt sheet failed the checks: the new parts go to Needs fixing, and the parts already on it stay.
  - A part pushed off its sheet by new parts goes back to Ready for CAM and is nested by the next run.
  - A part dragged back to Ready for CAM while its sheet is open (edited in Onshape) rebuilds that sheet with
    the new version instead of leaving a stale copy.
  - A sheet is only reopened if every part on it can move with it: all its parts in On a sheet (or in this
    run), none of them also on a sheet that isn't open, and their STEP files still in the cache.
  - The keep-or-rebuild choice is recorded once per run and thickness, and the open sheets are snapshotted when
    the run starts, so a service restart halfway through a rebuild finishes it the same way.

## 2026-10-02: sheet options: cut without stopping, air test (the user's choices)

Every cuttable sheet card gets an **Options** checklist with two boxes, both unticked. The service applies them
within a minute (Sheet review or Ready to cut), unticking undoes them, and a rebuilt sheet gets them again.

- **Cut the whole sheet without stopping:** the card's program becomes `<program>_NOSTOP.tap`, the posted
  program without the stop after each part. `pauses.remove` takes out exactly the blocks `pauses.insert` put
  in, and proves it by putting them back and comparing the bytes. The result is checked again with pauses off
  (no M0 allowed), and the description's RUN section says it doesn't stop. Only one real program is on the card
  at a time. A one-part sheet never stops anyway, so nothing changes. Without stops or tabs, cut parts sit
  loose in the sheet until the end.
- **Add an air test program:** `<the card's program>_AIRTEST.tap` (with no stops if that box is ticked too):
- **What it is:** the sheet's checked program with every Z and drill R raised by sheet thickness +
  `machine.air_test_gap_in` (0.5 in), so its lowest point, a through cut at Z0, runs 0.5 in above the top of
  the stock. Lines with G53 (machine coordinates) are untouched. Every move, feed, pause, spindle and mist
  command is the real one, so it shows the paths, the clamps and the pauses with the sheet clamped in place.
  A comment on line 2 says it's an air test.
- **Checks:** the same guard and pause checks as the real program (so it goes through the same
  `attach_program` gate), except "outlines reach the stock bottom", replaced by its opposite: nothing below
  stock top + gap. Made from the bytes in the job folder, after the service checks them again against the job.
- A rebuilt sheet's air test is remade from its new program; the box keeps its state. If the job folder is
  gone, the card says it can't be made (once).
- On the real r005 program: Z 0 to 2.49 in becomes 0.69 to 3.18 in above the spoilboard. The highest point must
  stay inside the Z travel (`[assumed]`).

## 2026-10-02: the service restarts itself into new code (the user's choice)

`autocam run` is now a small supervisor that runs the service loop in a child process (`restart.py`).
- **When it restarts:** between passes, the loop looks at the service and core code, the config and `.env`.
  Once a change has stayed the same for one more pass, so a `git pull` isn't caught half-written, it checks
  in a separate Python that the new code imports and the config loads. Only then does it exit for the
  supervisor to start it again. An empty `state/restart_service` file asks for a restart now.
- **Broken updates:** if the check fails, the old code keeps running and the error is logged; that version
  isn't tried again.
- **Crashes:** if the loop crashes at start, the supervisor tries again every minute. Ctrl+C stops both.
- **Why it's safe:** runs and Trello writes are recorded as they happen, so a restart between passes resumes
  cleanly, even mid-run.
- **What still needs a manual restart:** a change to `restart.py` itself (the supervisor is loaded once), or
  a new dependency (that needs `pip install -e .` first).

## 2026-10-02: M5, status and start at logon

- **System card description = live status** (`health.py`, `Runner.report_health`): whether Fusion is running (the add-in's
  heartbeat, stale after 5 min) and whether the add-in runs older code, the current run, cards waiting for the next
  run, Onshape calls, a 402 latch, and the last error of the past 24 h. Plain text, local time. It's rewritten when
  something in it changes, and every 10 minutes so the time shows the service is alive (`[status]`). Run comments
  stay comments on the same card.
- **Alerts:** one comment when jobs are waiting and Fusion isn't running, one when it's back
  (`state/alerts.json`, so a restart doesn't repeat them). A closed Fusion with nothing waiting (a quiet night) says
  nothing, except in the status.
- **One service at a time:** the supervisor holds an OS lock on `state/service.lock`, so a hand-started copy can't
  run beside the logon task's (two copies could start the same run). Checked on Windows Python.
- **Start at logon** (`ops/install-autostart.ps1`): two Task Scheduler tasks for the logged-in user, no admin.
  The service starts 30 s after logon in a visible "autocam service" window; Fusion starts through its Start Menu
  shortcut, whose launcher survives Fusion updates. The add-in's "Run on Startup" stays a tick in Fusion (the
  manifest stays off, so a development PC doesn't start it uninvited). Automatic Windows sign-in is left to the
  team: it's a security choice for the shop PC.

## 2026-10-02: the air test is the outlines only, at 200 in/min (the user's choice)

The air test now runs only the part outlines, every feed move at `machine.air_test_feed_ipm` (200 in/min),
still raised 0.5 in above the sheet. That's the part that matters near the clamps, and it makes the air test
quick. On P-2011 (r005) it's 164 in of moves, under a minute, instead of 750 in and 12.7 minutes.
- **Outlines only** (`airtest.outlines_only`): the ops before the outlines (holes, cutouts, pockets) lose their
  moves and their name comments, but keep their spindle, mist and mode lines, so the spindle still starts.
  The outlines are always the last ops. A check refuses the air test if taking those ops out would change any
  kept move: its motion mode, or (for anything but a rapid) its start point. The stops between parts are kept.
- **One feed** (`airtest.set_feed`): every G1, G2, G3 and drill-cycle move gets `F200.`, added where the post left F
  out (F carries over, and the move before might have been taken out). Rapids stay rapids.
- Checked like before (guard, pauses, nothing below stock top + 0.5 in). Checked on the real r005 program.
- An air test already on a card from the earlier version is remade (the options record which kind they made).

## 2026-10-02: better nests

- **Several tries, the best kept** (`pipeline.NEST_ORDERS`): each stock thickness is arranged with the parts as
  listed, then biggest first, then longest first. Sizes come from the imported copy's box. A try whose order
  is the same as an earlier one is skipped, so one kind of part is arranged once, as before.
  - **How the best is picked:** most copies placed, then fewest sheets, then the shortest last sheet. The last
    sheet is the open one that later parts fill, so the more room it leaves the better. Ties keep the listed
    order.
  - **Each try gets its own copies** (`add_copy`, made before anything is arranged, so they sit where the
    parts were imported) and its own sheet positions. The losing tries' copies are hidden, like leftovers
    always were. Nothing an Arrange moved is arranged again or deleted: that could make Fusion re-solve an
    Arrange and move parts already nested. Only Fusion calls already seen working are used.
  - **What it costs:** up to 3x the Arrange time, plus hidden copies in the saved file.
  - **What we don't know yet:** whether Fusion's Arrange cares about the order. The worker log says which
    order won and why ("kept 'biggest first' (...)"), and the result's notes say when an order beat the
    listed one. If real jobs never show a difference, the tries can go.
- **Sheet use on the card:** "Sheet use: N% of the cutting area is parts, and the last X in are empty". N is
  the parts' top-face area over the usable area, so big cutouts count as empty. X is the empty strip at the
  far end, the room for more parts.
- **Part-in-part stays off:** our cut order (holes and cutouts for the whole sheet, then the outlines) would
  cut the big part's cutout, and so free the slug, before the small part inside it is cut. It needs per-part
  cut ordering first.

## 2026-10-03: offcuts (the user's choice)

An offcut is **the same sheet put back on the machine**, not a sawn-off piece. The cut parts leave a
skeleton, but the clamped long edges are never cut, so it clamps and zeros like a new sheet. No saw needed,
and a cut across the sheet would run through the clamps anyway.
- **What's recorded** (`core/offcuts.py`, `state/offcuts.json`): the stretches along the sheet's length that are
  used, in the sheet's own coordinates (end A = the end at the zero corner when it was first cut). That's
  each sheet's parts from first to last along X, plus the part spacing, which covers the outlines' tool paths.
- **Where the next nest goes:** the longest free stretch the machine can reach, at least `nest.offcut_gap_in`
  (0.5 in, placement slack) from anything cut. The sheet is loaded as before or turned end for end, whichever
  gives more. Turning brings the end that hung off the bed onto it, so a sheet with a short strip used (like
  r006, 7 in) is nearly a whole new sheet the other way round. A stretch under `nest.offcut_min_in` (6 in)
  isn't worth loading.
- **On the board:**
  - **Keeping it:** a sheet card with room left gets an **Offcut** box, ticked: "Keep the rest of the sheet for
    the next run". When the card goes to Cut with it ticked, an offcut card goes in the new **Offcuts** list
    ("6061 3/16in offcut - 38 in free").
  - **Using it:** runs offer a material's offcuts to its job before new sheets, and the pipeline fills them
    first, inside the ordering tries (fewest new sheets wins). The sheet card's title says "offcut", and its
    Stock line says which offcut and which end goes where ("the end where r006 S1's parts were cut at the
    far end").
  - **Reservations:** the sheet card reserves the offcut. An open sheet that's rebuilt keeps it; a reviewed one
    holds it until it's cut; a retired one lets it go.
  - **After cutting:** the offcut card is updated ("30 in free now"), or archived once it's used up. Archiving
    an offcut card by hand tells the service the sheet is gone.
- **Job format:** `Job.offcuts` and two nest settings (core 0.3.0). Sheet results say which offcut, which way
  round, and the stretch used. The add-in reloads itself on the version change.
- Sheets published before this (like r006 before it's rebuilt) don't record their used stretch, so cutting
  them makes no offcut.

## 2026-10-03: the air test traces each outline once (the user's choice)

The real outlines ramp down and go round each part several times without retracting (P-2011: 164 in of
moves, 0.30 in down to 0, four to five laps). In the air that's wasted time, so `airtest.one_lap` keeps
**one lap per outline**: the lap at its final depth, entered straight down (a rapid to where that lap starts,
the op's own retract height, then down), and the op's own retract after it. The ramps and the other laps go.
- Every lap move kept starts at the same point and in the same mode as in the real program (checked), so the
  path traced is the real final-depth path.
- An outline op with anything but moves between its first and last move is left as it was.
- On the real programs: P-2011 164 in -> 29.6 in (about 9 s at 200 in/min); r006 (4 parts) 80 in, about 24 s,
  stops kept. Air tests already on cards are remade.

## 2026-10-03: offcuts go back on the same way round (the user's choice)

Spinning a sheet round is a chore, so an offcut is planned **the same way round as its last cut** whenever
that has room. Each offcut records how it was loaded for its last cut (`last.turned`; the job's
`OffcutSpec.last_turned`, core 0.3.1). The pipeline's tries use that way round, plus one extra try with
offcuts spun round where spinning gives more room. Spinning only wins if it places more parts or needs
fewer new sheets: it ranks right after those in the score. The sheet card says either "Put it in the same
way round as for r006 S1: ..." or "Spin it round from how it was for r006 S1 (flat, same side up, don't flip
it over): ...". The offcut card's "N in free" is still the most room either way round.

## 2026-10-03: the sheet runs along the machine's Y (found on the real machine)

On the team's ShopSabre 23, **X runs across the bed (30 in) and Y front to back (40 in)**, and the sheet's
back end overhangs. Everything until now had the sheet's length along X, which put the WCS origin at the
front-right corner (the user spotted it in Fusion). Programs posted before this (r005-r007) are turned 90
degrees for this machine and must not be cut.
- **Sheet coordinates = machine axes now:** X across the 24 in width, left to right; Y along the 48 in
  length, front to back; origin at the bottom front-left corner. The setup is unchanged in Fusion (model
  orientation, `wcs_origin_boxPoint = 'bottom 1'`, the minimum corner): the nest is laid out in this frame,
  so the design, the simulation and a hand re-post all match the machine.
- **Fixture:** the clamp strips are on the long edges, now the left and right edges (`clamps.edges =
  ["left", "right"]`; "front" is the end by the operator). The reach is along Y (`machine.reach_y_in = 40`),
  and the guard checks Y against it. The nest region is (1.25, 0.5) to (22.75, 39.5).
- **Pipeline:** sheets sit side by side along X (pitch = width + spacing). Offcut stretches, "last sheet
  reach" and the free length are along Y. Results carry `used_y_in`.
- **Cards:** "Clamps: left and right edges only"; an offcut's end goes "at the front (by you)" or "at the back
  (hanging off the bed)".
- Core 0.4.0 (SheetSpec.reach_in, GuardSpec.reach_y_in, SheetResult.used_y_in).
- **Not seen yet:** which way Fusion's Arrange packs in the new shape (21.5 in across by 39 in along). If it
  hugs the left edge and runs the length instead of filling the width from the front, the free length shrinks
  and offcuts get less. The "Sheet use ... the back N in are empty" line on the next sheets will show it, and
  the fix would be the "shortest strip" step (shrinking the Arrange area along Y).

## 2026-10-03: the last sheet is squeezed into a strip across the front

r009 showed that in the machine's axes, Fusion's Arrange packs against the **left edge**: the four parts ran
30.6 in down the sheet, 6.5 in wide, leaving a long strip on the right that the offcut model (stretches along
the length) can't use. So after the best try is picked, the last sheet of each thickness group is
**squeezed** (`pipeline._squeeze`): its parts are arranged again into full-width areas that get shorter,
halving between the longest "shorter side" of its parts and what they take now (up to 4 tries), and the
shortest area that holds them all is kept.
- **What it leaves:** the parts in a strip across the front, and the back of the sheet free, for parts that
  join it later or as an offcut.
- **How it's done:** each try has its own copies, and the copies not kept are hidden, so nothing an Arrange
  placed is moved or deleted.
- **When it's skipped:** if the last sheet couldn't get at least 2 in shorter.
- **Cost:** up to 4 Arranges (a few seconds each).
- **The preview** is now framed on the whole sheet and saved upright (1000 x 1600) for an upright sheet, as
  seen from the front of the machine. The old framing assumed a sideways sheet and cut off the ends.

