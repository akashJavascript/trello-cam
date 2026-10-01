# Status

_Last updated 2026-10-01 (overnight, branch `m1-offline`)._

## Where things are

| Milestone | State |
|---|---|
| M0 scaffolding, config, `.env` | Done, on `main` |
| M1.0 dump_params / api_probe / smoke-test checklist | Scripts written, **untested in Fusion**; waiting on your Fusion session |
| M1.1 pure core | Done and unit-tested |
| M1.2-M1.7 Fusion adapters + pipeline | Not started; waits for the M1.0 results (brief: smoke test first, then restructure) |
| M2 add-in hot-folder worker | Hot-folder logic done and tested (`autocam_core.hotfolder`); the add-in itself waits for M1.2 |
| M3 service: Trello read + Onshape export + jobs | Done offline with fakes; **never run against real Trello or Onshape** |
| M4 service: results back to Trello | Done offline with fakes and a fake Fusion worker |
| M5 ops | Health card text done (`health.py`); auto-start and wiring not started |
| M6 tabs | Not needed unless the no-tab trial fails |

M1.1 pure core covers:
- the `.tap` guard and pause insertion/verification;
- holes, plate rules and tool choice;
- the fixture, layout and ordering;
- the job/result schemas and the hot folder.

## What only you can do next

1. **Enable CI.** The workflow is parked in `ci/` because the GitHub token can't push workflow files. See
   `ci/README.md`; it takes two commands.
2. **Fusion session for M1.0.** Follow `docs/manual-tests.md` → "M1.0". Running `api_probe` and
   `dump_params` writes their JSON into the repo, so there's nothing to send. Then run the Phase 1 smoke test
   and fill in its table.
3. **Export the three CAM templates** into `fusion/templates/` (rules in `fusion/templates/README.md`). Jobs
   can't be built until the 4 mm templates exist.
4. **Review the defaults** in `docs/decisions.md` → "overnight work".

## Not verified yet (don't rely on these until they are)

- **Fusion scripts:** `fusion/tools/api_probe`, `fusion/tools/dump_params`. They only pass a smoke run
  against a fake `adsk` module.
- **Onshape:** HMAC signing, `/api/v10` paths and response field names.
- **Trello adapter:** request shapes follow the REST docs. It has only been tested against a fake HTTP layer.
- **Windows:** nothing has run on Windows yet. CI will cover the pure code once it's enabled.
