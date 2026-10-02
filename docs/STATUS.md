# Status

_Last updated 2026-10-01 (branch `m1-offline`)._

## Where things are

| Milestone | State |
|---|---|
| M0 scaffolding, config, `.env` | Done, on `main` |
| M1.0 dump_params / api_probe / smoke test / probes | Done: api_probe, dump_params, the smoke test (stopped at Arrange), `pipeline_probe` and three `pipeline_probe2` runs. Every call the through-cut pipeline needs has been seen working. Results: `docs/fusion-api-status.md` |
| M1.1 pure core | Done and unit-tested |
| M1.2-M1.7 Fusion adapters + pipeline | **Working in Fusion for through-cut aluminum plates** (2026-10-01): drilled, bored and bearing holes, cutouts, several parts per sheet, pauses, the guard and a check that the tool never enters a part. Still to run in Fusion: several sheets, the 1/8 in tool, polycarbonate. Pockets wait on a 2D Adaptive dump. Fusion Team save (M1.7) written, untested: needs the project and folder in config |
| M2 add-in hot-folder worker | Written: `fusion/autocam_addin/` (job loop in `autocam_worker/worker.py`, tested offline, including a full service run through it); **works in Fusion** (2026-10-01: a queued job ran by itself into `done/`). Still worth doing once on the shop PC: kill Fusion mid-job and check the retry (`docs/manual-tests.md` → M2, checks 4-5) |
| M3 service: Trello read + Onshape export + jobs | Done offline with fakes; **never run against real Trello or Onshape** |
| M4 service: results back to Trello | Done offline with fakes and a fake Fusion worker |
| M5 ops | Health card text done (`health.py`); wiring it into the service and Windows auto-start not started |
| M6 tabs | Not needed unless the no-tab trial fails |

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

## What only you can do next

1. ~~Enable CI~~: done. GitHub Actions runs pytest on Linux and Windows (Python 3.12) and the core on 3.14 (Fusion's Python).
2. ~~Run `pipeline_probe2`~~: done (three runs).
3. **Export the four CAM templates** into `fusion/templates/` (rules in `fusion/templates/README.md`). Every
   bottom height must be **stock bottom, offset 0**: the team's usual "From contour" stops at the plate top when
   the automation selects the top face, and the sheet check now rejects that program. Jobs can't be built until
   the 4 mm templates exist.
4. **Review the defaults** in `docs/decisions.md` → "overnight work".

## Not verified yet (don't rely on these until they are)

- **Fusion scripts:** all the probes have run; what they confirmed is in `docs/fusion-api-status.md`.
- **Onshape:** HMAC signing, `/api/v10` paths and response field names.
- **Trello adapter:** request shapes follow the REST docs. It has only been tested against a fake HTTP layer.
- **Windows:** CI passes the whole offline suite on `windows-latest`. Fusion, the real Trello and Onshape calls, and the shop PC itself are still untested.
