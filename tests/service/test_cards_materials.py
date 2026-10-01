from pathlib import Path

import pytest

from autocam_core.tapguard import GuardSpec, check_program
from autocam_service.batching import ReadyPart, make_batches
from autocam_service.cards import CardProblem, PartRequest, parse_card
from autocam_service.config import load_config
from autocam_service.materials import resolve_material
from autocam_service.onshape.urls import LinkError, first_onshape_url, parse_link
from autocam_service.tracker.base import Attachment, AutomationForbidden, Card, UploadRefused
from autocam_service.tracker.dryrun import DryRunTracker
from autocam_service.tracker.fake import FakeTracker

CFG = load_config()
D, V, W, E = "a" * 24, "b" * 24, "c" * 24, "d" * 24
VERSION_URL = f"https://cad.onshape.com/documents/{D}/v/{V}/e/{E}"
REPO = Path(__file__).resolve().parents[2]


def card(desc="", name="hood_gusset", labels=(), attachments=()):
    return Card("c1", name, desc, "ready_for_cam", "https://trello.com/c/c1", tuple(labels), tuple(attachments))


# ---- links

def test_version_link_parses():
    link = parse_link(VERSION_URL)
    assert (link.host, link.did, link.vid, link.eid) == ("cad.onshape.com", D, V, E)
    assert link.url == VERSION_URL and link.key == f"{D}_{V}_{E}"
    assert parse_link(f"https://team5940.onshape.com/documents/{D}/v/{V}/e/{E}?renderMode=0").host == "team5940.onshape.com"


@pytest.mark.parametrize("url, why", [
    (f"https://cad.onshape.com/documents/{D}/w/{W}/e/{E}", "workspace"),
    (f"https://cad.onshape.com/documents/{D}/m/{W}/e/{E}", "microversion"),
    (f"https://cad.onshape.com/documents/{D}/v/{V}", "Part Studio tab"),
    (f"http://cad.onshape.com/documents/{D}/v/{V}/e/{E}", "not an Onshape link"),
    (f"https://evil.example.com/documents/{D}/v/{V}/e/{E}", "not an Onshape link"),
])
def test_bad_links_are_rejected(url, why):
    with pytest.raises(LinkError, match=why):
        parse_link(url)


def test_first_url_in_text():
    assert first_onshape_url(f"see ({VERSION_URL}). thanks") == VERSION_URL
    assert first_onshape_url("no link") is None


# ---- cards

def test_good_card():
    req = parse_card(card(f"Qty: 4\n{VERSION_URL}", name="  hood_gusset ", labels=["smoked", "Tool 1/8"]))
    assert isinstance(req, PartRequest)
    assert (req.name, req.qty, req.smoked, req.force_small_tool) == ("hood_gusset", 4, True, True)
    assert req.link.vid == V


@pytest.mark.parametrize("desc, problem", [
    (VERSION_URL, "no `Qty: N` line"),
    (f"Qty: four\n{VERSION_URL}", "`Qty: four` isn't a whole number"),
    (f"Qty: 0\n{VERSION_URL}", "`Qty: 0` isn't a whole number"),
    (f"Qty: 2\nqty: 3\n{VERSION_URL}", "more than one `Qty:` line"),
    (f"Qty: 2\nhttps://cad.onshape.com/documents/{D}/w/{W}/e/{E}", "workspace"),
    ("Qty: 2", "no Onshape version link"),
])
def test_card_problems(desc, problem):
    res = parse_card(card(desc))
    assert isinstance(res, CardProblem)
    assert any(problem in p for p in res.problems), res.problems
    comment = res.comment()
    assert "How a part card should look" in comment and "`Smoked`" in comment


def test_step_attachment_fallback_needs_material():
    step = Attachment("a1", "plate.STEP", "https://trello.example/a1")
    res = parse_card(card("Qty: 1", attachments=[step]))
    assert isinstance(res, CardProblem) and "needs a `Material: ...` line" in res.problems[0]
    req = parse_card(card("Qty: 1\nMaterial: 6061", attachments=[step]))
    assert isinstance(req, PartRequest) and req.step_attachment == step and req.material_hint == "6061"


# ---- materials

@pytest.mark.parametrize("onshape, hint, smoked, key", [
    ("Aluminum - 6061", None, False, "al6061"),
    ("aluminum - 5052", None, False, "al5052"),
    ("Polycarbonate", None, False, "pc_clear"),
    ("Polycarbonate", None, True, "pc_smoked"),
    (None, "6061", False, "al6061"),
    (None, "PC", False, "pc_clear"),
    (None, "pc smoked", False, "pc_smoked"),
    (None, "PC", True, "pc_smoked"),
])
def test_material_resolution(onshape, hint, smoked, key):
    assert resolve_material(onshape, hint, smoked, CFG.onshape.material_map, CFG.materials).key == key


@pytest.mark.parametrize("onshape, hint, smoked, problem", [
    ("Steel - 1018", None, False, "isn't one we cut"),
    (None, None, False, "no material in Onshape"),
    ("Aluminum - 6061", None, True, "only polycarbonate comes smoked"),
    (None, "titanium", False, "isn't one we cut (5052, 6061, PC)"),
])
def test_material_problems(onshape, hint, smoked, problem):
    choice = resolve_material(onshape, hint, smoked, CFG.onshape.material_map, CFG.materials)
    assert choice.key is None and problem in choice.problem


# ---- batching

def test_batches_by_material_with_part_keys():
    def ready(material, name):
        req = parse_card(card(f"Qty: 1\n{VERSION_URL}", name=name))
        return ReadyPart(req, material, Path(f"/cache/{name}.step"), "0" * 64, "JHD")
    batches = make_batches([ready("pc_clear", "a"), ready("al6061", "b"), ready("pc_clear", "c")])
    assert [(b.material_key, [(k, p.request.name) for k, p in b.parts]) for b in batches] == [
        ("al6061", [("p01", "b")]), ("pc_clear", [("p01", "a"), ("p02", "c")])]


# ---- tracker safety rules

def test_nothing_is_ever_moved_or_created_in_ready_to_cut():
    t = FakeTracker([card()])
    with pytest.raises(AutomationForbidden):
        t.move("c1", "ready_to_cut")
    with pytest.raises(AutomationForbidden):
        t.create_card("ready_to_cut", "x", "")
    with pytest.raises(AutomationForbidden):
        DryRunTracker(t).move("c1", "ready_to_cut")
    assert t.get_card("c1").list_key == "ready_for_cam"


def test_programs_need_a_passing_guard_report_for_the_same_bytes():
    t = FakeTracker([card()])
    good = (REPO / "fusion/tests/pause_air_test.tap").read_bytes()
    report = check_program(good, GuardSpec())
    assert report.passed
    t.attach_program("c1", "air.tap", good, report)
    with pytest.raises(UploadRefused, match="not the ones the guard checked"):
        t.attach_program("c1", "air.tap", good + b"G0 Z-1\r\n", report)
    bad = good.replace(b"G0 Z3.", b"G0 Z-3.", 1)
    with pytest.raises(UploadRefused, match="guard failed"):
        t.attach_program("c1", "bad.tap", bad, check_program(bad, GuardSpec()))
    with pytest.raises(UploadRefused, match="rejected programs"):
        t.attach_program("c1", "x.REJECTED.tap", good, report)
    with pytest.raises(UploadRefused, match="attach_program"):
        t.attach_file("c1", "sneaky.tap", good, "text/plain")
    assert [name for _, name, _ in t.files.values()] == ["air.tap"]


def test_attachments_over_the_limit_fail_loudly():
    t = FakeTracker([card()], attachment_limit_mb=0.001)
    with pytest.raises(UploadRefused, match="attachment limit"):
        t.attach_file("c1", "nest.png", b"x" * 2000, "image/png")


def test_dry_run_records_writes_and_sends_none():
    inner = FakeTracker([card()])
    dry = DryRunTracker(inner)
    dry.comment("c1", "hello")
    dry.move("c1", "nested")
    new = dry.create_card("sheet_review", "S1", "")
    assert new.startswith("dryrun-")
    assert inner.comments == [] and inner.get_card("c1").list_key == "ready_for_cam"
    assert [i[0] for i in dry.intended] == ["comment", "move", "create_card"]
