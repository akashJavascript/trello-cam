"""SRPP (2026-11-02, the user): three kinds (Twill, MAXComposite, SWYFT), set to Polycarbonate in Onshape and cut
exactly like polycarbonate. A card names the kind on a Material: line; the panel's Sheet choice writes it."""

import json

from autocam_service.config import load_config
from test_panel import Panel, Q, card_in

CFG = load_config()


def test_srpp_is_cut_like_polycarbonate():
    for key in ("srpp_twill", "srpp_maxcomposite", "srpp_swyft"):
        m = CFG.materials[key]
        assert m.family == "polycarbonate" and m.thicknesses_in == (0.125, 0.1875, 0.25) and not m.use_mist
        assert key not in CFG.onshape.material_map.values()            # Onshape only ever says Polycarbonate
    assert dict(CFG.costs.sheet_prices["srpp_twill"]) == {0.125: 149.99, 0.1875: 199.99, 0.25: 249.99}


def test_the_panel_offers_each_kind_for_a_polycarbonate_part(tmp_path):
    p = Panel(tmp_path)
    kinds = {m["value"]: m for m in p.call("GET", "/api/studio", Q)[1]["materials"]}
    assert kinds["srpp_twill"]["label"] == "SRPP Twill" and kinds["srpp_twill"]["line"]
    assert kinds["pc_clear"]["label"] == "Polycarbonate, clear" and not kinds["pc_clear"]["line"]
    assert kinds["pc_smoked"]["smoked"] and not kinds["pc_smoked"]["line"]


def test_a_polycarbonate_part_sent_as_srpp_gets_its_material_line(tmp_path):
    p = Panel(tmp_path)
    status, data = p.send(p="JHK", sheet="srpp_twill", drafts=True)    # P-2016 plate: Polycarbonate in Onshape
    assert status == 200, data
    assert data["material"] == {"setInOnshape": False, "why": "", "line": "SRPP Twill", "missed": False}
    [card] = card_in(p, "inbox")
    assert "Material: SRPP Twill" in card.desc and "Smoked" not in card.labels
    form = p.call("GET", "/api/part", {**Q, "p": "JHK"})[1]["card"]["form"]
    assert form["sheet"] == "srpp_twill" and not form["smoked"]           # made over, it starts as SRPP Twill


def test_the_sheet_choice_can_be_smoked_or_onshapes_own(tmp_path):
    p = Panel(tmp_path)
    assert p.send(p="JHK", sheet="pc_smoked", drafts=True)[0] == 200
    [card] = [c for c in card_in(p, "inbox") if c.name.startswith("P-2016")]
    assert "Smoked" in card.labels and "Material:" not in card.desc
    assert p.call("GET", "/api/part", {**Q, "p": "JHK"})[1]["card"]["form"]["sheet"] == "pc_smoked"


def test_a_sheet_that_isnt_onshapes_material_is_ignored(tmp_path):
    p = Panel(tmp_path)
    assert p.send(p="JHD", sheet="srpp_twill")[0] == 200                # P-2014 is 6061 in Onshape
    [card] = card_in(p, "ready_for_cam")
    assert "Material:" not in card.desc


def test_srpp_picked_for_a_part_with_no_material_is_set_to_polycarbonate_and_named_on_the_card(tmp_path):
    p = Panel(tmp_path)
    p.call("GET", "/api/studio", Q)
    status, data = p.send(p="JHG", material="srpp_swyft")
    assert status == 200 and data["material"]["setInOnshape"] and data["material"]["line"] == "SRPP SWYFT"
    assert not data["material"]["missed"]
    [(_, _, _, body)] = [s for s in p.transport.sent if s[0] == "POST"]
    assert json.loads(body)["properties"][0]["value"]["displayName"] == "Polycarbonate"
    [card] = card_in(p, "ready_for_cam")
    assert "Material: SRPP SWYFT" in card.desc


# ---- each material's own sheet size (MAXComposite 47 x 23, SWYFT 24 x 24)

def test_maxcomposite_and_swyft_have_their_own_sheet_size():
    assert (CFG.sheet_for("srpp_swyft").length_in, CFG.sheet_for("srpp_swyft").width_in) == (24.0, 24.0)
    assert (CFG.sheet_for("srpp_maxcomposite").length_in, CFG.sheet_for("srpp_maxcomposite").width_in) == (47.0, 23.0)
    assert CFG.sheet_for("srpp_twill") == CFG.sheet and CFG.sheet_for("al6061") == CFG.sheet == CFG.sheet_for(None)


def test_a_sheet_size_needs_both_sides_and_the_width_is_the_short_one():
    import copy
    import pytest
    from autocam_service.config import ConfigError, parse_config
    from test_config import RAW, REPO_ROOT
    for sizes, why in (({"sheet_length_in": 24.0}, "give both"),
                       ({"sheet_length_in": 20.0, "sheet_width_in": 30.0}, "the short side")):
        data = copy.deepcopy(RAW)
        data["materials"]["srpp_twill"].update(sizes)
        with pytest.raises(ConfigError, match=why):
            parse_config(data, root=REPO_ROOT)


def test_the_panel_prices_a_part_by_its_own_sheet():
    from autocam_service.panel.checks import use
    swyft = use(CFG, "srpp_swyft", 0.125, {"outline_area_in2": 57.6})
    assert abs(swyft["sheet_share"] - 57.6 / CFG.costs.nest_efficiency / 576.0) < 1e-9
    assert swyft["each"] == round(79.99 * swyft["sheet_share"], 2)
    twill = use(CFG, "srpp_twill", 0.125, {"outline_area_in2": 57.6})
    assert abs(twill["sheet_share"] - swyft["sheet_share"] / 2) < 1e-9


def test_a_hand_made_swyft_offcut_is_at_most_24_in_long():
    from autocam_service.materials import resolve_material
    from autocam_service.offcuts import parse_scrap

    def resolve(hint):
        c = resolve_material(None, hint, False, CFG.onshape.material_map, CFG.materials)
        return c.key, c.problem or ""
    length = lambda key: CFG.sheet_for(key).length_in                        # noqa: E731
    assert parse_scrap("Material: SRPP SWYFT\nThickness: 1/8\nLength: 20", CFG.materials, length, resolve) == \
        ("srpp_swyft", 0.125, 20.0)
    assert "up to 24" in parse_scrap("Material: SRPP SWYFT\nThickness: 1/8\nLength: 30", CFG.materials, length, resolve)
    assert parse_scrap("Material: SRPP Twill\nThickness: 1/8\nLength: 30", CFG.materials, length, resolve)[2] == 30.0


def test_the_season_tally_counts_each_new_sheet_by_its_own_area():
    from datetime import datetime, timezone
    from autocam_service import tally
    cut = [tally.CutSheet("SRPP SWYFT 1/8in", False, 288.0, sheet_area_in2=576.0),
           tally.CutSheet("6061 1/8in", False, 576.0)]
    text = tally.season_text(cut, datetime(2026, 9, 1, tzinfo=timezone.utc), 1152.0, 0)
    assert "864 sq in, 50% of the new sheets" in text                         # 864 of 576 + 1152
