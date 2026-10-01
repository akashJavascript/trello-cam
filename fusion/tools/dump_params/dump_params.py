"""dump_params: write every CAM parameter of the open document to JSON (M1.0).

UNTESTED IN FUSION: written from the Autodesk API docs; see docs/manual-tests.md (M1.0).
Read-only: it never changes the document. Standard library only.

Run it with a document open that has CAM setups (a finished manual job, or a test part with
one of our templates applied). It writes out/<document>_<time>.json next to this script and
tells you where. Every API call is wrapped, so one failure is recorded and the dump goes on.
"""

import json
import os
import platform
import sys
import time
import traceback

import adsk.cam
import adsk.core

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_CHOICES = 60


def attempt(fn, errors, what):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - record and keep going
        errors.append(f"{what}: {type(e).__name__}: {e}")
        return None


def plain(x):
    return x if isinstance(x, (bool, int, float, str)) or x is None else repr(x)[:300]


def param_info(p, errors):
    info = {
        "name": attempt(lambda: p.name, errors, "param.name"),
        "title": attempt(lambda: p.title, errors, "param.title"),
        "expression": attempt(lambda: p.expression, errors, "param.expression"),
        "editable": attempt(lambda: p.isEditable, errors, "param.isEditable"),
        "enabled": attempt(lambda: p.isEnabled, errors, "param.isEnabled"),
    }
    value = attempt(lambda: p.value, errors, f"{info['name']}.value")
    if value is not None:
        info["value_type"] = attempt(lambda: value.objectType, errors, f"{info['name']}.value.objectType")
        raw = attempt(lambda: value.value, [], "value.value")
        info["value"] = plain(raw)
        if hasattr(value, "getChoices"):
            choices = attempt(lambda: value.getChoices(), errors, f"{info['name']}.getChoices")
            info["choices"] = repr(choices)[: MAX_CHOICES * 40] if choices is not None else None
    return info


def dump_parameters(collection, errors, what):
    out = []
    count = attempt(lambda: collection.count, errors, f"{what}.count") or 0
    for i in range(count):
        p = attempt(lambda: collection.item(i), errors, f"{what}.item({i})")
        if p is not None:
            out.append(param_info(p, errors))
    return out


def tool_info(op, errors):
    tool = attempt(lambda: op.tool, errors, f"{op.name}.tool")
    if tool is None:
        return None
    info = {"parameters": dump_parameters(tool.parameters, errors, f"{op.name}.tool.parameters")}
    raw = attempt(lambda: tool.toJson(), errors, f"{op.name}.tool.toJson")
    if raw:
        try:
            data = json.loads(raw)
            info["guid"] = data.get("guid")
            info["description"] = data.get("description")
            info["number"] = (data.get("post-process") or {}).get("number")
            info["toJson_keys"] = sorted(data)
        except ValueError as e:
            errors.append(f"{op.name}.tool.toJson parse: {e}")
    return info


def walk_posts(errors):
    lib = attempt(lambda: adsk.cam.CAMManager.get().libraryManager.postLibrary, errors, "postLibrary")
    if lib is None:
        return []
    found = []

    def walk(url, depth):
        for child in attempt(lambda: lib.childAssetURLs(url), errors, "childAssetURLs") or []:
            cfg = attempt(lambda: lib.postConfigurationAtURL(child), errors, "postConfigurationAtURL")
            found.append({"url": child.toString(),
                          "description": attempt(lambda: cfg.description, errors, "post.description") if cfg else None})
        if depth > 0:
            for sub in attempt(lambda: lib.childFolderURLs(url), errors, "childFolderURLs") or []:
                walk(sub, depth - 1)

    for name in ("LocalLibraryLocation", "CloudLibraryLocation"):
        loc = getattr(adsk.cam.LibraryLocations, name, None)
        root = attempt(lambda: lib.urlByLocation(loc), errors, f"urlByLocation({name})") if loc is not None else None
        if root is not None:
            walk(root, 2)
    return found


def dump(app):
    errors = []
    doc = app.activeDocument
    out = {
        "script": "dump_params",
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "fusion_version": attempt(lambda: app.version, errors, "app.version"),
        "python_version": sys.version,
        "platform": platform.platform(),
        "document": attempt(lambda: doc.name, errors, "document.name"),
        "setups": [],
        "nc_programs": [],
        "posts": walk_posts(errors),
        "errors": errors,
    }
    cam = attempt(lambda: adsk.cam.CAM.cast(doc.products.itemByProductType("CAMProductType")), errors, "CAM product")
    if cam is None:
        errors.append("this document has no Manufacture data; open a document with CAM setups")
        return out
    out["document_units"] = attempt(lambda: app.activeProduct.unitsManager.defaultLengthUnits, errors, "units")
    for i in range(cam.setups.count):
        setup = cam.setups.item(i)
        s = {
            "name": setup.name,
            "parameters": dump_parameters(setup.parameters, errors, f"setup {setup.name}"),
            "stock_mode": plain(attempt(lambda: setup.stockMode, errors, "setup.stockMode")),
            "operations": [],
        }
        ops = attempt(lambda: setup.allOperations, errors, f"{setup.name}.allOperations")
        for j in range(ops.count if ops else 0):
            try:
                op = ops.item(j)
                s["operations"].append({
                    "name": op.name,
                    "object_type": attempt(lambda: op.objectType, errors, "op.objectType"),
                    "strategy": attempt(lambda: op.strategy, errors, f"{op.name}.strategy"),
                    "has_toolpath": attempt(lambda: op.hasToolpath, errors, f"{op.name}.hasToolpath"),
                    "is_suppressed": attempt(lambda: op.isSuppressed, [], "op.isSuppressed"),
                    "tool": tool_info(op, errors),
                    "parameters": dump_parameters(op.parameters, errors, f"op {op.name}"),
                })
            except Exception as e:  # noqa: BLE001
                errors.append(f"{setup.name} operation {j}: {type(e).__name__}: {e}")
        out["setups"].append(s)
    programs = attempt(lambda: cam.ncPrograms, errors, "cam.ncPrograms")
    for i in range(programs.count if programs else 0):
        prog = programs.item(i)
        cfg = attempt(lambda: prog.postConfiguration, errors, "ncProgram.postConfiguration")
        post_params = attempt(lambda: prog.postParameters, errors, "ncProgram.postParameters")
        out["nc_programs"].append({
            "name": attempt(lambda: prog.name, errors, "ncProgram.name"),
            "post": attempt(lambda: cfg.description, errors, "post.description") if cfg else None,
            "parameters": dump_parameters(prog.parameters, errors, "ncProgram.parameters"),
            "post_parameters": dump_parameters(post_params, errors, "postParameters") if post_params else [],
        })
    return out


def run(context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        data = dump(app)
        folder = os.path.join(HERE, "out")
        os.makedirs(folder, exist_ok=True)
        safe_doc = "".join(c if c.isalnum() or c in "-_" else "_" for c in (data.get("document") or "document"))
        path = os.path.join(folder, f"{safe_doc}_{time.strftime('%Y%m%d-%H%M%S')}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        n_ops = sum(len(s["operations"]) for s in data["setups"])
        ui.messageBox(f"Wrote {len(data['setups'])} setup(s), {n_ops} operation(s), "
                      f"{len(data['errors'])} recorded error(s) to:\n{path}", "dump_params")
    except Exception:  # noqa: BLE001
        ui.messageBox("dump_params failed:\n" + traceback.format_exc(), "dump_params")
