# Program fixtures

`sheet_mist.tap`: hand-written in the exact shape the shop's post writes (comments in `[...]`, `G53 Z`
retracts, `G81` drilling, `G4 X4.` dwell, `M11 C8`/`M12 C8` mist, `G53 P10` park). It's a 1/8 in aluminum
sheet with three per-part outlines (`[outer p01-1]`, `[outer p02-1]`, `[outer p02-2]`) and no pauses yet.
It stays because it covers drilling, which the real programs below don't have yet.

Real output of the pinned post, from `pipeline_probe2` (2026-10-01, Fusion 2705.1.15). The probe printed each
program as a list of lines; the files were rebuilt from that list with CRLF line endings:
- `fusion_one_outline.tap`: one 2D Contour on a 0.125 in plate, bottom height "Stock bottom" (cuts to Z0).
- `fusion_two_outlines_shallow.tap`: two outline copies renamed `[outer] p01-1` / `[outer] p02-1`, bottom
  height "From contour" with the top face selected, so they stop at Z 0.125 (the plate top).
- `fusion_stop_passthrough.tap`: the same two outlines cut to Z0, with a Manual NC Stop between them and a
  Manual NC pass-through (`PT_MESSAGE`) moved to the front.
