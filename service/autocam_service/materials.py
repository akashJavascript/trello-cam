"""Material comes from Onshape, mapped to our materials by config (decision 11).

Unmapped or missing material sends the part card to Needs fixing. The `Smoked` label turns
clear polycarbonate into smoked (Onshape can't tell them apart). Cards with a .step file instead
of an Onshape link name the material on a `Material:` line.
"""

from dataclasses import dataclass
from typing import Mapping, Optional

from .config import Material


@dataclass(frozen=True)
class MaterialChoice:
    key: Optional[str]
    problem: Optional[str] = None


def _by_hint(hint: str, materials: Mapping[str, Material]) -> Optional[str]:
    want = " ".join(hint.lower().split())
    for key, m in materials.items():
        names = {key.lower(), m.name.lower(), f"{m.name} {m.color}".strip().lower()}
        if want in names:
            # "PC" alone means clear; the Smoked label picks smoked.
            if m.color and want == m.name.lower() and m.color != "clear":
                continue
            return key
    return None


def resolve_material(onshape_material: Optional[str], hint: Optional[str], smoked: bool,
                     material_map: Mapping[str, str], materials: Mapping[str, Material]) -> MaterialChoice:
    if onshape_material:
        key = material_map.get(onshape_material)
        if key is None:
            lowered = {k.lower(): v for k, v in material_map.items()}
            key = lowered.get(onshape_material.lower())
        if key is None:
            return MaterialChoice(None, f"Onshape material '{onshape_material}' isn't one we cut "
                                        "(not in config onshape.material_map)")
    elif hint:
        key = _by_hint(hint, materials)
        if key is None:
            names = ", ".join(sorted({m.name for m in materials.values()}))
            return MaterialChoice(None, f"`Material: {hint}` isn't one we cut ({names})")
    else:
        return MaterialChoice(None, "the part has no material in Onshape; assign one in the Part Studio "
                                    "(right-click the part > Assign material)")
    if smoked:
        base = materials[key]
        if base.family != "polycarbonate":
            return MaterialChoice(None, f"the Smoked label is on a {base.name} part; only polycarbonate comes smoked")
        smoked_keys = [k for k, m in materials.items()
                       if m.family == base.family and m.name == base.name and m.color == "smoked"]
        if not smoked_keys:
            return MaterialChoice(None, "no smoked polycarbonate is configured")
        key = smoked_keys[0]
    return MaterialChoice(key)
