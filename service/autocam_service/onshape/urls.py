"""Onshape document links. Cards may link a Part Studio's workspace or one of its versions.

    https://cad.onshape.com/documents/<did>/v/<vid>/e/<eid>       version: never changes
    https://cad.onshape.com/documents/<did>/w/<wid>/e/<eid>       workspace: pinned to its current microversion
                                                                  when a run reads it (Exporter.resolve)
    https://<company>.onshape.com/documents/...                   enterprise domains work the same way
    .../e/<eid>?configuration=<Onshape's configuration string>      a configured Part Studio's configuration
                                                                  (2026-11-01; empty: the default)

The configuration is kept exactly as Onshape writes it (e.g. "Length=19+in;List_x3=Option_2": a "+" there is
Onshape's own, so it's decoded without turning "+" into a space) and passed back to Onshape as the `configuration`
parameter of every read and export of that Part Studio.
"""

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Dict, Optional
from urllib.parse import quote, unquote, urlsplit

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
    configuration: str = ""          # a configured Part Studio's configuration ("": the default)

    @property
    def url(self) -> str:
        return f"https://{self.host}/documents/{self.did}/{self.wvm}/{self.vid}/e/{self.eid}" + \
            configuration_query(self.configuration)

    @property
    def is_workspace(self) -> bool:
        return self.wvm == "w"

    @property
    def studio(self) -> str:
        """Which Part Studio link this is, before any pinning (for grouping cards)."""
        return f"{self.did}_{self.wvm}{self.vid}_{self.eid}" + configuration_tag(self.configuration)

    @property
    def key(self) -> str:
        """Cache key: a version, or a workspace at one microversion (and the configuration). They never change."""
        if self.is_workspace:
            if not self.mid:
                raise ValueError("a workspace link has no cache key until it's pinned to a microversion")
            return f"{self.did}_m{self.mid}_{self.eid}" + configuration_tag(self.configuration)
        return f"{self.did}_{self.vid}_{self.eid}" + configuration_tag(self.configuration)

    def query(self, **more: str) -> Dict[str, str]:
        """An Onshape API query for this Part Studio: `more`, plus its configuration when it has one."""
        return {**more, **({"configuration": self.configuration} if self.configuration else {})}

    def pinned(self, mid: str) -> "OnshapeLink":
        return replace(self, mid=mid)


def normal_configuration(value: Optional[str]) -> str:
    """"" for the default configuration (Onshape writes "default", or nothing, or the Action URL's unfilled
    "{$configuration}")."""
    value = (value or "").strip()
    return "" if value.lower() in ("", "default", "{$configuration}") or value.startswith("{$") else value


def configuration_tag(configuration: str) -> str:
    """A short, file-name-safe tag for a configuration ("" for the default) for cache keys."""
    if not configuration:
        return ""
    return "_c" + hashlib.sha256(configuration.encode("utf-8")).hexdigest()[:10]


def configuration_query(configuration: str) -> str:
    return f"?configuration={quote(configuration, safe='')}" if configuration else ""


def configuration_from_query(query: str) -> str:
    """The configuration in a link's query, decoded once and keeping "+" (Onshape's, not a space)."""
    for item in (query or "").split("&"):
        name, _, value = item.partition("=")
        if unquote(name) == "configuration":
            return normal_configuration(unquote(value))
    return ""


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
    return OnshapeLink(parts.hostname, m["did"], m["wvmid"], m["eid"], m["wvm"],
                       configuration=configuration_from_query(parts.query))


def configuration_of(url: str) -> str:
    """The configuration in a link ("" for the default, or a link that isn't one)."""
    try:
        return parse_link(url or "").configuration
    except LinkError:
        return ""


def first_onshape_url(text: str) -> Optional[str]:
    m = URL_IN_TEXT_RE.search(text or "")
    return m.group(0).rstrip(".,;") if m else None
