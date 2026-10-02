"""`autocam trello-setup`: build the board the service expects.

- **Lists:** Inbox, Ready for CAM, Needs fixing, Nested, Sheet review, Ready to cut, Cut, Control, Run nest.
- **Cards:** the `Run nest` control card and the `System` status card (in Control), and a "How to add a
  part" card in Inbox.
- **Labels:** `Smoked` and `Tool 1/8`.

Only what's missing is created, so running it again is safe. It prints the `[trello]` config block: board,
list and card IDs aren't secrets (they do nothing without the key and token in .env). No Butler automation,
no custom fields (Trello Free).
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from .cards import FORMAT_HELP
from .config import TRELLO_LISTS

LIST_NAMES = {
    "inbox": "Inbox", "ready_for_cam": "Ready for CAM", "needs_fixing": "Needs fixing", "nested": "Nested",
    "sheet_review": "Sheet review", "ready_to_cut": "Ready to cut", "cut": "Cut", "control": "Control",
    "run_nest": "Run nest",
}
assert tuple(LIST_NAMES) == TRELLO_LISTS

RUN_NEST_DESC = """Drag this card into **Run nest** to start a CAM run. The service picks up every card in Ready for CAM,
then moves this card back to Control with a summary comment. One run at a time."""
SYSTEM_DESC = "The auto-CAM service keeps this card up to date: worker heartbeat, queue, Onshape budget, last error."
README_TITLE = "How to add a part (read me)"
LABEL_COLORS = {"smoked": "black", "tool_eighth": "orange"}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass
class BoardSetup:
    board_id: str
    url: str
    lists: Dict[str, str]
    cards: Dict[str, str]
    created: List[str]

    def config_block(self) -> str:
        lines = ["[trello]", f'board_id = "{self.board_id}"   # {self.url}', "", "[trello.lists]"]
        lines += [f'{key} = "{self.lists[key]}"' for key in LIST_NAMES]
        lines += ["", "[trello.cards]", f'run_nest_control = "{self.cards["run_nest_control"]}"',
                  f'system = "{self.cards["system"]}"']
        return "\n".join(lines)


def setup_board(http, *, board: Optional[str] = None, create: Optional[str] = None,
                labels: Optional[Dict[str, str]] = None, workspace: Optional[str] = None) -> BoardSetup:
    """http: TrelloHttp. Either an existing board (id or short link) or a new board's name (in `workspace`)."""
    created: List[str] = []
    if create:
        params = {"name": create, "defaultLists": "false", "defaultLabels": "false"}
        if workspace:
            params["idOrganization"] = workspace
        b = http.call("POST", "/boards", params)
        created.append(f"board {create}")
    else:
        b = http.call("GET", f"/boards/{board}", {"fields": "name,url"})
    board_id, url = b["id"], b.get("url", "")

    have = {_norm(l["name"]): l["id"] for l in http.call("GET", f"/boards/{board_id}/lists", {"fields": "name"})}
    lists: Dict[str, str] = {}
    for key, name in LIST_NAMES.items():
        if _norm(name) in have:
            lists[key] = have[_norm(name)]
        else:
            lists[key] = http.call("POST", "/lists", {"name": name, "idBoard": board_id, "pos": "bottom"})["id"]
            created.append(f"list {name}")

    cards = http.call("GET", f"/boards/{board_id}/cards", {"fields": "name,idList"})

    def card(title: str, list_key: str, desc: str) -> str:
        for c in cards:
            if _norm(c["name"]) == _norm(title):
                return c["id"]
        created.append(f"card {title}")
        return http.call("POST", "/cards", {"idList": lists[list_key], "name": title, "desc": desc,
                                            "pos": "top"})["id"]

    labels = labels or {}
    found = {
        "run_nest_control": card("Run nest", "control", RUN_NEST_DESC),
        "system": card("System", "control", SYSTEM_DESC),
    }
    card(README_TITLE, "inbox", FORMAT_HELP.format(smoked=labels.get("smoked", "Smoked"),
                                                   tool=labels.get("tool_eighth", "Tool 1/8")))

    have_labels = {_norm(l.get("name") or "") for l in http.call("GET", f"/boards/{board_id}/labels", {"fields": "name"})}
    for key, color in LABEL_COLORS.items():
        name = labels.get(key)
        if name and _norm(name) not in have_labels:
            http.call("POST", "/labels", {"name": name, "color": color, "idBoard": board_id})
            created.append(f"label {name}")
    return BoardSetup(board_id, url, lists, found, created)
