# Fusion API call status

Every Fusion API call the pipeline relies on, and whether it has been seen working in Fusion.
Status values: **untested** (written from docs/forums only), **works**, **fails** (with what happened),
**not used** (fallback chosen), **name confirmed** (the parameter exists with this name in a real job; setting it
from the API is still untested).

Results so far (2026-10-01, Fusion 2705.1.15, Python 3.14.0, fresh install):
- `api_probe` found 35 of 37 classes. Which 2 are missing is still to be read from its JSON.
- `dump_params` ran on a manual job: 1 setup, 4 ops (Drill, Bore, two 2D Contours).
- Phase 1 smoke test: STEP import and plate checks worked; it stopped at Arrange (faces), so nothing after it ran.
- `pipeline_probe` ran every remaining call on scratch documents (results below).
- `pipeline_probe2` first run: answered move vs copy, quantity by extra occurrences, op renaming and op order.
  Its overflow and two-sheet tests didn't run (every part was "pinned"), and its bottom-height and Manual NC
  steps had a probe bug (choice values already carry their quotes).
- `pipeline_probe2` second run: settled bottom height, Manual NC and op reordering, and showed that "pinned"
  is `isGroundToParent`, not `isGrounded`.
- `pipeline_probe2` version 3: settled Arrange (clearing ground-to-parent, STEP import, overflow, a second sheet,
  which way up). Every call the through-cut pipeline needs has now been seen working.
- The three programs it posted are test fixtures now (`tests/fixtures/taps/fusion_*.tap`): the guard, the depth
  check and pause insertion all pass on real post output.

| Area | Call / parameter | Used by | Status | Fallback |
|---|---|---|---|---|
| Import | `importManager.createSTEPImportOptions` + `importToTarget` | fx_import | **works** (Phase 1 smoke test) | none |
| Geometry | `measureManager.getOrientedBoundingBox`, `BRepBody.pointContainment` | fx_geometry | **works** (Phase 1 plate checks passed) | none |
| Arrange | `arrangeFeatures.createInput(Arrange2DTrueShapeSolverType)`, `arrangeComponents.add(occurrence)`, `setPlaneEnvelope`, `originXOffset`, `objectSpacing`, `arrangeFeatures.add` | fx_arrange | **works** with occurrences, with or without the definition settings | none |
| Arrange | `arrangeComponents.add(face)` | — | **fails** every way tried (before/after the envelope, with/without settings): `RuntimeError: 2 : InternalValidationError : arrange2DDefinition`, then `ARRANGE_2D_NO_SHAPES` | add occurrences |
| Arrange | `definition.globalRotation`, `isGlobalDirectionFaceUp`, `isPartInPartAllowed`, `isCreateCopies` | fx_arrange | **works** (all settable) | none |
| Arrange | `ArrangeComponent.quantity`, `envelope.quantity`, `envelope.envelopeSpacing` | — | **fails**: `RuntimeError: 3 : Cannot set ... for non-extension environment` (needs the Manufacturing Extension) | one occurrence per copy; one Arrange per sheet |
| Arrange | quantity by extra occurrences (`occurrences.addExistingComponent`) | fx_arrange | **works**: 2 occurrences of one component were both placed, no overlap | none |
| Arrange | `isCreateCopies = True`, then `deleteMe` the originals | fx_arrange (fallback) | **works**: new occurrences (`plate_a:2`) are placed, the originals stay put; deleting the originals leaves the copies in place and the feature healthy | none |
| Arrange | `isCreateCopies = False` (move the occurrences) | fx_arrange | **works once `isGroundToParent` is cleared**. The first component in a document (created, or the first STEP imported) and every occurrence of it come with `isGroundToParent = True` (`isGrounded` stays False), and Arrange refuses them: `Pinned component cannot be arranged`. Setting `occurrence.isGroundToParent = False` fixes it | copies + delete originals |
| Import | one STEP file per part, `importToTarget(root)` | fx_import | **works**: each file makes exactly one occurrence (`step_a:1`), at its modeled position | none |
| Arrange | envelope `originXOffset`, `originYOffset`, `objectSpacing` | fx_arrange | **works**: parts packed from the envelope corner (10, 2) with 0.25 in gaps | none |
| Arrange | which way up parts land | fx_arrange | Arrange turns the part so its `ArrangeComponent.upDirection` points up. For plates modeled flat that's **(0, 0, -1)**, so they land upside down whatever `isGlobalDirectionFaceUp` says. `upDirection` is read-only (`no setter`). **`isDirectionFlipped = True` keeps the modeled top up** (pocket still opens up); face-up False changes nothing | re-check the pocket side after Arrange (plan) |
| Arrange | side effects | fx_arrange | Arrange adds two empty components, `Arrange1` and `Envelope1(Qty: 1)`. The worker must keep its own list of part occurrences | none |
| Arrange | `ArrangeResultEnvelope.occurrences` → `ArrangeOccurrenceResult.occurrence` | fx_arrange | **works**: names what landed in the envelope | position check (`layout`) |
| Arrange | `ArrangeFeature.arrangeStatistics` | fx_arrange | **works**: a JSON string with "Components Arranged", "Components Unarranged", "Envelopes Used", areas | none |
| Arrange | `ArrangeFeature.unusedComponents` | — | **fails** after `add`: `Didn't roll editing feature back`; after `timelineObject.rollTo(True)`: `Cannot set envelope direction for non-extension environment` | our list minus `resultEnvelopes` occurrences (works) |
| Arrange | parts that don't fit: envelope `isPartialArrangeAllowed` | fx_arrange | **default False: the whole Arrange fails** (`ARRANGE_ERROR_NO_ROOM`) and nothing moves. **True: places what fits** (2 of 6), leaves the rest where they were; statistics say "Components Unarranged: 4" | none |
| Arrange | a second Arrange for the leftovers (sheet 2, envelope further along X) | fx_arrange | **works**: places them; sheet 1's parts don't move; each Arrange adds its own empty `ArrangeN` / `Envelope1(Qty: 1) (n)` components | none |
| Arrange | `resultEnvelopes` / `.boundingBox` | — | **not used**: reported `[0, 0, 20, 12]` in although the parts landed at x 10-12 (the offset isn't in it) | sheet origins come from our own envelope offsets |
| Arrange | other options seen (`ArrangeComponent.priority`, `rotation`, `upDirection`, `isFiller`; definition `grainDirection`) | — | exist, unused | none |
| Arrange | `frameWidth` | — | not used | envelope = exact nest region |
| Setup | `setups.createInput(MillingOperation)`, `models = [body]` (a list), `setups.add` | fx_setup | **works** | none |
| Setup | `Setup.stockMode = SolidStock`, `Setup.stockSolids` | fx_setup | **works**, but `stockSolids` takes an `ObjectCollection` (a list raises `TypeError`); `job_stockMode` reads back `'solid'` | none |
| Setup | clamp bodies as setup fixtures | fx_setup | untested | skip; guard's clamp-zone check |
| Setup | `wcs_origin_mode = 'stockPoint'`, `wcs_origin_boxPoint = 'bottom 1'` | fx_setup | **works** (set by expression). Z0 is the stock bottom: the posted Z values match a 0.125 in plate sitting on Z0 | XY corner: check in the nest preview (M1.3) |
| Template | `CAMTemplate.createFromFile`, `CreateFromCAMTemplateInput` + `setup.createFromCAMTemplate2` | fx_template | **works** | none |
| Template | `CAMTemplate.createFromOperations([op])` then apply (per-part `[outer]` copies) | fx_template | **works**: adds one op at the end of the setup | re-apply template, delete extras |
| Ops | `Operation.name = '[outer] p01-1'` | fx_template | **works**: posts as `[outer p01-1]`, exactly `names.outer_comment_line` | none |
| Ops | order | fx_template | new ops (template copies, Manual NC) are **appended** in creation order; `Operation.moveBefore(op)` **works** | create in cut order |
| Template | `Tool.toJson` → `guid` | fx_template | **works** (`e5dd75b2-…`, the 4 mm alu tool in config, from the smoke template) | not needed |
| Ops | `hasToolpath` / `hasError` / `hasWarning` / `isSuppressed` | pipeline | **works** (all readable; `isSuppressed` settable) | none |
| Ops | `Operation.deleteMe` | fx_template | **works** | `isSuppressed` |
| Ops | `bottomHeight_mode = 'from stock bottom'`, `bottomHeight_offset = '0 in'` | fx_template | **works** from the API: the outline then cuts to Z0. (`getChoices` returns values already quoted.) The team's manual contours use `'from contour'`, which follows the selected edges: with the automation's top-face selection the cut stops at the plate top (Z 0.125) | `sheetcheck` rejects any outline that doesn't reach Z0 |
| Ops | heights in the smoke template | — | clearance = retract + 0.4 in = Z0.725 on a 0.125 in plate (0.6 in above the stock). The README asks for 2.0 in above the stock top (clamps); the guard's clamp check rejects low rapids over the clamp zones | none |
| Selections | `adsk.cam.LoopTypes.OnlyInsideLoops` / `OnlyOutsideLoops` | fx_selections | **works** | none |
| Selections | 2D Contour `contours` → `CadContours2dParameterValue`, `createNewFaceContourSelection`, `inputGeometry = [face]`, `applyCurveSelections` | fx_selections | **works** (toolpaths generated) | none |
| Selections | drill `holeMode = 'selection-faces'`, `holeFaces` | fx_selections | name confirmed | none |
| Selections | bore `holeMode = 'selection-faces'`, `circularFaces` | fx_selections | name confirmed | Drill op, bore-milling cycle |
| Selections | pocket floor selection (the team uses 2D Adaptive) | fx_selections | pending: dump of a 2D Adaptive job | names from dump |
| Pauses | Manual NC: `setup.operations.createInput('manual')` + `add` | fx_manualnc | **works** (creates "Manual NC1"; `'manual_nc'` and `'manualnc'` are unknown strategies). Parameter `manualType`, values `'comment'`, `'stop'`, `'optional-stop'`, `'dwell'`, ... plus `comment`, `action`, `message`, `dwell`. A Manual NC left as an empty comment posts nothing. **Stop** (`'stop'`) posts a bare `M0` after the outline's last move, then the post restarts the spindle (`S18000`, `M3`, `G4 X4.`) at the next op: no retract, spindle stop, mist off or park first, and the guard rejects it ("M0 stop with the spindle running"). **Pass-through** (`'pass-through'`) writes its `message` parameter verbatim as its own line (moved to the front, it landed before the first `G53 Z`) | `.tap` text insertion (default, confirmed as the right choice) |
| Toolpaths | `generateAllToolpaths(False)`, `GenerateToolpathFuture.isGenerationCompleted` | pipeline | **works** (0.8 s for the probe plate) | none |
| Post | `PostProcessInput.create(name, <pinned .cps>, folder, InchesOutput)` + `CAM.postProcess(setup, input)` | fx_post | **works** (the preferred method: the pinned file, no library lookup) | library post + sha256 check |
| Post | post library lookup by description | fx_post | **works** in the local library (`user://shopsabre_automatic_mist.cps`); the cloud location raises `RuntimeError: 3 : Given URL does not point to an existing folder` on this install | not needed with post by path |
| Post | NC program params, `postParameters`, `updatePostParameters`, `NCProgram.postProcess` | fx_post | **works** (lists the post's properties: `useMist`, `safePositionMethod`, `useXYZFeeds`, `writeMachine`, ...) | none |
| Post | `getMachiningTime(setup, 100, feed, 0).machiningTime` | fx_post | **works** (seconds) | omit from card |
| Save | `Document.saveAs` to Fusion Team folder, wait for upload | fx_save | untested | local `.f3d` attachment |
| Save | `DataFile.fusionWebURL` | fx_save | untested | project/folder/file name on card |
| Save | `createFusionArchiveExportOptions` (local `.f3d`) | fx_save | **works** | none |
| Preview | `Viewport.saveAsImageFile` per sheet (top view, fit) | fx_preview | **works** | one image for all sheets |
| Add-in | custom event fired from a background thread | autocam_addin | untested | none |

## New in the M1.2 pipeline (`fusion/autocam_addin/autocam_worker/fx_*.py`), not seen working yet

Each run lists the ones it used in `result.json` (`worker.untested_steps`) and the `autocam_run` message box.

**First `autocam_run` (2026-10-01, run t174019): status ok.**
- **Job:** one 1/8 in aluminum plate with bore-size holes; 1 sheet; the program passed the guard and the sheet check.
- **Ran without errors, first time in Fusion:**
  - the Arrange flip from `upDirection`;
  - `Setup.name`;
  - bore `holeMode` + `circularFaces`;
  - `PostProcessInput.postProperties`;
  - the preview camera.
- **The posted program:**
  - `M11 C8` mist on;
  - only `[bore holes]` and `[outer p01-1]`: unused template ops were dropped and the outline copy was renamed;
  - clearance `Z2.225`;
  - `G53 Z` only at the start and the end.
- **Simulation (checked by the user):** looks right: the full-sheet stock, the origin corner, the part cut
  through, and the bored holes.
- **Multi-part run (t174221): status ok, "looks fine" in the simulation.**
  - **Job:** 1 sheet, `p02` x4 and `p01` x1, bore-size holes only.
  - **Setup:** several part bodies as setup models worked.
  - **Cut order:** `p02-1, p02-2, p02-3, p01-1, p02-4`.
  - **Pauses:** 4/4, each verified in position and content.
- **1/4 in C-shaped plates with drilled holes, bored holes, bearing holes and cutouts (runs t181442 to the
  run after t204258).** Each run turned up one problem, all now fixed:
  - toolpaths had to be waited for and retried;
  - the guard's arc bounds were too loose for big arcs;
  - single-loop chains need `isReverted = not isOpposedToEdge`.
  Drill `holeFaces` and the chain selections now work (simulated by the user).
- **Not exercised yet:**
  - whole-face inner selection (a face whose every inner loop is a cutout);
  - more than one sheet;
  - a part needing the 1/8 in tool.

| Area | Call | Used by | Fallback |
|---|---|---|---|
| Design | `Design.findEntityByToken(occurrence.entityToken)` | fx_adapter | keep the object references |
| Design | `addExistingComponent(component, occ.transform2)` for copies | fx_design | identity transform (probes used it) |
| Arrange | `isDirectionFlipped` chosen from `upDirection` vs the face that must face up (probes showed each half) | fx_design | none |
| Setup | `Setup.name = program name`; several part bodies as `models` | fx_cam | default names |
| Selections | drill: `holeMode` + `holeFaces` (`CadObjectParameterValue.value = [faces]`) | fx_cam | none yet |
| Selections | bore/bearing: `holeMode` + `circularFaces` | fx_cam | Drill op, bore-milling cycle |
| Selections | inner: several faces in one contour selection; single loops via `createNewChainSelection` with every edge of the loop (`loop.coEdges`). The cut side follows the chain direction, and `ChainSelection` has no `sideType` in Fusion 2705.1.15:
  - Run t184808, no direction set: round loops were cut outside, triangles inside.
  - Run t204258, `isReverted = isOpposedToEdge`: every loop was cut outside. The safety net rejected the sheet: **pointContainment on the cutting points works**.
  - `isReverted = not isOpposedToEdge` **works**: the next run had every single loop cut inside (the user
    simulated it), and the sheet passed the part-body check.
  - The generate retry was used in t204258. | fx_cam | none |
| Arrange | `ArrangeComponent.deleteMe()` to leave out a part that would lie on its side; matching placed parts by occurrence name | fx_design | reject the part |
| Design | `Occurrence.isLightBulbOn = False` for copies taken out of the job (not deleted: deleting an Arrange input could make Fusion solve it again) | fx_adapter | none |
| Post | `PostProcessInput.postProperties` (`NamedValues` of every property, `useMist` from the material) | fx_cam | NC program + `postParameters` (probe: works) |
| Preview | `Viewport.camera` framed on one sheet (target, eye, `viewExtents`) | fx_design | fit view |
| Toolpaths | `GenerateToolpathFuture.isGenerationCompleted` isn't enough. Run t181442 (a 1/4 in plate, 29 cutouts) read `[outer] p01-1` as "no toolpath, no error"; it generated fine by hand. Now: wait until no op `isGenerating`, then `CAM.generateToolpath` once more for ops with neither a toolpath nor an error | fx_cam | none |
| Add-in | `registerCustomEvent` + `fireCustomEvent` from a background thread; the handler runs the job on the main thread | autocam_addin | none |
| Add-in | the add-in's handler re-entered by `adsk.doEvents()` during a job (guarded by `busy` and a per-process flag) | autocam_addin | none |

## What this changes in the pipeline (M1.2)

- **Import:** one STEP per part (one occurrence each), then `qty - 1` more occurrences of its component with
  `addExistingComponent`. Keep our own list of part occurrences: Arrange adds empty components of its own.
- **Before arranging:** clear `isGroundToParent` (and `isGrounded`) on every part occurrence.
- **One Arrange per sheet**, moving (not copying), envelope = the sheet's nest region at our own offset,
  `isPartialArrangeAllowed = True`. What landed = the result envelope's occurrences; the rest go to the next
  sheet's Arrange. Sheet origins come from our offsets, not `resultEnvelopes`.
- **Which way up:** for each part, compare `upDirection` with the side that must face up (the open side of any
  pocket) and set `isDirectionFlipped` when they're opposite. Through-cut plates land either way. Afterwards,
  check the pocket side really faces +Z (plan).
- **Per-part outline ops** are `createFromOperations` copies renamed `[outer] <instance>`, in cut order.
- **The worker sets the bottom height of `[outer]` and `[inner]` ops** to stock bottom, offset 0, because its
  own top-face selection needs it; the sheet check still rejects any `[outer]` op whose lowest Z isn't the floor.
- **Pauses stay text-inserted.** A Manual NC Stop is a bare M0 with the spindle running.

## Tabs (M6), names confirmed

`group_tabs`, `tabShape`, `tabWidth`, `tabHeight`, `tabPositioning` ('distance' | 'tabCount'), `tabDistance`,
`tabPositions` (manual / at-points, a `CadObjectParameterValue`), `noTabZones`.
