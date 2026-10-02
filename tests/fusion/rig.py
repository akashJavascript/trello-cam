"""A config, a fake Fusion and job building for the pipeline and worker tests."""

import copy
import json
import sys
from dataclasses import replace

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from autocam_core.hotfolder import Queue
from autocam_service.batching import Batch, ReadyPart
from autocam_service.cards import PartRequest
from autocam_service.config import DEFAULT_CONFIG, REPO_ROOT, parse_config
from autocam_service.jobs import build_job
from autocam_service.onshape.cache import sha256_file
from autocam_service.results import ingest
from autocam_service.tracker.base import Card
from autocam_worker.pipeline import process_job
from fakeadapter import FakeAdapter
from geombuilder import PlateBuilder

RAW = tomllib.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
POST = REPO_ROOT / "fusion" / "posts" / "shopsabre_automatic_mist.cps"
OPS = ("[drill] holes", "[bore] holes", "[bearing] holes", "[inner] cutouts", "[outer] outline")


def plate(t=0.125, holes=(), name="plate"):
    b = PlateBuilder(name, thickness=t)
    for i, d in enumerate(holes):
        b.hole(d, center=(1.0 + i, 1.0))
    return b.build()


class Rig:
    def __init__(self, tmp_path, ops=OPS, guid_for=None, **nest):
        self.tmp = tmp_path
        data = copy.deepcopy(RAW)
        for key, t in data["templates"].items():
            guid = (guid_for or {}).get(key, data["tools"][t["tool"]]["guid"])
            f = tmp_path / "templates" / f"{key}.f3dhsm-template"
            f.parent.mkdir(exist_ok=True)
            f.write_text(json.dumps([[name, guid] for name in ops]))
            t["file"] = str(f)
        data["paths"] = {k: str(tmp_path / k) for k in ("queue", "cache", "state", "logs")}
        data["nest"].update(nest)
        self.cfg = parse_config(data, root=REPO_ROOT)
        self.fake = FakeAdapter()
        self.out = tmp_path / "out"

    def job(self, parts, material="al6061"):
        """parts: (name, qty, geometry, (w, h))"""
        ready = []
        for i, (name, qty, geometry, size) in enumerate(parts, 1):
            step = self.tmp / "steps" / f"{name}.step"
            step.parent.mkdir(exist_ok=True)
            step.write_bytes(f"ISO-10303-21; {name}".encode())
            self.fake.register(step, geometry, size)
            card = Card(f"local-{i}", name, "", None, "")
            ready.append(ReadyPart(PartRequest(card, name, qty, None, None, material, False, False),
                                   material, step, sha256_file(step)))
        job = build_job(self.cfg, Batch(material, tuple((f"p{i:02d}", r) for i, r in enumerate(ready, 1))),
                        "r001", "2026-10-01T00:00:00Z")
        return replace(job, post=replace(job.post, path=str(POST)))   # from WSL, build_job writes C:/...

    def run(self, job):
        return process_job(job, self.fake, self.out, now=lambda: "2026-10-01T00:00:00+00:00")

    def service_view(self, job, result):
        """What the service makes of the output: the same checks it runs before anything reaches Trello."""
        queue = Queue(self.tmp / "queue").ensure()
        (queue.done / job.job_id).parent.mkdir(parents=True, exist_ok=True)
        self.out.rename(queue.done / job.job_id)
        return ingest(queue, job.job_id, job)
