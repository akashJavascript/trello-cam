"""autocam_run: run one job through the auto-CAM pipeline in Fusion, by hand (M1 runs).

UNTESTED IN FUSION.

Either pick STEP files (the job is built from config/autocam.toml, like `autocam make-job`) or pick a
job.json. Outputs go to fusion/autocam_run/out/<job>-<time>/ with result.json written last. The document is
left open so you can look at the nest, the setups and the toolpaths. Nothing is uploaded anywhere.

Building a job here uses the service's job builder, which is standard library only. That is the one place
Fusion-side code imports autocam_service, and only for these manual runs; the add-in (M2) reads job.json.
"""

import sys
import time
import traceback
from pathlib import Path

import adsk.core

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TITLE = "autocam_run"
for p in (REPO / "core", REPO / "service", REPO / "fusion" / "autocam_addin"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _fresh_modules():
    """Fusion keeps one Python process alive: drop cached modules so a `git pull` takes effect."""
    for name in list(sys.modules):
        if name.split(".")[0] in ("autocam_core", "autocam_service", "autocam_worker"):
            del sys.modules[name]


def _ask(ui, prompt, default):
    value, cancelled = ui.inputBox(prompt, TITLE, default)
    if cancelled:
        raise KeyboardInterrupt
    return value.strip()


def _job_from_steps(ui):
    from autocam_service.batching import Batch, ReadyPart
    from autocam_service.cards import PartRequest
    from autocam_service.config import DEFAULT_CONFIG, load_config
    from autocam_service.jobs import build_job
    from autocam_service.onshape.cache import sha256_file
    from autocam_service.tracker.base import Card

    dlg = ui.createFileDialog()
    dlg.title = "Pick the STEP files (one part each)"
    dlg.filter = "STEP (*.step;*.stp)"
    dlg.isMultiSelectEnabled = True
    if dlg.showOpen() != adsk.core.DialogResults.DialogOK:
        raise KeyboardInterrupt
    steps = [Path(f) for f in dlg.filenames]
    cfg = load_config(DEFAULT_CONFIG)
    material = _ask(ui, f"Material key ({', '.join(cfg.materials)})", "al6061")
    if material not in cfg.materials:
        raise ValueError(f"unknown material {material!r}")
    qtys = _ask(ui, f"Quantity of each part, in order: {', '.join(s.stem for s in steps)}", ",".join("1" for _ in steps))
    qty = [int(q) for q in qtys.split(",")]
    if len(qty) != len(steps):
        raise ValueError(f"{len(qty)} quantities for {len(steps)} parts")
    ready = []
    for i, (step, n) in enumerate(zip(steps, qty), 1):
        card = Card(f"local-{i}", step.stem, "", None, "")
        req = PartRequest(card, step.stem, n, None, None, material, False, False)
        ready.append(ReadyPart(req, material, step, sha256_file(step)))
    batch = Batch(material, tuple((f"p{i:02d}", r) for i, r in enumerate(ready, 1)))
    run_id = time.strftime("t%H%M%S")
    return build_job(cfg, batch, run_id, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


def _summary(result, out_dir: Path) -> str:
    lines = [f"Status: {result.status}", ""]
    for s in result.sheets:
        what = s.tap or (f"NOT OFFERED ({s.errors[0].msg})" if s.errors else "not posted")
        n = sum(p.count for p in s.parts)
        lines.append(f"{s.name}: {n} part(s), {s.cutter_label}: {what}")
    for p in result.parts:
        if p.errors:
            lines.append(f"{p.part_key}: {p.errors[0].msg}")
        elif p.deferred:
            lines.append(f"{p.part_key}: didn't fit, deferred")
    for e in result.errors:
        lines.append(e.msg)
    if result.worker.untested_steps:
        lines += ["", "First time in Fusion for: " + "; ".join(result.worker.untested_steps)]
    lines += ["", f"Details: {out_dir / 'result.json'}"]
    return "\n".join(lines)


def run(context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        _fresh_modules()
        from autocam_core.schema_job import job_json, read_job
        from autocam_worker.fx_adapter import FusionAdapter
        from autocam_worker.pipeline import JobFailed, process_job

        choice = ui.messageBox("Yes: pick STEP files and build a job from config/autocam.toml\n"
                               "No: pick an existing job.json", TITLE,
                               adsk.core.MessageBoxButtonTypes.YesNoCancelButtonType)
        if choice == adsk.core.DialogResults.DialogCancel:
            return
        if choice == adsk.core.DialogResults.DialogYes:
            job = _job_from_steps(ui)
        else:
            dlg = ui.createFileDialog()
            dlg.title = "Pick a job.json"
            dlg.filter = "Job (*.json)"
            if dlg.showOpen() != adsk.core.DialogResults.DialogOK:
                return
            job = read_job(Path(dlg.filename))
        out_dir = HERE / "out" / f"{job.job_id}-{time.strftime('%Y%m%d-%H%M%S')}"
        out_dir.mkdir(parents=True)
        (out_dir / "job.json").write_text(job_json(job), encoding="utf-8")
        try:
            result = process_job(job, FusionAdapter(app, job), out_dir, keep_open=True)
        except JobFailed as e:
            ui.messageBox(f"Job stopped before anything was made:\n{e}", TITLE)
            return
        ui.messageBox(_summary(result, out_dir), TITLE)
    except KeyboardInterrupt:
        return
    except Exception:  # noqa: BLE001
        ui.messageBox("autocam_run failed:\n" + traceback.format_exc(), TITLE)
