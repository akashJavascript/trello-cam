"""API credentials from .env (gitignored) or the environment.

Values are never printed, logged, or included in a repr. Environment variables win over .env.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from dotenv import dotenv_values

GROUPS: Dict[str, tuple] = {
    "trello": ("TRELLO_API_KEY", "TRELLO_TOKEN"),
    "onshape": ("ONSHAPE_ACCESS_KEY", "ONSHAPE_SECRET_KEY"),
}
ALL_KEYS = tuple(k for keys in GROUPS.values() for k in keys)


class CredentialsError(Exception):
    pass


class Credentials:
    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, str]):
        self._values = {k: v for k, v in values.items() if k in ALL_KEYS and v}

    def has(self, key: str) -> bool:
        return key in self._values

    def get(self, key: str) -> str:
        if key not in ALL_KEYS:
            raise CredentialsError(f"{key} is not a known credential")
        try:
            return self._values[key]
        except KeyError:
            raise CredentialsError(f"{key} is not set (add it to .env; see .env.example)") from None

    def missing(self, *groups: str) -> List[str]:
        keys = []
        for group in groups or tuple(GROUPS):
            if group not in GROUPS:
                raise CredentialsError(f"unknown credential group {group!r}")
            keys += [k for k in GROUPS[group] if k not in self._values]
        return keys

    def require(self, *groups: str) -> None:
        missing = self.missing(*groups)
        if missing:
            raise CredentialsError(f"missing {', '.join(missing)} (add to .env; see .env.example)")

    def __repr__(self) -> str:
        shown = ", ".join(f"{k}={'<set>' if k in self._values else '<missing>'}" for k in ALL_KEYS)
        return f"Credentials({shown})"

    __str__ = __repr__


def load_credentials(env_file: Optional[Path] = None,
                     environ: Optional[Mapping[str, str]] = None) -> Credentials:
    values: Dict[str, str] = {}
    if env_file is not None and Path(env_file).is_file():
        for key, value in dotenv_values(env_file).items():
            if key in ALL_KEYS and value:
                values[key] = value.strip()
    env = os.environ if environ is None else environ
    for key in ALL_KEYS:
        if env.get(key):
            values[key] = env[key].strip()
    return Credentials(values)
