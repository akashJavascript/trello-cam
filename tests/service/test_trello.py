import json

import pytest

from autocam_core.tapguard import GuardSpec, check_program
from autocam_service.tracker.base import AutomationForbidden, UploadRefused
from autocam_service.tracker.trello import TrelloConfigError, TrelloError, TrelloHttp, TrelloTracker

LISTS = {"ready_for_cam": "L1", "needs_fixing": "L2", "sheet_review": "L3", "ready_to_cut": "L4", "nested": ""}
CARD = {"id": "C1", "name": "hood_gusset", "desc": "Qty: 2", "idList": "L1", "shortUrl": "https://trello.com/c/abc",
        "labels": [{"name": "Smoked"}], "attachments": [
            {"id": "A1", "name": "p.step", "url": "https://trello.com/1/cards/C1/attachments/A1/download/p.step",
             "mimeType": "", "bytes": 10, "isUpload": True}]}


class FakeHttpTransport:
    def __init__(self, responses):
        self.responses = list(responses)   # (status, body) consumed in order
        self.calls = []

    def __call__(self, method, url, params, files, headers):
        self.calls.append((method, url, dict(params), files, dict(headers)))
        status, body = self.responses.pop(0)
        return status, {}, body if isinstance(body, bytes) else json.dumps(body).encode()


def tracker(*responses):
    t = FakeHttpTransport(responses)
    return TrelloTracker(TrelloHttp("KEY", "TOKEN", transport=t, sleep=lambda s: None), LISTS), t


def test_list_cards_maps_lists_labels_and_attachments():
    tr, t = tracker((200, [CARD]))
    [card] = tr.list_cards("ready_for_cam")
    assert (card.list_key, card.labels, card.url) == ("ready_for_cam", ("Smoked",), "https://trello.com/c/abc")
    assert card.attachments[0].name == "p.step"
    method, url, params, _, _ = t.calls[0]
    assert url.endswith("/lists/L1/cards") and params["key"] == "KEY" and params["attachments"] == "true"


def test_empty_list_id_is_a_config_error():
    tr, _ = tracker()
    with pytest.raises(TrelloConfigError, match="trello.lists.nested is empty"):
        tr.move("C1", "nested")


def test_writes():
    tr, t = tracker((200, {}), (200, {**CARD, "id": "C9", "idList": "L3"}), (200, {}),
                    (200, {"id": "A9"}), (200, {"id": "CL1"}), (200, {}), (200, {}))
    tr.move("C1", "needs_fixing")
    new = tr.create_card("sheet_review", "S1", "desc")
    tr.comment("C1", "x" * 20000)
    report = check_program(b"G90\r\nG20\r\nG53 Z\r\nG0 X1. Y1.\r\nG0 Z1.\r\nG53 Z\r\nM5\r\nG53 P10\r\n", GuardSpec())
    assert report.passed, report.summary()
    tr.attach_program("C9", "S1.tap", b"G90\r\nG20\r\nG53 Z\r\nG0 X1. Y1.\r\nG0 Z1.\r\nG53 Z\r\nM5\r\nG53 P10\r\n", report)
    tr.add_checklist("C9", "Review", ["a", "b"])
    paths = [(m, u.split("/1", 1)[1]) for m, u, *_ in t.calls]
    assert paths == [("PUT", "/cards/C1"), ("POST", "/cards"), ("POST", "/cards/C1/actions/comments"),
                     ("POST", "/cards/C9/attachments"), ("POST", "/cards/C9/checklists"),
                     ("POST", "/checklists/CL1/checkItems"), ("POST", "/checklists/CL1/checkItems")]
    assert t.calls[0][2]["idList"] == "L2"
    assert new.id == "C9" and new.list_key == "sheet_review"
    assert len(t.calls[2][2]["text"]) == 16000
    assert t.calls[3][3]["file"][0] == "S1.tap"


def test_safety_rules_hold_in_the_trello_adapter():
    tr, t = tracker()
    with pytest.raises(AutomationForbidden):
        tr.move("C1", "ready_to_cut")
    with pytest.raises(UploadRefused):
        tr.attach_file("C1", "x.tap", b"G0 Z-1", "text/plain")
    assert t.calls == []


def test_checklist_state():
    tr, _ = tracker((200, [{"name": "Other", "checkItems": []},
                           {"name": "Review", "checkItems": [{"state": "complete"}, {"state": "incomplete"}]}]))
    state = tr.checklist("C1", "Review")
    assert (state.done, state.total, state.complete) == (1, 2, False)


def test_retries_on_429_then_fails_loudly_on_4xx():
    tr, t = tracker((429, b""), (200, [CARD]))
    assert len(tr.list_cards("ready_for_cam")) == 1 and len(t.calls) == 2
    tr, _ = tracker((401, b"invalid token"))
    with pytest.raises(TrelloError, match="401"):
        tr.list_cards("ready_for_cam")


def test_download_only_sends_credentials_to_trello():
    tr, t = tracker((200, b"ISO-10303-21;"))
    [card] = tracker((200, [CARD]))[0].list_cards("ready_for_cam")
    assert tr.download(card.attachments[0]) == b"ISO-10303-21;"
    assert t.calls[0][4]["Authorization"].startswith('OAuth oauth_consumer_key="KEY"')
    with pytest.raises(TrelloError, match="refusing"):
        tr.http.download("https://evil.example.com/x")
    assert "TOKEN" not in repr(tr.http)
