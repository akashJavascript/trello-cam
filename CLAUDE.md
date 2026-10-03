# FRC auto-CAM pipeline

Trello cards (Onshape part links) -> nested, CAM'd Fusion file + WinCNC .tap per sheet -> back to Trello.
Full context and decisions: `docs/BRIEF.md`. Read it before planning or changing architecture.

## Hard rules

- **Never call the Onshape API from tests, exploration, or scratch scripts.** The team has 10,000 calls/year
  shared across the whole enterprise. Every Onshape request in product code goes through the single client
  that records calls in the ledger. Tests use recorded fixtures only.
- **Never commit secrets.** Trello key/token and Onshape keys live in `.env` (gitignored). Provide `.env.example`.
- **Nothing is ever moved to "Ready to cut" automatically.** A human reviews every sheet.
- **Fusion code cannot be run in this environment.** Don't claim Fusion-side code works. Mark it untested,
  keep Fusion-dependent code in thin adapters, and add manual test steps to `docs/manual-tests.md`.
- **Inside Fusion: standard library only** (no pip packages in Fusion's bundled Python). Fusion API calls
  happen only on Fusion's main thread (background threads hand work over via custom events).
- **Units:** Fusion's API is centimeters internally. Config files and job files are inches. Convert at the edge.
- **Onshape links:** version links, or workspace links pinned to their current microversion when a run reads
  them (the user's choice, 2026-10-02). Microversion links get rejected back to the card with a comment.
- **Tools are identified by GUID only.** The GUIDs live in `config/autocam.toml` and must match
  `fusion/tools/5940_Tool_Library.tools` (the brief's tool table has older GUIDs). Never select by diameter or tool number.
- **Never edit `fusion/posts/*.cps`.** It's the shop's machine-proven post. Configure it through post properties only.

## Layout (see `docs/PLAN.md` for the module list)

- `core/autocam_core/` pure logic, **standard library only**: imported by the Fusion add-in and the service.
  Fusion 2705 bundles Python 3.14 (confirmed by api_probe); CI runs the core tests on 3.14.
- `service/autocam_service/` plain-Python Windows service: Trello + Onshape + hot folder.
- `fusion/autocam_addin/` the Fusion add-in (M2): `autocam_addin.py` wires Fusion to the job loop in
  `autocam_worker/worker.py`.
- `fusion/autocam_addin/autocam_worker/` the Fusion side: `pipeline.py` (pure job flow, tested offline against
  `tests/fusion/fakeadapter.py`), `worker.py` (the add-in's job loop), `adapter.py` (the interface), `fx_*.py`
  (the real Fusion calls; see `docs/fusion-api-status.md` for what has run in Fusion).
- `fusion/autocam_run/` Fusion script for manual runs: STEP files or a job.json -> the pipeline.
- `fusion/autocam_nest/` Phase 1 Fusion script (reference only; its Arrange calls fail in Fusion).
- `fusion/posts/` the shop's WinCNC post (reference copy; do not edit; sha256-pinned by a test).
- `fusion/tools/` the shop's Fusion tool library (reference copy).
- `fusion/templates/` the team's exported CAM templates (rules in its README).
- `config/autocam.toml` all settings (inches); `[assumed]` lists values still to confirm. No secrets.
- `docs/` brief, plan, decisions, manual test checklists, Fusion API status.

## Environment

- Development may happen in **WSL**, but everything runs on **Windows** in production (the spare shop PC):
  the service is native Windows Python, and Fusion is a Windows app. Use `pathlib`, no POSIX-only APIs or
  shell tools in product code, and keep Windows paths working in job/config files.
- The repo should live on the Windows drive (e.g. `C:\dev\frc-autocam`, `/mnt/c/dev/frc-autocam` from WSL) so
  Fusion can load `fusion/` directly.
- `.tap` and `.cps` files are CRLF and must stay byte-exact (see `.gitattributes`).
- Working on `/mnt/c` from WSL: use WSL's git only (not Git for Windows on the same checkout), with
  `git config core.fileMode false`; keep the Python venv in the WSL home (e.g. `~/.venvs/frc-autocam`), not in
  the repo; don't rely on file-watch events (inotify) for `/mnt/c` paths in tests; the production hot-folder
  watcher runs on Windows, and polling is fine.

## Commands

- Setup (WSL): `python3 -m venv ~/.venvs/frc-autocam && ~/.venvs/frc-autocam/bin/pip install -e ".[dev]"`
- Tests: `~/.venvs/frc-autocam/bin/pytest` (fully offline; sockets are blocked in every test).
- Config check: `~/.venvs/frc-autocam/bin/python -m autocam_service config-check`
