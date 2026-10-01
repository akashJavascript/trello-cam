import copy
import sys

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from autocam_service.config import (
    DEFAULT_CONFIG, REPO_ROOT, ConfigError, load_config, parse_config, post_property_names,
)

RAW = tomllib.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
ONSRUD_4MM = "423977fe-8784-4556-8207-c2279ed05597"
POLY_4MM = "ec14e4ef-aa38-4a96-af26-8885accae4a3"


def errors_for(mutate):
    data = copy.deepcopy(RAW)
    mutate(data)
    with pytest.raises(ConfigError) as exc:
        parse_config(data, root=REPO_ROOT)
    return exc.value.errors


def test_real_config_loads():
    cfg = load_config()
    assert len(cfg.stock_types()) == 14
    assert [t.key for t in cfg.tools.values() if t.available] == ["t1_4mm_alu", "t1_4mm_poly", "t12_eighth_alu"]
    assert not cfg.tools["eighth_poly"].available
    assert cfg.machine.z_floor_in == 0.0
    assert "ready_to_cut" not in cfg.trello.targets.values()


def test_placeholders_and_warnings_are_reported():
    cfg = load_config()
    assert {"pauses.resume_key", "tools.eighth_poly.guid", "trello.lists.ready_to_cut",
            "fusion_team.project"} <= set(cfg.placeholders)
    assert not any(p.endswith(".color") for p in cfg.placeholders)
    assert any("eighth_poly" in w for w in cfg.warnings)


def test_post_property_names_match_the_post():
    text = (REPO_ROOT / "fusion/posts/shopsabre_automatic_mist.cps").read_text(encoding="utf-8")
    assert post_property_names(text) == {
        "showSequenceNumbers", "sequenceNumberStart", "sequenceNumberIncrement", "separateWordsWithSpace",
        "useToolCall", "useTappingCycle", "rotaryTableAxis", "useCoolant", "useXYZFeeds",
        "safePositionMethod", "useMist", "writeMachine", "writeTools"}


def _set(*path_and_value):
    *path, value = path_and_value

    def mutate(d):
        for key in path[:-1]:
            d = d[key]
        d[path[-1]] = value
    return mutate


def _delete(*path):
    def mutate(d):
        for key in path[:-1]:
            d = d[key]
        del d[path[-1]]
    return mutate


def _append(*path_and_value):
    *path, value = path_and_value

    def mutate(d):
        for key in path:
            d = d[key]
        d.append(value)
    return mutate


def _remove(*path_and_value):
    *path, value = path_and_value

    def mutate(d):
        for key in path:
            d = d[key]
        d.remove(value)
    return mutate


def _same_list_ids(d):
    d["trello"]["lists"]["nested"] = "a" * 24
    d["trello"]["lists"]["ready_to_cut"] = "a" * 24


BAD_VARIANTS = [
    # safety
    ("z_floor_below_zero", _set("machine", "z_floor_in", -0.01), "machine.z_floor_in: must be >= 0"),
    ("clamp_clearance_too_low", _set("clamps", "min_clear_above_stock_in", 1.0), "must be above clamps.height_in"),
    ("target_ready_to_cut", _set("trello", "targets", "part_nested", "ready_to_cut"),
     "never moves cards to ready_to_cut"),
    ("target_shares_ready_to_cut_id", _same_list_ids, "same ID as ready_to_cut"),
    ("guard_allows_g91", _append("tapguard", "allowed_g", 91), "G91 can never be allowed"),
    ("guard_allows_g41", _append("tapguard", "allowed_g", 41), "G41 can never be allowed"),
    ("guard_without_g20", _remove("tapguard", "allowed_g", 20), "must include G20"),
    ("guard_without_mist_codes", _remove("tapguard", "allowed_m", 11), "must include M11"),
    ("post_edited", _set("fusion", "post_sha256", "0" * 64), "the post must never be edited"),
    ("post_retract_not_g53", _set("fusion", "post_properties", "safePositionMethod", "clearanceHeight"),
     "safePositionMethod: must be 'G53'"),
    ("post_tool_changer_on", _set("fusion", "post_properties", "useToolCall", True), "useToolCall: must be False"),
    ("mist_set_globally", _set("fusion", "post_properties", "useMist", True), "set per material"),
    # optionalStop is read by the post via getProperty() but never defined, so Fusion can't set it
    ("unknown_post_property", _set("fusion", "post_properties", "optionalStop", True),
     "the post has no such property"),
    ("post_property_left_implicit", _delete("fusion", "post_properties", "useXYZFeeds"),
     "useXYZFeeds: missing; every post property is set explicitly"),
    # tools by GUID only
    ("guid_not_in_library", _set("tools", "t1_4mm_alu", "guid", "00000000-0000-0000-0000-000000000000"),
     "is not in 5940_Tool_Library.tools"),
    ("onsrud_instead_of_alu_4mm", _set("tools", "t1_4mm_alu", "guid", ONSRUD_4MM), "library says T2"),
    ("poly_cutter_as_alu_cutter", _set("tools", "t1_4mm_alu", "guid", POLY_4MM), "two tools share a GUID"),
    ("wrong_number", _set("tools", "t12_eighth_alu", "number", 1), "library says T12"),
    ("wrong_diameter", _set("tools", "t1_4mm_alu", "diameter_in", 0.125), "library says 0.15748"),
    ("bad_guid_format", _set("tools", "t1_4mm_alu", "guid", "T1"), "has the wrong format"),
    ("poly_template_on_t12", _set("templates", "poly_4mm", "tool", "t12_eighth_alu"),
     "polycarbonate never uses t12_eighth_alu"),
    ("default_tool_without_template", _delete("templates", "alu_4mm"), "no template uses t1_4mm_alu"),
    # structure and types
    ("unknown_key", _set("machine", "z_flor_in", 0.0), "machine.z_flor_in: unknown key"),
    ("unknown_section", _set("extras", {}), "extras: unknown key"),
    ("missing_key", _delete("nest", "part_spacing_in"), "nest.part_spacing_in: missing"),
    ("float_for_int", _set("nest", "max_sheets_per_group", 2.5), "expected an integer"),
    ("bool_for_number", _set("machine", "reach_x_in", True), "expected a number"),
    ("schema_mismatch", _set("schema", 2), "this code reads schema 1"),
    ("assumed_key_missing", _set("assumed", "clamps.reach_mm", "typo"), "assumed.clamps.reach_mm: no such key"),
    # shop rules
    ("poly_without_color", _delete("materials", "pc_clear", "color"), "polycarbonate needs a color"),
    ("unsafe_program_prefix", _set("materials", "al6061", "program_prefix", "6061 1/8"), "has the wrong format"),
    ("drill_size_in_bore_range", _set("holes", "drill_sizes_in", "t1_4mm_alu", [0.156, 0.2]),
     "must stay below the bore range"),
    ("thickness_tol_too_loose", _set("plate", "thickness_tol_in", 0.04), "under half the smallest gap"),
    ("spacing_below_tool", _set("nest", "part_spacing_in", 0.1), "must exceed the largest tool diameter"),
    ("attachment_over_free_limit", _set("trello", "attachment_limit_mb", 25), "caps attachments at 10 MB"),
    ("bad_trello_id", _set("trello", "lists", "inbox", "nope"), "has the wrong format"),
    ("material_map_unknown", _set("onshape", "material_map", "Steel", "steel"), "no material 'steel'"),
    ("http_base_url", _set("onshape", "base_url", "http://cad.onshape.com"), "has the wrong format"),
]


@pytest.mark.parametrize("mutate, expected", [v[1:] for v in BAD_VARIANTS], ids=[v[0] for v in BAD_VARIANTS])
def test_bad_variant_is_rejected(mutate, expected):
    errors = errors_for(mutate)
    assert any(expected in e for e in errors), errors


def test_all_errors_are_reported_together():
    def mutate(d):
        d["machine"]["z_floor_in"] = -1.0
        d["trello"]["targets"]["sheet_created"] = "ready_to_cut"
        d["tapguard"]["allowed_g"].append(92)
    errors = errors_for(mutate)
    assert len(errors) >= 3, errors
