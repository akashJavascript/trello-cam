"""Build job.json for one batch: everything Fusion needs, copied out of config (Fusion never reads it)."""

import copy
import os
import re
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import List, Optional, Sequence

from autocam_core import CORE_VERSION, fixture
from autocam_core.holes import HoleRules
from autocam_core.schema import to_dict
from autocam_core.schema_job import (
    JOB_SCHEMA, FixtureSpec, FusionTeamSpec, GuardSettings, Job, MaterialSpec, NestSpec, OnshapeRef, PartSpec,
    PauseSettings, PlateSpec, PostSpec, SheetSpec, ToolingSpec, ToolSpec, load_job,
)

from .batching import Batch
from .config import Config, Tool

_WSL_MOUNT = re.compile(r"^/mnt/([a-zA-Z])(/.*)?$")


class JobBuildError(Exception):
    pass


def fusion_path(path: Path) -> str:
    """A path Fusion (a Windows app) can open: C:/... on Windows; /mnt/c/... from WSL becomes C:/...."""
    if os.name == "nt":
        return Path(path).resolve().as_posix()
    posix = PurePosixPath(Path(path).resolve()).as_posix()
    m = _WSL_MOUNT.match(posix)
    return f"{m.group(1).upper()}:{m.group(2) or '/'}" if m else posix


def _tool_spec(cfg: Config, tool: Tool) -> Optional[ToolSpec]:
    template = next((t for t in cfg.templates.values() if t.tool == tool.key), None)
    if not tool.available or template is None or not template.file.is_file():
        return None
    return ToolSpec(key=tool.key, guid=tool.guid, number=tool.number, diameter_in=tool.diameter_in,
                    flute_in=tool.flute_in, min_inside_radius_in=tool.min_inside_radius_in,
                    drill_sizes_in=tuple(cfg.holes.drill_sizes_in[tool.key]), cutter_label=tool.cutter_label,
                    template_key=template.key, template_path=fusion_path(template.file))


def build_job(cfg: Config, batch: Batch, run_id: str, created_utc: str, carried: Sequence[PartSpec] = ()) -> Job:
    """carried: parts already on open sheets of this material (from the jobs that made them), nested again
    with the new ones; they keep everything but get part keys after the new parts'."""
    m = cfg.materials[batch.material_key]
    family = cfg.tooling[m.family]
    default = _tool_spec(cfg, cfg.tools[family.default])
    if default is None:
        raise JobBuildError(f"{m.family}: the default tool {family.default} has no exported template yet")
    small = _tool_spec(cfg, cfg.tools[family.small_features])
    tools = {default.key: default}
    if small is not None:
        tools[small.key] = small

    fx = fixture.build(
        sheet_length_in=cfg.sheet.length_in, sheet_width_in=cfg.sheet.width_in, reach_x_in=cfg.machine.reach_x_in,
        edge_margin_in=cfg.nest.edge_margin_in, reach_margin_in=cfg.nest.reach_margin_in,
        clamp_edges=cfg.clamps.edges, clamp_reach_in=cfg.clamps.reach_in, clamp_clearance_in=cfg.clamps.clearance_in,
        clamp_height_in=cfg.clamps.height_in)

    parts: List[PartSpec] = []
    for key, ready in batch.parts:
        req = ready.request
        link = req.link
        parts.append(PartSpec(
            part_key=key, card_id=req.card.id, card_url=req.card.url, name=req.name, qty=req.qty,
            step=fusion_path(ready.step_path), step_sha256=ready.step_sha256,
            source="onshape" if link else ("local" if req.card.id.startswith("local-") else "trello_attachment"),
            onshape=OnshapeRef(link.did, link.vid, link.eid, ready.onshape_part_id or "", link.url,
                               ready.onshape_microversion) if link else None,
            force_small_tool=req.force_small_tool))
    for n, spec in enumerate(carried, len(parts) + 1):
        parts.append(replace(spec, part_key=f"p{n:02d}"))

    job = Job(
        schema=JOB_SCHEMA, core_version=CORE_VERSION, job_id=f"{run_id}-{batch.material_key}", run_id=run_id,
        created_utc=created_utc,
        material=MaterialSpec(key=m.key, name=m.name, family=m.family, color=m.color,
                              thicknesses_in=tuple(m.thicknesses_in), thickness_tol_in=cfg.plate.thickness_tol_in,
                              use_mist=m.use_mist, program_prefix=m.program_prefix),
        sheet=SheetSpec(cfg.sheet.length_in, cfg.sheet.width_in, cfg.machine.reach_x_in),
        fixture=FixtureSpec(fx.nest_region_in, fx.clamp_zones_in, cfg.clamps.height_in,
                            cfg.clamps.min_clear_above_stock_in),
        nest=NestSpec(cfg.nest.part_spacing_in, cfg.nest.max_sheets_per_group, cfg.nest.rotation,
                      cfg.nest.part_in_part, cfg.nest.envelope_spacing_in, cfg.nest.short_qty),
        tooling=ToolingSpec(default=default.key, small_features=small.key if small else None, tools=tools),
        holes=HoleRules(cfg.holes.drill_tol_in, cfg.holes.bore_min_in, cfg.holes.bore_min_tol_in,
                        cfg.holes.bore_max_in, tuple(cfg.holes.bearing_sizes_in), cfg.holes.bearing_tol_in),
        plate=PlateSpec(cfg.plate.strict_inside_radius),
        pauses=PauseSettings(cfg.pauses.enabled, cfg.pauses.after_last_part, cfg.pauses.mode, cfg.machine.park,
                             cfg.machine.spindle_rpm, cfg.machine.spin_up_dwell_s),
        post=PostSpec(cfg.fusion.post_description, fusion_path(cfg.fusion.post_file), cfg.fusion.post_sha256,
                      {**dict(cfg.fusion.post_properties), "useMist": m.use_mist}, cfg.machine.units),
        guard=GuardSettings(cfg.machine.z_floor_in, tuple(cfg.tapguard.allowed_g), tuple(cfg.tapguard.allowed_m),
                            cfg.tapguard.clamp_margin_in),
        fusion_params=copy.deepcopy(dict(cfg.fusion.params)),
        fusion_team=FusionTeamSpec(cfg.fusion_team.project, cfg.fusion_team.folder),
        parts=tuple(parts),
    )
    return load_job(to_dict(job))  # same validation Fusion will apply
