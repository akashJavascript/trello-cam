# Fusion API call status

Every Fusion API call the pipeline relies on, and whether it has been seen working in Fusion.
Status values: **untested** (written from docs/forums only), **works**, **fails** (with what happened),
**not used** (fallback chosen). Nothing here has been run yet; M1.0 fills in the first results.

| Area | Call / parameter | Used by | Status | Fallback |
|---|---|---|---|---|
| Import | `importManager.createSTEPImportOptions` + `importToTarget` | fx_import | untested | none |
| Geometry | `measureManager.getOrientedBoundingBox`, `BRepBody.pointContainment` | fx_geometry | untested | none |
| Arrange | `arrangeFeatures.createInput(Arrange2DTrueShapeSolverType)`, `setPlaneEnvelope` | fx_arrange | untested | none |
| Arrange | `ArrangeComponent.quantity`, `isCreateCopies`, `isPartialArrangeAllowed` | fx_arrange | untested | one envelope per Arrange |
| Arrange | `frameWidth` | — | not used | envelope = exact nest region |
| Arrange | `resultEnvelopes` / `ArrangePlaneResultEnvelope.boundingBox` | fx_arrange | untested | one envelope per Arrange |
| Setup | `Setup.stockSolids`, `SetupStockModes.SolidStock` | fx_setup | untested | fixed-box stock params (names from dump) |
| Setup | clamp bodies as setup fixtures | fx_setup | untested | skip; guard's clamp-zone check |
| Setup | `wcs_origin_boxPoint = 'bottom 1'` (bottom front-left) | fx_setup | untested | value from dump + preview check |
| Template | `CAMTemplate.createFromFile`, `createFromCAMTemplate2` | fx_template | untested | none |
| Template | `CAMTemplate.createFromOperations` (per-part `[outer]` copies) | fx_template | untested | re-apply template, delete extras |
| Template | `Tool.toJson` → `guid` | fx_template | untested | match against pinned library |
| Ops | `Operation.hasError` / `error` / `hasWarning` / `warning` | pipeline | untested | `hasToolpath` only |
| Ops | `Operation.isSuppressed` / `deleteMe` (empty ops) | fx_template | untested | `deleteMe` |
| Selections | `adsk.cam.LoopTypes` enum names | fx_selections | untested | names from probe |
| Selections | `CadContours2dParameterValue`, chain/face contour selections | fx_selections | untested | none |
| Selections | drill `holeMode = 'selection-faces'`, `holeFaces` | fx_selections | untested | names from dump |
| Selections | bore face-selection parameter (candidates in config) | fx_selections | untested | Drill op, bore-milling cycle |
| Selections | pocket floor selection (`pockets`) | fx_selections | untested | names from dump |
| Pauses | Manual NC creation (Stop, Pass Through) | fx_manualnc | untested | `.tap` text insertion (default) |
| Toolpaths | `generateAllToolpaths`, `isGenerationCompleted` | pipeline | untested | none |
| Post | `PostProcessInput` with the pinned `.cps` path | fx_post | untested | library post + sha256 check |
| Post | post library lookup by description | fx_post | untested | fail loudly |
| Post | NC program params, `postParameters`, `updatePostParameters`, `postProcess` | fx_post | untested | none |
| Post | `getMachiningTime` | fx_post | untested | omit from card |
| Save | `Document.saveAs` to Fusion Team folder, wait for upload | fx_save | untested | local `.f3d` attachment |
| Save | `DataFile.fusionWebURL` | fx_save | untested | project/folder/file name on card |
| Save | `createFusionArchiveExportOptions` (local `.f3d`) | fx_save | untested | none |
| Preview | `Viewport.saveAsImageFile` per sheet | fx_preview | untested | one image for all sheets |
| Add-in | custom event fired from a background thread | autocam_addin | untested | none |
