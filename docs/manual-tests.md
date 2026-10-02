# Manual tests

Everything Fusion-side or machine-side that can't run in CI. Fusion code counts as **untested** until a
section here has been run and its results recorded (date, who, Fusion version, outcome).
Record per-call API results in `docs/fusion-api-status.md`.

## M1.0: API probe, parameter dumps, Phase 1 smoke test

**Where:** Fusion on a Windows PC that can see `C:\dev\frc-autocam`, with branch `m1-offline` checked out.
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

1. `git pull` on the Fusion PC (branch `m1-offline`).
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

Run on the machine with no material, work zero set as usual. Use `fusion/tests/pause_air_test.tap`
(no mist) and the generated `pause_air_test_mist.tap` (M1.6).

- [ ] The machine stops at every pause, and nothing moves until start/continue is pressed.
- [ ] The spindle actually stops at each pause and restarts after resume (4 s spin-up before moving).
- [ ] Mist turns off at each pause and back on after resume (mist file only).
- [ ] The first move after resume comes down from the top, not sideways at cutting depth.
- [ ] Record the key/button that resumes (open question 9): ______  (never Esc: it aborts the job)

## Before the first real cut

- [ ] Open question 4: Z is touched off on the spoilboard next to the sheet (decision 22).
- [ ] Open question 10: no-tab trial on one taped sheet (decision 6).
- [ ] Both air tests above passed.

## M2: add-in hot-folder worker

The add-in runs whatever the service puts in `C:\dev\frc-autocam\queue\incoming\`, one job at a time, with
no dialogs. Until the service runs for real, queue jobs by hand from earlier `autocam_run` runs.

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
| 4 | Close Fusion (Task Manager) while a job runs; restart Fusion and the add-in: the job runs again (attempt 2) | |
| 5 | Kill it again during attempt 2: on the next start the job goes to `queue\failed\<id>\` saying it was started 2 times | |
| 6 | Edit a queued job's `"core_version"` to `"0.0.1"`: it goes to `failed\` with `CORE_VERSION_MISMATCH` | |
| 7 | While the add-in is idle, Fusion stays usable (only a job run blocks it) | |
| 8 | Stop the add-in, `git pull`, start it again: the new code runs (the log's start line) | |

## M5: reboot test on the shop PC

_Added in M5._
