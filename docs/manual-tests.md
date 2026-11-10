# Manual tests

Everything Fusion-side or machine-side that can't run in CI. Fusion code counts as **untested** until a
section here has been run and its results recorded (date, who, Fusion version, outcome).
Record per-call API results in `docs/fusion-api-status.md`.

## M1.0: API probe, parameter dumps, Phase 1 smoke test

**Where:** Fusion on a Windows PC that can see `C:\dev\frc-autocam`, with branch `main` checked out (`m1-offline` until 2026-11-01).
All three scripts only read the repo, and they write their output inside it. The probe and dump scripts change
nothing in Fusion documents. The Phase 1 script creates a new document of its own.

**Adding a script in Fusion:**
1. Open UTILITIES → ADD-INS → Scripts and Add-Ins (`Shift+S`).
2. On the Scripts tab, use the "+" next to My Scripts to add an existing script, then pick the script's folder.
3. Select the script and click Run.

### 1. API probe (about 2 minutes)

Add `C:\dev\frc-autocam\fusion\tools\api_probe` and run it. No document needs to be open.
- [ ] A message shows the Fusion and Python versions and the output file,
      `fusion\tools\api_probe\out\api_probe_<time>.json`.

### 2. Parameter dumps (about 10 minutes)

Add `C:\dev\frc-autocam\fusion\tools\dump_params`. It reads the open document and writes
`fusion\tools\dump_params\out\<document>_<time>.json`.
- [ ] **A finished manual job** that has been cut before. Ideally it has drill, bore, pocket and 2D contour ops,
      inch units, WCS at the stock box point, and an NC program using our post. Open it and run dump_params.
- [ ] **Each template that exists** (aluminum 4 mm, aluminum 1/8, poly 4 mm):
  1. Open any plate and make a setup like the manual job's.
  2. Right-click the setup → Create from Template → pick the template → Generate.
  3. Run dump_params.

### 3. Phase 1 smoke test, run as-is (about 30 minutes)

Purpose: learn which API calls in `fusion/autocam_nest/autocam_nest.py` fail before restructuring it
(brief, "How to treat it"). Don't fix anything in the script; just record what happens.

**Prepare:**
- **Parts:** 2-3 plain through-cut 1/8 in aluminum plates (holes fine, no pockets or countersinks). Export each from
  Onshape's UI (right-click the part → Export → STEP) into e.g. `C:\autocam-smoke\parts\`. UI exports are not
  API calls.
- **Template:** two 2D Contour ops, `[inner] holes` and `[outer] outline`.
  - Both use the `4mm 0 flute Aluminum` tool.
  - Bottom height = stock bottom, offset 0.
  - Clearance height at least 2 in above the stock top.
  - Save it as an `.f3dhsm-template` file in `C:\autocam-smoke\templates\`.
- **Job file:** copy `fusion\autocam_nest\job.example.json` to `C:\autocam-smoke\job.json`. Edit `parts` (name,
  `step` path, `qty`, with at least one qty of 2 or more), `thickness_in`, and `cam_template`. Leave `post` as it is.

**Run:** add `C:\dev\frc-autocam\fusion\autocam_nest`, run it, and pick `job.json`. It can take several minutes.
It writes `out\` next to the job file, containing `<job>.f3d`, `<job>_S<n>.tap`, `<job>_nest.png` and
`<job>_result.json`. If a dialog shows a traceback, copy all of it.

**Record** `ok`, the exact error text, or what you saw:

| # | Step | API calls involved | Result |
|---|---|---|---|
| 1 | STEP files import, one component each | `importManager.createSTEPImportOptions`, `importToTarget` | |
| 2 | Good plates pass the plate checks | `measureManager.getOrientedBoundingBox`, `pointContainment` | |
| 3 | Arrange runs and parts land in the sheet rectangle | `arrangeFeatures.createInput`, `setPlaneEnvelope`, `add` | |
| 4 | `notes` in result.json mention `frameWidth`, `isCreateCopies` or `isPartialArrangeAllowed`? | try_set | |
| 5 | Qty > 1 makes that many copies, and result.json counts them on the right sheets | `resultEnvelopes`, binning | |
| 6 | Setup stock is the sheet solid (check the setup's Stock tab) | `Setup.stockSolids` | |
| 7 | Template loads into each setup | `CAMTemplate.createFromFile`, `createFromCAMTemplate2` | |
| 8 | `[inner]` and `[outer]` get the right geometry | `CadContours2dParameterValue`, `LoopTypes` | |
| 9 | Toolpaths generate without errors | `generateAllToolpaths` | |
| 10 | Op errors and warnings are reported in result.json | `hasError`, `warning` | |
| 11 | Our post is found by its description | post library walk | |
| 12 | A `.tap` is written per sheet | NC program, `postProcess` | |
| 13 | result.json notes "lowest Z in program: ..." | `check_tap_floor` | |
| 14 | `.f3d` is exported | `exportManager` | |
| 15 | The nest preview PNG looks right | `saveAsImageFile` | |
| 16 | Simulating the `.tap` shows the origin at the sheet's bottom front-left corner | setup `wcs_origin_boxPoint` | |

**Send back:**
- The filled table.
- The smoke `out\` folder, copied to `C:\dev\frc-autocam\docs\smoke\`.
- Nothing extra for the JSON from steps 1-2: it's already in the repo's `out\` folders.

**Result (2026-10-01):** stopped at step 3, `arrangeComponents.add(face)` →
`RuntimeError: 2 : InternalValidationError : arrange2DDefinition`. Steps 4-16 didn't run; `pipeline_probe`
covered them instead.

### 4. pipeline_probe (done 2026-10-01)

Add `C:\dev\frc-autocam\fusion\tools\pipeline_probe`, run it, pick the smoke template. Results are in
`docs/fusion-api-status.md`. The posted programs passed the guard but only went down to Z 0.125 (the plate
top): the template's outline used bottom height "From contour", so it followed the selected top face.

### 5. pipeline_probe2 (about 3 minutes)

First run (2026-10-01): answered move vs copy, quantity by extra occurrences, op renaming and op order.
Second run: answered the bottom height, Manual NC and op reordering, and found that "pinned" means
`isGroundToParent`. Version 3 (2026-10-01, `out\20261001-155657`): answered the rest of Arrange (clearing
ground-to-parent, the STEP import path, parts that don't fit, a second sheet, which way up). Done.

1. `git pull` on the Fusion PC (branch `main`).
2. Add `C:\dev\frc-autocam\fusion\tools\pipeline_probe2` and run it. Pick the same smoke template when asked.
   Scratch documents open and close; it saves nothing.
3. Paste the output of:
   ```powershell
   $f = Get-ChildItem C:\dev\frc-autocam\fusion\tools\pipeline_probe2\out\*\probe2.json | Sort-Object LastWriteTime | Select-Object -Last 1
   $j = Get-Content $f.FullName -Raw | ConvertFrom-Json
   "$($f.FullName)  version $($j.version)"
   $j.steps | ForEach-Object {
     "{0,-5} {1}  {2}{3}  {4}" -f $_.ok, $_.step, $_.error, $_.note, ($_.result | ConvertTo-Json -Compress -Depth 8)
   }
   ```

## M1.2 to M1.7: restructured pipeline in Fusion

### First run of `autocam_run` (about 20 minutes)

The whole Fusion side in one script: import, plate checks, nesting, CAM, posting, pauses and the checks the
service repeats. Every Fusion call it makes that hasn't been seen working yet is listed at the end of its
message box and in `result.json` (`worker.untested_steps`).

**Prepare:**
1. `git pull` on the Fusion PC.
2. **The `alu_4mm` template**, saved as `C:\dev\frc-autocam\fusion\templates\alu_4mm.f3dhsm-template`:
   - Start from the team's usual ops for 4 mm aluminum: a Drill op, a Bore op, a 2D Contour for cutouts and
     a 2D Contour for the outline.
   - Name them `[drill] ...`, `[bore] ...`, `[inner] ...` and `[outer] ...`.
   - Every op uses `4mm 0 flute Aluminum` from the pinned library (`fusion/tools/5940_Tool_Library.tools`).
   - Clearance height at least 2 in above the stock top; full retraction; the rest of
     `fusion/templates/README.md`.
   - Export the template to that file name.
3. **2-3 test plates** in 1/8 in aluminum: plain through-cut plates with a few holes, no pockets. Export each
   from Onshape's UI as STEP (not an API call), anywhere on the PC.

**Run:**
1. Add `C:\dev\frc-autocam\fusion\autocam_run` (Scripts and Add-Ins → "+") and run it.
2. Answer **Yes** (pick STEP files), select the plates, enter `al6061`, then a quantity for each (e.g. `2,1,1`).
3. Wait for the message box. The document stays open.

**Check and record:**

| # | Check | Result |
|---|---|---|
| 1 | The message box status and one line per sheet (copy its text) | |
| 2 | In the design: each part sits inside its sheet's stock outline, none overlapping, none upside down | |
| 3 | In Manufacture: one setup per sheet; ops `[drill]`/`[bore]`/`[inner]` and one `[outer] pNN-n` per part copy | |
| 4 | Simulate one setup: stock is the full 24 x 48 sheet, origin at its bottom front-left corner, every part cut free | |
| 5 | Open the `.tap` in `fusion\autocam_run\out\<job>-<time>\`: a pause block before each `[outer ...]` but the first | |
| 6 | Any sheet "NOT OFFERED": its reason (also in `result.json` under `sheets[].errors`) | |
| 7 | A plate with drilled/bored holes **and** a cutout on the same face: simulate it; the cutout is cut on the inside (not one tool diameter too big) | |
| 8 | Run again with one part's quantity too high to fit: it's reported as deferred, and the other parts didn't move (check 2 again) | |
| 9 | A plate modeled standing up in Onshape (not flat on the top plane): nested flat, or rejected with "can't lay it flat" | |
| 10 | A part with rounded corners: not rejected for "crosses the edge" | |

**Send back:** the message box text (or a screenshot) and, if anything failed, the end of `worker.log` from
the same folder.

### Already known checks
- [ ] A pocketed part posts without "rapid sideways below the stock top". If it doesn't, the template's linking
      isn't full retraction; see `fusion/templates/README.md`.
- [ ] `result.json` reports each sheet's **nominal** stock thickness (e.g. 0.125), not the measured one. The
      service rejects any other value.

## M1.7: saving to Fusion Team

Needs `[fusion_team] project` and `folder` in `config/autocam.toml` (a nested folder is written `CAM/Auto`).
The folder must already exist; the automation doesn't create folders.

| # | Check | Result |
|---|---|---|
| 1 | Run a job (`autocam_run` or the add-in): the file appears in that project folder, named after the job (e.g. `t181442-al6061`) | |
| 2 | `result.json` has `fusion_team.url`, and the link opens the file in a browser | |
| 3 | Set a project name that doesn't exist: the job still finishes, with the local `.f3d` and a note naming the projects it did find | |

## M1.6: pause air tests (decision 19). Required before any real cut.

Run on the machine, work zero set as usual. Easiest: a real sheet with 2 or more parts. Tick **Add an air test
program** under Options on its card, and within a minute the card has `<program>_AIRTEST.tap`: the part outlines and
the stops between them, at 200 in/min, raised so the cutter stays 0.5 in above the sheet (it can run with the
sheet clamped). Or use `fusion/tests/pause_air_test.tap`
(no mist) and the generated `pause_air_test_mist.tap` (M1.6) with no material.

- [ ] The machine stops at every pause, and nothing moves until start/continue is pressed.
- [ ] The spindle actually stops at each pause and restarts after resume (4 s spin-up before moving).
- [ ] Mist turns off at each pause and back on after resume (mist file only).
- [ ] The first move after resume comes down from the top, not sideways at cutting depth.
- [ ] ENTER resumes (open question 9; the WinCNC manual says ENTER after a bare G4): ______  (never Esc: it
  aborts the job)
- [ ] (Air test file) The cutter never comes closer than 0.5 in to the sheet, and its highest moves stay inside
  the Z travel (no soft-limit error).

## Before the first real cut

- [x] Open question 4: Z0 is the spoilboard (decision 22); the fixed tool setter zeros there (the user, 2026-10-31).
- [ ] Open question 10: no-tab trial on one taped sheet (decision 6).
- [ ] Both air tests above passed.

## M2: add-in hot-folder worker

The add-in runs whatever the service puts in `C:\dev\frc-autocam\queue\incoming\`, one job at a time, with
no dialogs. Until the service runs for real, queue jobs by hand from earlier `autocam_run` runs.

**New code:** the add-in reloads itself while idle when `CORE_VERSION` changes or a `queue\reload_addin`
file appears (checks 9-10). Stop/Run still works and is needed once to get the self-reload itself.

**Start it:** Scripts and Add-Ins (`Shift+S`) → **Add-Ins** tab → "+" → `C:\dev\frc-autocam\fusion\autocam_addin`
→ Run. Leave "Run on Startup" off for now (that's M5).

**Queue a job by hand** (copies the newest `autocam_run` job into the hot folder under its job id):
```powershell
$src = Get-ChildItem C:\dev\frc-autocam\fusion\autocam_run\out\*\job.json | Sort-Object LastWriteTime | Select-Object -Last 1
$id = (Get-Content $src.FullName -Raw | ConvertFrom-Json).job_id
New-Item -ItemType Directory -Force C:\dev\frc-autocam\queue\incoming | Out-Null
Copy-Item $src.FullName "C:\dev\frc-autocam\queue\incoming\$id.json"
```
To queue the same job again, first delete `queue\done\<id>` (or `queue\failed\<id>`).

| # | Check | Result |
|---|---|---|
| 1 | Within ~10 s of starting the add-in, `queue\worker_heartbeat.json` exists and its `ts` keeps updating | |
| 2 | A queued job ends up in `queue\done\<id>\` with `result.json`, the `.tap` and the `.png`; the document is closed afterwards | |
| 3 | `logs\fusion_worker.log` shows the job's attempt and its status | |
| 4 | Close Fusion (Task Manager) while a job runs; restart Fusion and the add-in: the job runs again (attempt 2) | **Passed** 2026-10-03 (by accident: signing out of Fusion closed it during `ttabs3-al6061`; the add-in had to be added again, then "start-up: re-queued", attempt 2 finished fine) |
| 5 | Kill it again during attempt 2: on the next start the job goes to `queue\failed\<id>\` saying it was started 2 times | **Passed** 2026-10-03 (Fusion crashed on both attempts of `tsize1`: "the worker started this job 2 times and never finished it") |
| 6 | Edit a queued job's `"core_version"` to `"0.0.1"`: it goes to `failed\` with `CORE_VERSION_MISMATCH` | |
| 7 | While the add-in is idle, Fusion stays usable (only a job run blocks it) | |
| 8 | Stop the add-in, `git pull`, start it again: the new code runs (the log's start line) | |
| 9 | Self-reload: while the add-in is idle, create an empty `queue\reload_addin` file. Within ~10 s the file is gone and `logs\fusion_worker.log` says `reloaded: core X -> X` | **Passed** 2026-10-02 23:42 |
| 10 | After a `git pull` that changes `CORE_VERSION`, the log says `reloaded: core <old> -> <new>` within ~10 s, and `queue\worker_heartbeat.json` shows the new `core_version`, with no Stop/Run | |

## M3: real board setup and the first supervised run

The service runs on the **same PC as Fusion**: they share the `queue\` folder.

### 1. Python and the service (once per PC, about 10 minutes)

```powershell
winget install -e --id Python.Python.3.12        # or python.org; tick "Add python.exe to PATH"
# close and reopen PowerShell, then:
cd C:\dev\frc-autocam
py -3.12 -m venv C:\dev\venvs\frc-autocam
C:\dev\venvs\frc-autocam\Scripts\pip install -e .
C:\dev\venvs\frc-autocam\Scripts\autocam config-check
Copy-Item .env.example .env                      # .env is gitignored: keys never go into git
notepad .env
```

### 2. Trello key and token (only you can do this; never paste them into chat)

1. Sign in to Trello as the account the service will act as (a mentor's, or a bot account in the team's
   workspace; a bot uses one of the 10 Free-plan seats).
2. Open https://trello.com/power-ups/admin, click **New**, name it `5940 AutoCAM`, pick the team's workspace.
   Leave the iframe connector URL empty. Create it.
3. On its page, open **API key**, click **Generate a new API key**, and copy the key into `.env` as
   `TRELLO_API_KEY`.
4. Next to the key, follow the link to generate a **token** by hand, click **Allow**, and copy the token into
   `.env` as `TRELLO_TOKEN`.

### 3. Make the board

```powershell
C:\dev\venvs\frc-autocam\Scripts\autocam trello-setup --create "5940 AutoCAM"
```
It creates:
- the 8 lists;
- the `System` card in Control (run comments go there);
- a "How to add a part" card and the "New part" template (with the "Nest this part" box) in Drafts;
- the `Smoked` and `Tool 1/8` labels.

On an older board it also archives the `Run nest` list and card.

It prints a `[trello]` block. Send that block over to be committed to `config/autocam.toml` (IDs aren't secrets),
or set up an existing board instead with `--board <short link>`.

### 4. Onshape API keys

1. Create a key at https://dev-portal.onshape.com/keys, or your enterprise's developer portal. Read access is enough.
2. Copy the access key and secret key into `.env` as `ONSHAPE_ACCESS_KEY` and `ONSHAPE_SECRET_KEY`.
3. If the team's Onshape address isn't `cad.onshape.com` (an enterprise address like `<name>.onshape.com`),
   `onshape.base_url` must be that address: enterprise keys only work there.
4. Check them with one logged call (it counts against the budget, in this PC's ledger):
   ```powershell
   C:\dev\venvs\frc-autocam\Scripts\autocam onshape-check
   ```
   It says whose keys they are, or why Onshape refused them.

### 5. First supervised run (1-2 parts; `onshape.per_run_max_calls` is 15 for now)

1. Add one or two real part cards to **Ready for CAM** (the "New part" template in Drafts has the format: the
   Part Studio link, workspace or version, and a quantity).
2. Check the board without Trello writes or Onshape calls:
   ```powershell
   C:\dev\venvs\frc-autocam\Scripts\autocam dry-run --offline --verbose
   ```
3. With Fusion and the add-in running, start the service (`autocam run --verbose`). About 2 minutes after the
   last card landed in Ready for CAM, the System card says "Run r00N started". (`autocam tick --now` runs one
   pass without the wait.)
4. When the add-in has finished the job, the next pass puts the sheet cards in **Sheet review**.
5. Check `autocam ledger` (Onshape calls used) and the cards' comments.

### 5b. Better nests (needs the add-in restarted once: Shift+S > Add-Ins > autocam_addin > Stop, Run)

| # | Check | Result |
|---|---|---|
| 1 | Put 2+ different parts of one material in a run: `logs\fusion_worker.log` has a line like "0.125 in: kept 'as listed' (as listed: 6 placed on 1 sheet(s), last 21.3 in; biggest first: ...)" | **Passed** (r006; the job's own `worker.log`) |
| 2 | The sheet's parts are where the preview shows them, with no extra copies in the setup (the hidden copies of the other tries don't get toolpaths) | |
| 3 | The sheet card has a "Sheet use: N% ..." line | **Passed** (r006: 4%, the last 32 in empty) |
| 4 | Note how long the job took compared with earlier runs (up to 3x the Arrange time) | |

### 5c. Offcuts

| # | Check | Result |
|---|---|---|
| 1 | A new sheet card with room left has an **Offcut** box, ticked | |
| 2 | Move it to Cut: an offcut card appears in **Offcuts** ("... offcut - N in free"), and the sheet card links to it | |
| 3 | Queue parts of the same material and thickness: the new sheet card's title says "offcut", and its Stock line says which end goes where | |
| 4 | Load the sheet as the card says and **air-test it first**: the cutter stays in the free stretch, clear of the earlier cut-outs | |
| 5 | Cut it: the offcut card is updated with what's left, or archived when it's used up | |
| 6 | Untick the Offcut box before moving a sheet to Cut: no offcut card | |
| 7 | Cut a sheet with one small part: the offcut card says "+ room for small parts" and gives the room's size | |
| 8 | The next small part of that stock goes beside the earlier cut (worker log: "placed N (N beside earlier cuts)"). **Air-test it first**: the cutter stays at least about 0.5 in clear of the earlier cut-outs | |
| 9 | An offcut whose free stretch is used up but has room beside its cuts is kept ("small parts only"), not archived | |

### 6. The service picks up new code by itself

| # | Check | Result |
|---|---|---|
| 1 | `autocam run --verbose` prints "the service restarts by itself..." and then the usual log | **Passed** 2026-10-02 |
| 2 | Create an empty `C:\dev\frc-autocam\state\restart_service`: within ~1 min the window says "restarting the service with the new code" and the log shows "service started" again; the file is gone | **Passed** 2026-10-02: flag made 21:17:38, restarted 21:18:14 into a new child under the same supervisor |
| 3 | After a `git pull` (or a commit pushed from the dev machine onto this checkout), the same happens within ~2 min | |
| 4 | Ctrl+C stops it (no restart afterwards) | |

### 7. New parts filling an open sheet (the 2026-10-02 flow)

| # | Check | Result |
|---|---|---|
| 1 | A card made from the New part template, and one made by hand, each get the "Nest this part" box, ticked, within about a minute | |
| 2 | Drag two cards into Ready for CAM 1 minute apart: one run starts, about 2 minutes after the second | |
| 3 | Untick "Nest this part" on a card in Ready for CAM: it stays there and no run takes it. Tick it again: a run starts | |
| 4 | With a sheet in Sheet review and nothing ticked, add a part of the same material: the **same** sheet card is rebuilt (new run number in the title, a "Rebuilt" comment, one `.tap`, checklists unticked), and the new part's card links to it | **Passed** (r005 S1 -> r006 S1, P-2011 carried with no Onshape calls) |
| 5 | Tick a Review item on that sheet, add another part: it gets a new sheet card; the ticked one doesn't change | |
| 6 | Open the rebuilt sheet's Fusion file: the old parts and the new one are all there, and the toolpaths look right | |

### 8. Rush, freed offcuts, the stock tally (2026-10-03)

First run `autocam trello-setup` once (it adds the **Rush** label and the new line on the read-me card).

| # | Check | Result |
|---|---|---|
| 1 | With a sheet waiting in Sheet review, give a part card of the same material the **Rush** label and move it to Ready for CAM: a run starts within a minute with only that card, its sheet card is titled "RUSH - ...", and the waiting sheet isn't rebuilt | |
| 2 | A normal card added next waits the usual 2 minutes and fills the waiting sheet, not the rush sheet | |
| 3 | Archive a sheet card that's on an offcut, without cutting it: within a minute the offcut card says "Free again: ..." and the next run can use the offcut | |
| 4 | Move a sheet card to Cut: the System card's "Stock since ..." line counts it (new sheet or offcut) | |

### 9. Finding offcuts (2026-10-03)

Run `autocam trello-setup` once (the read-me card's new Offcuts paragraph).

| # | Check | Result |
|---|---|---|
| 1 | Every offcut card's title starts with its number ("#1 - ..."); a sheet cut on a new sheet says "The rest of the sheet is offcut #N ... Write #N on it" | |
| 2 | A sheet card on an offcut has a **Stock** checklist with "The offcut isn't on the rack". Tick it with another offcut on the rack that's clear where the program cuts: the card switches to that offcut (title, Stock line), the missing one is archived | |
| 3 | Tick it when nothing fits: the card offers "Re-nest it on other stock" and "Cut it on a new sheet". New sheet: the Stock line says a new sheet and where the parts are; cut it, and the rest becomes an offcut | |
| 4 | Re-nest: the sheet card is archived, its part cards go back to Ready for CAM with the Rush label, and a run starts within a minute | |
| 5 | Unarchive the missing offcut's card (send it back to Offcuts): "Back on the rack", and runs use it again | |
| 6 | Make a card in Offcuts with Material, Thickness and Length lines: it becomes "#N - ... offcut"; a sheet nested on it says to push it against the front stop. A card missing a line gets one reply saying what's needed | |
| 7 | **Air-test** the first sheet switched to another offcut, and the first one on a scrap | |
| 8 | Hand cuts (2026-10-31): add "Cut by hand: 12 to 18 along, 5 to 10 across" to an offcut card: within a minute it's written back as read, a comment says how much is free, and the next sheet on it keeps clear (look at its preview) | |
| 9 | Tick "Something's already cut where this program goes" on a sheet card on an offcut: it moves to another offcut it fits (or asks: re-nest or a new sheet), and the offcut stays, with a comment asking for the cut to be recorded | |
| 10 | Hand cut from its program (2026-11-01): attach the .tap someone ran to an offcut card and add "Zeroed at: 10 along, 5 across" (where they zeroed X/Y). Within a minute the card shows a "Cut by hand" line for the program's reach from that spot and the comment says "from <file>"; measure the real cut against it. Without the Zeroed at line, one reply asks for it | |
| 11 | Own program placed by AutoCAM (2026-11-01): a card in Ready for CAM with a .tap someone made (Z0 = spoilboard) and lines Material: 6061, Thickness: 3/16. Within a minute a card "... own program ... H1" is in Sheet review with <file>_H1_PLACED.tap; simulate it (or open it in a G-code viewer) to see it's the same cut, moved; air-cut it zeroed at the piece's corner. Moved to Cut: the offcut's card says H1 was cut from it | |

### 10. Tabs (2026-10-03)

Run `autocam trello-setup` once (the read-me card's line about tabs).

| # | Check | Result |
|---|---|---|
| 1 | Part cards get a **Tabs** box, "Hold it in with tabs", ticked | |
| 2 | Untick it on one of two parts and nest them: the sheet card's cut order says "- tabs" for the other, and the description says to break tabbed parts out at the end (with every part tabbed, it says so once) | |
| 3 | Open the Fusion file: that part's `[outer]` op has triangular Tabs on, By distance 1000 in with Tab positions from a `TABS <sheet>` sketch: on straight edges, clear of corners where there's room; its cutouts are in "[inner] cutouts - N tabs each" ops (after the plain `[inner]` op, before the outlines), every one with tabs, even small ones; the other parts' don't. The sheet's notes in result.json say how many landed on straight edges. Simulate it | |
| 4 | Tick "Add an air test program": the air test traces the whole outline, tabs included (small bumps up) | |
| 5 | Cut it: the tabs hold the part through the cut and break out cleanly. If they're too weak or too strong, change the template's tab width/height (or `[tabs] distance_in`) | |

### 11. Parts placed by hand (2026-10-03)

| # | Check | Result |
|---|---|---|
| 1 | Untick "Nest this part" on a card and move it to Ready for CAM: about 2 minutes later a sheet card "... - placed by hand - ..." appears with its own program | |
| 2 | Open its Fusion file: the part alone, long side along X, the stock exactly its box, the setup's origin at the box's front-left bottom corner | |
| 3 | Air-test it: zero X/Y where you want the part's front-left corner, Z from the fixed tool setter; the cutter stays within the area the card gives | |
| 4 | Qty 3: the card says to run it 3 times, and the part card follows the sheet card to Cut | |

### 12. Duplicate part cards and "did you mean" (2026-10-04)

| # | Check | Result |
|---|---|---|
| 1 | Make a second card for a part that's already on a card in Needs fixing or On a sheet: within a minute it gets one comment linking the other card | |
| 2 | Give a card a name with a typo (P-201 for P-2011): the Needs fixing comment says "Did you mean P-2011?" | |

### 13. Part labels on the preview (2026-10-04)

To see them without the board: after any job has gone through Fusion (add-in 0.12.0 or later), run
`C:\dev\venvs\frc-autocam\Scripts\autocam.exe preview-labels queue\done\<job>` in `C:\dev\frc-autocam`. It
writes `<sheet>.labelled.png` beside each preview: the picture the sheet card would get.

| # | Check | Result |
|---|---|---|
| 1 | A new sheet card's cover shows a yellow numbered badge on every part, its name under it, and "the numbers are the cut order" along the top | |
| 2 | The numbers match the card's CUT ORDER list. Each badge sits on its part's material (not in a cutout), except on a part that's mostly cutouts, where it's in the middle of the part (lbl1: spots on the material, right numbers and copies, 2026-10-04) | |
| 3 | At the machine, the labels are enough to tell which part is which when unloading | |

### 14. Pockets (2026-10-31)

Needs the `pockets` code and the new `alu_4mm` (with `[pocket]` and `[pocketfinish]`) on the Fusion PC, and the
add-in reloaded. Use a part with a counterbore or pocket and a hole through its floor (the test part from the
dump, or P-2015), as a card with its STEP attached so no Onshape calls are made.

| # | Check | Result |
|---|---|---|
| 1 | The sheet card comes back (not Needs fixing). result.json's untested steps list `pocket_floors` and `pocket_walls`, and its notes say how the pockets were selected | r024 (P-2015, 2026-10-31): "pockets selected as pocket selection". r023 (face contour) cleared the whole sheet around the floor; the guard rejected it |
| 2 | In the Fusion file: `[pocket] pockets` and `[pocketfinish] pocket walls` come first, each with every pocket floor selected; the finish pass runs inside the pocket, not in the wall | r024: pocket, pocketfinish, then holes in the program; adaptive within the pocket; the finish pass's tool center one radius inside the wall; checks passed |
| 3 | Simulate: the pocket is cleared to its floor, including over the hole; the hole is cut after; nothing below a floor | |
| 4 | A part with a raised island in a pocket goes to Needs fixing with "needs manual CAM ... island" | |

### 15. Faced parts (2026-11-01)

Needs the `face` code and `fusion/templates/face_6mm.f3dhsm-template` on the Fusion PC, and the add-in reloaded
(core 0.14.0 does it). Run `autocam trello-setup` once so the board has the Face label and the how-to card
mentions it. A plain plate as a Rush card with its STEP attached and the **Face** label (no Onshape calls).

| # | Check | Result |
|---|---|---|
| 1 | The sheet card has two programs: `<sheet>_FACE.tap` and `<sheet>.tap`. Its RUN says to face first with the 6 mm 3-flute ALU (T2), then cut with the 4 mm. CUT ORDER marks the part "faced" | r029 (P-2014, 2026-11-01): both attached; title "1 part, 1 faced - 5 min"; RUN steps 1 and 2 as described |
| 2 | result.json lists `face_setup` and `face_tops` in the untested steps. In the Fusion file a second setup `<sheet>_FACE` has one op, `[face] tops`, with the 6 mm tool and only the faced parts' tops selected | r029: both listed, status ok, no warnings. The Fusion file is still to be looked at |
| 3 | Simulate the FACE setup: the whole top of each faced part is skimmed (inside corners and thin bits too), 0.005 in deep, the cutter's edge going at most about 0.1 in past the outline; nothing else is touched | |
| 4 | The FACE program's lowest Z is the stock top less 0.005 (e.g. Z0.1825 on 3/16 stock) | r029: Z0.1825; cutter edge 0.06-0.11 in past the outline all round, center inside |
| 5 | At the machine: run the FACE program with T2 measured on the setter, then the sheet's program with its own cutter; the faced tops are clean and the parts are 0.005 in thinner | |

### 16. Bearing fits (2026-11-01)

| # | Check | Result |
|---|---|---|
| 1 | A part with a bearing hole: the sheet has a `[bearing holes]` op after `[bore holes]`, and no "no [bearing] op" warning | r030 (P-2015): yes; the finishing lap 0.005 in out from the roughing helix |
| 2 | `holes.bearing_stock_in = 0.002`: the finishing lap comes in 0.002 | r031: 0.3613 -> 0.3593 |
| 3 | Cut a bearing hole and press a real bearing in. Too tight: lower `bearing_stock_in` (more negative); too loose: raise it. Record the value that fits, per material | |

### 17. Fit series (2026-11-01)

| # | Check | Result |
|---|---|---|
| 1 | A card with `Fit: 1.125 hole` (or the part's bearing size): its sheet card has the program and 4 laps `..._FIT1_1` to `_4`, RUN says to leave the part in and run the laps one by one, and a Fit checklist | r034, r036 (P-2015, 0.88 hole): laps 0.3593 -> 0.3623 in path radius; no dialog in r036 |
| 2 | `Fit: outline`: the laps cut only that part's outline, with the same tabs, each to Z0 | r034 (P-2014): 0.001 per side per lap |
| 2b | `Fit: 2 hole` on a hole over 1.5 in (cut as a cutout): the program keeps its slug tabbed; the laps have no tabs, each 0.001 bigger, all to Z0; the card says to break the slug out first | r038 (a made-up 2 in hole) |
| 3 | Cut one: run the program, try the fit (a bearing, a shaft or the mating part), run laps until it fits, tick it. The next sheet with that fit starts two steps above it | |

### 17c. More than one cutter per part (2026-11-01)

| # | Check | Result |
|---|---|---|
| 1 | A part with an 8-32 hole (0.136) and plain parts: the sheet is on the 4 mm and has `<sheet>_SMALL.tap`; RUN says the 1/8 in first, then the 4 mm, outlines last | r052 (P-2011 with its 0.156 holes made 0.136): yes, 2 G81s in the SMALL program, none in the 4 mm one |
| 2 | A cutout with a corner under 2 mm radius goes in the SMALL program (with its tabs, if the part has tabs); a tight outline corner still puts the sheet on the 1/8 | offline tests |
| 3 | At the machine: run the SMALL program with T12 measured, swap to T1 without moving the sheet, run the sheet's; the small holes line up with the rest | |

### 17b. CAD changed (2026-11-01)

| # | Check | Result |
|---|---|---|
| 1 | Change a part in Onshape after its sheet is in Sheet review (or move the sheet to Ready to cut): within 12 h (Ready to cut: within 2 minutes) its card and the sheet card have the purple CAD changed label, and the part card a "CAD changed" box | |
| 2 | Tick "Cut it as it was CAM'd": within 2 minutes both labels are gone and the sheet card says who decided | |
| 3 | Instead, press "Nest the new version" in the panel (or move the card to Ready for CAM): the labels come off and the next run nests the changed part | |

### 18. Scribed bend lines (2026-11-01)

A bent part (Part 13, doc 31359564..., Part Studio c5ca4dcf...) as a Rush card with `Scribe: endmill`, then again
with `Scribe: chamfer mill`.

| # | Check | Result |
|---|---|---|
| 1 | Endmill: the sheet's program has a `[scribe bend lines]` op before the outlines, one pass along each bend's centerline 0.012 in deep; CUT ORDER says "bent - scribed" | r044: one G1 5 in across at Z0.113 (1/8 stock), within 0.0009 in of the line, guard passed |
| 2 | Chamfer mill: the sheet card has `<sheet>_SCRIBE.tap` too, RUN says T16 first then the sheet's cutter | r047: attached, RUN steps 1 and 2, plunge 13.3 in/min then 50 in/min (T16's preset), within 0.0001 in, 7 s |
| 3 | A part whose scribing can't be made is still cut, with "mark them by hand" under CHECK | r040, r045, r046 (while it was being fixed): yes, the sheet posted each time |
| 4 | Look at both in the Fusion file (simulate): the endmill line centered on the bend, the chamfer line a fine V; nothing else touched | |
| 5 | At the machine: run one of each on scrap. The line is down the middle of the bend zone; bend on it in the brake and check the flange length | |
| 6 | A U or D beside each line, readable, clear of holes and edges; bending the way it says gives the part in the CAD | r050 (endmill): a U 0.59 x 0.78 in centered beside the line (Part 13 lay bottom-up, Bend D is down); r051 (chamfer mill): a 0.18 x 0.25 in U, within 0.0001 in, 15 s. Cut and bent: |

### 19. Spares (2026-11-04, core 0.24.0)

A test part sent with Quantity 2 and Spares 2 (Rush + Smoked, a cached STEP), then a spare bigger than the space the
robot parts leave.

| # | Check | Result |
|---|---|---|
| 1 | Two cards in Ready for CAM: the part's own (Qty 2) and its spares (Qty 2, the Spare label, sky blue) | Offline and in a browser with fakes |
| 2 | One sheet: the spares beside or behind the robot parts, a part spacing clear; CUT ORDER says "spare" on them | r085: robot plate + 6 spacer spares on S1, "Parts marked spare go in the spares box", each spare marked |
| 3 | A spare that doesn't fit beside them goes on the next sheet; no robot part moves to a later sheet | r085: the big spare alone on S2 (r084 failed here on Fusion's no-room error; 0.24.1) |
| 4 | The phone: Plates tab, Spares section shows them; Send spares lists the plate under its stock; sending 1 more adds to the waiting card | |
| 5 | The sheet's air test: an offcut sheet's Options has the air test ticked, and its comment says why | |

### 20. Pictures from the programs (2026-11-04)

| # | Check | Result |
|---|---|---|
| 1 | A new sheet card gets `<sheet>_toolpath.png` within a pass: the cuts where the preview has the parts, the numbers in the CUT ORDER's order, tabs orange | |
| 2 | At the machine: the cut sheet looks like its toolpath picture (no hole missing, nothing cut twice) | |
| 3 | Each offcut card's cover shows the piece: hold the picture next to the piece on the rack (end A toward you): the cut-outs match | |
| 4 | Cut a sheet on an offcut: its card's picture is redrawn with the new cuts, the old picture gone | |

### 21. Signing in to the shop page with Slack (2026-11-04)

| # | Check | Result |
|---|---|---|
| 1 | After the Slack app's id and secret are in `.env`: `/shop/sign-in` on a phone goes to Slack, and back says to add `AUTOCAM_SLACK_TEAM_ID=T...`; nobody is let in yet | |
| 2 | With the team id added: Sign in with Slack opens the shop page, the name at the top right is your first name | |
| 3 | A phone with the Slack app installed: the sign-in still finishes in the browser (Slack may email a code once) | |
| 4 | iPhone, page added to the home screen: signing in comes back to the page (if it ends up in Safari, note it) | |
| 5 | The old key link shows the "Sign in with Slack" bar with the date it stops; after signing in, the key is gone from the address | |
| 6 | Tap your name, Sign out: the sign-in page. `shop-sign-out --all` on the laptop: every phone gets the sign-in page on its next refresh | |
| 7 | A Slack account from another workspace is turned away | |

### 22. The clamps in Fusion's simulation (2026-11-04, core 0.25.0)

Already seen by Claude in the run results (r086-r091): the blocks made, set as fixtures and measured where they belong.
What only a person can see is the simulation itself.

| # | Check | Result |
|---|---|---|
| 1 | Open a new sheet's Fusion file: a `CLAMPS <sheet>` component with a block on each clamped edge (1.25 in wide, the whole length, 1.5 in tall, sitting on the plate) | |
| 2 | Simulate the sheet's setup with the fixture shown: the blocks show, and nothing the program does touches them (rapids pass over at the clearance height) | |
| 3 | Its FACE or SMALL setup shows the same blocks | |
| 4 | The sheet card's preview picture looks as before (no blocks on it) | |
| 5 | In a copy of the file, lower an op's clearance height below 1.5 in above the plate: the simulation flags the collision | |

### 23. Checking a hand-made program (the Check label, 2026-11-04)

Needs the Check label on the board once: run `autocam trello-setup --board <board>` on the laptop (it adds missing
labels and updates the how-to card), or make a label named Check by hand.

| # | Check | Result |
|---|---|---|
| 1 | A card in Ready for CAM with the Check label, a .tap someone posted from a sheet-sized setup (origin at the stock's bottom front-left corner), Material: 6061 and Thickness: 1/8: within a minute a comment says it passes (or why not) and the card is back in Drafts; no sheet card, no run | |
| 2 | The same card in Drafts: commented once, stays in Drafts; attaching a new .tap gets a new comment | |
| 3 | A polycarbonate program posted with Use mist coolant ticked: fails, and says to untick it | |
| 4 | A program for one part, zeroed at its own corner: fails on the clamps and says to add Place it myself; with that line it passes | |
| 5 | An own program (no Check label) that would have passed before but has the wrong mist: refused in Needs fixing saying so | |

### 24. A fit with the outline cut last (2026-11-04, core 0.26.0)

A part card with `Fit: 0.88 hole` (or any bored hole size it has), on a sheet with another part.

| # | Check | Result |
|---|---|---|
| 1 | The sheet card lists the laps, then a `LAST:` line with `..._OUTLINES.tap`; in the cut order the fitted part comes last, marked "cut out last" | |
| 2 | Air: the sheet's program cuts the other part's outline and the fitted hole, never the fitted part's outline | |
| 3 | After the program the fitted part is still solid in the sheet; a lap opens the hole a little; try the fit | |
| 4 | Once it fits, `..._OUTLINES.tap` with the same cutter and zero cuts the part out on its tabs | |
| 5 | Tick the lap that fit: the card comments what it learned | |

### 25. The machine screen (2026-11-05)

On the laptop: `python -m autocam_service machine-link`, then open `state/machine-link.html` on the ShopSabre's PC and
bookmark the screen.

| # | Check | Result |
|---|---|---|
| 1 | The list shows the sheets in Ready to cut (Rush first), placed-by-hand ones and passed Check programs apart | |
| 2 | Start, Loaded: the card's At the machine boxes are ticked | |
| 3 | Download: the .tap lands in Downloads without a prompt (else switch the browser's "ask where to save" off); WinCNC's yellow folder opens it | |
| 4 | The Stop after each part switch off: the `_NOSTOP` program appears within a minute and downloads | |
| 5 | A fit: Not yet shows the next lap; Yes ticks that run in the card's Fit checklist | |
| 6 | Move to Cut: the card stays in Ready to cut for 3 minutes (Undo works), then moves to Cut and the part cards follow | |
| 7 | Something went wrong: the card is in Sheet review with what happened in a comment | |
| 8 | Close the browser mid-sheet and reopen: it's under In progress and Continue opens the same step | |

### 26. Straight rows after Fusion nests (2026-11-05, core 0.27.0)

A few long parts on one sheet (e.g. three of p0201), Rush so they don't join an open sheet.

| # | Check | Result |
|---|---|---|
| 1 | `worker.log` says "laid in N straight row(s): ... -> ..." or "stays as Fusion nested it (...)" for the sheet | |
| 2 | Laid straight: the sheet picture shows the parts square across the sheet, one row behind the other, at the front | |
| 3 | The card's offcut keeps the room beside the rows (Offcut line), and the program passes the guard like any other | |
| 4 | A sheet Fusion already laid square (e.g. a few small plates) is left as it was | |
| 5 | (core 0.28.0) A part drawn at an odd angle (p0201 at 1/4 in, drawn at 11 deg): `worker.log` says "off square, laid along a straight edge in the rows: p01" and "laid in N straight row(s)"; the Arrange notes say "laid along an edge"; the picture shows the bars square | |

### 27. Offcuts on the rack (2026-11-05, core 0.28.0)

| # | Check | Result |
|---|---|---|
| 1 | Cut a sheet that keeps an offcut, then send a part the same day: `worker.log` says "offcuts, smallest first: <id> (on the bed)", and it's only turned round if that saves a new sheet | |
| 2 | The next day (or after Loaded on another sheet on the machine screen), a part that fits an offcut only turned round goes on it turned round, and the card says which end goes at the front | |
| 3 | Two offcuts on the rack that both fit: the smaller one is used | |

### 28. Parts drawn off square laid square (2026-11-05, core 0.29.0)

| # | Check | Result |
|---|---|---|
| 1 | A part drawn at an angle in CAD (p0201 at 11 deg): `worker.log` says "drawn off square, laid square in every Arrange: p01 (turned ... deg on from its edge)", and the sheet picture shows it square from Fusion's own nest | **Pass** (r105, 2026-11-05: -2.67 deg; three bars square, 7.5 in long) |
| 2 | Parts drawn square: the log has no "drawn off square" line and the sheets look as before | |
| 3 | The f3d: the probe copies (`~m1`..`~m3`) are hidden, behind the sheets (negative Y), and in no setup | |

### 29. Rush joins a sheet in review, smaller gaps, nests that leave one clean piece (2026-11-06, core 0.31.0)

| # | Check | Result |
|---|---|---|
| 1 | With a sheet in Sheet review and nothing ticked on it, send a Rush part of the same material and thickness that fits: the same card is rebuilt, titled RUSH, with both parts and its Review boxes unticked; other waiting cards still wait | |
| 2 | A second Rush part while that rush sheet is still unreviewed: it joins the rush sheet | |
| 3 | A sheet someone has ticked a Review item on: a Rush part doesn't join it | |
| 4 | Cut a sheet that keeps an offcut, and nest a part on it the same day: `worker.log` says "(on the bed)", the part sits about 0.75 in from the earlier parts in the sheet picture, and the card's Stock line says it was nested as still clamped | |
| 5 | The same the next day (on the rack): about 1 in, and the Stock line says "part spacing, plus a little" | |
| 6 | A nest on an offcut with a room by earlier cuts: `worker.log` lists the tries with "in2 kept in one piece" and "written off", and the picture keeps the bigger rectangle in one piece | |

### 30. The print queue (2026-11-09, `docs/prints.md`)

| # | Check | Expect |
|---|---|---|
| 1 | In the Onshape panel, open a part made of PLA (or any print material) | The print form: Material, Quantity, Color, Infill %, Walls, Rush, Note, Send |
| 2 | Change the material, pick Other… and type a color, Send | It's on `/shop#prints` under Waiting with that material and color; the part in Onshape is unchanged |
| 3 | Download it on the shop page | A `.step` file after a few seconds (try again if the page says it's still exporting); a second download makes no Onshape calls (the ledger) |
| 4 | Start printing: pick a printer, 1 h 30 m | Under Printing with "<printer> · done ~<now + 1:30>" |
| 5 | Failed | Back in Waiting, "Reprint · failed 1×" |
| 6 | Start it again, then Done | Done today, with Undo on that phone only |
| 7 | Add a print: upload an STL | On the queue, named after the file; downloads as the same file |
| 8 | Send an assembly with a printed part ticked under Prints | On the queue with the assembly's count; sending it again while it waits is refused |

### 31. Offcuts from a photo (2026-11-09, `docs/offcut-photos.md`)

| # | Check | Expect |
|---|---|---|
| 1 | Shop page > Offcuts | The rack: each offcut's number, thickness, size and picture |
| 2 | Add an offcut: a film-covered poly scrap flat on the floor; material, thickness, taped length | The corners are suggested on its corners (the front edge is the end at the bottom) |
| 3 | Next | The piece straightened, a box over each hole; fix any (delete a wrong one, drag round a missed one) |
| 4 | Add it | "Numbering it…", then "Write #N on the front end"; the card in Offcuts says Added as offcut #N, with the photo |
| 5 | The same with bare aluminum on the plywood table, then on the floor | Table: nearly every hole boxed. Floor: fewer; box the rest |
| 6 | Re-photo an offcut after cutting something out of it by hand | Its record hatched; only the new cut gets a box; Save adds one Cut by hand line |
| 7 | Gone, then Undo within 5 s; then Gone and wait | Undo: still there. Waiting: the card archived, off the tab |
| 8 | Take a photo (Android, then an iPhone): allow the camera (and, on the iPhone, motion) | The page's camera opens with the piece's frame, FRONT at the bottom; tilt the phone: the level's bubble moves to the high side, green when flat |
| 9 | Take one while moving the phone, then one with the lights off | The shutter waits for "Hold still"; a smeared or dark one says Too blurry / Too dark: Take it again, or Use it anyway |
| 10 | Use the camera app instead; pick a blurry photo | "This photo looks blurry…", Next stays off until "use it anyway" |
| 11 | On the laptop: `python -m autocam_service offcut-photo-report` | One row per way of finding, counting the photos saved in 4-6 (holes found, missed, extra boxes, corners right) |

## M5: status card, start at logon, reboot test on the shop PC

### 1. The System card's status (works as soon as the service has the M5 code)

| # | Check | Result |
|---|---|---|
| 1 | The System card's description reads "Status at <time>...", then Fusion, Run, Ready for CAM and Onshape lines, and its time moves on about every 10 minutes | |
| 2 | Drag a card into Ready for CAM: within a minute the status says "1 card waiting, the next run starts in about 2 min" | |
| 3 | Close Fusion with a job waiting: within ~6 minutes the System card gets one comment, "Fusion isn't running, and 1 job is waiting for it...". Start Fusion again: one "Fusion is running again" comment | |
| 4 | Start a second `autocam run` while one runs: it says "the service is already running" and quits (needs one manual restart of the service first) | |

### 2. Start everything at logon (once, as the user the shop PC logs in as)

```powershell
powershell -ExecutionPolicy Bypass -File C:\dev\frc-autocam\ops\install-autostart.ps1 -DryRun   # shows what it will set up
powershell -ExecutionPolicy Bypass -File C:\dev\frc-autocam\ops\install-autostart.ps1
```
Run it from an administrator PowerShell (the service's task runs without a window, whether or not anyone is
logged in, which Task Scheduler only lets an administrator set up). Then, in Fusion: Shift+S > Add-Ins >
autocam_addin > tick **Run on Startup**. Set Sleep to Never (when plugged in). Stop any service window you
started by hand first, or the task's copy will say it's already running. Undo with `ops\uninstall-autostart.ps1`.

| # | Check | Result |
|---|---|---|
| 1 | Task Scheduler shows "AutoCAM service" (at startup, then every 5 minutes; "Run whether user is logged on or not") and "AutoCAM Fusion" (at logon) | |
| 2 | Log off and on: Fusion opens by itself; no service window appears, and the System card keeps updating | |
| 2b | End the service's processes (Task Manager, or `Stop-ScheduledTask "AutoCAM service"`): within 5 minutes it's running again and the System card updates | |
| 3 | The add-in starts with Fusion: `queue\worker_heartbeat.json` updates and the System card says "Fusion: running" | |
| 4 | Reboot the PC (after someone logs in, or with automatic sign-in): the same, with no other steps | |
| 5 | A card dragged into Ready for CAM after the reboot is nested without anyone touching the PC | |

## The Onshape panel (`docs/panel.md`)

After registering the extension in Onshape:

| # | Check | Expect |
|---|---|---|
| 1 | Open a Part Studio's right panel, pick AutoCAM | The parts list, each with its status; one Onshape call in the ledger (`run_id` panel) |
| 2 | Click a part in the CAD | It opens in the panel. If not, read "Onshape selection message" in `logs\service.log` and fix `selectedPart` in `panel.js` |
| 3 | A part used in an assembly | Quantity starts at the assembly's count, "from <assembly> (n)" |
| 4 | Send to Drafts | A card in Drafts with the link, Qty, Part ID and "Sent from the Onshape panel by <name>"; Nest and Tabs boxes as set. Archive it after |
| 5 | A part with no material: pick 6061, send to Drafts | Onshape shows 6061 on the part, or the panel says it couldn't and the card has `Material: 6061` |
| 6 | Send the same part again | Refused; Change qty works |
| 7 | Open the panel from a document the team account can't see | "AutoCAM can't open this document" |

