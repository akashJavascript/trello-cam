"""Onshape's webhook (panel/hooks.py) and part pictures (panel/pictures.py), 2026-11-01 (the user is an enterprise
admin): a model change Onshape reports gets its open sheets checked within minutes; each part on a sheet gets its
picture on its card once."""

import base64
import json

from autocam_service.panel.hooks import EVENT, HOOK_PREFIX, ChangeInbox, WebhookSetup, secret_ok
from autocam_service.panel.pictures import PictureWatch
from autocam_service.panel.precut import PrecutWatch
from fakeonshape import FakeTransport, resp
from onshape_body import plate_body, rectangle
from test_panel import D, E, W, Panel
from test_panel_more import MID, SHEET, precut_panel

SECRET = "s" * 43
CID = "c" * 24
PART = "a" * 24
THEN = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.25)])
NOW = plate_body(rectangle(4, 2), 0.125, [(1, 1, 0.5)])


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def event(did=D, wid=W, eid=E, kind=EVENT):
    return {"event": kind, "documentId": did, "workspaceId": wid, "elementId": eid, "messageId": "m1"}


def test_only_the_secret_address_takes_events_and_only_model_changes_are_kept(tmp_path):
    p = Panel(tmp_path)
    inbox = ChangeInbox(tmp_path / "changes.json")
    p.app.inbox, p.app._hook_secret = inbox, SECRET
    r = p.app.handle("POST", HOOK_PREFIX + SECRET, {}, {}, json.dumps(event()).encode())
    assert r.status == 200 and inbox.quiet(0) == {(D, W, E)}
    assert p.app.handle("POST", HOOK_PREFIX + "wrong", {}, {}, json.dumps(event()).encode()).status == 404
    assert p.app.handle("GET", HOOK_PREFIX + SECRET, {}, {}).status == 404
    ping = {"event": "webhook.ping", "webhookId": "w1"}
    assert p.app.handle("POST", HOOK_PREFIX + SECRET, {}, {}, json.dumps(ping).encode()).status == 200   # Onshape's check
    assert not inbox.note(event(did="not an id")) and not inbox.note(event(kind="onshape.comment.create"))
    assert secret_ok(HOOK_PREFIX + SECRET, SECRET) and not secret_ok(HOOK_PREFIX + SECRET, "")


def test_quiet_means_nobody_changed_it_for_five_minutes(tmp_path):
    clock = Clock()
    inbox = ChangeInbox(tmp_path / "changes.json", clock=clock)
    inbox.note(event())
    clock.t += 200
    inbox.note(event())                                   # still being edited
    clock.t += 200
    assert inbox.quiet() == set()
    clock.t += 200
    assert inbox.quiet() == {(D, W, E)}
    inbox.forget(inbox.quiet())
    assert inbox.quiet(0) == set()


def test_a_reported_change_checks_its_sheet_in_review_right_away(tmp_path):
    p, watch = precut_panel(tmp_path, "8" * 24, THEN, NOW)
    p.t.move(SHEET, "sheet_review")
    clock = Clock(5000.0)
    inbox = ChangeInbox(tmp_path / "changes.json", clock=clock)
    watch = PrecutWatch(p.onshape, p.board, p.h.store, p.t, inbox=inbox)
    assert watch.tick(now=5000.0) == 0 and p.transport.sent == []     # just CAM'd, nothing said: no call
    inbox.note(event(did=D, wid=W, eid=E))
    clock.t += 400
    assert watch.tick(now=5400.0) == 1                                   # not 12 h later: now
    assert "CAD changed" in p.t.get_card(SHEET).labels and inbox.quiet(0) == set()
    calls = len(p.transport.sent)
    inbox.note(event(did="9" * 24, wid=W, eid=E))                        # a document on no sheet: no call
    clock.t += 400
    watch.tick(now=5800.0)
    assert len(p.transport.sent) == calls and inbox.quiet(0) == set()


def test_the_webhook_is_registered_once_for_the_company_and_checked_daily(tmp_path):
    transport = FakeTransport([("GET", "/companies", [resp(body={"items": [{"id": CID, "name": "FRC 5940"}]})]),
                               ("GET", "/webhooks", [resp(body={"items": []}),
                                                     resp(body={"items": [{"id": "w1", "url": "https://x.example"
                                                                           + HOOK_PREFIX + SECRET}]})]),
                               ("POST", "/webhooks", [resp(body={"id": "w1"})])])
    p = Panel(tmp_path, transport=transport)
    clock = Clock()
    hooks = WebhookSetup(p.onshape, "https://x.example/", SECRET, tmp_path / "webhook.json", clock=clock)
    assert hooks.ensure() == "registered" and hooks.active()
    [(_, url, _, body)] = [s for s in transport.sent if s[0] == "POST"]
    sent = json.loads(body)
    assert sent["url"] == "https://x.example" + HOOK_PREFIX + SECRET and sent["companyId"] == CID
    assert sent["events"] == [EVENT] and sent["options"] == {"collapseEvents": True}
    assert hooks.ensure() == "checked recently"
    clock.t += 25 * 3600
    assert hooks.ensure() == "already registered"
    assert [m for m, u, _, _ in transport.sent].count("POST") == 1 and \
        len([u for m, u, _, _ in transport.sent if "/companies" in u]) == 1         # the company asked once


def test_a_webhook_that_cant_be_registered_is_tried_hourly_not_every_tick(tmp_path):
    transport = FakeTransport([("GET", "/companies", [resp(body={"items": [{"id": CID, "name": "FRC 5940"}]})]),
                               ("GET", "/webhooks", [resp(403, body={"message": "not an admin"})] * 2)])
    p = Panel(tmp_path, transport=transport)
    clock = Clock()
    hooks = WebhookSetup(p.onshape, "https://x.example", SECRET, tmp_path / "webhook.json", clock=clock)
    assert hooks.ensure().startswith("failed") and not hooks.active()
    clock.t += 120
    assert hooks.ensure() == "failed recently"
    clock.t += 3600
    assert hooks.ensure().startswith("failed")
    assert len([u for _, u, _, _ in transport.sent if "/webhooks" in u]) == 2


def test_each_part_on_a_sheet_gets_its_picture_once(tmp_path):
    png = b"\x89PNG fake picture"
    p, _ = precut_panel(tmp_path, "8" * 24, THEN, NOW)
    p.transport.routes.append(("GET", "/shadedviews", [resp(body={"images": [base64.b64encode(png).decode()]})]))
    pics = PictureWatch(p.onshape, p.h.store, p.t)
    assert pics.tick() == 1
    [(att, (card, name, data))] = [(a, f) for a, f in p.t.files.items() if f[0] == PART]
    assert name == "P-2014_gusset.png" and data == png
    assert p.t.covers.get(PART) == att
    [url] = [u for _, u, _, _ in p.transport.sent if "shadedviews" in u]
    assert f"/m/{MID}/" in url and "viewMatrix=isometric" in url
    assert pics.tick() == 0 and len([u for _, u, _, _ in p.transport.sent if "shadedviews" in u]) == 1
