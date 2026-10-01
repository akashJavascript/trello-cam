"""Onshape document links (decision 3: cards link to versions, never workspaces).

    https://cad.onshape.com/documents/<did>/v/<vid>/e/<eid>       version: accepted
    https://cad.onshape.com/documents/<did>/w/<wid>/e/<eid>       workspace: rejected
    https://<company>.onshape.com/documents/...                   enterprise domains work the same way
"""

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

_ID = r"[0-9a-f]{24}"
_PATH_RE = re.compile(rf"^/documents/(?P<did>{_ID})/(?P<wvm>[wvm])/(?P<wvmid>{_ID})/e/(?P<eid>{_ID})/?$")
URL_IN_TEXT_RE = re.compile(r"https://[A-Za-z0-9.-]*onshape\.com/documents/[^\s)>\]\"']+")


class LinkError(ValueError):
    pass


@dataclass(frozen=True)
class OnshapeLink:
    host: str
    did: str
    vid: str
    eid: str

    @property
    def url(self) -> str:
        return f"https://{self.host}/documents/{self.did}/v/{self.vid}/e/{self.eid}"

    @property
    def key(self) -> str:
        """Cache key for anything under this Part Studio version (it never changes)."""
        return f"{self.did}_{self.vid}_{self.eid}"


def parse_link(url: str) -> OnshapeLink:
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not (parts.hostname or "").endswith("onshape.com"):
        raise LinkError("not an Onshape link")
    m = _PATH_RE.match(parts.path)
    if not m:
        raise LinkError("not a link to a Part Studio tab (expected .../documents/<id>/v/<id>/e/<id>)")
    if m["wvm"] == "w":
        raise LinkError("links to a workspace, which can change; link to a version instead "
                        "(create a version in Onshape, open it, and copy that URL)")
    if m["wvm"] == "m":
        raise LinkError("links to a microversion; link to a named version instead")
    return OnshapeLink(parts.hostname, m["did"], m["wvmid"], m["eid"])


def first_onshape_url(text: str) -> Optional[str]:
    m = URL_IN_TEXT_RE.search(text or "")
    return m.group(0).rstrip(".,;") if m else None
