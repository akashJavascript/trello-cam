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

## M1.2 to M1.7: restructured pipeline in Fusion

_Added per sub-milestone._

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

_Added in M2._

## M5: reboot test on the shop PC

_Added in M5._
