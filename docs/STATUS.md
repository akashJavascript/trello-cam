# Status

_Last updated 2026-10-03, evening (branch `m1-offline`)._

## Where things are

| Milestone | State |
|---|---|
| M0 scaffolding, config, `.env` | Done, on `main` |
| M1.0 dump_params / api_probe / smoke test / probes | Done: api_probe, dump_params, the smoke test (stopped at Arrange), `pipeline_probe` and three `pipeline_probe2` runs. Every call the through-cut pipeline needs has been seen working. Results: `docs/fusion-api-status.md` |
| M1.1 pure core | Done and unit-tested |
| M1.2-M1.7 Fusion adapters + pipeline | **Working in Fusion for through-cut aluminum plates** (2026-10-01): drilled, bored and bearing holes, cutouts, several parts per sheet, pauses, the guard and a check that the tool never enters a part. Still to run in Fusion: several sheets, the 1/8 in tool, polycarbonate. Pockets wait on a 2D Adaptive dump. Fusion Team save (M1.7) **works**: jobs save to 5940 BREAD / AutoCAM test with a file link |
| M2 add-in hot-folder worker | Written: `fusion/autocam_addin/` (job loop in `autocam_worker/worker.py`, tested offline, including a full service run through it); **works in Fusion** (2026-10-01: a queued job ran by itself into `done/`). Still worth doing once on the shop PC: kill Fusion mid-job and check the retry (`docs/manual-tests.md` → M2, checks 4-5) |
| M3 service: Trello read + Onshape export + jobs | **First real run worked** (2026-10-02, board https://trello.com/b/fsuTYbO3/5940-autocam): a real card -> Onshape STEP export -> job. It found and fixed: Markdown around `Qty:`, and an Onshape 400 crashing the pass (now that card goes to Needs fixing) |
| M4 service: results back to Trello | **First real run worked** (2026-10-02): the sheet card with the program came back to Sheet review |
| Board flow (2026-10-02) | Runs start by themselves 2 min after cards land in Ready for CAM; "Nest this part" box; new parts fill sheets nobody has started reviewing (cards updated in place). Tested offline; first real run (r005) published fine. **Options** on sheet cards since 2026-10-02: cut without stopping, and an air test (a raised copy that cuts nothing). Tested offline and on r005's real program; the service applies them on the real board. The service restarts itself into new code (seen working twice) |
| M5 ops | **Built 2026-10-02:** the System card's description is a live status (Fusion alive, run, waiting cards, Onshape budget, last error), with one comment when jobs wait for a closed Fusion; one service at a time (a lock); `ops/install-autostart.ps1` starts the service and Fusion at logon (dry run checked; not installed yet). Checks in `docs/manual-tests.md` → M5 |
| M6 tabs | **Built 2026-10-03** (tape alone didn't hold parts in the team's trials): a "Hold it in with tabs" box on part cards, ticked by default (`[tabs] default`); the template's tabs on that part's outline and every cutout, 2 to 6 per contour by its length, triangular (from the template), placed on straight edges clear of corners where possible, at points. Works in Fusion (test jobs); not cut yet |

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

## What's left (2026-10-03, evening)

### Before the first real cut
1. **Z with the probe** (the user has a Z height probe): set it on the spoilboard next to the sheet, not on
   the sheet. Every program's Z0 is the spoilboard (decision 22); probed on top of the sheet, nothing would cut
   through.
2. **Tabs trial:** cut a sheet with tabbed parts (triangular, 0.157 in wide, 1 mm high) and check the parts
   and slugs stay put and the tabs break out cleanly (tape alone didn't hold parts in the team's trials).
3. **Pause test** on a sheet with 2+ parts: tick "Add an air test program" under Options and run it
   (`docs/manual-tests.md` → M1.6). Record the resume key: `pauses.resume_key` is empty, so cards say
   "the continue key".
4. **Confirm the clamp values** and the other `[assumed]` entries in `config/autocam.toml` (`autocam
   config-check` lists them): clamp reach, clearance and height decide where parts may go.
5. A **corner stop** on the bed (the user's idea): every sheet and offcut square, in reach and at one zero.

### Still to build
- **Placed by hand** (built 2026-10-03, tested offline, untested in Fusion): "Nest this part" unticked gives a
  part its own program, long side along X, zeroed at its box's front-left corner, run once per copy.
- **Templates:** `alu_eighth`, `poly_4mm`, `poly_eighth` (only `alu_4mm` exists). Until then, polycarbonate
  parts and parts that need the 1/8 in endmill stay in Ready for CAM. Set Tab shape: Triangular in each.
- **Pockets:** needs a `dump_params` run on a job with a 2D Adaptive op (P-2015 waits in Needs fixing).
- **0.3 in tabs:** crashed Fusion before tabs were kept on one edge; try a test job before using them.
- **Adapters:** the user's, later.
- **M5, on the shop PC:** run `ops\install-autostart.ps1`, tick the add-in's Run on Startup, then a reboot test
  (`docs/manual-tests.md` → M5). On this PC: tick Run on Startup (a Fusion restart left the add-in off once).

### Decisions waiting on you
- **Merge `m1-offline` into `main`:** everything since M0 is on the branch.
- **Onshape per-run cap:** `onshape.per_run_max_calls` is still 15, about 2 uncached parts per run (more cards
  just wait for the next run). Raise it once the ledger looks right (17 calls so far).

### Built, not yet seen on the machine or the real board
- **Better nests:** several Arrange tries per thickness, the best kept, then the last sheet squeezed into a
  strip across the front. Work in Fusion (r006, r014).
- **Offcuts**, with **room beside** earlier cuts: work in Fusion (r013 to r016, a test job); none cut yet
  (manual tests M3 5c).
- **Rush**, freed offcuts, smallest offcut first, the season's stock tally: tested offline (M3 section 8).
- **Finding offcuts:** numbers, "The offcut isn't on the rack", scraps added by hand: tested offline (M3 section 9).
- **Tabs:** on by default, counts per contour, placed at points on straight edges clear of corners,
  triangular from the template. Work in Fusion (test jobs, r018 S1); none cut yet (M3 section 10).
- **Board:** "Cut the whole sheet without stopping" on a 2+ part sheet.
- **Fusion:** several sheets in one run, the 1/8 in tool, polycarbonate. (Fusion dying mid-job is handled:
  M2 checks 4-5 passed by accident on 2026-10-03.)
- **Self-updating** works (the service and the add-in, most recently into core 0.11.0).

## Not verified yet (don't rely on these until they are)

- **Fusion:** what has run is in `docs/fusion-api-status.md`; everything else in `fx_*.py` is untested.
- **The machine:** no program has been cut or air-tested yet.
- **Onshape and Trello** have worked for real (runs r001-r016, `.step` attachments included); version links,
  Smoked labels and 5052 haven't been through a real run.
