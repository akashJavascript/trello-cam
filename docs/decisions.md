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
