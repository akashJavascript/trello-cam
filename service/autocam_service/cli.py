"""Command line: `python -m autocam_service <command>`.

    config-check      validate config/autocam.toml; list ASSUMED values and empty placeholders
    tick              one service pass (poll Trello, collect finished jobs, start a run if cards waited long
                      enough; `--now` doesn't wait)
    run               the service loop (tick every trello.poll_interval_s)
    dry-run           one pass that reads the real board but writes nothing to Trello (prints the writes)
    make-job          build a job.json from local STEP files (no Trello, no Onshape) for manual Fusion runs
    ledger            Onshape call counts; `ledger reset-latch` after a 402 has been dealt with
    trello-discover   print the board's list and card IDs as a ready-to-paste [trello.lists] block
    trello-setup      create the board's lists, control/status cards and labels (only what's missing)
    onshape-check     one logged Onshape call: do the keys and onshape.base_url work, and whose are they
    preview-labels    draw the part labels on a finished job's sheet previews, to look at what the card gets
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Optional

from autocam_core import CORE_VERSION

from .config import DEFAULT_CONFIG, TRELLO_LISTS, ConfigError, load_config
from .credentials import ALL_KEYS, CredentialsError, load_credentials


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="autocam", description="FRC auto-CAM service")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env-file", type=Path, default=None, help="default: .env in the repo root")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("config-check", help="validate the config and list what is still assumed")
    for name, help_text in (("tick", "one service pass"), ("run", "the service loop"),
                            ("dry-run", "one pass, no Trello writes")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--offline", action="store_true", help="no Onshape calls (cache and .step attachments only)")
        p.add_argument("--verbose", action="store_true")
        if name == "tick":
            p.add_argument("--now", action="store_true", help="start a run for waiting cards without the usual delay")
        if name == "run":
            p.add_argument("--child", action="store_true", help=argparse.SUPPRESS)   # the loop under the supervisor
    mj = sub.add_parser("make-job", help="job.json from local STEP files (for manual Fusion runs)")
    mj.add_argument("--material", required=True, help="material key from config, e.g. al6061")
    mj.add_argument("--part", action="append", required=True, metavar="NAME=PATH[:QTY]")
    mj.add_argument("--run-id", default="local001")
    mj.add_argument("--submit", action="store_true", help="put it in queue/incoming/ instead of printing it")
    lg = sub.add_parser("ledger", help="Onshape call counts")
    lg.add_argument("action", nargs="?", default="report", choices=("report", "reset-latch"))
    td = sub.add_parser("trello-discover", help="print list and card IDs for config")
    td.add_argument("--board", help="board ID or short link (default: trello.board_id)")
    sub.add_parser("onshape-check", help="one logged Onshape call to check the keys and the address")
    ts = sub.add_parser("trello-setup", help="create the lists, cards and labels the service needs")
    where = ts.add_mutually_exclusive_group(required=True)
    where.add_argument("--create", metavar="NAME", help="make a new board with this name")
    where.add_argument("--board", help="set up an existing board (ID or short link from its URL)")
    ts.add_argument("--workspace", help="workspace (organization) ID for a new board")
    pl = sub.add_parser("preview-labels", help="draw the part labels on a finished job's sheet previews")
    pl.add_argument("path", type=Path, help="a job folder (queue/done/<job>) or one sheet's .png")
    pl.add_argument("--out", type=Path, help="where to write it (one .png only; default: <sheet>.labelled.png beside it)")
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)

    if args.command == "config-check":
        return config_check(args.config, args.env_file)
    if args.command == "preview-labels":
        return preview_labels(args.path, args.out)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"Config INVALID: {args.config}\n" + "\n".join(f"  - {x}" for x in e.errors))
        return 1
    env_file = args.env_file or cfg.root / ".env"
    try:
        if args.command == "run" and not args.child:
            from .restart import AlreadyRunning, SingleInstance, supervise
            try:
                lock = SingleInstance(cfg.paths.state / "service.lock")
            except AlreadyRunning as e:
                print(f"autocam: {e}. Close the other one first (its window is titled 'autocam service').")
                return 1
            print("autocam: the service restarts by itself when its code or config changes. Ctrl+C to stop.")
            try:
                return supervise([sys.executable, "-m", "autocam_service", *argv, "--child"])
            finally:
                lock.release()
        if args.command in ("tick", "run", "dry-run"):
            return service(cfg, env_file, args.command, args.offline, args.verbose, getattr(args, "now", False),
                           config_path=args.config, env_arg=args.env_file)
        if args.command == "make-job":
            return make_job(cfg, args.material, args.part, args.run_id, args.submit)
        if args.command == "ledger":
            return ledger(cfg, args.action)
        if args.command == "trello-discover":
            return trello_discover(cfg, env_file, args.board)
        if args.command == "onshape-check":
            return onshape_check(cfg, env_file)
        if args.command == "trello-setup":
            return trello_setup(cfg, env_file, args.board, args.create, args.workspace)
    except CredentialsError as e:
        print(f"Credentials: {e}")
        return 1
    return 2


def preview_labels(path: Path, out: Optional[Path]) -> int:
    """The previews as a sheet card would get them, with the labels the worker placed (<sheet>.labels.json)."""
    from . import labels
    if path.is_dir():
        pngs = sorted(p for p in path.glob("*.png") if not p.name.endswith(".labelled.png"))
    else:
        pngs = [path] if path.is_file() and path.suffix.lower() == ".png" else []
    if not pngs:
        print(f"No sheet preview (.png) at {path}")
        return 1
    if out is not None and len(pngs) > 1:
        print(f"--out is for one picture, and {path} has {len(pngs)}: give one .png instead")
        return 1
    status = 0
    for png in pngs:
        stem = png.name[:-len(png.suffix)]
        spots = png.with_name(f"{stem}.labels.json")
        if not spots.is_file():
            print(f"{png.name}: no {spots.name} beside it. Sheets CAM'd before add-in 0.12.0 have none: "
                  f"run the job through Fusion again.")
            status = 1
            continue
        try:
            drawn = labels.draw(png.read_bytes(), json.loads(spots.read_text(encoding="utf-8")))
        except ImportError:
            print("Pillow isn't installed in this Python (pip install pillow).")
            return 1
        target = out or png.with_name(f"{stem}.labelled.png")
        target.write_bytes(drawn)
        print(f"{png.name}: labelled -> {target}")
    return status


def config_check(path: Path, env_file: Optional[Path]) -> int:
    try:
        cfg = load_config(path)
    except ConfigError as e:
        print(f"Config INVALID: {path}")
        for err in e.errors:
            print(f"  - {err}")
        return 1

    print(f"Config OK: {path} (core {CORE_VERSION})")
    available = [t.key for t in cfg.tools.values() if t.available]
    print(f"  stock types: {len(cfg.stock_types())}   tools: {', '.join(available)}   "
          f"templates: {', '.join(cfg.templates)}")
    print(f"\nASSUMED values ({len(cfg.assumed)}), confirm and then delete from [assumed]:")
    for key, why in cfg.assumed.items():
        print(f"  - {key}: {why}")
    print(f"\nPlaceholders still empty ({len(cfg.placeholders)}):")
    for key in cfg.placeholders:
        print(f"  - {key}")
    print(f"\nWarnings ({len(cfg.warnings)}):")
    for w in cfg.warnings:
        print(f"  - {w}")
    creds = load_credentials(env_file if env_file is not None else cfg.root / ".env")
    print("\nCredentials (values never shown):")
    for key in ALL_KEYS:
        print(f"  - {key}: {'set' if creds.has(key) else 'missing'}")
    return 0


def service(cfg, env_file: Path, command: str, offline: bool, verbose: bool, now: bool = False,
            config_path: Optional[Path] = None, env_arg: Optional[Path] = None) -> int:
    from .app import build_services, run_forever, setup_logging
    from .restart import CodeWatch, new_code_loads
    from .runner import Runner
    setup_logging(cfg, verbose)
    services = build_services(cfg, env_file=env_file, dry_run=command == "dry-run", offline=offline)
    runner = Runner(services, start_delay_s=0 if now or command == "dry-run" else None)
    if command == "run":
        config_path = config_path or DEFAULT_CONFIG
        code = CodeWatch(cfg.root, cfg.paths.state / "restart_service", extra=[Path(config_path), env_file],
                         validate=lambda: new_code_loads(config_path, env_arg))
        try:
            return run_forever(runner, cfg.trello.poll_interval_s, code)
        except KeyboardInterrupt:
            print("stopped")
        return 0
    runner.tick()
    if command == "dry-run":
        print("Trello writes that a real run would have made:")
        for write in services.tracker.intended:
            print("  " + " | ".join(str(x)[:120].replace("\n", " / ") for x in write))
    return 0


def make_job(cfg, material: str, parts: List[str], run_id: str, submit: bool) -> int:
    from autocam_core.hotfolder import Queue
    from autocam_core.schema_job import job_json
    from .batching import Batch, ReadyPart
    from .cards import PartRequest
    from .jobs import JobBuildError, build_job
    from .onshape.cache import sha256_file
    from .onshape.ledger import utc_now
    from .tracker.base import Card

    if material not in cfg.materials:
        print(f"unknown material {material!r}; one of: {', '.join(cfg.materials)}")
        return 1
    ready = []
    for i, spec in enumerate(parts, 1):
        m = re.match(r"^(?P<name>[^=]+)=(?P<path>.+?)(?::(?P<qty>\d+))?$", spec)
        if not m:
            print(f"--part {spec!r}: expected NAME=PATH[:QTY]")
            return 1
        path = Path(m["path"]).expanduser().resolve()
        if not path.is_file():
            print(f"--part {spec!r}: {path} not found")
            return 1
        card = Card(f"local-{i}", m["name"], "", None, "")
        req = PartRequest(card, m["name"], int(m["qty"] or 1), None, None, material, False, False)
        ready.append(ReadyPart(req, material, path, sha256_file(path)))
    batch = Batch(material, tuple((f"p{i:02d}", r) for i, r in enumerate(ready, 1)))
    try:
        job = build_job(cfg, batch, run_id, utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"))
    except JobBuildError as e:
        print(f"can't build the job: {e}")
        return 1
    text = job_json(job)
    if submit:
        path = Queue(cfg.paths.queue).ensure().submit(job.job_id, text)
        print(f"queued {path}")
    else:
        sys.stdout.write(text)
    return 0


def ledger(cfg, action: str) -> int:
    from .app import ledger_for
    led = ledger_for(cfg)
    if action == "reset-latch":
        print("latch cleared" if led.reset_latch() else "no latch was set")
        return 0
    latch = led.latched()
    print(f"Onshape calls this month: {led.month_count()} (soft limit {cfg.onshape.monthly_soft_calls})")
    print(f"Onshape calls this budget year: {led.year_count(cfg.onshape.budget_year_start)} "
          f"(cap {cfg.onshape.yearly_cap_calls})")
    print(f"402 latch: {latch['reason'] if latch else 'not set'}")
    return 0


def _list_names(key: str):
    from .trello_setup import LIST_NAMES, OLD_NAMES
    return [_norm(n) for n in (LIST_NAMES[key],) + OLD_NAMES.get(key, ())]


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def onshape_check(cfg, env_file: Optional[Path], transport=None) -> int:
    """One Onshape call, through the one client and its ledger like every other call."""
    from .app import ledger_for
    from .onshape.client import OnshapeClient, OnshapeError, RequestsTransport
    creds = load_credentials(env_file)
    creds.require("onshape")
    led = ledger_for(cfg)
    latch = led.latched()
    if latch:
        print(f"Onshape calls are latched off ({latch['reason']}); see `autocam ledger`")
        return 1
    client = OnshapeClient(base_url=cfg.onshape.base_url, access_key=creds.get("ONSHAPE_ACCESS_KEY"),
                           secret_key=creds.get("ONSHAPE_SECRET_KEY"), ledger=led,
                           transport=transport or RequestsTransport(), run_id="check", max_calls=1,
                           retry_after_max_wait_s=cfg.onshape.retry_after_max_wait_s)
    try:
        info = client.get_json("/api/v10/users/sessioninfo", purpose="key_check")
    except OnshapeError as e:
        print(f"Onshape refused: {e}")
        if e.status in (401, 403):
            print(f"Check the keys in .env. Keys made in an enterprise only work at the enterprise's address "
                  f"(like https://<name>.onshape.com); onshape.base_url is {cfg.onshape.base_url}.")
        return 1
    finally:
        print(f"Onshape calls this month (this PC's ledger): {led.month_count()}")
    print(f"OK: {cfg.onshape.base_url} accepts these keys. They belong to {info.get('name', '?')} "
          f"({info.get('email', 'no email shown')}).")
    return 0


def trello_setup(cfg, env_file: Path, board: Optional[str], create: Optional[str],
                 workspace: Optional[str] = None) -> int:
    from .app import trello_tracker
    from .trello_setup import setup_board
    tracker = trello_tracker(cfg, env_file)
    result = setup_board(tracker.http, board=board, create=create, workspace=workspace,
                         labels={"smoked": cfg.labels.smoked, "tool_eighth": cfg.labels.tool_eighth,
                                 "rush": cfg.labels.rush},
                         nest_box=(cfg.trello.nest_checklist, cfg.trello.nest_item), tabs_default=cfg.tabs.default)
    print(f"Board: {result.url}")
    print("Created: " + (", ".join(result.created) or "nothing (everything was already there)"))
    print("\nPaste this into config/autocam.toml (IDs aren't secrets):\n")
    print(result.config_block())
    return 0


def trello_discover(cfg, env_file: Path, board: Optional[str]) -> int:
    from .app import trello_tracker
    board = board or cfg.trello.board_id
    if not board:
        print("pass --board <id or short link> (from the board URL https://trello.com/b/<short link>/...)")
        return 1
    tracker = trello_tracker(cfg, env_file)
    lists = tracker.board_lists(board)
    by_norm = {_norm(name): (lid, name) for lid, name in lists}
    print("[trello.lists]")
    for key in TRELLO_LISTS:
        hit = next((by_norm[n] for n in _list_names(key) if n in by_norm), None)
        print(f'{key} = "{hit[0]}"   # {hit[1]}' if hit else f'{key} = ""   # no list named like "{key}" found')
    print("\n# all lists on the board:")
    for lid, name in lists:
        print(f"#   {lid}  {name}")
    cards = tracker.board_cards(board)
    system = [c for c in cards if _norm(c[1]) == "system"]
    print("\n[trello.cards]")
    print(f'system = "{system[0][0] if system else ""}"')
    return 0
