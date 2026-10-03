"""Wire the real services together (Trello, Onshape, hot folder, state) and run the poll loop."""

import logging
import logging.handlers
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from autocam_core.hotfolder import Queue

from . import health
from .config import Config
from .credentials import load_credentials
from .restart import EXIT_RESTART, CodeWatch
from .onshape.cache import OnshapeCache
from .onshape.client import OnshapeClient, RequestsTransport
from .onshape.export import Exporter, PollSchedule
from .onshape.ledger import Ledger
from .run_state import RunStore
from .runner import Runner, Services
from .tracker.dryrun import DryRunTracker
from .tracker.trello import TrelloHttp, TrelloTracker

log = logging.getLogger("autocam")


def setup_logging(cfg: Config, verbose: bool = False) -> None:
    cfg.paths.logs.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("autocam")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not root.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        file_handler = logging.handlers.RotatingFileHandler(cfg.paths.logs / "service.log", maxBytes=1_000_000,
                                                            backupCount=5, encoding="utf-8")
        file_handler.setFormatter(fmt)
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(file_handler)
        root.addHandler(console)


def ledger_for(cfg: Config) -> Ledger:
    return Ledger(cfg.paths.state / "onshape_ledger.jsonl")


def trello_tracker(cfg: Config, env_file: Optional[Path]) -> TrelloTracker:
    creds = load_credentials(env_file)
    creds.require("trello")
    http = TrelloHttp(creds.get("TRELLO_API_KEY"), creds.get("TRELLO_TOKEN"))
    return TrelloTracker(http, cfg.trello.lists, cfg.trello.attachment_limit_mb)


def build_services(cfg: Config, *, env_file: Optional[Path], dry_run: bool, offline: bool) -> Services:
    creds = load_credentials(env_file)
    tracker = trello_tracker(cfg, env_file)
    if dry_run:
        tracker = DryRunTracker(tracker)
    ledger = ledger_for(cfg)
    online = not offline and not creds.missing("onshape")
    if not offline and not online:
        log.warning("Onshape keys missing from .env: only cached parts and .step attachments can be used")
    polls = PollSchedule(cfg.onshape.poll_first_s, cfg.onshape.poll_factor, cfg.onshape.poll_max_s,
                         cfg.onshape.poll_max_count)

    def exporter_for(run_id: str) -> Exporter:
        client = None
        if online:
            client = OnshapeClient(base_url=cfg.onshape.base_url, access_key=creds.get("ONSHAPE_ACCESS_KEY"),
                                   secret_key=creds.get("ONSHAPE_SECRET_KEY"), ledger=ledger,
                                   transport=RequestsTransport(), run_id=run_id,
                                   max_calls=cfg.onshape.per_run_max_calls,
                                   retry_after_max_wait_s=cfg.onshape.retry_after_max_wait_s)
        return Exporter(client, OnshapeCache(cfg.paths.cache), polls)

    # A dry run keeps its own run state and "d" run ids: it never touches a real run, and vice versa.
    store = RunStore(cfg.paths.state / "dryrun", prefix="d") if dry_run else RunStore(cfg.paths.state)
    return Services(cfg, tracker, Queue(cfg.paths.queue).ensure(), store, ledger, exporter_for)


def disable_quick_edit() -> None:
    """Windows consoles pause a program the moment someone clicks in the window ("QuickEdit" selection) until
    Esc or Enter, and the service then hangs on its next log line. Seen 2026-10-02: a finished job waited
    24 minutes. Turn QuickEdit off for this console (right-click > Mark still selects text)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)                  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, (mode.value & ~0x0040) | 0x0080)   # -QUICK_EDIT, +EXTENDED_FLAGS
    except Exception as e:  # noqa: BLE001 - no console (e.g. a scheduled task): nothing to do
        log.debug("couldn't turn off QuickEdit: %s", e)


ERROR_SHOWN_S = 24 * 3600     # how long the System card shows the last error


def run_forever(runner: Runner, interval_s: float, code: Optional[CodeWatch] = None,
                sleep=time.sleep, clock=lambda: datetime.now(timezone.utc)) -> int:
    """Tick every interval_s and update the System card's status. Returns EXIT_RESTART when `code` says new
    code or config is ready to run."""
    disable_quick_edit()
    log.info("service started; polling every %s s", interval_s)
    last_error: Optional[Tuple[datetime, str]] = None
    while True:
        try:
            runner.tick()
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001 - keep polling; the error is logged with its traceback
            log.exception("tick failed")
            last_error = (clock(), f"{type(e).__name__}: {e}"[:300])
        report = getattr(runner, "report_health", None)
        if report is not None:
            shown = None
            if last_error and (clock() - last_error[0]).total_seconds() < ERROR_SHOWN_S:
                shown = f"{health.local_time(last_error[0])}: {last_error[1]}"
            try:
                report(shown)
            except KeyboardInterrupt:
                raise
            except Exception:  # noqa: BLE001 - the status is a nice-to-have; the next pass tries again
                log.exception("updating the System card failed")
        sleep(interval_s)
        if code is not None and code.should_restart():
            return EXIT_RESTART
