# frc-autocam

Trello cards with Onshape part links → nested, CAM'd sheets in Fusion → WinCNC `.tap` files back on Trello
for the team's ShopSabre 23. A human reviews every sheet before it is cut.

- `docs/BRIEF.md`: the settled decisions.
- `docs/PLAN.md`: architecture and milestones.
- `CLAUDE.md`: the hard rules.

## Development (WSL)

The repo lives on the Windows drive (`C:\dev\frc-autocam`, `/mnt/c/dev/frc-autocam` from WSL) so Fusion can
load `fusion/` directly. Keep the venv in the WSL home, not in the repo.

```bash
python3 -m venv ~/.venvs/frc-autocam
~/.venvs/frc-autocam/bin/pip install -e ".[dev]"
~/.venvs/frc-autocam/bin/pytest                                 # fully offline; network is blocked in tests
~/.venvs/frc-autocam/bin/python -m autocam_service config-check
```

Secrets go in `.env` (copy `.env.example`), which is gitignored.

## Layout

| Path | What |
|---|---|
| `core/autocam_core/` | pure logic, standard library only (also imported inside Fusion) |
| `service/autocam_service/` | the Windows service: Trello, Onshape, hot folder |
| `fusion/` | Fusion scripts and add-in, the pinned post, tool library, and templates |
| `config/autocam.toml` | all settings, in inches; `[assumed]` lists values still to confirm |
| `tests/` | pytest suite |
