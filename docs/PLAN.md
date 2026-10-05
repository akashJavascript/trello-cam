# FRC auto-CAM pipeline: implementation plan

## Context

Students put plates on Trello cards (Onshape version link + qty). On a manual trigger, a Windows service:
1. Exports STEP from Onshape (10,000 calls/year shared across the enterprise).
2. Batches the parts and hands `job.json` to a Fusion add-in through a hot folder.

The add-in:
1. Nests the parts on 24" x 48" sheets (only about 24" x 40" reachable) and applies tagged CAM templates.
2. Posts one WinCNC `.tap` per sheet with the shop's proven post.
3. Writes `result.json`.

The service then puts each `.tap` and its preview on a new sheet card in "Sheet review". A human approves every
sheet.

The Phase 1 Fusion script has never run in Fusion. This plan restructures it into tested pure-Python logic plus
thin Fusion adapters, and builds the service around it. Settled decisions live in `docs/BRIEF.md` and are not
repeated here.

### Findings from reading the files (they shape the plan)

- **The repo was empty.** The project only exists in `frc-autocam.zip` (identical copies in Downloads and
  Documents). M0 imports it byte-exact.
- **Post (`.cps`) behavior, checked in code:**
  - **Retracts.** `G53 Z` is written only at the first op, on a tool/WCS/work-plane change, and at program end.
    Between ops on one sheet (same tool) the post moves `G0 X Y` at the previous op's clearance height. The brief
    says retracts use `G53 Z` between ops, which isn't true. Template clearance heights must clear the clamps for
    every op, and the guard checks this on the real file.
  - **One-tool rule.** It is enforced by tool **number** only. The library has duplicate numbers: T1 alu/poly,
    two T2s, two T3s. The `.tap` has no T word, so a sheet mixing alu and poly 4 mm ops would post with no error.
    **Fusion-side tool GUID verification is the only real tool check.**
  - **After `M0`.** `COMMAND_STOP` writes `M0` and makes the next op re-emit `S/M3/G4 X4.`. `M11 C8` (mist) is
    only written in `onOpen`, so a pause must turn mist back on itself. Text inserted into the `.tap` gets no help
    from the post, so it must also restart the spindle and dwell.
  - **Comments.** They are `[...]`, filtered to ` a-z0-9.,=_-:()` and cut to 118 characters. `[outer] P03 gusset`
    posts as `[outer P03 gusset]`. The program name and program comment also print as comments, so op names must
    be unique and distinct from them.
  - **Units.** Inch writes `G20`, metric writes `G22`. Phase 1's guard wrongly treats "no G20" as metric.
  - **Properties.** `useXYZFeeds`, `safePositionMethod`, and `useToolCall` change the output. Every property is
    set explicitly on every post, then recorded.
- **Phase 1 bugs to fix in the restructure.**
  - It waits for "file exists", so a retried job can pick up an old `.tap`. Post into a fresh empty folder per
    attempt and check the return value.
  - `adsk.doEvents()` in wait loops lets a custom event re-enter mid-job. Use a busy flag.

## 1. Questions that blocked the plan (answered)

1. **Repo root:** `C:\dev\frc-autocam` (`/mnt/c/dev/frc-autocam`), matching CLAUDE.md. M0 moves the empty `.git`
   with its `trello-cam` remote there. `C:\dev` stays a plain parent folder.
2. **Thickness source:** **Fusion measures it.**
   - The service batches by material (+ color) only.
   - Fusion measures each part's oriented bounding box, snaps it to that material's stock thicknesses, and nests
     each thickness separately, so every sheet is still one stock type (decision 10).
   - A non-stock thickness sends the part to `Needs fixing`. No extra Onshape calls.

Nothing else blocks the plan. Brief open questions 1-11 stay where the brief schedules them.

**Defaults I picked (not blocking; say if any is wrong):**
- **Config format.** Human config is TOML (`config/autocam.toml`), so ASSUMED notes can be comments. Fusion never
  reads it: the service copies what a job needs into `job.json`, so jobs are self-contained.
- **Pauses.**
  - Default mode is `.tap` **text insertion**. It is pure Python, unit-tested, and works whatever the API can do.
    Creating Manual NC entries via the API is probably unsupported. If `api_probe` proves it works, Manual NC
    becomes a config switch. The same verifier checks the final file in both modes.
  - No pause after the **last** part, because the program end already retracts, stops, and parks. Pauses = parts
    on the sheet minus 1.
- **Short quantity.** If a card's full qty doesn't fit, none of its copies are cut this run. The worker removes
  that part's placed copies before CAM, and the card stays in `Ready for CAM` with a comment.
- **Post.** Preferred: post with the **pinned `.cps` by path** (`PostProcessInput`), if the probe confirms it.
  Otherwise: the library post, verified against the pinned file's sha256, failing loudly on mismatch.
- **Names.** Program/file names carry a run number (`6061_0p125_r017_S1`). `dump_params` lives in
  `fusion/tools/dump_params/` (a Fusion script needs a folder plus manifest).

## 2. Repo layout and modules

```
frc-autocam/
  CLAUDE.md README.md pyproject.toml .env.example .gitattributes .gitignore
  .github/workflows/tests.yml        offline pytest, windows-latest + ubuntu-latest
  config/autocam.toml                committed defaults, placeholders, [assumed] table; no secrets
  docs/  BRIEF.md PLAN.md manual-tests.md fusion-api-status.md decisions.md
  core/autocam_core/                 PURE, stdlib only; the only non-adapter code Fusion imports
  service/autocam_service/           Windows service (requests, python-dotenv allowed)
  fusion/
    autocam_addin/                   add-in: hot-folder poller + worker/ adapters (UNTESTED until you run them)
    autocam_run/                     Fusion script: pick a job.json -> same pipeline (manual M1 runs)
    autocam_nest/                    Phase 1, reference only; removed once M1.2 reaches parity
    tools/  5940_Tool_Library.tools  dump_params/  api_probe/
    posts/  shopsabre_automatic_mist.cps  (pinned, never edited, sha256-tested)
    templates/                       team-exported *.f3dhsm-template
    tests/  pause_air_test.tap  (+ generated pause_air_test_mist.tap in M1.6)
  tests/  conftest.py core/ service/ fixtures/{taps,trello,onshape,results,jobs}/
  queue/ cache/ state/ logs/         runtime, gitignored
```

### `core/autocam_core/`: pure logic, stdlib only

Python 3.9-compatible syntax until `dump_params` reports Fusion's version (probably 3.12+).

| Module | Responsibility |
|---|---|
| `__init__` | `CORE_VERSION`. The job carries it, and the worker refuses a mismatch ("restart the add-in"), so stale code in Fusion's long-lived interpreter can't run a job |
| `units` | in <-> cm at the edge; tolerance compare |
| `names` | post-safe program/op names (`[A-Za-z0-9_-]`), the filtered-comment form, uniqueness, card titles |
| `schema_job`, `schema_result` | dataclasses with `from_dict` (strict) and `to_dict`; a schema string |
| `errors` | error/warning codes + message templates (the service turns them into Trello comments) |
| `geometry` | dataclasses for what the Fusion extractor reports: faces, cylinders (axis, radius, concave, through), floors, loops, oriented-bbox thickness |
| `holes` | group split half-cylinders by axis + radius; `classify(hole, tool)` -> drill / bearing / bore / contour-warn / inner / too-small, in brief precedence. Called per candidate tool, and again for the sheet's final tool |
| `plate` | plate rules on extracted data: single body, thickness snapped to stock sizes, floors open to one side (pocket side = up), two-sided reject, chamfer/fillet/3D reject, inside radii vs tool, sharp-corner warning |
| `tooling` | per-part tool need (4 mm / 1/8", `Tool 1/8` label, poly never T12, missing poly 1/8" template -> exact review comment); per-sheet tool + template |
| `fixture` | nest region = reach minus margins minus clamp strips; clamp zones; sheet origin from envelope bbox |
| `layout` | position-based binning (from Phase 1); placed-vs-qty with the defer-whole-card rule; checks that every body bbox is inside its region |
| `ordering` | per-part `[outer]` order (nearest neighbour from the zero corner) |
| `pauses` | build the pause block (mist-aware); insert it before the next per-part outer op's comment line; `verify(tap, outer_order)` checks each pause's position and content, comparing numbers rather than strings (`G4 X4.` == `G4 X4`); generates the mist air-test file from the same builder |
| `tapguard` | fail-closed program check; returns a `GuardReport` (min Z, offenders, sha256 of the exact bytes). Rules in Risks #3 |
| `hotfolder` | atomic write, claim, requeue, attempt sidecar, complete/fail; `result.json` written last |
| `toollib` | read `.tools` (zip -> tools.json), look up by GUID, sanity-check configured GUIDs. Never selects a tool |

### `fusion/autocam_addin/worker/`: thin adapters

Each file is headed `UNTESTED IN FUSION`. Status is tracked in `docs/fusion-api-status.md`.

| Module | Responsibility |
|---|---|
| `bootstrap` | put `<repo>/core` on `sys.path`; check `CORE_VERSION`; purge `autocam_*` modules on stop |
| `fx_params` | get/set CAM params by name; candidate lists from the job; logs which name worked |
| `fx_import` | STEP import -> one occurrence per part; cleanup on failure |
| `fx_geometry` | BRep -> `core.geometry`; the only place BRep is walked. Also re-checks after Arrange that the chosen up face points +Z |
| `fx_arrange` | Arrange per thickness group with a plane envelope = the exact nest region (no `frameWidth`); park it away from parts. Fallback if `resultEnvelopes` is unreliable: one envelope per Arrange, carrying leftovers forward |
| `fx_fixture` | 24x48 sheet solid at the sheet origin (setup stock); clamp-zone bodies |
| `fx_setup` | setup per sheet: models, stock from solid, fixtures if supported, WCS box point, inch units |
| `fx_template` | apply the template (`createFromCAMTemplate2`); verify every op's tool **GUID** (`Tool.toJson`; fallback: match against the pinned library); find ops by tag; per-part `[outer]` copies (`CAMTemplate.createFromOperations`, else re-apply the template and delete the extras); delete empty ops |
| `fx_selections` | drill/bore/bearing faces (all faces of a hole), pocket floors, `[inner]` through-loop chains, `[outer]` chains |
| `fx_post` | set every post property, post into a fresh folder, check the result, read machining time |
| `fx_save` | `saveAs` to the Fusion Team folder, wait for upload, get the web URL; local `.f3d` |
| `fx_preview` | one PNG per sheet |
| `pipeline` | `process_job(job)`: adapters + core, post-processing pass (pause insert -> verify -> guard), `result.json` last |

`autocam_addin.py` (M2): a polling thread fires a custom event, and `process_job` runs on the main thread. It
keeps a busy flag (no re-entry), runs one job at a time, writes a heartbeat file, shows no modal dialogs, closes
the document after each job, and re-queues `processing/` on start.

### `service/autocam_service/`

**Pure** (unit-tested):

| Module | Responsibility |
|---|---|
| `cards` | title, first `cad.onshape.com` URL, `Qty: N`, labels, `.step` fallback; every format error -> comment text |
| `onshape/urls` | parse version URLs; reject `/w/` and non-Part-Studio elements |
| `materials` | Onshape material -> key; `Smoked` override |
| `batching` | group by material + color |
| `jobs` | build `job.json` |
| `sheet_cards` | title, description, comments, checklist |
| `budget` | estimate and decide |
| `run_state` | which Trello writes, incl. attachment IDs, are done |

**Clients and IO** (tested with fakes):

| Module | Responsibility |
|---|---|
| `config` | TOML -> dataclasses, strict validation, `[assumed]` report |
| `credentials` | `.env`, redacted repr |
| `onshape/client` | the **only** Onshape HTTP path: HMAC request signing, no automatic redirects, ledger entries around every request |
| `onshape/ledger`, `onshape/cache` | the call ledger; the version-keyed cache |
| `onshape/export` | parts list, translation, poll, download |
| `trello_http`, `tracker/base`, `tracker/trello`, `tracker/dryrun` | Trello HTTP; the brief's tracker interface; Trello adapter; dry-run (records intended writes) |
| `results` | ingest `done/`, re-verify every `.tap` |
| `runner` | the run orchestration |
| `health` | M5 |
| `cli` | `run`, `dry-run`, `config-check`, `make-job`, `ledger`, `trello-discover` |

## 3. Data models

### `config/autocam.toml` (inches; excerpt; M0 includes every section)

```toml
schema = 1
[assumed]   # dotted key -> why. config-check lists these; delete a line once confirmed
"clamps.reach_in" = "decision 20"  "clamps.height_in" = "decision 20"  "machine.z_touchoff" = "decision 22"
"holes.drill_sizes_in.t12_eighth_alu" = "decision 7"  "materials.pc_clear.use_mist" = "decision 20"
"fusion.params.setup.wcs_origin_boxPoint" = "dump"

[machine]  reach_x_in = 40.0  z_floor_in = 0.0  units = "in"  spindle_rpm = 18000  spin_up_dwell_s = 4  park = "G53 P10"
[sheet]    length_in = 48.0  width_in = 24.0
[nest]     edge_margin_in = 0.5  reach_margin_in = 0.5  part_spacing_in = 0.25  max_sheets_per_group = 4
           rotation = "all"  part_in_part = false  envelope_spacing_in = 6.0  short_qty = "defer_card"
[clamps]   edges = ["front", "back"]  reach_in = 1.0  clearance_in = 0.25  height_in = 1.5  min_clear_above_stock_in = 2.0
[pauses]   enabled = true  after_last_part = false  mode = "tap_text"   # or "manual_nc" once proven
           resume_key = "TBD (open question 9)"
[plate]    thickness_tol_in = 0.005  strict_inside_radius = true

[materials.al6061]    name = "6061"  family = "aluminum"  color = ""  thicknesses_in = [0.0625, 0.125, 0.1875, 0.25]  use_mist = true
[materials.al5052]    name = "5052"  family = "aluminum"  thicknesses_in = [0.0625, 0.125]  use_mist = true
[materials.pc_clear]  name = "PC"  family = "polycarbonate"  color = "clear"  thicknesses_in = [0.0625, 0.125, 0.1875, 0.25]  use_mist = false
[materials.pc_smoked] name = "PC"  family = "polycarbonate"  color = "smoked" ...   # 14 stock types total

[tools.t1_4mm_alu]     guid = "7b77ef53-1ace-4e3b-ac2d-380b01a638bd"  number = 1   diameter_in = 0.15748  flute_in = 0.472  min_inside_radius_in = 0.0787  cutter_label = "4 mm O-flute ALU"
[tools.t1_4mm_poly]    guid = "ec14e4ef-aa38-4a96-af26-8885accae4a3"  number = 1   diameter_in = 0.15748  flute_in = 0.709  ...  cutter_label = "4 mm O-flute POLY"
[tools.t12_eighth_alu] guid = "7e0681ca-7664-4d53-ae3a-7457a77fd00a"  number = 12  diameter_in = 0.125    flute_in = 0.472  ...  cutter_label = "1/8 in ALU"
[tools.eighth_poly]    guid = ""   # open question 11; empty = unavailable
[holes]    drill_tol_in = 0.002  bore_min_in = 0.197  bore_min_tol_in = 0.002  bore_max_in = 1.5  bearing_sizes_in = [1.125, 0.875]  bearing_tol_in = 0.010
[holes.drill_sizes_in]  t1_4mm_alu = [0.156, 0.159]  t1_4mm_poly = [0.156, 0.159]  t12_eighth_alu = [0.125, 0.136]  eighth_poly = [0.125, 0.136]

[templates.alu_4mm]    family = "aluminum"       tool = "t1_4mm_alu"      file = "fusion/templates/alu_4mm.f3dhsm-template"
[templates.alu_eighth] family = "aluminum"       tool = "t12_eighth_alu"  file = "fusion/templates/alu_eighth.f3dhsm-template"
[templates.poly_4mm]   family = "polycarbonate"  tool = "t1_4mm_poly"     file = "fusion/templates/poly_4mm.f3dhsm-template"
# poly_eighth: added when the tool exists (config only)

[labels]   smoked = "Smoked"  tool_eighth = "Tool 1/8"
[onshape]  base_url = "https://cad.onshape.com"   # enterprise subdomain TBD
           calls_per_part_estimate = 4  per_run_max_calls = 60  monthly_soft_calls = 150  yearly_cap_calls = 1500
           budget_year_start = "01-01"  poll_first_s = 5  poll_factor = 3  poll_max_s = 60  poll_max_count = 4
           retry_after_max_wait_s = 60   # longer Retry-After -> stop the run, resume next trigger
[onshape.material_map]  "Aluminum - 6061" = "al6061"  "Aluminum - 5052" = "al5052"  "Polycarbonate" = "pc_clear"   # open question 8
[trello]   board_id = ""  poll_interval_s = 60  attachment_limit_mb = 10  job_timeout_s = 3600  checklist_name = "Review"
           checklist = ["Simulated in Fusion", "Correct stock loaded", "Correct cutter loaded", "Clamps placed as shown"]
[trello.lists]  inbox = ""  ready_for_cam = ""  needs_fixing = ""  nested = ""  sheet_review = ""  ready_to_cut = ""  cut = ""  control = ""  run_nest = ""
[trello.cards]  run_nest_control = ""  system = ""
[fusion]   post_description = "ShopSabre with automatic mist"  post_file = "fusion/posts/shopsabre_automatic_mist.cps"
           post_sha256 = "<pinned in M0>"  job_timeout_s = 1800  max_attempts = 2
[fusion.post_properties]   # sent explicitly on every post; useMist comes from the material
showSequenceNumbers = "false"  sequenceNumberStart = 10  sequenceNumberIncrement = 1  separateWordsWithSpace = true
writeMachine = false  writeTools = false  useToolCall = false  useTappingCycle = false
rotaryTableAxis = "none"  useCoolant = false  useXYZFeeds = "standard"  safePositionMethod = "G53"
[fusion.params]   # undocumented names; placeholders until dump_params confirms
setup = { wcs_origin_boxPoint = "'bottom 1'" }
drill = { hole_mode = "holeMode", hole_mode_value = "'selection-faces'", faces = "holeFaces" }
bore_faces_candidates = ["circularFaces", "holeFaces"]  pocket_selection = "pockets"  contour_selection = "contours"
tabs_at_points = {}   # M6
[tapguard] allowed_g = [0, 1, 2, 3, 4, 20, 53, 80, 81, 90]  allowed_m = [0, 3, 5, 11, 12]  clamp_margin_in = 0.25
[fusion_team] project = ""  folder = ""   # open question 7
[paths] queue = "queue"  cache = "cache"  state = "state"  logs = "logs"
```

Validation rejects:
- unknown keys
- `z_floor_in < 0`
- a GUID missing from the pinned `.tools`, or not matching its number/diameter/flute
- any automation target that is `ready_to_cut`
- a family/tool mismatch, or any poly template on T12
- a `tapguard` allowlist that includes `G91`/`G92`/`G28`/`G30`/`G10`/`G41`/`G42`/`G21`/`G22`

### `queue/incoming/<job_id>.json` (self-contained)

```json
{
  "schema": "autocam.job/1", "core_version": "0.1.0", "job_id": "r017-al6061", "run_id": "r017", "created_utc": "...",
  "material": {"key": "al6061", "name": "6061", "family": "aluminum", "color": "",
               "thicknesses_in": [0.0625, 0.125, 0.1875, 0.25], "thickness_tol_in": 0.005, "use_mist": true},
  "sheet": {"length_in": 48, "width_in": 24},
  "fixture": {"nest_region_in": [0.5, 1.25, 39.5, 22.75], "clamp_zones_in": [[0, 0, 48, 1.25], [0, 22.75, 48, 24]],
              "clamp_height_in": 1.5, "min_clear_above_stock_in": 2.0},
  "nest": {"part_spacing_in": 0.25, "max_sheets_per_group": 4, "rotation": "all", "part_in_part": false,
           "envelope_spacing_in": 6, "short_qty": "defer_card"},
  "tools": {"t1_4mm_alu": {"guid": "...", "number": 1, "diameter_in": 0.15748, "flute_in": 0.472,
                           "min_inside_radius_in": 0.0787, "drill_sizes_in": [0.156, 0.159], "cutter_label": "4 mm O-flute ALU"},
            "t12_eighth_alu": {"...": "..."}},
  "templates": {"t1_4mm_alu": {"key": "alu_4mm", "path": "C:/dev/frc-autocam/fusion/templates/alu_4mm.f3dhsm-template"},
                "t12_eighth_alu": {"key": "alu_eighth", "path": "..."}},
  "default_tool": "t1_4mm_alu", "fallback_tool": "t12_eighth_alu",
  "holes": {"drill_tol_in": 0.002, "bore_min_in": 0.197, "bore_min_tol_in": 0.002, "bore_max_in": 1.5,
            "bearing_sizes_in": [1.125, 0.875], "bearing_tol_in": 0.01},
  "plate": {"strict_inside_radius": true},
  "pauses": {"enabled": true, "after_last_part": false, "mode": "tap_text", "park": "G53 P10", "spindle_rpm": 18000, "dwell_s": 4},
  "post": {"description": "ShopSabre with automatic mist", "path": "C:/dev/frc-autocam/fusion/posts/shopsabre_automatic_mist.cps",
           "sha256": "...", "properties": {"useMist": true, "safePositionMethod": "G53", "...": "..."}, "units": "in"},
  "tapguard": {"z_floor_in": 0.0, "allowed_g": [0, 1, 2, 3, 4, 20, 53, 80, 81, 90], "allowed_m": [0, 3, 5, 11, 12],
               "park": "G53 P10", "clamp_margin_in": 0.25},
  "fusion_params": {"...": "..."}, "fusion_team": {"project": "", "folder": ""},
  "program_prefix": "6061",
  "parts": [{"part_key": "p01", "card_id": "...", "card_url": "...", "name": "hood_gusset", "qty": 4,
             "step": "C:/dev/frc-autocam/cache/step/<did>_<vid>_<eid>_<pid>.step", "step_sha256": "...",
             "source": "onshape", "onshape": {"did": "...", "vid": "...", "eid": "...", "part_id": "JHD", "url": "..."},
             "force_tool": null}]
}
```
Attempts live in a `processing/<job_id>.attempts` sidecar, not in the job.

### `queue/done/<job_id>/result.json` (written last; its presence = done)

```json
{
  "schema": "autocam.result/1", "core_version": "0.1.0", "job_id": "r017-al6061", "status": "ok | needs_review | failed",
  "worker": {"fusion_version": "...", "python_version": "...", "attempt": 1, "started_utc": "...", "finished_utc": "...",
             "untested_steps": ["fx_template.create_from_operations"]},
  "post": {"method": "path | library", "sha256_verified": true, "properties": {"...": "..."}},
  "fusion_team": {"url": "...", "name": "..."}, "f3d": "r017-al6061.f3d", "f3d_bytes": 812345,
  "sheets": [{
    "index": 1, "name": "6061_0p125_r017_S1", "stock_type": "al6061-0.125", "thickness_in": 0.125,
    "tool": "t1_4mm_alu", "tool_guid": "7b77ef53-...", "cutter_label": "4 mm O-flute ALU", "template": "alu_4mm",
    "tap": "6061_0p125_r017_S1.tap", "tap_rejected": null, "tap_bytes": 48211, "tap_sha256": "...",
    "guard": {"passed": true, "floor_in": 0.0, "min_z_in": 0.0, "units": "G20", "offenders": [], "clamp_violations": []},
    "pauses": {"mode": "tap_text", "list": [{"after": "p01#1", "line": 412}], "expected": 4, "found": 4},
    "machining_time_s": 1380, "preview_png": "6061_0p125_r017_S1.png",
    "parts": [{"part_key": "p01", "count": 2}], "outer_order": ["p01#1", "p03#1"],
    "tool_forced_by": [{"part_key": "p07", "reason": "0.136 in hole (8-32) smaller than 4 mm"}],
    "errors": [{"code": "...", "msg": "..."}], "warnings": [], "notes": []}],
  "parts": [{"part_key": "p01", "card_id": "...", "qty": 4, "placed": 4, "deferred": false, "sheets": [1, 2],
             "measured_thickness_in": 0.1252, "stock_thickness_in": 0.125, "tool_need": "t1_4mm",
             "holes": {"drill": 6, "bore": 2, "bearing": 1, "inner": 1, "contour_warn": 0}, "pockets": 0,
             "errors": [], "warnings": []}],
  "notes": []
}
```

### Onshape ledger: `state/onshape_ledger.jsonl` (append-only, fsync per line, gitignored)

```json
{"ts": "...", "id": "c-000123", "phase": "pending", "run_id": "r017", "method": "POST", "endpoint": "/api/v10/partstudios/d/{did}/v/{vid}/e/{eid}/translations", "purpose": "export_step", "part_key": "p01"}
{"ts": "...", "id": "c-000123", "phase": "done", "status": 200, "billable": true, "ms": 412}
```
- **Pending first.** A `pending` line is written before every request. A pending entry with no `done` line counts
  as billable, which keeps the count safe across crashes.
- **What counts.** 2xx/3xx are billable. Redirects are followed manually, re-signed, and each hop is logged.
  429s are not billable. Budget refusals get a `phase: "blocked"` line.
- **402 latch.** Any 402 writes `state/onshape_latch.json`. All calls are refused until a human runs
  `cli ledger reset-latch`.
- **Budget decision.** `budget.decide()` returns proceed, warn (monthly soft; comment on `System` card), or refuse
  (per-run or yearly cap).
- **Estimate.** Made before the first call: cache misses x `calls_per_part_estimate`, plus one parts-list call per
  uncached Part Studio version.
- **Run state.** `state/runs/<run_id>.json` records the cards and jobs, plus every Trello write as it lands
  (sheet card IDs, attachment IDs, moves). A restart resumes without duplicating anything.

## 4. Milestones

Fusion-side work counts as done only after you run it; until then I report it as untested. Each manual step has a
checklist in `docs/manual-tests.md`.

### M0: Scaffolding, config, `.env` (the only milestone I start on approval)

**Steps:**
1. Move the empty `/mnt/c/dev/.git` (remote `trello-cam`) into `/mnt/c/dev/frc-autocam/`. Run
   `git config core.fileMode false`.
2. Extract the zip with Python `zipfile` (byte-exact). Verify each file's sha256 against its zip member.
3. Save this plan as `docs/PLAN.md`. Add skeleton `docs/manual-tests.md`, `docs/fusion-api-status.md`, and
   `docs/decisions.md`.
4. Python setup:
   - `pyproject.toml`: pytest `pythonpath = ["core", "service"]`, `testpaths = ["tests"]`.
   - Dependencies in `pyproject.toml`: `requests`, `python-dotenv`, `tomli` on Python < 3.11; extra `dev`: `pytest`.
   - venv at `~/.venvs/frc-autocam`.
   - `.env.example` (Trello key/token, Onshape access/secret keys).
   - `.gitignore`: add `state/` and `logs/`.
5. Code and config:
   - `core/autocam_core/__init__.py` (`CORE_VERSION`) and `toollib.py`.
   - `service/autocam_service/config.py` and `credentials.py`.
   - `config/autocam.toml` with every section from §3.
   - `cli config-check`.
6. Tests:

   | Test | What it checks |
   |---|---|
   | `conftest.py` | autouse **socket block**: any network attempt fails the test |
   | `test_config` | the real config loads, and each validation rule rejects a bad variant |
   | `test_credentials` | missing keys are named; values are redacted |
   | `test_toollib` | the 3 GUIDs resolve to T1/T1/T12 at 4 mm/4 mm/0.125"; the Onsrud T2 and other duplicates never match |
   | `test_pinned_files` | post sha256 matches; `.cps`/`.tap` are all CRLF; `.gitattributes` rules present |

7. `.github/workflows/tests.yml`: pytest on windows-latest and ubuntu-latest (Python 3.12), plus `tests/core` on
   3.9.

**Accept:**
- `pytest` is green offline.
- `config-check` exits 0 and lists the ASSUMED keys and empty placeholders.
- `git ls-files --eol` shows `.cps`/`.tap` as `-text` CRLF.
- No secrets are tracked.

I show you the diff and **ask before committing or pushing**.

**As built (differences from the plan above):**
- **Credentials module.** It is named `credentials.py`, not `secrets.py`, so it can't shadow the standard
  library's `secrets` module.
- **Dependencies.** They live in `pyproject.toml` (`pip install -e ".[dev]"`), not in `requirements*.txt` files.
- **`tomli` fallback.** WSL ships Python 3.10, so the service reads TOML with `tomli` there and with
  `tomllib` on Python 3.11+.
- **`.gitattributes` fix.** In the original, the trailing `* text=auto` line overrode the machine-file rules,
  because the later matching line wins. That would have let git rewrite `.cps`/`.tap` line endings. The
  catch-all now comes first, and a test checks the order plus `git check-attr`.
- **Post properties.** The post also defines `writeMachine` and `writeTools`. Config validation now requires
  every post property except `useMist` to be set explicitly.

### M1: Fusion script hardening (M1.1 runs in parallel with M1.0)

**M1.0: your Fusion session.**
- **I write two scripts:**
  - `fusion/tools/dump_params/`: every setup/op/NC-program parameter (name, type, value, expression), each op's
    tool JSON/GUID, post library entries, Fusion and Python versions.
  - `fusion/tools/api_probe/`: introspection only, in a scratch document. It checks `LoopTypes` names,
    `Setup.stockSolids`, Arrange input attrs, `resultEnvelopes`, Manual NC creation, `createFromOperations`,
    `PostProcessInput`, `Tool.toJson`, `DataFile.fusionWebURL`, `getMachiningTime`, `isSuppressed`, and setup
    fixtures.
- **You run** Phase 1 **as-is** on 2-3 through-cut plates with an `[inner]`/`[outer]` template, using a checklist
  table of every step.
- **Accept:** you return 3 JSON files plus the table. Placeholders and `fusion-api-status.md` get filled in, and
  each failed call has a chosen fallback.

**M1.1: pure core (offline).** `tapguard`, `pauses`, `holes`, `plate`, `tooling`, `fixture`, `layout`,
`ordering`, `names`, the schemas, `hotfolder`. **Accept** when unit tests cover:
- `pause_air_test.tap`: passes the guard (min Z 3.0) and the pause verifier (2 pauses).
- Guard cases, each with its expected verdict:
  - Z forms: `Z-0.0001`, `Z-.5`, `Z+1`, Z inside a comment, an unclosed `[`.
  - G53 lines: `G53 Z` (ok) and `G53 X5` (reject).
  - Canned cycles: G81 with negative Z, G81 with negative R, G83 (reject by default).
  - Rejected codes: G91, G41.
  - Units: missing G20, G22.
  - Clamp zones: a rapid across a clamp zone at low Z (reject), and the same rapid after `G53 Z` (ok).
  - Footer: a missing footer.
- Holes, with boundaries:
  - Drill sizes 0.156/0.159 ±0.002.
  - Bore lower edge: 0.195 vs 0.197-0.002.
  - Bore upper edge: 1.5 inclusive; 1.5001 goes to inner.
  - Bearing sizes 0.875/1.125 ±0.010.
  - 0.136 needs 1/8". A 0.159 hole on a 1/8" sheet becomes contour-warn.
  - Half-cylinder grouping.
  - A poly part needing 1/8" with no template gets the exact comment.
- Fixture, layout, and ordering on hand-made cases.

**M1.2: restructure to parity.** `worker/` + `pipeline` + `autocam_run` reproduce Phase 1 on through-cut plates.
**Accept (you):** the same plates give `.tap`s that pass the guard; the status table is updated.

**M1.3: fixture and thickness groups.** Full-sheet stock solid, nest region, clamp-zone bodies, WCS at bottom
front-left, one nest per measured thickness. **Accept (you):**
- The preview shows the sheet, strips, and parts inside the region.
- Simulation stock is 24x48, and the origin is verified in Fusion.
- The guard's region and clamp checks pass.

**M1.4: holes and pockets.** Extraction, classification, the `[drill]`/`[bore]`/`[bearing]`/`[pocket]`/`[inner]`
selections, pocket side up (re-checked after Arrange), two-sided reject. **Accept (you):** a test-part set gives
the expected per-part counts and errors, and the toolpaths generate. The set:
- holes: 0.159, 5 mm, 0.875, 1.125, 8-32
- slot
- pocket
- two-sided pocket
- chamfer

**M1.5: per-sheet tool choice.** Template per sheet, GUID verification, `Tool 1/8` label. **Accept:**
- Offline: the logic tests pass.
- In Fusion (you):
  - an aluminum sheet with an 8-32 part switches to T12, and `tool_forced_by` names the part;
  - a poly small-hole part is rejected with the exact comment;
  - an op with the wrong-GUID tool fails the sheet.

**M1.6: per-part outlines and pauses.** Per-part `[outer]` ops in `ordering` order, then pause insertion, then
`pauses.verify`, then the guard, all on the final bytes. Also generate `pause_air_test_mist.tap` from the
production builder. **Accept:**
- The pause list matches `outer_order` (parts minus 1).
- Each block is complete: `G53 Z`, `M5`, `M12 C8` if mist, park, `M0`, `M11 C8` if mist, `S18000`, `M3`, `G4 X4`.
- **Air test on the machine with both air-test files before any real use** (the decision 19 checklist).

**M1.7: outputs.** Fusion Team save + link, per-sheet PNG, local `.f3d` under 10 MB, machining time, complete
`result.json`. **Accept (you):** the file is in the project folder and the link opens.

### M2: Fusion add-in hot-folder worker

**What it does:**
- Poller -> custom event -> main-thread `process_job`.
- Busy flag, one job at a time.
- Re-queue `processing/` on start. The attempt sidecar sends a job to `failed/` after 2 crashes.
- Heartbeat, timeout, no dialogs, document closed after each job.

**Accept:**
- Offline: `hotfolder` tests (atomic claim, requeue, attempts, result-last) and a fake worker.
- Manual (you):
  - a dropped job reaches `done/`;
  - killing Fusion mid-job re-queues it on restart;
  - a poison job reaches `failed/` after 2 attempts;
  - a job with an older `core_version` is refused with a clear message.

### M3: Service, Trello read + Onshape export + job writer (dry-run)

**What it does:**
- Polls the board and triggers on the control card in `Run nest`. One run at a time; a second trigger gets a
  comment.
- Validates every card **before any Onshape call**, rejecting:
  - workspace links and non-Part-Studio links;
  - names that are missing or duplicated in the studio;
  - non-solid parts.
- Exports with cache + ledger + budget, maps materials, batches by material + color, and writes jobs.
- Dry-run sends no Trello writes.

**Accept (offline):**
- Card-format errors produce the right comment text.
- `Smoked` override works.
- A cache hit makes zero client calls; the ledger counts billable calls only, and a dangling pending entry counts.
- Each redirect hop is logged; a 402 latches.
- A 429 with a long `Retry-After` stops the run cleanly.
- Budget refusal and the per-run cap work.
- Jobs validate against `schema_job`.
- End-to-end with fake tracker + fake Onshape transport + fake worker.

**Manual (needs your OK):** the first supervised real run with `per_run_max_calls = 15` on 1-2 parts. It records
sanitized responses as fixtures and confirms whether the parts list carries `material`.

### M4: Service, results back to Trello

**What it does:**
- Ingests `done/`/`failed/` and re-runs `tapguard` + `pauses.verify` on each `.tap`.
- Creates sheet cards:
  - title: stock, tool, cutter, sheet, pauses;
  - description: Fusion Team link, `LOAD:` cutter line, resume key, lowest Z, machining time, forced-tool part,
    part list.
- Attaches `.tap` + PNG (fails loudly over 10 MB) and adds the checklist.
- Part cards get a comment, a link attachment, and a move to `Nested` (or `Needs fixing` / stay deferred).
- Posts a summary comment on the control card.
- Runs the Ready-to-cut checklist guard.

**Accept (offline, fake tracker + fake worker):**
- A `.REJECTED.tap`, or bytes without a matching passing `GuardReport`, can't be uploaded (sha256 bound).
- No code path moves a card to `ready_to_cut`: the tracker raises, and a test proves it.
- Crash-and-resume duplicates no cards or attachments.
- An incomplete checklist in Ready to cut is moved back with a comment.
- A Fusion job past `job_timeout_s` is reported.

**Manual (you):** one real run on the board.

### M5: Ops

**What it does:**
- Rotating logs for both processes.
- Windows auto-start: Task Scheduler at logon for the service; Fusion at logon with the add-in `runOnStartup`.
- A pinned `System` card shows last poll, worker heartbeat age, queue depth, Onshape calls month/year, latch, and
  last error.
- A stale-heartbeat alert comment.

**Accept:**
- Offline: health rendering and staleness tests.
- Manual (you): after a reboot of the shop PC, the pipeline comes back on its own.

### M6: Tabs (only if the no-tab trial fails)

Pure tab placement per the brief's rules, plus an At-points adapter that uses the names from the dump.

**Accept:**
- Offline: unit tests for every rule.
- Manual: a cut test.

## 5. Risks and how the plan handles them

### 1. Untested Fusion API calls
- **Order of work.** Run Phase 1 as-is plus `api_probe` before restructuring.
- **Containment.** BRep/CAM calls live only in `worker/fx_*`. Each one feature-detects
  (`hasattr(type(obj), ...)`) and returns a coded error naming the call. Status is tracked per call.
- **Fallbacks:**

  | Area | Fallback |
  |---|---|
  | Bore face param | Drill op with the bore-milling cycle on `holeFaces` |
  | Manual NC | Text insertion (already the default) |
  | Op copies | Re-apply the template, delete extras |
  | Arrange results | One envelope per Arrange |
  | Setup fixtures | Skipped; the guard's clamp check covers it |
  | `Tool.toJson` | Match against the pinned library |
  | Post by path | Library post plus sha256 check |
  | Web link | Name on the card + attached `.f3d` |

- **Correctness lives outside Fusion.** Pause insertion, pause verification, and the guard are pure Python. They
  run on the final bytes in Fusion **and again in the service**.

### 2. Shared Onshape budget
- **Single path.** One client, with a ledger entry written *before* each request.
- **Cache.** Keyed by version, kept forever (STEP files and parts lists).
- **Checks before spending.** Card validation and a run estimate happen before the first call.
- **Caps.** Per-run cap, monthly soft warning, yearly cap for our share.
- **Errors.** A 402 latches until a human resets it. 429s and polls are capped, and a long `Retry-After` stops
  the run instead of sleeping.
- **Tests.** No network in tests: socket block plus an injected fake transport. Real fixtures come only from the
  supervised M3 run, then get sanitized.
- **Limits of the ledger.** Other enterprise users' calls are invisible to it, so the 402 latch is the backstop.

### 3. A program with Z < 0 reaching Trello
- **Templates.** Bottom = stock bottom, offset 0.
- **`tapguard` fails closed:**
  - Exactly one `G20` before the first motion.
  - Only `G53 Z` and the park `G53` line; any other `G53` is rejected.
  - Allowlisted G/M words only. G91/G92/G28/G30/G10/G21/G22/G41/G42/G73/G83/G84 are rejected by default.
  - Checks `Z` *and* `R` on every block, accepting `+`/`-`/leading-dot forms.
  - Strips `[...]` comments and rejects an unclosed `[`.
  - Tracks "Z at top" after `G53 Z`.
  - Rejects any XY move over a clamp zone (arcs by bbox) below stock top + clamp height + margin.
  - Checks the header/footer fingerprint against `useMist`.
- **Fusion side.** Failures are renamed `*.REJECTED.tap`.
- **Service side.** It re-checks the bytes and can only upload bytes whose sha256 matches a passing report.
- **Config.** It refuses `z_floor_in < 0` and any dangerous allowlist entry.

### 4. Pause safety
- `M0` with the spindle off, never a dwell. The block re-issues mist and spindle itself.
- The verifier checks each pause's position and content.
- Two air tests (with and without mist), generated by the production builder, run before real use.
- The card states the resume key and pause count.

### 5. Wrong cutter / post drift
- **Tool.** Ops are verified by tool GUID; the post only sees numbers and T1 is duplicated.
- **Card.** The title and a checklist item name the physical cutter.
- **Post integrity.** The pinned post is sha256-tested and posted by path (or the library copy is verified).
  Every post property is set explicitly and recorded.

### 6. Clamp collisions between ops
There is no `G53` between same-tool ops, and the guard's clamp-zone check runs on the real file. Simulation is a
checklist item.

### 7. One 1/8" part slows a whole sheet
This is the settled decision (13/21). `tool_forced_by` puts the responsible part on the card so the team sees why.

### 8. Fusion hangs, crashes, or runs stale code
Busy flag, job timeout in both processes, attempt sidecar, heartbeat + stale alert, no modal dialogs, and the
`CORE_VERSION` handshake.

### 9. Trello Free limits and card mistakes
- Hard 10 MB check and rate-limit backoff.
- Strict parsing with a format-help comment.
- No custom fields or Butler.
- Attachment IDs are recorded before moving on.

### 10. Public GitHub repo
- Secrets only in `.env`.
- Fixtures are sanitized (names, emails, keys) before commit.
- Board and list IDs are harmless without a token.

### 11. WSL vs Windows
`pathlib` only, Windows CI, polling instead of inotify, byte-exact CRLF machine files.

## 6. Verification for M0 (after approval)

- `cd /mnt/c/dev/frc-autocam && ~/.venvs/frc-autocam/bin/pytest -q`: all green, with the socket block active.
- `python -m autocam_service config-check`: exit 0, and it lists the ASSUMED keys and empty placeholders (Trello
  IDs, Fusion Team, poly 1/8" tool, templates not yet exported).
- Every imported file's sha256 equals its zip member. `git ls-files --eol` shows CRLF `-text` for `.cps`/`.tap`.
  No `.env` is tracked.
- `git remote -v` still shows `trello-cam`. Nothing is committed or pushed until you say so.
