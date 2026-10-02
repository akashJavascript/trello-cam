"""autocam_addin: the Fusion end of the hot folder (M2). UNTESTED IN FUSION.

While the add-in runs, a background thread wakes every POLL_S seconds, updates queue/worker_heartbeat.json and,
when a job is waiting, fires a custom event. The event handler runs on Fusion's main thread (the only thread
allowed to call the Fusion API) and runs one job through the pipeline (autocam_worker/worker.py). No dialogs:
problems go to the job's failed/error.txt and to logs/fusion_worker.log.

Stopping the add-in drops every autocam_* module, so starting it again after a `git pull` runs the new code.
"""

import logging
import logging.handlers
import sys
import threading
import traceback
from pathlib import Path

import adsk.core

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
EVENT_ID = "autocam_worker_tick"
POLL_S = 10.0

for _p in (REPO / "core", HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_app = None
_worker = None
_stop = None
_thread = None
_handlers = []          # Fusion only holds weak references to handlers
_log = logging.getLogger("autocam_addin")


class _TickHandler(adsk.core.CustomEventHandler):
    def notify(self, args):
        try:
            if _worker is not None:
                _worker.tick()
        except Exception:  # noqa: BLE001 - never let an exception escape into Fusion
            _log.error("tick failed:\n%s", traceback.format_exc())


def _poll():
    while not _stop.wait(POLL_S):
        try:
            _worker.heartbeat()
            if _worker.wants_tick():
                _app.fireCustomEvent(EVENT_ID, "")
        except Exception:  # noqa: BLE001
            _log.error("poll failed:\n%s", traceback.format_exc())


def _setup_log(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    if not _log.handlers:
        h = logging.handlers.RotatingFileHandler(folder / "fusion_worker.log", maxBytes=1_000_000, backupCount=5,
                                                 encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _log.addHandler(h)
        _log.setLevel(logging.INFO)


def run(context):
    global _app, _worker, _stop, _thread
    _app = adsk.core.Application.get()
    try:
        from autocam_core.hotfolder import Queue
        from autocam_worker.fx_adapter import FusionAdapter
        from autocam_worker.worker import Worker, load_settings

        settings = load_settings(REPO)
        _setup_log(settings.logs)
        queue = Queue(settings.queue).ensure()
        _worker = Worker(queue, lambda job: FusionAdapter(_app, job), max_attempts=settings.max_attempts,
                         fusion_version=_app.version, log=_log.info)
        event = _app.registerCustomEvent(EVENT_ID)
        handler = _TickHandler()
        event.add(handler)
        _handlers.append(handler)
        _worker.heartbeat()
        _stop = threading.Event()
        _thread = threading.Thread(target=_poll, name="autocam_poll", daemon=True)
        _thread.start()
        _log.info("started: queue %s, Fusion %s", settings.queue, _app.version)
    except Exception:  # noqa: BLE001
        msg = traceback.format_exc()
        _log.error("start failed:\n%s", msg)
        _app.userInterface.messageBox("The auto-CAM add-in couldn't start:\n" + msg, "autocam_addin")


def stop(context):
    global _worker, _thread
    try:
        if _stop is not None:
            _stop.set()
        if _thread is not None:
            _thread.join(timeout=POLL_S + 2)
        if _app is not None:
            _app.unregisterCustomEvent(EVENT_ID)
        _handlers.clear()
        _log.info("stopped")
    except Exception:  # noqa: BLE001
        _log.error("stop failed:\n%s", traceback.format_exc())
    finally:
        _worker = _thread = None
        for h in list(_log.handlers):
            h.close()
            _log.removeHandler(h)
        for name in list(sys.modules):
            if name.split(".")[0] in ("autocam_core", "autocam_worker"):
                del sys.modules[name]
