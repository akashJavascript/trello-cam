# Fusion API call status

Every Fusion API call the pipeline relies on, and whether it has been seen working in Fusion.
Status values: **untested** (written from docs/forums only), **works**, **fails** (with what happened),
**not used** (fallback chosen), **name confirmed** (the parameter exists with this name in a real job; setting it
from the API is still untested).

First results (2026-10-01, Fusion 2705.1.15, Python 3.14.0, fresh install):
- `api_probe` found 35 of 37 classes. Which 2 are missing is still to be read from its JSON.
- `dump_params` ran on a manual job: 1 setup, 4 ops (Drill, Bore, two 2D Contours).
- Phase 1 smoke test: STEP import and plate checks worked; it stopped at Arrange (below), so nothing after it ran.
  `fusion/tools/pipeline_probe` tests the remaining calls one step at a time.

| Area | Call / parameter | Used by | Status | Fallback |
|---|---|---|---|---|
| Import | `importManager.createSTEPImportOptions` + `importToTarget` | fx_import | **works** (Phase 1 smoke test) | none |
| Geometry | `measureManager.getOrientedBoundingBox`, `BRepBody.pointContainment` | fx_geometry | **works** (Phase 1 plate checks passed) | none |
| Arrange | `arrangeFeatures.createInput(Arrange2DTrueShapeSolverType)`, `setPlaneEnvelope` | fx_arrange | **fails as Phase 1 calls it:** `arrangeComponents.add(face)` raised `RuntimeError: 2 : InternalValidationError : arrange2DDefinition` (after setting definition options, before the envelope). `pipeline_probe` tries variants | add occurrences instead of faces; envelope first; no definition options |
| Arrange | `ArrangeComponent.quantity`, `isCreateCopies`, `isPartialArrangeAllowed` | fx_arrange | untested | one envelope per Arrange |
| Arrange | `frameWidth` | — | not used | envelope = exact nest region |
| Arrange | `resultEnvelopes` / `ArrangePlaneResultEnvelope.boundingBox` | fx_arrange | untested | one envelope per Arrange |
| Setup | `Setup.stockSolids`, `SetupStockModes.SolidStock` | fx_setup | untested | params `job_stockMode` = 'solid' + `job_stockSolid` (names confirmed) |
| Setup | clamp bodies as setup fixtures | fx_setup | untested | skip; guard's clamp-zone check |
| Setup | `wcs_origin_mode = 'stockPoint'`, `wcs_origin_boxPoint = 'bottom 1'` | fx_setup | name confirmed (the team's manual value) | check the corner in the nest preview (M1.3) |
| Template | `CAMTemplate.createFromFile`, `createFromCAMTemplate2` | fx_template | untested | none |
| Template | `CAMTemplate.createFromOperations` (per-part `[outer]` copies) | fx_template | untested | re-apply template, delete extras |
| Template | `Tool.toJson` → `guid` | fx_template | **works** (returned `7b77ef53-…` for the 4 mm alu tool) | not needed |
| Ops | `Operation.hasError` / `error` / `hasWarning` / `warning` | pipeline | untested | `hasToolpath` only |
| Ops | `Operation.isSuppressed` / `deleteMe` (empty ops) | fx_template | untested | `deleteMe` |
| Selections | `adsk.cam.LoopTypes` enum names | fx_selections | untested | names from probe |
| Selections | 2D Contour selection param `contours` (`CadContours2dParameterValue`) | fx_selections | name confirmed; setting selections untested | none |
| Selections | drill `holeMode = 'selection-faces'`, `holeFaces` | fx_selections | name confirmed | none |
| Selections | bore `holeMode = 'selection-faces'`, `circularFaces` | fx_selections | name confirmed | Drill op, bore-milling cycle |
| Selections | pocket floor selection (the team uses 2D Adaptive) | fx_selections | pending: dump of a 2D Adaptive job | names from dump |
| Pauses | Manual NC creation (Stop, Pass Through) | fx_manualnc | untested | `.tap` text insertion (default) |
| Toolpaths | `generateAllToolpaths`, `isGenerationCompleted` | pipeline | untested | none |
| Post | `PostProcessInput` with the pinned `.cps` path | fx_post | untested | library post + sha256 check |
| Post | post library lookup by description | fx_post | partly seen: on a fresh install the local folder doesn't exist and `childAssetURLs` raises RuntimeError 3; handle it | fail loudly |
| Post | NC program params, `postParameters`, `updatePostParameters`, `postProcess` | fx_post | untested | none |
| Post | `getMachiningTime` | fx_post | untested | omit from card |
| Save | `Document.saveAs` to Fusion Team folder, wait for upload | fx_save | untested | local `.f3d` attachment |
| Save | `DataFile.fusionWebURL` | fx_save | untested | project/folder/file name on card |
| Save | `createFusionArchiveExportOptions` (local `.f3d`) | fx_save | untested | none |
| Preview | `Viewport.saveAsImageFile` per sheet | fx_preview | untested | one image for all sheets |
| Add-in | custom event fired from a background thread | autocam_addin | untested | none |

## Tabs (M6), names confirmed

`group_tabs`, `tabShape`, `tabWidth`, `tabHeight`, `tabPositioning` ('distance' | 'tabCount'), `tabDistance`,
`tabPositions` (manual / at-points, a `CadObjectParameterValue`), `noTabZones`.
