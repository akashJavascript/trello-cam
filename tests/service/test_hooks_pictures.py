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


def test_quiet_means_nobody_changed_it_for_two_minutes(tmp_path):
    clock = Clock()
    inbox = ChangeInbox(tmp_path / "changes.json", clock=clock)
    inbox.note(event())
    clock.t += 100
    inbox.note(event())                                   # still being edited
    clock.t += 100
    assert inbox.quiet() == set()
    clock.t += 30
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


def test_a_webhook_per_document_on_an_open_sheet_made_once_checked_daily_removed_when_unused(tmp_path):
    transport = FakeTransport([("POST", "/webhooks", [resp(body={"id": "w1"}), resp(body={"id": "w2"})]),
                               ("GET", "/webhooks", [resp(body={"items": [{"id": "w1"}]})] * 2),
                               ("DELETE", "/webhooks/w1", [resp(body={})])])
    p = Panel(tmp_path, transport=transport)
    clock = Clock()
    hooks = WebhookSetup(p.onshape, "https://x.example/", SECRET, tmp_path / "webhook.json", clock=clock)
    assert hooks.ensure({(D, W)}) == f"made {D}/{W}" and hooks.covered(D, W) and not hooks.covered(D, "9" * 24)
    [(_, url, _, body)] = [s for s in transport.sent if s[0] == "POST"]
    sent = json.loads(body)
    assert sent["url"] == "https://x.example" + HOOK_PREFIX + SECRET and sent["events"] == [EVENT]
    assert sent["filter"] == f"{{$DocumentId}} = '{D}' && {{$WorkspaceId}} = '{W}'"
    assert sent["options"] == {"collapseEvents": True} and "companyId" not in sent
    calls = len(transport.sent)
    assert hooks.ensure({(D, W)}) == "nothing to do" and len(transport.sent) == calls         # no call
    clock.t += 25 * 3600                                    # a day on: listed once, still there
    hooks.ensure({(D, W)})
    assert [m for m, *_ in transport.sent[calls:]] == ["GET"]
    clock.t += 8 * 24 * 3600                                # a week with no open sheet from it: removed
    assert hooks.ensure({("8" * 24, W)}) == f"removed {D}/{W}; made {'8' * 24}/{W}"
    assert not hooks.covered(D, W)


def test_a_webhook_that_cant_be_made_is_tried_hourly_not_every_tick(tmp_path):
    transport = FakeTransport([("POST", "/webhooks", [resp(400, body={"message": "nope"})] * 2)])
    p = Panel(tmp_path, transport=transport)
    clock = Clock()
    hooks = WebhookSetup(p.onshape, "https://x.example", SECRET, tmp_path / "webhook.json", clock=clock)
    assert hooks.ensure({(D, W)}).startswith("failed") and not hooks.covered(D, W)
    assert "nope" in hooks.ensure.__self__._state()["error"]
    clock.t += 120
    assert hooks.ensure({(D, W)}) == "failed recently"
    clock.t += 3600
    assert hooks.ensure({(D, W)}).startswith("failed")
    assert len([s for s in transport.sent if s[0] == "POST"]) == 2


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


def test_pictures_and_webhooks_only_for_sheets_on_the_board(tmp_path):
    p, _ = precut_panel(tmp_path, "8" * 24, THEN, NOW)
    p.t.archive(SHEET)                                   # archived in Trello: the records may still say open
    calls = []
    p.onshape.picture = lambda *a: calls.append(a) or b"png"
    wanted = []
    hooks = type("Hooks", (), {"ensure": lambda self, w: wanted.append(w) or "ok", "covered": lambda self, *a: False})()
    watch = PrecutWatch(p.onshape, p.board, p.h.store, p.t, hooks=hooks, pictures=PictureWatch(p.onshape, p.h.store, p.t))
    watch.tick(now=1000.0)
    assert calls == [] and wanted == [set()]
