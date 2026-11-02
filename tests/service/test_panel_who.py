"""Refused panel requests are logged with who sent them (2026-11-02: an unknown poller asked for /api/status, which
the panel doesn't have, twice a minute), never the key or an IP address."""

from http.client import HTTPMessage

from autocam_service.panel.server import who


def headers(**kw):
    m = HTTPMessage()
    for k, v in kw.items():
        m[k.replace("_", "-")] = v
    return m


def test_says_whether_it_came_through_the_tunnel_and_what_asked():
    assert who(headers(User_Agent="Go-http-client/1.1")) == " (from this computer; Go-http-client/1.1)"
    got = who(headers(Cf_Ray="8f1a-SJC", User_Agent="Mozilla/5.0 (X11)", Cf_Connecting_Ip="203.0.113.9",
                      Referer="https://5940.onshape.com/documents/abc?key=SECRET&documentId=x"))
    assert got == " (through the tunnel; Mozilla/5.0 (X11); from 5940.onshape.com/documents/abc)"
    assert "SECRET" not in got and "203.0.113.9" not in got


def test_the_page_path_is_kept_but_never_its_query():
    got = who(headers(User_Agent="x", Referer="http://localhost:8770/panel?key=SECRET#frag"))
    assert got == " (from this computer; x; from localhost:8770/panel)"
    assert who(headers(User_Agent="x", Origin="http://localhost:8770")) == " (from this computer; x; from localhost:8770)"


def test_without_headers():
    assert who(None) == ""
    assert who(headers()) == " (from this computer; no user agent)"
