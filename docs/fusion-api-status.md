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
  steps had a probe bug (choice values already carry their quotes). The second version re-tests those.

| Area | Call / parameter | Used by | Status | Fallback |
|---|---|---|---|---|
| Import | `importManager.createSTEPImportOptions` + `importToTarget` | fx_import | **works** (Phase 1 smoke test) | none |
| Geometry | `measureManager.getOrientedBoundingBox`, `BRepBody.pointContainment` | fx_geometry | **works** (Phase 1 plate checks passed) | none |
| Arrange | `arrangeFeatures.createInput(Arrange2DTrueShapeSolverType)`, `arrangeComponents.add(occurrence)`, `setPlaneEnvelope`, `originXOffset`, `objectSpacing`, `arrangeFeatures.add` | fx_arrange | **works** with occurrences, with or without the definition settings | none |
| Arrange | `arrangeComponents.add(face)` | — | **fails** every way tried (before/after the envelope, with/without settings): `RuntimeError: 2 : InternalValidationError : arrange2DDefinition`, then `ARRANGE_2D_NO_SHAPES` | add occurrences |
| Arrange | `definition.globalRotation`, `isGlobalDirectionFaceUp`, `isPartInPartAllowed`, `isCreateCopies` | fx_arrange | **works** (all settable) | none |
| Arrange | `ArrangeComponent.quantity`, `envelope.quantity`, `envelope.envelopeSpacing` | — | **fails**: `RuntimeError: 3 : Cannot set ... for non-extension environment` (needs the Manufacturing Extension) | one occurrence per copy; one Arrange per sheet |
| Arrange | quantity by extra occurrences (`occurrences.addExistingComponent`) | fx_arrange | **works**: 2 occurrences of one component were both placed, no overlap | none |
| Arrange | `isCreateCopies = True` | — | **works but not used**: places new occurrences (`plate_a:2`) and leaves the originals where they were | delete the originals (`pipeline_probe2` A12) |
| Arrange | `isCreateCopies = False` (move the occurrences) | fx_arrange | **works** for most occurrences, but every occurrence of the **first component created** fails: `RuntimeError: 3 : Pinned component cannot be arranged`. Probably grounded; `pipeline_probe2` A11 tries `isGrounded = False` | copies + delete originals |
| Arrange | envelope `originXOffset`, `originYOffset`, `objectSpacing` | fx_arrange | **works**: parts packed from the envelope corner (10, 2) with 0.25 in gaps | none |
| Arrange | which way up parts land | fx_arrange | **parts get turned upside down** (a 180° turn about a horizontal axis) even with `isGlobalDirectionFaceUp = True`. Harmless for through-cut plates; wrong for pocketed ones. `pipeline_probe2` A15 tests face-up off and `ArrangeComponent.isDirectionFlipped` | re-check the pocket side after Arrange (plan) |
| Arrange | side effects | fx_arrange | Arrange adds two empty components, `Arrange1` and `Envelope1(Qty: 1)`. The worker must keep its own list of part occurrences | none |
| Arrange | parts that don't fit: `isPartialArrangeAllowed` (on the **envelope** input), `ArrangeFeature.unusedComponents`, `ArrangeResultEnvelope.occurrences` | fx_arrange | untested (all exist; `pipeline_probe2` A13/A14) | one envelope per Arrange, leftovers carried forward |
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
| Ops | order | fx_template | new ops (template copies, Manual NC) are **appended** in creation order. `moveBefore` / `moveAfter` / `duplicate` exist (`moveBefore` is `pipeline_probe2` B6) | create in cut order |
| Template | `Tool.toJson` → `guid` | fx_template | **works** (`e5dd75b2-…`, the 4 mm alu tool in config, from the smoke template) | not needed |
| Ops | `hasToolpath` / `hasError` / `hasWarning` / `isSuppressed` | pipeline | **works** (all readable; `isSuppressed` settable) | none |
| Ops | `Operation.deleteMe` | fx_template | **works** | `isSuppressed` |
| Ops | `bottomHeight_mode`, `bottomHeight_offset` | fx_template | name and values confirmed: the choice for stock bottom is `'from stock bottom'` (`getChoices` returns values already quoted). The team's manual contours use `'from contour'`, which follows the selected edges: with the automation's top-face selection the cut stops at the plate top (probe program min Z 0.125). Setting it from the API is `pipeline_probe2` B5 | templates use stock bottom (README); `sheetcheck` rejects any outline that doesn't reach Z0 |
| Ops | heights in the smoke template | — | clearance = retract + 0.4 in = Z0.725 on a 0.125 in plate (0.6 in above the stock). The README asks for 2.0 in above the stock top (clamps); the guard's clamp check rejects low rapids over the clamp zones | none |
| Selections | `adsk.cam.LoopTypes.OnlyInsideLoops` / `OnlyOutsideLoops` | fx_selections | **works** | none |
| Selections | 2D Contour `contours` → `CadContours2dParameterValue`, `createNewFaceContourSelection`, `inputGeometry = [face]`, `applyCurveSelections` | fx_selections | **works** (toolpaths generated) | none |
| Selections | drill `holeMode = 'selection-faces'`, `holeFaces` | fx_selections | name confirmed | none |
| Selections | bore `holeMode = 'selection-faces'`, `circularFaces` | fx_selections | name confirmed | Drill op, bore-milling cycle |
| Selections | pocket floor selection (the team uses 2D Adaptive) | fx_selections | pending: dump of a 2D Adaptive job | names from dump |
| Pauses | Manual NC: `setup.operations.createInput('manual')` + `add` | fx_manualnc | **works** (creates "Manual NC1"; `'manual_nc'` and `'manualnc'` are unknown strategies). Parameter `manualType`, values `'comment'`, `'stop'`, `'optional-stop'`, `'dwell'`, ... plus `comment`, `action`, `message`, `dwell`. A Manual NC left as an empty comment posts nothing. Making it a Stop and a pass-through: `pipeline_probe2` B6 | `.tap` text insertion (default) |
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

## What this changes in the pipeline (M1.2)

- **Arrange by occurrence, one copy per occurrence.** Quantity isn't available, so the worker adds
  `qty - 1` occurrences of each part's component and arranges all of them (confirmed).
- **Unground before arranging** (if A11 confirms grounding is what "pinned" means), and keep our own list of
  part occurrences: Arrange adds empty components of its own.
- **Arrange turns parts upside down.** Fine for through-cut plates; pocketed parts need the pocket side
  re-checked after Arrange (and a fix from A15).
- **One Arrange per sheet.** Multiple envelopes aren't available either. Each sheet is its own Arrange with its
  envelope offset along X, and the parts that don't fit go to the next sheet's Arrange (depends on A9/A10).
- **Sheet origins come from our own offsets**, not from `resultEnvelopes`.
- **Per-part outline ops** come from `createFromOperations` copies, renamed to `[outer] <instance>`.
- **Through cuts are checked twice.** The template rule says stock bottom, offset 0, and the sheet check now
  rejects any `[outer]` op whose lowest Z isn't the floor.

## Tabs (M6), names confirmed

`group_tabs`, `tabShape`, `tabWidth`, `tabHeight`, `tabPositioning` ('distance' | 'tabCount'), `tabDistance`,
`tabPositions` (manual / at-points, a `CadObjectParameterValue`), `noTabZones`.
