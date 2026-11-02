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
from .onshape.ledger import PANEL_RUN, Ledger
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


def start_panel(cfg: Config, env_file: Optional[Path], services: Services) -> bool:
    """The Onshape panel (panel/): its server, and its tunnel when [panel] tunnel_dir is set. Off unless [panel]
    enabled and AUTOCAM_PANEL_KEY is in .env. True if it started."""
    if not cfg.panel.enabled:
        return False
    creds = load_credentials(env_file)
    if creds.missing("panel") or creds.missing("trello"):
        log.warning("panel: AUTOCAM_PANEL_KEY (or the Trello keys) isn't in .env, so the Onshape panel is off")
        return False
    from .panel.api import Names, PanelApp
    from .panel.board import PanelBoard
    from .panel.hooks import ChangeInbox, WebhookSetup
    from .panel.onshape_reads import PanelOnshape
    from .panel.pictures import PictureWatch
    from .panel.precut import PrecutWatch
    from .panel.server import Tunnel, start_server

    def client_for(max_calls: int) -> OnshapeClient:
        return OnshapeClient(base_url=cfg.onshape.base_url, access_key=creds.get("ONSHAPE_ACCESS_KEY"),
                             secret_key=creds.get("ONSHAPE_SECRET_KEY"), ledger=services.ledger,
                             transport=RequestsTransport(timeout_s=30.0), run_id=PANEL_RUN, max_calls=max_calls,
                             retry_after_max_wait_s=5.0)

    p = cfg.panel
    onshape = PanelOnshape(ledger=services.ledger, client_for=None if creds.missing("onshape") else client_for,
                           cache_root=cfg.paths.cache, calls_per_day=p.onshape_calls_per_day,
                           calls_per_year=p.onshape_calls_per_year, year_start=cfg.onshape.budget_year_start,
                           parts_cache_s=p.parts_cache_s, assembly_cache_s=p.assembly_cache_s,
                           max_assemblies=p.max_assemblies)
    tracker = trello_tracker(cfg, env_file)
    board = PanelBoard(tracker, cfg, services.store)
    secret = "" if creds.missing("webhook") else creds.get("AUTOCAM_WEBHOOK_SECRET")
    inbox = hooks = None
    if p.webhooks and secret and p.public_url and not creds.missing("onshape"):
        inbox = ChangeInbox(cfg.paths.state / "panel_changes.json")
        hooks = WebhookSetup(onshape, p.public_url, secret, cfg.paths.state / "panel_webhook.json", p.company_id)
    elif p.webhooks:
        log.info("panel: no Onshape webhook (needs AUTOCAM_WEBHOOK_SECRET in .env, [panel] public_url and Onshape keys)")
    app = PanelApp(cfg, creds.get("AUTOCAM_PANEL_KEY"), onshape, board, Names(cfg.paths.state / "panel_users.json"),
                   inbox=inbox, hook_secret=secret)
    start_server(app, p.port)
    pictures = PictureWatch(onshape, services.store, tracker) if p.pictures else None
    PrecutWatch(onshape, board, services.store, tracker, inbox=inbox, hooks=hooks,
                pictures=pictures).keep_running()                    # changed before it's cut, pictures
    if p.tunnel_dir:
        Tunnel(Path(p.tunnel_dir), cfg.paths.state, cfg.paths.logs).keep_running()
    return True


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
