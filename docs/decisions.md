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
