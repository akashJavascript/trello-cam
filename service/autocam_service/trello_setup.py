"""`autocam trello-setup`: build or update the board the service expects.

- **Lists:** Drafts, Ready for CAM, Needs fixing, On a sheet, Sheet review, Ready to cut, Cut, Control, Run nest.
  Lists with an older name (Inbox, Nested) are renamed in place, so their IDs and the config stay the same.
- **Cards:** the `Run nest` control card and the `System` status card (in Control), the "How to add a part"
  card and the "New part" card template (in Drafts). Their text is brought up to date.
- **Labels:** `Smoked` and `Tool 1/8`.

Only what's missing or out of date is written, so running it again is safe. It prints the `[trello]` config
block: board, list and card IDs aren't secrets (they do nothing without the key and token in .env). No Butler
automation, no custom fields (Trello Free).
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from .cards import FORMAT_HELP, README_CARD
from .config import TRELLO_LISTS

LIST_NAMES = {
    "inbox": "Drafts", "ready_for_cam": "Ready for CAM", "needs_fixing": "Needs fixing", "nested": "On a sheet",
    "sheet_review": "Sheet review", "ready_to_cut": "Ready to cut", "cut": "Cut", "control": "Control",
    "run_nest": "Run nest",
}
OLD_NAMES = {"inbox": ("Inbox",), "nested": ("Nested",)}     # renamed in place
assert tuple(LIST_NAMES) == TRELLO_LISTS

RUN_NEST_DESC = "Drag this card into Run nest to CAM everything in Ready for CAM. It comes back here with a summary."
SYSTEM_DESC = "Status of the auto-CAM service. The service updates this card."
README_TITLE = README_CARD
TEMPLATE_TITLE = "New part"
TEMPLATE_DESC = "Paste the Part Studio link here\nQty: "
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

    have = {_norm(l["name"]): (l["id"], l["name"]) for l in http.call("GET", f"/boards/{board_id}/lists",
                                                                      {"fields": "name"})}
    lists: Dict[str, str] = {}
    for key, name in LIST_NAMES.items():
        found = next((have[_norm(n)] for n in (name,) + OLD_NAMES.get(key, ()) if _norm(n) in have), None)
        if found is None:
            lists[key] = http.call("POST", "/lists", {"name": name, "idBoard": board_id, "pos": "bottom"})["id"]
            created.append(f"list {name}")
            continue
        lists[key] = found[0]
        if found[1] != name:
            http.call("PUT", f"/lists/{found[0]}", {"name": name})
            created.append(f"renamed list {found[1]} to {name}")

    cards = http.call("GET", f"/boards/{board_id}/cards", {"fields": "name,idList,desc,isTemplate"})

    def card(title: str, list_key: str, desc: str, template: bool = False) -> str:
        for c in cards:
            if _norm(c["name"]) == _norm(title):
                if c.get("desc", "") != desc:
                    http.call("PUT", f"/cards/{c['id']}", {"desc": desc})
                    created.append(f"updated card {title}")
                return c["id"]
        created.append(f"card {title}")
        params = {"idList": lists[list_key], "name": title, "desc": desc, "pos": "top"}
        if template:
            params["isTemplate"] = "true"
        return http.call("POST", "/cards", params)["id"]

    labels = labels or {}
    found = {
        "run_nest_control": card("Run nest", "control", RUN_NEST_DESC),
        "system": card("System", "control", SYSTEM_DESC),
    }
    card(README_TITLE, "inbox", FORMAT_HELP.format(smoked=labels.get("smoked", "Smoked"),
                                                   tool=labels.get("tool_eighth", "Tool 1/8")))
    card(TEMPLATE_TITLE, "inbox", TEMPLATE_DESC, template=True)

    have_labels = {_norm(l.get("name") or "") for l in http.call("GET", f"/boards/{board_id}/labels", {"fields": "name"})}
    for key, color in LABEL_COLORS.items():
        name = labels.get(key)
        if name and _norm(name) not in have_labels:
            http.call("POST", "/labels", {"name": name, "color": color, "idBoard": board_id})
            created.append(f"label {name}")
    return BoardSetup(board_id, url, lists, found, created)
