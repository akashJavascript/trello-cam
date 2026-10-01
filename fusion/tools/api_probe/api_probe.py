"""api_probe: which Fusion API classes, attributes and enum values exist (M1.0).

UNTESTED IN FUSION: see docs/manual-tests.md (M1.0).
Read-only: it only inspects the adsk modules (dir/hasattr). It opens, creates and changes nothing.
Writes out/api_probe_<time>.json next to this script and tells you where.

Each entry answers a question the pipeline depends on (see docs/fusion-api-status.md), e.g.
"does Setup have stockSolids?", "can the API create Manual NC entries?", "is there a way to
copy an operation?", "what are the LoopTypes names?".
"""

import json
import os
import platform
import sys
import time
import traceback

import adsk.cam
import adsk.core
import adsk.fusion

HERE = os.path.dirname(os.path.abspath(__file__))

# (module, class, attributes the pipeline wants to use)
CLASS_CHECKS = [
    ("cam", "Setup", ["stockSolids", "stockMode", "fixtures", "fixtureEnabled", "models", "allOperations",
                      "createFromCAMTemplate", "createFromCAMTemplate2", "operations", "parameters"]),
    ("cam", "Operation", ["isSuppressed", "hasError", "error", "hasWarning", "warning", "hasToolpath", "deleteMe",
                          "duplicate", "copy", "tool", "strategy", "parameters"]),
    ("cam", "OperationBase", ["isSuppressed", "hasError", "hasWarning", "deleteMe", "name", "parameters"]),
    ("cam", "CAM", ["setups", "ncPrograms", "generateAllToolpaths", "generateToolpath", "getMachiningTime",
                    "postProcess", "postProcessAll", "personalPostFolder", "genericPostFolder", "exportManager"]),
    ("cam", "CAMTemplate", ["createFromFile", "createFromOperations", "createFromXML", "save", "name"]),
    ("cam", "CreateFromCAMTemplateInput", ["create", "camTemplate", "mode"]),
    ("cam", "Tool", ["toJson", "createFromJson", "parameters", "description"]),
    ("cam", "NCProgram", ["postConfiguration", "postParameters", "updatePostParameters", "postProcess", "operations",
                          "parameters"]),
    ("cam", "NCPrograms", ["createInput", "add"]),
    ("cam", "NCProgramInput", ["displayName", "operations", "parameters"]),
    ("cam", "NCProgramPostProcessOptions", ["create"]),
    ("cam", "PostProcessInput", ["create", "isOpenInEditor", "areToolChangesMinimized", "programComment"]),
    ("cam", "PostConfiguration", ["description", "extension", "url"]),
    ("cam", "CadContours2dParameterValue", ["getCurveSelections", "applyCurveSelections"]),
    ("cam", "CurveSelections", ["clear", "createNewFaceContourSelection", "createNewChainSelection",
                                "createNewPocketSelection", "createNewSilhouetteSelection"]),
    ("cam", "FaceContourSelection", ["loopType", "isSelectingSamePlaneFaces", "inputGeometry"]),
    ("cam", "ChainSelection", ["inputGeometry", "isOpen", "isReverted"]),
    ("cam", "CadObjectParameterValue", ["value"]),
    ("cam", "SetupInput", ["models", "stockMode", "fixtures"]),
    ("fusion", "ArrangeFeatures", ["createInput", "add"]),
    ("fusion", "ArrangeFeatureInput", ["definition", "arrangeComponents", "setPlaneEnvelope",
                                       "setSketchEnvelope", "setFaceEnvelope", "envelopes"]),
    ("fusion", "ArrangeFeature", ["resultEnvelopes", "envelopes", "arrangeComponents", "timelineObject"]),
    ("fusion", "ArrangeDefinition", ["globalRotation", "isGlobalDirectionFaceUp", "isPartInPartAllowed",
                                     "isCreateCopies", "solverType"]),
    ("fusion", "Arrange2DDefinition", ["globalRotation", "isGlobalDirectionFaceUp", "isPartInPartAllowed",
                                       "isCreateCopies", "frameWidth", "isPartialArrangeAllowed"]),
    ("fusion", "ArrangePlaneEnvelope", ["originXOffset", "originYOffset", "quantity", "objectSpacing",
                                        "envelopeSpacing", "frameWidth", "isPartialArrangeAllowed"]),
    ("fusion", "ArrangePlaneResultEnvelope", ["boundingBox", "arrangedComponents", "occurrences"]),
    ("fusion", "ArrangeComponent", ["quantity", "priority", "rotation"]),
    ("fusion", "ArrangeComponents", ["add", "count", "item"]),
    ("core", "Document", ["saveAs", "save", "close", "dataFile"]),
    ("core", "DataFile", ["fusionWebURL", "publicLink", "isComplete", "id", "versionId"]),
    ("core", "ImportManager", ["createSTEPImportOptions", "importToTarget", "importToTarget2"]),
    ("core", "Viewport", ["saveAsImageFile", "saveAsImageFileWithOptions", "camera"]),
    ("fusion", "ExportManager", ["createFusionArchiveExportOptions", "execute"]),
    ("fusion", "MeasureManager", ["getOrientedBoundingBox"]),
    ("fusion", "BRepBody", ["pointContainment", "faces", "edges", "boundingBox", "orientedMinimumBoundingBox"]),
    ("fusion", "BRepFace", ["loops", "evaluator", "geometry", "pointOnFace", "area"]),
    ("fusion", "BRepLoop", ["isOuter", "coEdges", "edges"]),
]

ENUM_CHECKS = [
    ("cam", "LoopTypes"), ("cam", "SetupStockModes"), ("cam", "OperationTypes"), ("cam", "LibraryLocations"),
    ("cam", "PostOutputUnitOptions"), ("cam", "OperationStrategyTypes"),
    ("fusion", "ArrangeSolverTypes"), ("fusion", "ArrangeRotationTypes"), ("fusion", "PointContainment"),
    ("core", "SurfaceTypes"), ("core", "DocumentTypes"),
]

# Any class whose name contains one of these words (answers "is there an API for X at all?").
SEARCH_WORDS = ["ManualNC", "Manual", "Arrange", "Template", "Fixture", "Stock", "PostProcess", "NCProgram",
                "Duplicate", "Copy"]

MODULES = {"cam": adsk.cam, "core": adsk.core, "fusion": adsk.fusion}


def public(names):
    return sorted(n for n in names if not n.startswith("_") and n not in ("this", "thisown"))


def probe():
    app = adsk.core.Application.get()
    out = {
        "script": "api_probe",
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "fusion_version": app.version,
        "python_version": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "classes": {},
        "enums": {},
        "search": {},
    }
    for mod, cls_name, attrs in CLASS_CHECKS:
        cls = getattr(MODULES[mod], cls_name, None)
        key = f"adsk.{mod}.{cls_name}"
        if cls is None:
            out["classes"][key] = {"exists": False}
            continue
        out["classes"][key] = {
            "exists": True,
            "wanted": {a: hasattr(cls, a) for a in attrs},
            "all_public": public(dir(cls)),
        }
    for mod, enum_name in ENUM_CHECKS:
        enum = getattr(MODULES[mod], enum_name, None)
        out["enums"][f"adsk.{mod}.{enum_name}"] = (
            {n: getattr(enum, n) for n in public(dir(enum)) if isinstance(getattr(enum, n), int)}
            if enum is not None else None)
    for word in SEARCH_WORDS:
        out["search"][word] = {
            mod: [n for n in public(dir(m)) if word.lower() in n.lower()] for mod, m in MODULES.items()}
    return out


def run(context):
    ui = adsk.core.Application.get().userInterface
    try:
        data = probe()
        folder = os.path.join(HERE, "out")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"api_probe_{time.strftime('%Y%m%d-%H%M%S')}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        missing = [k for k, v in data["classes"].items() if not v.get("exists")]
        ui.messageBox(f"Fusion {data['fusion_version']}, Python {sys.version.split()[0]}\n"
                      f"{len(data['classes']) - len(missing)} of {len(data['classes'])} classes found.\n"
                      f"Wrote:\n{path}", "api_probe")
    except Exception:  # noqa: BLE001
        ui.messageBox("api_probe failed:\n" + traceback.format_exc(), "api_probe")
