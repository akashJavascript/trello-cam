"""The shop's 3D print queue (prints.py, 2026-11-09; the mockup the user approved): printed parts sent from the Onshape
panel or added from a file, on the shop page's Prints tab (Waiting, Printing, Done). A part's model file is a STEP
exported from Onshape the first time someone downloads it (the user: "export step files not stl")."""

import base64
from datetime import datetime, timedelta

import pytest

from autocam_service.panel.api import shop_key
from autocam_service.people import People
from autocam_service.prints import PrintList, print_material
from autocam_service.shoplist import ListError
from fakeonshape import FakeTransport, resp
from test_panel import DEFINITION, ELEMENTS, KEY, PARTS, Panel, Q

SHOP = shop_key(KEY)
PHONE = "phone-aaaa"
OTHER = "phone-bbbb"
PRINTERS = ("Bartholomew", "Bilbo Baggins", "Darcy", "Ivy", "Oven", "Tandoori")
PLA_PART = {"name": "Spline XL spacer", "partId": "JPL", "bodyType": "solid", "material": {"displayName": "PLA"}}


class Now:
    def __init__(self):
        self.t = datetime(2026, 11, 9, 14, 25)

    def __call__(self):
        return self.t


def queue(tmp_path, now=None):
    return PrintList(tmp_path / "prints.json", tmp_path / "print_files", printers=PRINTERS, now=now or Now())


# ---------------------------------------------------------------- the list

def test_a_print_waits_then_prints_then_is_done(tmp_path):
    now = Now()
    q = queue(tmp_path, now)
    [x] = q.add([{"name": "Spline XL spacer", "qty": 8, "material": "PLA", "color": "Orange", "note": "Spline face down",
                  "source": "d|e|JPL|", "sender": "Akash"}])
    q.start(x["id"], PHONE, "Darcy", 135)                        # 2 h 15 m from 2:25 PM
    [p] = q.shop_view(PHONE)["prints"]
    assert (p["status"], p["printer"], p["finish"], p["mine"]) == ("printing", "Darcy", "4:40 PM", True)
    assert q.shop_view(OTHER)["prints"][0]["mine"] is False
    q.done(x["id"], PHONE)
    view = q.shop_view(PHONE)
    assert view["prints"] == [] and [d["name"] for d in view["printsDone"]] == ["Spline XL spacer"]
    assert view["printsDone"][0]["undo"] and not q.shop_view(OTHER)["printsDone"][0]["undo"]
    q.undo(x["id"], PHONE)                                       # done by mistake: printing again
    assert q.shop_view(PHONE)["prints"][0]["status"] == "printing"


def test_printer_and_print_time_are_optional_and_checked(tmp_path):
    q = queue(tmp_path)
    [a, b] = q.add([{"name": "A", "qty": 1}, {"name": "B", "qty": 1}])
    q.start(a["id"], PHONE)                                      # neither
    assert q.shop_view(PHONE)["prints"][0]["printer"] == "" and q.shop_view(PHONE)["prints"][0]["finish"] == ""
    with pytest.raises(ListError):
        q.start(b["id"], PHONE, "The Prusa", 30)                  # not one of the team's printers
    with pytest.raises(ListError):
        q.start(b["id"], PHONE, "Ivy", 8 * 24 * 60)               # over a week: a typo
    with pytest.raises(ListError) as e:
        q.start(a["id"], OTHER, "Ivy", 30)                        # already printing
    assert e.value.status == 409


def test_a_failed_print_goes_back_to_wait_counted(tmp_path):
    q = queue(tmp_path)
    [x] = q.add([{"name": "Hex Spacer", "qty": 6}])
    for n in (1, 2):
        q.start(x["id"], PHONE, "Oven", 60)
        q.failed(x["id"], PHONE)
        [p] = q.shop_view(PHONE)["prints"]
        assert (p["status"], p["fails"], p["printer"], p["finish"]) == ("waiting", n, "", "")


def test_the_same_part_isnt_queued_twice_while_it_waits_or_prints(tmp_path):
    q = queue(tmp_path)
    [x] = q.add([{"name": "Spline XL spacer", "qty": 8, "source": "d|e|JPL|"}])
    with pytest.raises(ListError) as e:
        q.add([{"name": "Spline XL spacer", "qty": 2, "source": "d|e|JPL|"}])
    assert e.value.status == 409
    q.done(x["id"], PHONE)
    q.add([{"name": "Spline XL spacer", "qty": 2, "source": "d|e|JPL|"}])     # printed: more can be sent


def test_infill_and_walls_are_optional_numbers(tmp_path):
    q = queue(tmp_path)
    [x] = q.add([{"name": "A", "qty": 1, "infill": "40", "walls": 4}, ])
    assert (x["infill"], x["walls"]) == (40, 4)
    [y] = q.add([{"name": "B", "qty": 1, "infill": "", "walls": None}])
    assert (y["infill"], y["walls"]) == (None, None)
    for bad in ({"infill": 120}, {"walls": 0}, {"infill": "lots"}):
        with pytest.raises(ListError):
            q.add([{"name": "C", "qty": 1, **bad}])


def test_a_file_added_on_the_page_is_kept_and_named_for_download(tmp_path):
    q = queue(tmp_path)
    x = q.add_file(b"solid camera-mount", "camera-mount v3.stl", {"qty": 1, "material": "PETG"})
    assert x["name"] == "camera-mount v3" and q.file_of(x["id"]).read_bytes() == b"solid camera-mount"
    assert q.download_name(q.get(x["id"])) == "camera-mount v3.stl"
    with pytest.raises(ListError):
        q.add_file(b"x", "notes.txt", {"qty": 1})


def test_done_prints_and_their_files_drop_off_after_keep_days(tmp_path):
    now = Now()
    q = queue(tmp_path, now)
    x = q.add_file(b"solid a", "a.stl", {"qty": 1})
    q.done(x["id"], PHONE)
    now.t += timedelta(days=15)
    assert q.all() == [] and not (tmp_path / "print_files" / f"{x['id']}.stl").exists()


def test_print_material_reads_onshapes_name():
    mats = ("PLA", "PETG", "TPU 95A", "PLA-CF", "PA-CF", "ASA")
    assert print_material("3D Printed PLA", mats) == "PLA"
    assert print_material("PLA-CF", mats) == "PLA-CF"
    assert print_material("TPU", mats) == "TPU 95A"
    assert print_material("Nylon", mats) == "PLA"                      # none of them: the first


# ---------------------------------------------------------------- the panel and the shop page

def print_panel(tmp_path, extra=()):
    parts = PARTS + [PLA_PART]
    transport = FakeTransport([("GET", "/parts/", [resp(body=parts)] * 5), ("GET", "/elements", [resp(body=ELEMENTS)]),
                               ("GET", "/assemblies/", [resp(body=DEFINITION)]), *extra])
    p = Panel(tmp_path, transport=transport)
    p.app.prints = queue(tmp_path / "state")
    p.app.people = People(tmp_path / "people.json")
    p.onshape.sleep = lambda s: None
    return p


def shop(p, **q):
    status, d = p.call("GET", "/api/shop", q={"device": PHONE, **q}, key=SHOP)
    assert status == 200, d
    return d


def test_a_printed_part_is_sent_from_the_panel_and_shows_on_the_prints_tab(tmp_path):
    p = print_panel(tmp_path)
    status, part = p.call("GET", "/api/part", q={**Q, "p": "JPL"})
    assert status == 200 and part["print"]["printed"] and part["print"]["material"] == "PLA"
    assert part["print"]["queued"] is None
    status, r = p.call("POST", "/api/send-print", body={**Q, "p": "JPL", "qty": 8, "material": "TPU 95A",
                                                       "color": "Neon green", "infill": 40, "walls": 4, "rush": True,
                                                       "note": "Spline face down", "name": "Akash", "u": "user1",
                                                       "server": "https://5940.onshape.com"})
    assert status == 200 and r["print"]["label"] == "Waiting to print"
    d = shop(p)
    assert d["printsOn"] and d["printers"] == list(PRINTERS)
    [x] = d["prints"]
    assert (x["name"], x["qty"], x["material"], x["color"], x["infill"], x["walls"], x["rush"], x["sender"]) == \
        ("Spline XL spacer", 8, "TPU 95A", "Neon green", 40, 4, True, "Akash")      # TPU though Onshape says PLA
    status, again = p.call("POST", "/api/send-print", body={**Q, "p": "JPL", "qty": 2, "material": "PLA",
                                                           "name": "Akash", "u": "user1"})
    assert status == 409 and "already in the print queue" in again["error"]
    status, part = p.call("GET", "/api/part", q={**Q, "p": "JPL", "cached": "1"})
    assert part["print"]["queued"]["active"]


def test_the_prints_tab_buttons(tmp_path):
    p = print_panel(tmp_path)
    [x] = p.app.prints.add([{"name": "Hex Spacer", "qty": 6, "material": "PLA"}])

    def post(action, **body):
        return p.call("POST", "/api/shop/" + action, body={"id": x["id"], "device": PHONE, **body}, key=SHOP)
    status, _ = p.call("POST", "/api/shop/assign", body={"device": PHONE, "kind": "print", "ids": [x["id"]], "who": "Maya"}, key=SHOP)
    assert status == 200 and shop(p)["prints"][0]["who"] == "Maya"
    assert post("print-start", printer="Tandoori", minutes=90)[0] == 200
    assert shop(p)["prints"][0]["printer"] == "Tandoori"
    assert post("print-failed")[0] == 200 and shop(p)["prints"][0]["fails"] == 1
    assert post("print-start")[0] == 200 and post("print-done")[0] == 200
    assert shop(p)["printsDone"][0]["name"] == "Hex Spacer"
    status, r = post("print-start", printer="Bartholomew")
    assert status == 409                                         # done: it can't start again


def test_a_file_is_added_and_downloaded_on_the_shop_page(tmp_path):
    p = print_panel(tmp_path)
    data = base64.b64encode(b"solid mount\nendsolid").decode()
    status, r = p.call("POST", "/api/shop/print-upload", body={"device": PHONE, "filename": "mount.stl", "data": data,
                                                               "qty": 2, "material": "PETG", "color": "Black"}, key=SHOP)
    assert status == 200
    [x] = shop(p)["prints"]
    assert x["name"] == "mount" and x["upload"] and x["file"]
    status, body = p.call("GET", "/api/shop/print-file", q={"id": r["id"], "key": SHOP}, key="")
    assert status == 200 and body == b"solid mount\nendsolid"
    status, r = p.call("POST", "/api/shop/print-upload", body={"device": PHONE, "filename": "x.txt", "data": data}, key=SHOP)
    assert status == 400 and "STEP, STL or 3MF" in r["error"]


def test_a_parts_step_file_is_exported_from_onshape_the_first_time(tmp_path):
    step = b"ISO-10303-21; spline spacer"
    p = print_panel(tmp_path, extra=[
        ("POST", "/translations", [resp(body={"id": "tr1"})]),
        ("GET", "/translations/tr1", [resp(body={"requestState": "ACTIVE"}),
                                      resp(body={"requestState": "DONE", "resultExternalDataIds": ["x1"]})]),
        ("GET", "/externaldata/x1", [resp(body=step)])])
    p.call("POST", "/api/send-print", body={**Q, "p": "JPL", "qty": 8, "material": "PLA", "name": "Akash", "u": "user1"})
    [x] = shop(p)["prints"]
    assert x["file"] and not x["upload"]
    status, body = p.call("GET", "/api/shop/print-file", q={"id": x["id"], "key": SHOP}, key="")
    assert status == 200 and body == step
    calls = len(p.transport.sent)
    status, body = p.call("GET", "/api/shop/print-file", q={"id": x["id"], "key": SHOP}, key="")
    assert status == 200 and body == step and len(p.transport.sent) == calls       # kept: no more Onshape calls
    assert p.app.prints.file_of(x["id"]).suffix == ".step"


def test_while_onshape_is_still_making_the_step_the_page_is_told_to_wait(tmp_path):
    p = print_panel(tmp_path, extra=[
        ("POST", "/translations", [resp(body={"id": "tr1"})]),
        ("GET", "/translations/tr1", [resp(body={"requestState": "ACTIVE"})] * 30)])
    p.call("POST", "/api/send-print", body={**Q, "p": "JPL", "qty": 1, "material": "PLA", "name": "Akash", "u": "user1"})
    [x] = shop(p)["prints"]
    status, r = p.call("GET", "/api/shop/print-file", q={"id": x["id"], "key": SHOP}, key="")
    assert status == 202 and r["kind"] == "wait"
    assert p.app.prints.get(x["id"])["translation"] == "tr1"     # the next download picks it up, no new export


def test_an_assemblys_printed_parts_go_to_the_print_queue(tmp_path):
    from test_panel_more import assembly_panel
    from test_panel import Q as AQ
    p = assembly_panel(tmp_path)
    p.app.prints = queue(tmp_path / "state")
    status, a = p.call("GET", "/api/assembly", q=AQ)
    assert status == 200
    assert "P-2016 spacer" not in [i["name"] for i in a["items"]]          # not a plate to cut
    [pr] = a["prints"]
    assert (pr["name"], pr["qty"], pr["material"], pr["sendable"]) == ("P-2016 spacer", 4, "PLA", True)
    status, r = p.call("POST", "/api/send-many", body={**AQ, "items": [], "prints": [{"key": pr["key"], "qty": 4}],
                                                      "name": "Akash", "u": "user1", "rush": True})
    assert status == 200 and [x for x in r["results"] if x.get("print")] == [
        {"key": pr["key"], "name": "P-2016 spacer", "sent": True, "print": True}]
    [x] = p.app.prints.shop_view(PHONE)["prints"]
    assert (x["name"], x["qty"], x["material"], x["color"], x["rush"], x["assembly"]) == \
        ("P-2016 spacer", 4, "PLA", "Any", True, "Intake")
    status, a = p.call("GET", "/api/assembly", q={**AQ, "cached": "1"})
    assert a["prints"][0]["queued"]["label"] == "Waiting to print" and not a["prints"][0]["sendable"]
