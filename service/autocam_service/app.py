"""Wire the real services together (Trello, Onshape, hot folder, state) and run the poll loop."""

import logging
import logging.handlers
import time
from pathlib import Path
from typing import Optional

from autocam_core.hotfolder import Queue

from .config import Config
from .credentials import load_credentials
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

    return Services(cfg, tracker, Queue(cfg.paths.queue).ensure(), RunStore(cfg.paths.state), ledger, exporter_for)


def run_forever(runner: Runner, interval_s: float) -> None:
    log.info("service started; polling every %s s", interval_s)
    while True:
        try:
            runner.tick()
        except KeyboardInterrupt:
            raise
        except Exception:  # noqa: BLE001 - keep polling; the error is logged with its traceback
            log.exception("tick failed")
        time.sleep(interval_s)
