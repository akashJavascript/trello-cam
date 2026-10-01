"""Group ready parts into jobs: one job per material (+ color) per run (decision 10).

Clear and smoked polycarbonate never share a job, neither do 6061 and 5052. Fusion splits each
job further by measured thickness, so every sheet is one stock type.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .cards import PartRequest


@dataclass(frozen=True)
class ReadyPart:
    request: PartRequest
    material_key: str
    step_path: Path
    step_sha256: str
    onshape_part_id: Optional[str] = None


@dataclass(frozen=True)
class Batch:
    material_key: str
    parts: Tuple[Tuple[str, ReadyPart], ...]   # (part_key, part); part keys p01, p02, ... per batch


def make_batches(parts: Sequence[ReadyPart]) -> List[Batch]:
    by_material: Dict[str, List[ReadyPart]] = {}
    for p in parts:
        by_material.setdefault(p.material_key, []).append(p)
    return [Batch(key, tuple((f"p{i:02d}", p) for i, p in enumerate(group, 1)))
            for key, group in sorted(by_material.items())]
