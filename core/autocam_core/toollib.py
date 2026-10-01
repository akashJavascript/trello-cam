"""Read a Fusion tool library (.tools file = zip holding tools.json).

Tools are looked up by GUID only. The shop library has duplicate tool numbers (both
4 mm endmills are T1) and several tools share a diameter, so there is deliberately no
lookup by number or diameter here.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List

MM_PER_IN = 25.4


class ToolLibraryError(Exception):
    """The .tools file is missing, unreadable, or not shaped like a Fusion library."""


@dataclass(frozen=True)
class LibraryTool:
    guid: str
    description: str
    number: int
    type: str
    diameter_in: float
    flute_length_in: float
    flutes: int


def _to_inches(value: float, unit: str) -> float:
    if unit == "millimeters":
        return value / MM_PER_IN
    if unit == "inches":
        return value
    raise ToolLibraryError(f"unknown tool unit {unit!r}")


def _parse_tool(raw: dict) -> LibraryTool:
    try:
        unit = raw["unit"]
        geometry = raw["geometry"]
        return LibraryTool(
            guid=raw["guid"],
            description=raw.get("description", ""),
            number=int(raw["post-process"]["number"]),
            type=raw.get("type", ""),
            diameter_in=_to_inches(float(geometry["DC"]), unit),
            flute_length_in=_to_inches(float(geometry["LCF"]), unit),
            flutes=int(geometry.get("NOF", 0)),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ToolLibraryError(f"tool entry {raw.get('guid', '?')!r} is malformed: {e!r}") from e


class ToolLibrary:
    def __init__(self, tools: List[LibraryTool]):
        self._by_guid: Dict[str, LibraryTool] = {}
        for tool in tools:
            if tool.guid in self._by_guid:
                raise ToolLibraryError(f"duplicate tool GUID {tool.guid}")
            self._by_guid[tool.guid] = tool

    @classmethod
    def load(cls, path: Path) -> "ToolLibrary":
        path = Path(path)
        try:
            with zipfile.ZipFile(path) as z:
                data = json.loads(z.read("tools.json").decode("utf-8"))
        except (OSError, KeyError, zipfile.BadZipFile, ValueError) as e:
            raise ToolLibraryError(f"cannot read tool library {path}: {e}") from e
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise ToolLibraryError(f"{path}: tools.json has no 'data' list")
        return cls([_parse_tool(t) for t in data["data"]])

    def by_guid(self, guid: str) -> LibraryTool:
        try:
            return self._by_guid[guid]
        except KeyError:
            raise KeyError(f"no tool with GUID {guid} in the library") from None

    def __contains__(self, guid: object) -> bool:
        return guid in self._by_guid

    def __iter__(self) -> Iterator[LibraryTool]:
        return iter(self._by_guid.values())

    def __len__(self) -> int:
        return len(self._by_guid)

    def duplicate_numbers(self) -> Dict[int, List[LibraryTool]]:
        """Tool numbers used by more than one tool (why a number never identifies a tool)."""
        by_number: Dict[int, List[LibraryTool]] = {}
        for tool in self:
            by_number.setdefault(tool.number, []).append(tool)
        return {n: ts for n, ts in by_number.items() if len(ts) > 1}
