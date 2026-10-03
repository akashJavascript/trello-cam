"""`autocam trello-setup` against a fake Trello that keeps board state (no network)."""

import itertools
import json
from urllib.parse import urlsplit

from autocam_service.config import TRELLO_ID_RE, load_config, parse_config, DEFAULT_CONFIG
from autocam_service.tracker.trello import TrelloHttp
from autocam_service.trello_setup import LIST_NAMES, README_TITLE, setup_board

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib


class FakeTrello:
    def __init__(self):
        self.ids = (f"{n:024x}" for n in itertools.count(1))
        self.boards, self.lists, self.cards, self.labels, self.checklists = {}, [], [], [], []
        self.posts = []
        self.puts = []

    def __call__(self, method, url, params, files, headers):
        path = urlsplit(url).path.removeprefix("/1")
        parts = path.strip("/").split("/")
        given = {k: v for k, v in params.items() if k not in ("key", "token")}
        if method == "DELETE":
            self.checklists = [c for c in self.checklists if c["id"] != parts[1]]
            return 200, {}, b"{}"
        if method == "PUT":
            item = next(x for x in self.lists + self.cards if x["id"] == parts[1])
            changes = {"closed": given["value"]} if parts[2:] == ["closed"] else given
            item.update(changes)
            self.puts.append((path, changes))
            return 200, {}, json.dumps(item).encode()
        if method == "POST":
            self.posts.append(path)
            item = {"id": next(self.ids), **given}
            if path == "/boards":
                item["url"] = f"https://trello.com/b/{item['id'][-8:]}/x"
                self.boards[item["id"]] = item
            elif parts[0] == "cards" and parts[2:] == ["checklists"]:
                self.checklists.append({**item, "idCard": parts[1], "checkItems": []})
            elif parts[0] == "checklists":
                cl = next(c for c in self.checklists if c["id"] == parts[1])
                cl["checkItems"].append({**item, "state": "complete" if given.get("checked") == "true" else "incomplete"})
            else:
                {"/lists": self.lists, "/cards": self.cards, "/labels": self.labels}[path].append(item)
            return 200, {}, json.dumps(item).encode()
        if parts[0] == "cards":
            body = [c for c in self.checklists if c["idCard"] == parts[1]]
            return 200, {}, json.dumps(body).encode()
        board = parts[1]
        open_ = lambda x: x.get("closed") != "true"      # Trello lists only open lists and cards by default
        if len(parts) == 2:
            body = self.boards[board]
        elif parts[2] == "lists":
            body = [l for l in self.lists if l["idBoard"] == board and open_(l)]
        elif parts[2] == "cards":
            ids = {l["id"] for l in self.lists if l["idBoard"] == board and open_(l)}
            body = [c for c in self.cards if c["idList"] in ids and open_(c)]
        else:
            body = [l for l in self.labels if l["idBoard"] == board]
        return 200, {}, json.dumps(body).encode()


LABELS = {"smoked": "Smoked", "tool_eighth": "Tool 1/8"}


def test_creates_a_whole_board_and_prints_valid_config():
    fake = FakeTrello()
    http = TrelloHttp("KEY", "TOKEN", transport=fake, sleep=lambda s: None)
    result = setup_board(http, create="5940 AutoCAM", labels=LABELS, workspace="w" * 24)
    assert fake.boards[result.board_id]["idOrganization"] == "w" * 24
    names = [l["name"] for l in fake.lists]
    assert names == list(LIST_NAMES.values())
    control = result.lists["control"]
    assert {c["name"] for c in fake.cards if c["idList"] == control} == {"System"}
    readme = next(c for c in fake.cards if c["name"] == README_TITLE)
    assert readme["idList"] == result.lists["inbox"] and "Qty: 2" in readme["desc"] and "`" not in readme["desc"]
    template = next(c for c in fake.cards if c["name"] == "New part")
    assert template["isTemplate"] == "true" and template["idList"] == result.lists["inbox"]
    assert [cl for cl in fake.checklists if cl["idCard"] == template["id"]] == []   # the service adds the box
    assert {l["name"] for l in fake.labels} == {"Smoked", "Tool 1/8"}
    assert all(TRELLO_ID_RE.match(v) for v in list(result.lists.values()) + list(result.cards.values()))

    # The printed block drops straight into the config, and the config still validates.
    block = tomllib.loads(result.config_block())
    raw = tomllib.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    raw["trello"].update({k: v for k, v in block["trello"].items() if k != "lists" and k != "cards"})
    raw["trello"]["lists"] = block["trello"]["lists"]
    raw["trello"]["cards"] = block["trello"]["cards"]
    cfg = parse_config(raw, root=DEFAULT_CONFIG.parent.parent)
    assert cfg.trello.board_id == result.board_id and dict(cfg.trello.lists) == result.lists


def test_running_it_again_creates_nothing():
    fake = FakeTrello()
    http = TrelloHttp("KEY", "TOKEN", transport=fake, sleep=lambda s: None)
    first = setup_board(http, create="5940 AutoCAM", labels=LABELS)
    posts = len(fake.posts)
    again = setup_board(http, board=first.board_id, labels=LABELS)
    assert len(fake.posts) == posts and again.created == []
    assert again.lists == first.lists and again.cards == first.cards


def test_existing_lists_are_reused_by_name():
    fake = FakeTrello()
    http = TrelloHttp("KEY", "TOKEN", transport=fake, sleep=lambda s: None)
    board = http.call("POST", "/boards", {"name": "Old board"})["id"]
    keep = http.call("POST", "/lists", {"name": "ready for cam", "idBoard": board})["id"]
    result = setup_board(http, board=board, labels=LABELS)
    assert result.lists["ready_for_cam"] == keep
    assert "list Ready for CAM" not in result.created and "list Drafts" in result.created


def test_repo_config_still_loads():
    load_config()



def test_an_existing_board_is_updated_in_place():
    fake = FakeTrello()
    http = TrelloHttp("KEY", "TOKEN", transport=fake, sleep=lambda s: None)
    board = http.call("POST", "/boards", {"name": "5940 AutoCAM"})["id"]
    inbox = http.call("POST", "/lists", {"name": "Inbox", "idBoard": board})["id"]
    nested = http.call("POST", "/lists", {"name": "Nested", "idBoard": board})["id"]
    control = http.call("POST", "/lists", {"name": "Control", "idBoard": board})["id"]
    run_nest = http.call("POST", "/lists", {"name": "Run nest", "idBoard": board})["id"]
    old_run = http.call("POST", "/cards", {"name": "Run nest", "idList": control, "desc": "old **markdown** text"})["id"]
    result = setup_board(http, board=board, labels=LABELS)
    assert result.lists["inbox"] == inbox and result.lists["nested"] == nested      # same IDs, config unchanged
    assert {"renamed list Inbox to Drafts", "renamed list Nested to On a sheet", "archived list Run nest",
            "archived card Run nest"} <= set(result.created)
    renames = [p for p in fake.puts if p[0].startswith("/lists/") and "name" in p[1]]
    assert sorted(n["name"] for _, n in renames) == ["Drafts", "On a sheet"]
    assert next(l for l in fake.lists if l["id"] == run_nest)["closed"] == "true"
    assert next(c for c in fake.cards if c["id"] == old_run)["closed"] == "true"
    assert set(result.cards) == {"system"}
    again = setup_board(http, board=board, labels=LABELS)
    assert again.created == []


def test_a_nest_box_on_the_template_is_removed():
    # Trello copies a template's checklist items unticked, so cards made from it would never be nested.
    fake = FakeTrello()
    http = TrelloHttp("KEY", "TOKEN", transport=fake, sleep=lambda s: None)
    first = setup_board(http, create="5940 AutoCAM", labels=LABELS)
    template = next(c for c in fake.cards if c["name"] == "New part")
    http.call("POST", f"/cards/{template['id']}/checklists", {"name": "Nest"})
    result = setup_board(http, board=first.board_id, labels=LABELS)
    assert result.created == ["removed the Nest box from New part (copies would come out unticked)"]
    assert [cl for cl in fake.checklists if cl["idCard"] == template["id"]] == []
