"""Version-keyed Onshape cache. Versions never change, so entries never expire (decision 3).

    cache/onshape/parts_<did>_<vid>_<eid>.json        the Part Studio's parts list
    cache/onshape/translation_<did>_<vid>_<eid>_<part>.json   an unfinished STEP translation to resume
    cache/step/<did>_<vid>_<eid>_<part>.step          exported STEP
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Optional, Tuple

from autocam_core.hotfolder import write_atomic
from autocam_core.names import safe_token

from .urls import OnshapeLink


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class OnshapeCache:
    def __init__(self, root: Path):
        self.meta = Path(root) / "onshape"
        self.steps = Path(root) / "step"

    def _ensure(self) -> None:
        self.meta.mkdir(parents=True, exist_ok=True)
        self.steps.mkdir(parents=True, exist_ok=True)

    def parts(self, link: OnshapeLink) -> Optional[Any]:
        path = self.meta / f"parts_{link.key}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def put_parts(self, link: OnshapeLink, data: Any) -> None:
        self._ensure()
        write_atomic(self.meta / f"parts_{link.key}.json", json.dumps(data, indent=1).encode("utf-8"))

    def step_path(self, link: OnshapeLink, part_id: str) -> Path:
        return self.steps / f"{link.key}_{safe_token(part_id)}.step"

    def has_step(self, link: OnshapeLink, part_id: str) -> bool:
        return self.step_path(link, part_id).is_file()

    def put_step(self, link: OnshapeLink, part_id: str, data: bytes) -> Tuple[Path, str]:
        self._ensure()
        path = self.step_path(link, part_id)
        write_atomic(path, data)
        self.forget_translation(link, part_id)
        return path, hashlib.sha256(data).hexdigest()

    def translation(self, link: OnshapeLink, part_id: str) -> Optional[str]:
        path = self.meta / f"translation_{link.key}_{safe_token(part_id)}.json"
        return json.loads(path.read_text(encoding="utf-8"))["id"] if path.exists() else None

    def put_translation(self, link: OnshapeLink, part_id: str, translation_id: str) -> None:
        self._ensure()
        write_atomic(self.meta / f"translation_{link.key}_{safe_token(part_id)}.json",
                     json.dumps({"id": translation_id}).encode("utf-8"))

    def forget_translation(self, link: OnshapeLink, part_id: str) -> None:
        path = self.meta / f"translation_{link.key}_{safe_token(part_id)}.json"
        if path.exists():
            path.unlink()
