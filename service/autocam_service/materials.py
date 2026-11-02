"""Material comes from Onshape, mapped to our materials by config (decision 11).

Unmapped or missing material sends the part card to Needs fixing. The `Smoked` label turns
clear polycarbonate into smoked (Onshape can't tell them apart). Cards with a .step file instead
of an Onshape link name the material on a `Material:` line.

A `Material:` line also picks a material Onshape has no name of its own for, from the same family as the part's
Onshape material: SRPP is set to Polycarbonate in Onshape, and `Material: SRPP Twill` says which sheet (2026-11-02).
It never overrides between two materials Onshape names itself (6061 and 5052).
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


def _kinds(hint: str, materials: Mapping[str, Material]) -> str:
    """For a Material: line naming only a material that comes in several kinds ("SRPP"): which to write."""
    want = " ".join(hint.lower().split())
    kinds = [f"{m.name} {m.color}" for m in materials.values() if m.name.lower() == want and m.color]
    return f" (say which: {', '.join(kinds)})" if len(kinds) > 1 else ""


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
        if hint:
            picked = _by_hint(hint, materials)
            named = set(material_map.values())
            if picked is not None and picked != key and picked not in named \
                    and materials[picked].family == materials[key].family and materials[picked].color != "smoked":
                if smoked:
                    return MaterialChoice(None, f"the Smoked label and `Material: {hint}` both say which sheet; "
                                                "keep one")
                return MaterialChoice(picked)
            if picked is None and _kinds(hint, materials):
                return MaterialChoice(None, f"`Material: {hint}` comes in more than one kind{_kinds(hint, materials)}")
    elif hint:
        key = _by_hint(hint, materials)
        if key is None:
            names = ", ".join(sorted({m.name for m in materials.values()}))
            return MaterialChoice(None, f"`Material: {hint}` isn't one we cut ({names}){_kinds(hint, materials)}")
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
