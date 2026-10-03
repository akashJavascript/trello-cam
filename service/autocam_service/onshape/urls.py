"""Onshape document links. Cards may link a Part Studio's workspace or one of its versions.

    https://cad.onshape.com/documents/<did>/v/<vid>/e/<eid>       version: never changes
    https://cad.onshape.com/documents/<did>/w/<wid>/e/<eid>       workspace: pinned to its current microversion
                                                                  when a run reads it (Exporter.resolve)
    https://<company>.onshape.com/documents/...                   enterprise domains work the same way
"""

import re
from dataclasses import dataclass, replace
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
    vid: str                         # the version id, or the workspace id when wvm == "w"
    eid: str
    wvm: str = "v"                   # "v" version, "w" workspace
    mid: Optional[str] = None        # workspace links: the microversion a run pinned it to

    @property
    def url(self) -> str:
        return f"https://{self.host}/documents/{self.did}/{self.wvm}/{self.vid}/e/{self.eid}"

    @property
    def is_workspace(self) -> bool:
        return self.wvm == "w"

    @property
    def studio(self) -> str:
        """Which Part Studio link this is, before any pinning (for grouping cards)."""
        return f"{self.did}_{self.wvm}{self.vid}_{self.eid}"

    @property
    def key(self) -> str:
        """Cache key: a version, or a workspace at one microversion. Both never change."""
        if self.is_workspace:
            if not self.mid:
                raise ValueError("a workspace link has no cache key until it's pinned to a microversion")
            return f"{self.did}_m{self.mid}_{self.eid}"
        return f"{self.did}_{self.vid}_{self.eid}"

    def pinned(self, mid: str) -> "OnshapeLink":
        return replace(self, mid=mid)


def parse_link(url: str) -> OnshapeLink:
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not (parts.hostname or "").endswith("onshape.com"):
        raise LinkError("isn't an Onshape link")
    m = _PATH_RE.match(parts.path)
    if not m:
        raise LinkError("isn't a link to a Part Studio. Open the Part Studio tab in Onshape and copy the "
                        "address bar")
    if m["wvm"] == "m":
        raise LinkError("points at a microversion. Open the Part Studio (or one of its versions) and copy the "
                        "address bar")
    return OnshapeLink(parts.hostname, m["did"], m["wvmid"], m["eid"], m["wvm"])


def first_onshape_url(text: str) -> Optional[str]:
    m = URL_IN_TEXT_RE.search(text or "")
    return m.group(0).rstrip(".,;") if m else None
