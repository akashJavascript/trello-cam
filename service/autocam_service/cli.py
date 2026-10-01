"""Command line: `python -m autocam_service <command>`."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from autocam_core import CORE_VERSION

from .config import DEFAULT_CONFIG, ConfigError, load_config
from .credentials import ALL_KEYS, load_credentials


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="autocam", description="FRC auto-CAM service")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("config-check", help="validate config/autocam.toml and list what is still assumed")
    check.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    check.add_argument("--env-file", type=Path, default=None, help="default: .env next to config/")
    args = parser.parse_args(argv)

    if args.command == "config-check":
        return config_check(args.config, args.env_file)
    return 2


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
