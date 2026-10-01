"""Smoke-run the M1.0 Fusion scripts against a fake adsk (Python mistakes only; not proof they work in Fusion)."""

import importlib.util
import json
from pathlib import Path

import pytest

import fakeadsk

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def fake():
    adsk, messages = fakeadsk.install()
    yield adsk, messages
    fakeadsk.uninstall()


def load(rel):
    path = REPO / rel
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dump_params_collects_everything(fake):
    adsk, _ = fake
    mod = load("fusion/tools/dump_params/dump_params.py")
    data = mod.dump(adsk.core.Application.get())
    json.dumps(data)  # must be serializable
    assert data["errors"] == []
    assert data["fusion_version"] == "2.0.99999"
    ops = data["setups"][0]["operations"]
    assert [o["name"] for o in ops] == ["[outer] contour", "[drill] holes"]
    assert ops[0]["tool"]["guid"] == "7b77ef53-1ace-4e3b-ac2d-380b01a638bd"
    assert ops[0]["parameters"][1]["choices"]
    assert data["setups"][0]["parameters"][0]["name"] == "wcs_origin_boxPoint"
    assert data["nc_programs"][0]["post_parameters"][0]["name"] == "useMist"
    assert data["posts"][0]["description"] == "ShopSabre with automatic mist"


def test_dump_params_records_errors_instead_of_stopping(fake):
    adsk, _ = fake
    mod = load("fusion/tools/dump_params/dump_params.py")
    app = adsk.core.Application.get()
    app.activeDocument.products.itemByProductType = lambda name: None
    data = mod.dump(app)
    assert any("no Manufacture data" in e for e in data["errors"])


def test_api_probe_reports_what_exists(fake):
    mod = load("fusion/tools/api_probe/api_probe.py")
    data = mod.probe()
    json.dumps(data)
    setup = data["classes"]["adsk.cam.Setup"]
    assert setup["exists"] and setup["wanted"]["stockSolids"] and not setup["wanted"]["fixtures"]
    assert data["classes"]["adsk.cam.CAMTemplate"] == {"exists": False}
    assert data["enums"]["adsk.cam.LoopTypes"] == {"AllLoops": 2, "OnlyInsideLoops": 1, "OnlyOutsideLoops": 0}
    assert data["search"]["Arrange"]["fusion"] == ["ArrangeSolverTypes"]
