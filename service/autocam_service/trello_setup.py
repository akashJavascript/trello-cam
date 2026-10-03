"""`autocam trello-setup`: build or update the board the service expects.

- **Lists:** Drafts, Ready for CAM, Needs fixing, On a sheet, Sheet review, Ready to cut, Cut, Offcuts, Control.
  Lists with an older name (Inbox, Nested) are renamed in place, so their IDs and the config stay the same.
  The old `Run nest` list and its control card are archived (runs start from Ready for CAM now).
- **Cards:** the `System` status card (in Control), the "How to add a part" card and the "New part" card
  template (in Drafts). Their text is brought up to date. The template has no "Nest this part" box: Trello
  unticks checklist items when it copies a card (seen 2026-10-02), so the service adds the box, ticked, to
  every part card instead (runner.ready_cards). A box left on the template from before is removed.
- **Labels:** `Smoked` and `Tool 1/8`.

Only what's missing or out of date is written, so running it again is safe. It prints the `[trello]` config
block: board, list and card IDs aren't secrets (they do nothing without the key and token in .env). No Butler
automation, no custom fields (Trello Free).
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .cards import FORMAT_HELP, README_CARD
from .config import TRELLO_LISTS

LIST_NAMES = {
    "inbox": "Drafts", "ready_for_cam": "Ready for CAM", "needs_fixing": "Needs fixing", "nested": "On a sheet",
    "sheet_review": "Sheet review", "ready_to_cut": "Ready to cut", "cut": "Cut", "offcuts": "Offcuts",
    "control": "Control",
}
RETIRED_LISTS = ("Run nest",)       # archived (with the cards in them) if they're still there
OLD_NAMES = {"inbox": ("Inbox",), "nested": ("Nested",)}     # renamed in place
assert tuple(LIST_NAMES) == TRELLO_LISTS

RETIRED_CARDS = ("Run nest",)
SYSTEM_DESC = "The auto-CAM service writes its status here, and comments when a run starts and finishes."
README_TITLE = README_CARD
TEMPLATE_TITLE = "New part"
TEMPLATE_DESC = "Paste the Part Studio link here\nQty: "
LABEL_COLORS = {"smoked": "black", "tool_eighth": "orange", "rush": "red"}


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
        lines += ["", "[trello.cards]", f'system = "{self.cards["system"]}"']
        return "\n".join(lines)


def setup_board(http, *, board: Optional[str] = None, create: Optional[str] = None,
                labels: Optional[Dict[str, str]] = None, workspace: Optional[str] = None,
                nest_box: Tuple[str, str] = ("Nest", "Nest this part")) -> BoardSetup:
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

    for list_id, list_name in have.values():
        if _norm(list_name) in {_norm(n) for n in RETIRED_LISTS}:
            http.call("PUT", f"/lists/{list_id}/closed", {"value": "true"})
            created.append(f"archived list {list_name}")

    cards = http.call("GET", f"/boards/{board_id}/cards", {"fields": "name,idList,desc,isTemplate"})
    for c in cards:
        if _norm(c["name"]) in {_norm(n) for n in RETIRED_CARDS}:
            http.call("PUT", f"/cards/{c['id']}", {"closed": "true"})
            created.append(f"archived card {c['name']}")

    def card(title: str, list_key: str, desc: str, template: bool = False, keep_desc: bool = False) -> str:
        for c in cards:
            if _norm(c["name"]) == _norm(title):
                if c.get("desc", "") != desc and not keep_desc:
                    http.call("PUT", f"/cards/{c['id']}", {"desc": desc})
                    created.append(f"updated card {title}")
                return c["id"]
        created.append(f"card {title}")
        params = {"idList": lists[list_key], "name": title, "desc": desc, "pos": "top"}
        if template:
            params["isTemplate"] = "true"
        return http.call("POST", "/cards", params)["id"]

    labels = labels or {}
    found = {"system": card("System", "control", SYSTEM_DESC, keep_desc=True)}   # the service writes its status there
    card(README_TITLE, "inbox", FORMAT_HELP.format(smoked=labels.get("smoked", "Smoked"),
                                                   tool=labels.get("tool_eighth", "Tool 1/8"),
                                                   rush=labels.get("rush", "Rush")))
    template = card(TEMPLATE_TITLE, "inbox", TEMPLATE_DESC, template=True)
    box = nest_box[0]
    for cl in http.call("GET", f"/cards/{template}/checklists", {"fields": "name"}):
        if cl.get("name") == box:
            http.call("DELETE", f"/checklists/{cl['id']}")
            created.append(f"removed the {box} box from {TEMPLATE_TITLE} (copies would come out unticked)")

    have_labels = {_norm(l.get("name") or "") for l in http.call("GET", f"/boards/{board_id}/labels", {"fields": "name"})}
    for key, color in LABEL_COLORS.items():
        name = labels.get(key)
        if name and _norm(name) not in have_labels:
            http.call("POST", "/labels", {"name": name, "color": color, "idBoard": board_id})
            created.append(f"label {name}")
    return BoardSetup(board_id, url, lists, found, created)
