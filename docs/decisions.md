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
  placed is moved or deleted. The copies are all made up front, with the ordering tries' copies, before any
  Arrange runs (`pipeline._reserve`): r012 and r013 showed Fusion's Arrange refuses copies made after an
  Arrange has run, even ones put back where the part was imported ("upDirection (-1.0, 0.0, 0.0) is across
  the top face (-0.0, 0.0, 1.0)"). The tries' copies, made before any Arrange, were fine even when they were
  arranged after another try's Arrange. So every job makes 4 more sets of copies; the unused ones are hidden.
- **When it's skipped:** if the last sheet couldn't get at least 2 in shorter.
- **Cost:** up to 4 Arranges (a few seconds each).
- **The preview** is now framed on the whole sheet and saved upright (1000 x 1600) for an upright sheet, as
  seen from the front of the machine. The old framing assumed a sideways sheet and cut off the ends.

## 2026-10-03: offcuts keep the room beside the parts cut (the user's choice)

A used stretch is the whole width, but its parts rarely are: Fusion's Arrange packs from the left edge, so a
one-off part marked a full-width band as used and the rest of the band's width was written off (a 6 x 3 in
gusset cost about 85 sq in of an 18 sq in part). The metal is still there. So the offcut now also keeps the
**room beside** the parts (`autocam_core/offcuts.py`, core 0.5.0):
- **What's kept:** for each band cut along the length, the rectangle from the parts' right edge to the edge
  of the nest region, as deep as the band. It starts 0.75 in right of the parts: 0.25 in for the cutter's
  path and 0.5 in for loading the sheet back a little off, the same slack as between stretches. Both sides
  must be at least 3 in (`nest.offcut_beside_min_in`).
- **How it's used:** the next nest on that offcut arranges into each such rectangle first, one Arrange each,
  then into the free stretch. It's all one sheet: one setup, one program, one card. A rectangle that got
  parts is replaced by what's left of it (right of its new parts).
- **Either way round:** rectangles are kept in the offcut's own coordinates and turned with the sheet; what
  falls past the 40 in reach that way round is cut off (`beside_as_loaded`).
- **Used up:** an offcut is archived only when it has neither a free stretch worth loading (6 in) nor any
  room beside its cuts. An offcut with only room beside its cuts says "small parts only".
- **The squeeze** on a last sheet that also has parts beside earlier cuts arranges those again in the same
  rooms (fresh copies, made up front) along with the shorter stretch, so the sheet stays in one piece.
- **Not kept:** room behind short parts within a band, and room left of them. Arrange packs left and the
  squeeze packs to the front, so most of the free metal is to the right.

## 2026-10-03: Rush, freed offcuts, best fit, a stock tally (the user's choice)

- **Rush:** a part card with the **Rush** label (`labels.rush`, any case) starts a run on the next pass,
  with only the rush cards, before any other waiting run (after one already in Fusion). It's nested on its
  own: offcuts (room beside cuts first) or a new sheet, never folded into a sheet waiting in review. Its
  sheet card is titled "RUSH - ...", and later runs don't open it for new parts, unless one of its own
  parts comes back (it holds the old copy, so it's rebuilt). A sheet a rush card is already on is the one
  exception to "on its own", so no part is ever on two uncut sheets. The other waiting cards' 2-minute wait
  starts again after the rush run is queued.
- **Freed offcuts:** each pass reads the sheet card holding each reserved offcut (one Trello read each).
  Archived or deleted without being cut: the offcut is free again, with a comment on its card, and the sheet
  is marked archived in the registry (its parts can go on other sheets). Archived while in Cut: handled as
  cut. Trello errors other than "not found" leave it held until the next pass.
- **Best fit:** a thickness's offcuts are tried smallest room first (free stretch x nest width, plus room
  beside cuts, loaded as before), so parts go on the scraps they fit and the big offcuts stay for big parts.
- **Stock tally:** the System card counts sheet cards that went to Cut since `status.season_start` (MM-DD,
  default 09-01): new sheets by stock, sheets cut from offcuts, and the parts' area as a share of the new
  sheets' area (it grows as the offcuts on the shelf get used). Cut time and parts area are now recorded
  with each sheet; older sheets get their area from their job's result.json.

## 2026-10-03: finding offcuts at the machine (the user's choice)

The operator has to find the piece a sheet card names. What happens when it isn't there:
- **Numbers:** every offcut gets a number that's never reused (`state/offcut_numbers.json`). The offcut card's
  title starts with it, the comment when a sheet's rest becomes an offcut says to write it on the piece, and
  the sheet card's title and Stock line name it. Offcuts from before get one on the next pass.
- **"The offcut isn't on the rack"** (a Stock checklist on every sheet card nested on an offcut). Ticked:
  - **The missing offcut:** its card is archived, but it's kept on record ("missing"). Sending the card back to
    Offcuts makes it usable again.
  - **Another offcut the same program fits:** the card switches to it, with no new run and no new review.
    Fits: its used stretches are 0.5 in clear of every band the program cuts in (the free-stretch band and
    the bands of any rooms beside earlier cuts it used, recorded at publish as `bands`). It prefers the same
    way round, then the one with least room. When that sheet is cut, the bands are recorded on the new offcut.
  - **No fit:** the card offers "Re-nest it on other stock" (the card is emptied and archived, its part cards
    go back to Ready for CAM with the Rush label) or "Cut it on a new sheet" (the same program: offcut
    programs stay inside a whole sheet's nest area, so they're safe on one; its Stock line says where the
    parts are, and the rest becomes an offcut).
- **Scraps:** a card someone makes in Offcuts with Material, Thickness and Length lines becomes an offcut: a
  full-width piece with the missing length counted as used. It's never turned round (`OffcutSpec.can_turn`,
  core 0.6.0): turned, it would sit past its own front edge, with nothing to zero on. A card that's missing
  something gets one reply, and another only when it changes.

## 2026-10-03: tabs, per part, on by default (the user's choice)

Tape alone didn't hold parts in the team's earlier trials, so tabs are built (M6), as an option. It was
built off by default, then switched on by default the same day ("make tabs on by default"):
- **Where it's set:** a "Hold it in with tabs" box on part cards (checklist Tabs), ticked to start
  (`[tabs] default = true`; untick it for a part tape holds on its own). When a sheet in review is rebuilt,
  the parts carried over from it follow their card's box as it is then (no box: the default), so sending one
  card back rebuilds the whole sheet with the current tab settings. The service adds it, like the Nest box. A part already on a sheet: tick it and
  move the card back to Ready for CAM (its sheet is rebuilt if nobody has started reviewing it).
- **What it does:** that part's outline (`[outer]` op) and cutouts (`[inner]` loops) get the template's own
  tabs turned on (`group_tabs`), the template keeping their shape, width and height (rectangular, the
  cutter's width, a quarter of it high).
- **How many** (`autocam_core/tabs.py`, same day, after the user saw cutouts get none): spacing them by
  distance (2.5 in) gave contours shorter than that no tabs. Now each contour gets a count from its length:
  one per `[tabs] distance_in`, at least 2 and at most 6 (the brief's numbers), but no more than fit (one per
  four cutter widths of contour, so a cutout under about 0.6 in around gets none and is cut free). Fusion
  spreads them evenly (`tabPositioning = 'tabCount'`, `tabsPerContour`). Lengths come from the wall faces'
  areas over the plate thickness. A cutout op holds one count for all its contours, so a tabbed part's
  cutouts go in copies of the `[inner]` op, one per count ("[inner] cutouts - 2 tabs each"), made before
  it's filled and placed after it, before the outlines.
- **Where** (the brief's placement, as the user set it later the same day: "put them on lines when you can
  but if you have to put them on arc it's fine ... one tool diameter from any corner when it is possible"):
  `tabs.place_tabs` takes each contour as straight lines and curves (read from the copy's top face in
  Fusion, `fx_geometry.loop_segments`), spreads its tabs evenly, and snaps each to the best spot nearby:
  a straight edge with the whole tab a cutter diameter from any corner, then a straight edge nearer a corner,
  then a curve, and only then across a corner. A line spot wins unless it's more than about half the spacing
  further off. Tabs stay a tab width plus two cutter diameters apart, and the even pattern is turned to
  whichever start snaps least (so 2 tabs end up roughly opposite). Fusion gets them as sketch points in a
  `TABS <sheet>` sketch, in the op's `tabPositions`. Fusion 2705 has no "at points" positioning (only By
  distance and Number of tabs), so positioning is By distance at 1000 in, which adds none of Fusion's own:
  the tabs land on the points (checked in a test job: every outline tab 0.079 in, the cutter's radius, from its
  point). If any of it fails, the op falls back to the same count spread evenly by Fusion, and the sheet's
  notes say so. Each sheet's points are saved next to its program (`<sheet>.tabs.json`, sheet X/Y).
- **Shape:** triangular, set in the template (`alu_4mm`: `tabShape` on `[inner]` and `[outer]`). Fusion's API
  refuses every `tabShape` value ("Invalid enumeration value", even the one already set), but takes it from
  the template: the cutter ramps up onto each tab and back down.
- **Size** (`[tabs] width_in`, `height_in`, set on each tabbed op; 0 keeps the template's): 1 mm high. The user
  chose 0.3 in wide (twice the cutter, since a triangular tab only reaches full height in its middle), but
  with it Fusion crashed in its stock simulation on an arc move, on both attempts of test job `tsize1`; the
  0.157 in tabs never did. Most likely a 0.3 in tab ramped over a small fillet, so placement now keeps every
  tab on one edge when it can, and the width is back to the template's until 0.3 in passes a test job. The placement and the count use the same width: each tab needs about twice its width plus a cutter
  diameter of contour, so with 0.3 in tabs a cutout under about 0.9 in around gets none.
- **Sheet cards** say to break tabbed parts out once the program is done (at the stops they stay held):
  once if every part has tabs, else with each tabbed part marked in the cut order.
- **The air test** follows the outline through tab lifts: the lap at the final depth may rise up to 0.25 in
  over a tab and come back down (`airtest.TAB_MAX_IN`).

## 2026-10-03: parts placed by hand (the user's choices)

A part card in Ready for CAM with **"Nest this part" unticked** is no longer skipped: it's **placed by hand**
(a card nobody wants processed stays in Drafts).
- **Its program:** one copy, alone, laid flat by an Arrange of its own in an area just long enough for its
  longest side along X and its shorter side along Y, so it lands **long side across the bed**. The stock is the
  part's own bounding box, so the program's X/Y zero is the **front-left corner of the part's box** (no
  margin); Z0 is still the spoilboard. Qty N: the same program, **run N times**, zeroed at a fresh spot each
  time (`SheetResult.repeat`; the part counts N placed).
- **Its checks:** the same, except there are no fixed clamp strips, sheet or reach to check against
  (`check_sheet_program(..., by_hand=True)`). The guard reports what the cutter covers below the stock top
  (`GuardReport.cut_box_in`, grown by its radius), and the sheet card says it: "The cutter reaches X -0.21 to
  6.33 and Y -0.21 to 2.51 in from there", so the piece must cover that, clamps 1/2 in clear of it.
- **Its card:** "... - placed by hand - ... - 1 part, run 3x - ...", no Sheet use, no Offcut or Stock box, never
  opened for other parts. Tabs, the air test and Rush work as usual. The stock tally counts these separately.

## 2026-10-04: duplicate part cards and "did you mean" (the user's choice, from the stretch list)

- **Duplicates:** each pass, a part card in Drafts or Ready for CAM that's new or changed is compared with the
  other part cards in Drafts, Ready for CAM, Needs fixing and On a sheet (Cut doesn't count: cutting again is
  normal). The same name (ignoring case and spaces; a card the service can't read is matched by its title),
  and the same Part Studio when both link one, gets one comment naming the other card and its list. Nothing is
  blocked: two cards for one part may be on purpose, and each card's Qty is added. Checked again only when the
  card changes (`state/duplicates.json`).
- **Did you mean:** when the card's name isn't in the Part Studio, the comment suggests the closest part name
  (difflib, ignoring case and spaces, close enough to be a typo), e.g. "Did you mean P-2032?".

## 2026-10-04: part labels on the sheet preview (the user's choice: the most visible for the operator)

Drawn on the preview picture (not as sketch text in Fusion), because the picture is what's on the card and
the drawing can be big and high-contrast, where Fusion's sketch text would be thin lines in its own colour:
- **Where:** the worker finds each part's spot (`autocam_core/preview.py`, `label_spot`): inside its outline,
  outside its cutouts (both read from Fusion, `loop_segments`), as far from any edge as possible; a grid, then
  a finer one round the best, ties going to the middle. It maps that into the picture with the same framing it
  gives Fusion's camera (`preview_view`, now shared with `fx_design.preview`) and writes `<sheet>.labels.json`
  next to the picture.
- **What:** the service (`labels.py`, Pillow) draws a yellow badge with the part's cut-order number (the card's
  CUT ORDER uses the same numbers), its name and copy ("2/3") in bold white with a black outline under it, and a
  line along the top: "<sheet>: the numbers are the cut order". The picture in the job folder stays as Fusion
  made it; the card gets the labelled one.
- **If anything fails** (no Pillow, a bad spot file): the card gets the plain picture, and the log says why.
  Pillow is a service dependency now (pyproject); it was installed in the shop venv on 2026-10-04.
- **Parts that are mostly cutouts** (seen on lbl1, 2026-10-04: the spots were right, on the material, but on
  rails 4 to 8 px wide, so the 16 px badges hung off the edges): when the material is thinner than the
  smallest badge everywhere, the label goes in the middle of the part's outline instead, over the cutouts
  (core 0.12.1). On the picture that reads more clearly as "this part" than a badge on an edge.

## 2026-10-04: fewer Onshape calls (the user's choice)

The ledger (19 billable calls, runs r001-r020) showed status checks on STEP exports as the largest share (6),
then workspace pins (5). Changed:
- **Exports start together.** A run starts every uncached part's translation before it waits on any
  (`Exporter.start`). Onshape works on them side by side, and each part's checks are timed from when its own
  export started (the cache keeps the start time), so once the first part is done the rest usually need one
  check each. Before, each part started only when the one before it had finished. A problem met while
  starting (bad name, an Assembly link, a 401) is kept and raised in the usual order, so it costs no second
  call; running out of calls stops the starts.
- **A slower check schedule:** 5, 20, 65 and 125 s after the export started (was 4, 12, 28, 58, 88, 118 s).
  r004's export, done between 58 and 88 s, would take 3 or 4 checks instead of 5. A fast one (r005) still takes 1.
- **An export left from an earlier run** is past its schedule: one check per run, not a whole new schedule.
- **Estimate:** `calls_per_part_estimate` 5 -> 4, so 3 uncached parts fit the per-run cap of 15 (was 2).

Not changed:
- **Pins stay one per document per run.** It's the least that never exports a stale part: reusing a pin
  across runs would miss a fix made between them.
- **Re-exporting unchanged parts** after an edit elsewhere in the document is still the largest cost in real
  use (the cache is keyed by the document's microversion). The parts list's `microversionId` is the
  document's state, the same for every part (checked in the cached lists), so it can't say which parts
  changed. A geometry fingerprint from Onshape could, but its meaning would have to be confirmed with real
  calls first. Version links avoid all of it: no pin, and cached for good.
- **One export per Part Studio** (several parts in one STEP) needs the add-in to split the bodies; not done.

## 2026-10-04: Onshape per-run cap 15 -> 60 (the user's choice)

The ledger checked out before raising it: 19 billable calls (r004, r005, r007, r009, r019, r020 and 2 key
checks), none left dangling, each run's calls what it should have made, the most in one run 8 (r004). 60 is
the plan's figure once a real run had gone well: about 14 uncached parts per run (one pin and one parts list
per Part Studio, about 4 per part). The monthly warning (150) and the yearly cap (1500) are unchanged and
still stop a runaway.

## 2026-10-30: part spacing 1/4 -> 1/2 in (the user's choice)

At 1/4 in, two neighbouring parts' outlines with the 4 mm cutter (0.157 in) overlap by about 0.065 in, so the
second outline cuts through the first part's tabs on the side facing it (`tabs.py` places tabs without
looking at neighbours). A part boxed in by others could lose most of its tabs. 1/2 in leaves a solid strip of
about 0.19 in between the two cuts, and 0.25 in with the 1/8 in cutter. 3/8 in was ruled out: it leaves a
0.06 in sliver that can break off or grab the cutter. Cost: the arrange envelope shrinks by the spacing too,
so the largest part that fits goes from 21 x 38.5 in to 20.5 x 38 in; material use is no concern at the
sheet use seen so far (0-4 %). Open sheets keep the spacing they were nested with until they're rebuilt.

## 2026-10-31: pockets (the user's choice: adaptive, then a finish pass)

`dump_params` on the user's test part (a counterbore with a hole through its floor) named 2D Adaptive's pocket
selection `pockets`, a CadContours2dParameterValue like the contours' `contours`. Built on that:
- **Two ops:** `[pocket]` (2D Adaptive, wall stock left) and `[pocketfinish]` (2D Contour, no stock), a new tag,
  because a template allows one op per tag. `[pocketfinish]` is optional: a template without it is fine.
- **Pockets first** in the template, before the holes: a hole in a pocket floor starts at the floor, and a hole
  op run before the pocket is cleared would plunge through it. Fusion runs the ops in template order.
- **Selection:** `[pocket]` takes Fusion's pocket selection on the floors (what picking a floor in the dialog
  makes), so it clears everything above the floor, over its holes too; if that fails, each floor's outer loop
  as a chain, inside; which one took is in the run notes. Not a face contour: the first test (r023) showed a
  2D Adaptive keeps a selected face and clears around it, out to the stock. The guard rejected that program. `[pocketfinish]` takes each floor's outer loop as a chain, cut on
  the floor's side: the same rule as single cutouts (`fx_cam._inside`) with the direction reversed, since an
  outer loop's face is the region it encloses. The posted program's check against the part bodies catches a
  wrong side either way.
- **Islands** (a floor's inner loop whose walls rise from it) aren't cut around yet: the part goes back for
  manual CAM. One that reaches the top was already caught (it splits the top face).
- **Bottom height** stays the template's (the selected floor); only `[inner]` and `[outer]` are set to the
  stock bottom.
- **First Fusion runs (r023, r024, P-2015):** r023's face contour cleared the whole sheet around the floor and
  the guard rejected it. r024's pocket selection stayed in the pocket, down to its floor; the finish pass ran
  inside the wall (this Fusion's ChainSelection has no sideType, so the direction rule did it); every check passed.

## 2026-10-31: the other three templates, made from alu_4mm (the user's choice: alu_4mm's settings for all)

`alu_eighth`, `poly_4mm` and `poly_eighth` are `alu_4mm` with another tool from the pinned library and its
preset feeds, every op setting the same (one full-depth pass on outlines and cutouts, 1/16 in pocket
stepdowns, 0.02 in wall stock). Made by `fusion/tools/make_templates/` with exact text swaps, each count
checked; the scripts reproduce the committed files byte for byte. Learned in Fusion on the way:
- **An op's feeds read in mm/min, whatever the tool's unit.** The 1/8 tools are in inches; r026 ran 96 in/min
  as 3.78. Lengths do follow the tool's unit (the 1/8 outline sits a radius difference, 0.0157 in, closer).
  At ~2 in/min alu_eighth's outline failed outright ("Failed to write Job File", r025, r026); that was the feed,
  not the holder, which was dropped anyway so the 1/8 tools match the library (it gives them none).
- **The tab size check** said "didn't stick" on inch tools although the expression took: a length's value
  isn't in cm there. A matching expression now counts as taken.
- The ops' coolant stays disabled: mist comes from the material (on for aluminum, off for polycarbonate).
Results: r025 poly_4mm 100 in/min, r027 alu_eighth 48 and poly_eighth 96 (ramp 48, plunge 10), all checks
passed, no warnings.

## 2026-10-31: parts cut out of offcuts by hand (the user's choice: a rectangle, not the whole width)

Someone can cut a part out of a numbered offcut outside AutoCAM; its record wouldn't know, and the next nest
could land on the hole. Two pieces:
- **Recording it:** a line on the offcut's card, `Cut by hand: 12 to 18 along, 5 to 10 across` (inches; along
  from the end AutoCAM cut first, the same measure as its "Used along its length"; across from the left edge
  with that end toward you; no `across` = the whole width). The service takes it out once (`core offcuts.cut_away`):
  its stretch along the length, plus the part spacing either side, is used, and the room to its left and right
  becomes room beside (1 in clear: spacing + loading slack, 3 in minimum), the same mechanism as the room beside
  AutoCAM's own cuts. It writes the line back as it read it, and an unreadable line gets one reply. A scrap card
  can carry such lines from the start (so old pieces with holes in them can be added; mark the end measured from).
- **Rooms keep their place:** a sheet nested on an offcut holds its room beside by index, so while one is, a room
  the cut takes whole stays as an empty placeholder; `compact` drops them once nothing holds them.
- **A sheet already on the offcut** where the cut lands gets a comment. At the machine, the Stock checklist's new
  item "Something's already cut where this program goes" works like "The offcut isn't on the rack" (another
  offcut the program fits, or re-nest / new sheet), but the offcut stays, with a comment asking for the cut.
- Not done: a cut that isn't a rectangle is recorded as its bounding rectangle by whoever measures it.

## 2026-10-31: the service runs in the background, no window (the user's choice)

The logon task opened a visible "autocam service" PowerShell window on the shared scouting laptop; closing it, or
signing out, stopped AutoCAM until the next logon. Now the "AutoCAM service" task runs `autocam.exe run` as the
same user without a window or a stored password (Task Scheduler's S4U: it may reach the internet but not network
shares, which it doesn't use), 30 s after the PC starts and again every 5 minutes; a run still going makes that a
no-op (MultipleInstances IgnoreNew), so a stopped service is back within 5 minutes. Its output is
`logs/service.log`; the System card shows whether it's alive. To stop it for a while: disable the task, then stop
it. Fusion's task is unchanged (interactive, at logon: it needs the desktop). Registering an S4U task needs an
administrator PowerShell. `ops/start-service.ps1` stays for running it by hand.

## 2026-11-01: faced parts (the user's choice: per part, a clean top, 0.005 in, the 6 mm endmill)

A **Face** label on a part card skims that part's top clean before it's cut, so the parts come out 0.005 in
(`[face] depth_in`) thinner with a clean top. Built so a skim can only ever touch what asked for it:
- **A program of its own, run first.** Each sheet with faced parts gets `<sheet>_FACE.tap`: a second Fusion setup on
  the same stock solid and zero, so the main program and its checks are unchanged. The sheet card's RUN lists both
  in order with their cutters. The FACE program has no stops; the options (without stopping, air test) apply to
  the main program only.
- **The tool:** the "6mm 3 flute Alu" endmill (T2), the user's choice over the 12 mm surfacing bit (T3) planned
  first. It's a metric flat endmill with the same holder as the 4 mm cutters, so the face template carries it like
  the other templates do and its GUID is checked the same way (the surfacing bit would have needed the tool set
  through the API at run time). The library also has a "6mm single flute" (T9); T2 is the aluminum one.
- **The template** (`face_6mm`, made by `make_face.py` from alu_4mm's `[pocket]` op): the same 2D Adaptive, whose
  linking already passes the guard, on each faced copy's top outer loop as a chain. Radial stock to leave is
  -0.45 x the tool diameter, so the cutter's edge runs up to 0.45 D (0.106 in) past the outline: the whole top is
  covered, inside corners and thin bits included, while the cutter's center stays inside the outline. Optimal load
  0.5 D (the library's 1.75 mm stepover is for full-depth clearing). Heights from the stock top, the bottom set to
  the stock top less the depth by the automation.
- **Checks (both sides):** the FACE program goes no deeper than the stock top less the depth (one posting unit,
  0.0001 in, of slack), must reach it, has no M0 and no outlines, and lists only parts that ask for facing; the
  sheet's guard (clamps, reach, rapids, mist) with T2's radius. The worker also checks every point where the cutter
  is below the stock top is inside a faced part's outline, so the skim never reaches a part that didn't ask for it.
  The service refuses a faced sheet with no FACE program, or one that doesn't face exactly the faced copies; a
  FACE program that fails stops the whole sheet.
- Depth is capped at 0.02 in in config and the job: deeper is a pocket, not a skim. Part spacing (0.5 in) keeps
  neighbours clear of the 0.106 in overreach.
- A part placed by hand gets its FACE program the same way, on its own box and zero (run it before each copy).

## 2026-11-01: a hand cut can be recorded from its program (the user's choice: optional, the zero measured)

People cutting their own parts zero X/Y on the piece wherever they want the part, not at the sheet's corner, so a
program alone says what was cut but not where. Optionally (`[hand_cuts] programs`, on), the offcut's card can carry
the .tap they ran plus one measured line, `Zeroed at: 10 along, 5 across` (the zero, measured like a "Cut by hand:"
line), instead of the four numbers of the rectangle. The service works out what the cutter covered below the stock
top (its center's path grown by its radius: `Cutter: 4 mm` on the card, else `[hand_cuts] cutter_in`, 1/4 in),
moves it to the zero, cuts it down to the piece, and records it like a typed line (written back as one, "from
P-3001.tap"). Programs with Z0 on top of the stock are read too (and the reply says so); mm programs are refused.
With more than one new program on a card, each line names its program (`... for P-3001.tap`). A scrap card can
carry programs the same way. Each attachment is downloaded once (cached) and recorded once. The typed line still
works and stays the fallback. Not done (for later): WinCNC's machine coordinates instead of a tape (needs the corner
stop, or a second reading at the piece's corner), and AutoCAM choosing the spot for a program (option 3).

## 2026-11-01: AutoCAM places someone's own program (option 3, the user's choice)

A card in Ready for CAM with a .tap attached instead of an Onshape link or STEP file, plus `Material:` and
`Thickness:` lines (`Cutter:` if it's over 1/4 in), is a part someone CAM'd themselves. AutoCAM doesn't CAM it and
starts no Fusion run (`own_programs.py`, runner `own_programs`; `[hand_cuts] place_programs` switches it off):
- **The spot:** the program's footprint (what its cutter covers below the stock top) can't be turned, so it goes as
  it is: the smallest room beside earlier cuts or free stretch on an offcut of that stock that it fits, the way round
  the offcut was last cut if it can (a scrap never turns), else a new sheet's front-left corner. It keeps half the part
  spacing (0.25 in) inside the nest region: a footprint right at the region's edge touches the clamp strip.
- **The program on the card is a copy moved there:** every X/Y shifted so its zero is the piece's front-left corner,
  like AutoCAM's own sheets (not G53 lines, not a G4 dwell's X; arc centers are relative). The copy gets the sheet
  checks at its real spot: Z floor (so Z0 must be the spoilboard; a program zeroed on top of the stock is sent back
  saying how to fix it in Fusion), clamps, reach, rapids, allowed codes. The card also says how to run the original
  instead (zero at the corner, move to the offset, zero again): the operator never needs absolute positions.
- **The card** goes to Sheet review with the Review and At the machine checklists (and Offcut on a new sheet), the part
  card to On a sheet. Labels are H1, H2, ... Moving the part card back to Ready for CAM replaces the card (the way to
  get another spot if the offcut isn't on the rack). No Options or Stock checklist on these cards.
- **At Cut** the cut is taken out of the offcut like a hand cut (the room either side kept; not listed as "Cut by
  hand"), or the rest of a new sheet becomes an offcut if its box is ticked; the part card follows to Cut.
- Not done: no preview picture or machining time on the card, one program per card, not counted in the season's
  stock tally, and AutoCAM's nested parts don't share the piece with it in the same run.

## 2026-11-01: one branch, `main` (the user's choice)

Everything since M0 had been on `m1-offline`, with a short branch per Fusion test (`pockets`, `poly4mm`,
`handcuts`, `face`). `main` was moved up to `m1-offline` (a fast-forward: it held nothing else) and is now the branch
the scouting laptop runs and new work goes on; the others were all fully in it and were deleted. A change that needs a Fusion run
still gets a short branch, checked out on the laptop for the test and deleted once `main` has it.

## 2026-11-01: bearing holes get their own op, and press fits are tuned in config (the user's ask: no Fusion)

Bearing holes (`holes.bearing_sizes_in`) used to be bored with `[bore]` (a warning on every sheet with one), so
their size couldn't be tuned apart from every other bore. Now every template has a `[bearing]` op: `[bore]`'s
settings plus a finishing lap, so the final size comes from one light (0.005 in radial) pass at full depth rather
than the roughing helix. It's added to alu_4mm by `make_templates/add_bearing.py` (an exact text change, checked),
and the other templates are made from alu_4mm as before (the generators now count ops instead of assuming six; a
test checks each committed template is exactly what its generator makes). Fusion was never opened by hand.
Press fits are tuned without Fusion: `holes.bearing_stock_in` is the radial stock `[bearing]` leaves on each sheet
(+ tighter, - looser; 0.0005 = 0.001 in on the diameter; at most 0.01; 0 keeps the template's, which leaves none).
Seen in Fusion (r030, r031): the lap took exactly 0.005, and 0.002 of stock made the hole 0.004 smaller across.

## 2026-11-01: fit series (the user's choice: one feature per part, tuned at the machine; can be switched off)

A part card's `Fit: 1.125 hole` (its bored holes of that size) or `Fit: outline` line: that feature is cut tight by
the sheet's program (`[fit] start_in`, 0.003 in radial: a hole smaller, the part bigger), and the sheet gets lap
programs, `<sheet>_FIT<n>_<k>`, each leaving `step_in` (0.001) less, down to 0.001 loose (`laps` 4). Same cutter and
zero, run after the program one at a time until it fits. Copies of a part share a series. `[fit] enabled` turns it
off. The simple version: fitted parts are always held by tabs and stay in at their stop, since the laps run after
the outline (a lap is light: 0.001 in). Drilled holes can't be fitted, and parts placed by hand aren't yet.
- **Fusion:** the fitted holes get their own `[bore]`/`[bearing]` op (the template applied again in the sheet's setup,
  the other ops it brings deleted); an outline fit sets the stock on the copy's `[outer]` ops. Each lap is a setup of
  its own on the sheet's stock and zero with only that op (outline laps with the same tabs at the same points),
  posted and checked before the main program, so a failing lap stops the sheet.
- **Checks:** `check_fit_program` (the sheet's guard and tool, no stops, only fitted parts, an outline lap's outlines
  exactly the fitted copies and each to the bottom), the gouge check, and the service wants every lap with the stocks
  the job asked for.
- **Learning:** the sheet card has a Fit checklist (which run fit, or still tight after the last). Ticking one keeps
  that stock per material and hole size (or part, for an outline) in `state/fits.json`, and the next series is
  centred on it: the program two steps tight, the laps from one tight to two loose.
- **Learned in Fusion on the way** (r032-r036):
  - A 2D Contour's axial stock to leave follows its radial by default: 0.002 radial stopped an outline 0.002 above
    Z0 (the depth check caught it). Fitted ops set the axial to 0.
  - Renaming a Bore op that has no holes selected yet makes Fusion regenerate it, and it fails with a modal
    "Failed to generate toolpath" dialog that holds the job until someone clicks OK. Found with the new call trace
    (`fusion_calls.log` in each job folder). Hole ops are never renamed now; the fitted one keeps "[bearing] holes
    (2)".
  - Op names an applied template brings get " (2)", " (3)" across the whole document; renamed outline ops may repeat.

## 2026-11-01: fits on holes cut as cutouts (the user: a required feature), the team's way

A round hole over the bore range (1.5 in), or a small one that's contoured, is cut by `[inner]`, so a `Fit:` line for
it gets that op from the template again, alone and tight. The team's way of fitting such a hole: cut it with tabs,
break the slug out by hand, then run stock-to-leave contours until it fits. So the sheet's program keeps the slug
tabbed, the card says to break it out first, and the laps are full contours with no tabs (the first one also takes
the tab stubs off the wall). Outline fits keep their tabs in every lap (the part has to stay held). Every lap must
now reach the stock bottom. `Fit:` lines read fractions and millimeters too (`1/2` was read as a 1 in hole).
Seen in Fusion (r038, a made-up 6 x 6 x 3/16 plate with a 2.0 in hole: no cached part has a round hole over 1.5 in):
the program's cutout has its tabs, each lap opens the path 0.001 in, none has a tab, all to Z0.

## 2026-11-01: the Onshape panel, Phase 1 (the user's choices)

A "Send to AutoCAM" panel in Onshape, built into the service instead of the teammate's Node tool (its card format,
call budget and temporary tunnel didn't fit). Choices: cards go straight to Ready for CAM, with a Send to Drafts
switch; the quantity comes from the assemblies but stays changeable; no spares box; every option in one list; the
student picks a missing material and AutoCAM sets it in Onshape; the style is BreadCompetition's (light only, since
it sits in Onshape). Who sent a card: a name typed once, not Onshape sign-in (OAuth inside Onshape's frame is a day
of fiddly work for little gain). Hosting: its own named Cloudflare tunnel on team5940.org (`autocam`), run on the
laptop's Windows side by the service, apart from BreadCompetition's. The panel's Onshape calls have their own daily
and yearly limits, counted apart from runs', so it can never use up a run's calls. A second card for a part that
has one going is refused; its quantity can be changed instead.

## 2026-11-01: bent sheet metal parts (the user's choices)

Cut the flat pattern Onshape makes (the parts list with includeFlatParts), and tell the operator how to bend it from
Onshape's bend table, on the sheet card. No 3/32 in stock. Scribing the bend lines with the router: an option, off by
default. A top split into faces in one plane (every flat pattern's is, along its bends) is cut as one outline for every
part, not only bent ones: the old refusal existed only because the outline came from one face.

## 2026-11-01: Onshape webhooks and part pictures (the user is an enterprise admin)

- **Webhooks, one per document workspace** with parts on a sheet in Sheet review or Ready to cut
  (`panel/hooks.py`). A company-wide webhook was the plan, but Onshape refuses model changes at that level
  ("lifecycle.changed event is not supported at enterprise level"). Event `onshape.model.lifecycle.changed`,
  collapseEvents, filter `{$DocumentId} = ... && {$WorkspaceId} = ...`, calling
  `<public_url>/api/onshape-hook/<AUTOCAM_WEBHOOK_SECRET>`: the address is its password (Onshape can't send the
  panel's key), and the server's log writes it as `<secret>`. Made when a sheet first needs it (one call), listed
  once a day (one call), removed a week after no sheet on the board needs it (one call); events cost nothing. An
  element Onshape says changed is checked once nobody has touched it for 5 minutes; a sheet whose documents all have
  webhooks gets the periodic check only every 72 h instead of 12. Seen working 2026-11-01: registered for Part 13's
  document, Onshape's validation and a ping came through the tunnel (200). A real edit's event is still to be seen.
- **Part pictures:** Onshape's shaded isometric view of each part on a sheet on the board (as it was CAM'd: the job's
  microversion), 300 x 300, attached to its card as the cover. One panel call per part state, cached for good, three
  per tick at most. Seen working: Part 13.
- Not done: the Manufacturing property (the user changed their mind). The enterprise's API usage on the System card
  waits on finding where Onshape exposes it (the public API has no endpoint for it).

## 2026-11-01: more than one cutter per part (core 0.19.0; the user's "bigger idea" #1)

The brief's "later option" (decision 21: an 8-32 hole moves a whole aluminum sheet to the 1/8 in) done per feature.
A part whose only problems for the 4 mm are **through holes too small for it** and **cutouts with an inside corner
too tight for it** is split (`tooling.split_features`): those go in the sheet's `<sheet>_SMALL` program with the
1/8 in, run just before the sheet's own, and the sheet stays on the 4 mm with its outlines last (so a part is only
freed by the last program, with its stops). Two programs because the post allows one tool per program; the fixed
tool setter makes the swap quick. Still the whole part on the 1/8 in (and its whole sheet, as before): a tight
corner on the outline or in a pocket, a blind hole, or the Tool 1/8 label. The SMALL program is a setup of its own
on the sheet's stock and zero with the small tool's template (its GUIDs checked), filled only with those features
(tabbed parts' tight cutouts keep their tabs), the other ops deleted untouched. Checked both sides
(`check_small_program`: the small tool's guard, through to Z0, no outlines, no stops) plus the tool center never in a
part; a failure stops the sheet, since its parts would miss those holes. `[tooling.<family>] split = true` (false:
as before). Seen in Fusion: r052, P-2011 with its two 0.156 holes made 0.136: the 1/8 in drills just those two (5 s),
the 4 mm does the other 47 features and the outline.

## 2026-11-01: CAD changed, with an accept step (from the teammate's panel)

A part that changed in Onshape after it was CAM'd, while its sheet isn't cut: the **CAD changed** label (purple) on
the part card and the sheet card, a comment on each, and a "CAD changed" checklist on the part card with one box,
"Cut it as it was CAM'd (the change doesn't matter here)". The two ways out: tick it (or the panel's "Cut it as it
is", the same thing, with who in the sheet's comment), or nest the new version (the card back to Ready for CAM, or
the panel's button). The service clears the labels within a couple of minutes of either (`panel/board.py`
settle_changes). A sheet card loses its label when none of its parts is unsettled. Cut with it unsettled: the part
card keeps the label, since what was cut is the old version. Changed again after being accepted: asked again (once
per new shape). Nothing is blocked: the operator sees the label, as on the teammate's panel. Sheets in Sheet review
are checked every 12 hours (not when they're new: they were just CAM'd), one current-state call per document, so a
board of sheets from five documents costs about ten of the panel's 60 calls a day.

## 2026-11-01: scribed bend lines (core 0.18.0; the user: "Do both and make them both options", 10 to 15 thou)

- **Two ways, per part, off unless asked:** `Scribe: endmill` (the sheet's own cutter, a `[scribe]` op in the sheet's
  program before the outlines: no tool change, a line as wide as the cutter) or `Scribe: chamfer mill` (T16, the
  6 mm 45 degree chamfer, in a `<sheet>_SCRIBE` program after FACE and before the sheet's: a fine line). Both
  `[scribe]` 0.012 in below the part's top (a faced part's top is the face depth lower). Only bent parts.
- **Where:** Onshape splits a flat's top into the flats and a strip per bend (Part 13: 0.1669 in, R 0.05 on 1/8,
  k 0.45). A strip is a top face with exactly two straight, parallel edges shared with other top faces, as far apart
  as one of the part's bends can be (angle x (R + k T), k 0 to 1); its centerline is scribed. Not exactly one strip
  per bend: that part isn't scribed (no guessing).
- **How in Fusion:** a copy of the template's `[outer]` 2D Contour along root sketch lines, leads, ramp, tabs and
  extra passes off, from the stock top down by the depth. Compensation 'off' is listed but refused ("Invalid
  enumeration value"), so the radial stock is minus the radius it offsets by plus 0.001 in: the endmill's radius;
  the chamfer mill's tip radius (0 for T16: Fusion offsets a cone by its tip; its full radius was "Failed to write
  Job File", r046). The chamfer mill is set on the op from its library entry (`Tool.createFromJson`); Fusion gives
  that a GUID of its own, so it's checked by type, units, size, angle, tip, flutes and number instead.
- **Never deleted once changed:** deleting an op whose settings were changed crashed Fusion's next toolpath
  generation three times (r041-r043, in `makeStockContours`). A scribe op that fails is suppressed instead; the
  fake adapter refuses to delete a changed op. The worker's `fusion_calls.log` is now written as it goes.
- **Checks, both sides:** the sheet's program may scribe only for parts that asked, before any outline, at exactly
  the depth; a SCRIBE program the same with the guard's floor at the depth and no outlines or stops. The worker also
  checks every scribing point is within 0.002 in of a bend line, and keeps scribing out of the "tool center in a
  part" check.
- **Never holds a sheet up:** a part whose lines can't be found, or whose scribe op or program fails, is cut anyway
  with a CHECK line to mark its bends by hand. If any other op on the sheet fails with scribing there (a broken
  scribe op left the outline after it "Failed to write Job File", r048), the scribing is suppressed and the sheet
  generated again without it.
- **U or D beside each line** (the user: "just do the U or D"): U, the flange comes up toward the scribed face (it
  ends up inside the bend); D, away. Onshape's bend table says up/down from the flat pattern's top, the exported
  STEP's +Z: checked on Part 13's real bent body (its row of 0.256 in holes sits off-center across the bend and is
  mirrored between the flat seen from +Z and the bent part seen from inside the bend, on both flanges, so the
  flat's top is outside its "down" Bend D). The extractor records which side of the STEP is up
  (`PartGeometry.axis_z`; Fusion keeps the file's coordinates: r048 imported Part 13 exactly at the STEP's box).
  Each letter's strokes are separate sketch lines, 0.002 in apart (one chain of connected lines came back "missing
  selections", r048). Letter size: 0.18 x 0.25 in with the chamfer mill, five cutters tall with the endmill. It goes
  at the first of a few spots along the line, either side, where its box (grown by the cutter's radius and a margin)
  is on the part clear of holes, edges and every bend zone. No room, or two bends of the same size going opposite
  ways (which strip is which can't be told): no letter, a CHECK line, and the bend list still says.

