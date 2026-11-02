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
