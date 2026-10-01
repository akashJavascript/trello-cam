"""Scripted stand-in for the Onshape API (tests never touch the network)."""

import json

from autocam_service.onshape.client import HttpResponse


def resp(status=200, body=b"{}", **headers):
    if not isinstance(body, bytes):
        body = json.dumps(body).encode()
    return HttpResponse(status, {k.lower().replace("_", "-"): v for k, v in headers.items()}, body)


class FakeTransport:
    """Routes are (method, url fragment, [responses...]); each matching request takes the next response."""

    def __init__(self, routes):
        self.routes = [(m, frag, list(rs)) for m, frag, rs in routes]
        self.sent = []

    def send(self, method, url, headers, body):
        self.sent.append((method, url, dict(headers), body))
        for m, frag, rs in self.routes:
            if m == method and frag in url and rs:
                r = rs.pop(0)
                if isinstance(r, Exception):
                    raise r
                return r
        raise AssertionError(f"unexpected request {method} {url}")
