"""Signing in to the shop's phone page with Slack (panel/signin.py, 2026-11-04; the user: "should still build auth").
Offline: a fake Slack answers the token swap and "who is it"; nothing here reaches the network."""

import html
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from autocam_service.panel.api import PanelApp, shop_key
from autocam_service.panel.signin import (AUTHORIZE, CALLBACK, START, TOKEN, USERINFO, ShopSignIn, SlackError, cookies,
                                          end_sessions)
from autocam_service.people import People
from test_panel import KEY
from test_shafts import shaft_panel

TEAM, SECRET = "T0TEAM", "s3cret-not-real"
NOW = datetime(2026, 11, 4, 20, 0, tzinfo=timezone.utc)


class FakeSlack:
    """Slack's two calls. codes: code -> who signs in with it."""

    def __init__(self):
        self.codes = {"maya": {"user": "U0MAYA", "given_name": "Maya", "family_name": "Patel"},
                      "maya2": {"user": "U0MAYK", "given_name": "Maya", "family_name": "Khan"},
                      "sam": {"user": "U0SAM", "given_name": "Sam", "family_name": "Lee"},
                      "other": {"user": "U0OUT", "given_name": "Ola", "team": "T0ELSE"}}
        self.calls = []
        self.down = False

    def __call__(self, url, data=None, token=""):
        self.calls.append((url, dict(data or {}), token))
        if self.down:
            raise SlackError("URLError")
        if url == TOKEN:
            if data.get("client_secret") != SECRET or data.get("code") not in self.codes:
                return {"ok": False, "error": "invalid_code"}
            return {"ok": True, "access_token": "xoxp-" + data["code"], "token_type": "Bearer", "id_token": "a.b.c"}
        assert url == USERINFO
        who = self.codes[token[len("xoxp-"):]]
        return {"ok": True, "sub": who["user"], "https://slack.com/user_id": who["user"],
                "https://slack.com/team_id": who.get("team", TEAM), "name": f"{who['given_name']} {who.get('family_name', '')}",
                "given_name": who["given_name"], "family_name": who.get("family_name", "")}


def signin_panel(tmp_path, team=TEAM):
    p = shaft_panel(tmp_path)
    p.people = People(p.h.cfg.paths.state / "people.json")
    p.slack = FakeSlack()
    p.when = [NOW]
    p.sessions = p.h.cfg.paths.state / "shop_sessions.json"
    p.signin = ShopSignIn(p.sessions, "123.456", SECRET, team, "https://autocam.team5940.org" + CALLBACK,
                          session_days=60, old_link_days=7, people=p.people, post=p.slack, now=lambda: p.when[0])
    p.app = PanelApp(p.h.cfg, KEY, p.onshape, p.board, p.names, clock=p.clock, shafts=p.shafts, people=p.people,
                     signin=p.signin)
    return p


def go(p, path, query=None, cookie="", key=""):
    headers = {}
    if cookie:
        headers["cookie"] = cookie
    if key:
        headers["x-autocam-key"] = key
    return p.app.handle("GET", path, dict(query or {}), headers)


def sign_in(p, code="maya", tab="", state_cookie=None):
    out = go(p, START, {"tab": tab} if tab else {})
    assert out.status == 302
    state = parse_qs(urlsplit(out.headers["Location"]).query)["state"][0]
    kept = out.headers["Set-Cookie"].split(";")[0] if state_cookie is None else state_cookie
    return out, go(p, CALLBACK, {"code": code, "state": state}, cookie=kept)


def session_of(back):
    assert back.status == 302, back.body
    return back.headers["Set-Cookie"].split(";")[0]


def shop(p, cookie="", key="", device="phone-aaaa", method="GET", action="", body=None):
    headers = {"cookie": cookie} if cookie else {}
    if key:
        headers["x-autocam-key"] = key
    if method == "GET":
        r = p.app.handle("GET", "/api/shop", {"device": device}, headers)
    else:
        r = p.app.handle("POST", "/api/shop/" + action, {}, headers, json.dumps(body or {}).encode())
    return r.status, json.loads(r.body), r


def test_signing_in_with_slack_opens_the_shop_page(tmp_path):
    p = signin_panel(tmp_path)
    page = go(p, "/shop")
    assert page.status == 200 and b"Sign in with Slack" in page.body and b"/static/signin.js?v=" in page.body
    assert page.headers["Referrer-Policy"] == "no-referrer"
    out, back = sign_in(p, tab="parts")
    url = urlsplit(out.headers["Location"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == AUTHORIZE
    assert q["scope"] == "openid profile" and "email" not in q["scope"]            # the name, never the email
    assert (q["client_id"], q["team"], q["response_type"]) == ("123.456", TEAM, "code")
    assert q["redirect_uri"] == "https://autocam.team5940.org/shop/signed-in"
    state_cookie = out.headers["Set-Cookie"]
    assert f"Path={CALLBACK}" in state_cookie and "HttpOnly" in state_cookie and "Secure" in state_cookie
    assert back.headers["Location"] == "/shop#parts"
    set_cookie = back.headers["Set-Cookie"]
    assert all(a in set_cookie for a in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/", f"Max-Age={60 * 86400}"))
    (token_url, sent, _), (info_url, _, token) = p.slack.calls
    assert token_url == TOKEN and sent["client_secret"] == SECRET and sent["grant_type"] == "authorization_code"
    assert sent["redirect_uri"] == q["redirect_uri"] and info_url == USERINFO and token == "xoxp-maya"
    cookie = session_of(back)
    assert go(p, "/shop", cookie=cookie).body.startswith(b"<!doctype html>")
    assert b"shop.js" in go(p, "/shop", cookie=cookie).body
    status, d, _ = shop(p, cookie)
    assert status == 200 and d["signedIn"] == "Maya" and d["me"] == "Maya" and "signIn" not in d
    assert p.people.me("phone-aaaa") == "Maya" and "Maya" in p.people.names()
    kept = p.sessions.read_text(encoding="utf-8")
    value = cookie.split("=", 1)[1]
    assert value not in kept and "xoxp-" not in kept and SECRET not in kept           # only the cookie's hash
    assert "Patel" not in kept                                                        # the first name only


def test_the_onshape_panel_still_needs_its_key(tmp_path):
    p = signin_panel(tmp_path)
    cookie = session_of(sign_in(p)[1])
    assert go(p, "/panel", cookie=cookie).status == 403
    assert p.app.handle("GET", "/api/assembly", {}, {"cookie": cookie}).status == 403
    assert go(p, "/panel", {"key": KEY}).status == 200


def test_who_gets_turned_away(tmp_path):
    p = signin_panel(tmp_path)

    def refused(back, words):
        assert back.status == 200 and html.escape(words).encode() in back.body and "Set-Cookie" not in back.headers

    refused(sign_in(p, code="other")[1], "isn't in the team's Slack")
    out = go(p, START)
    state = parse_qs(urlsplit(out.headers["Location"]).query)["state"][0]
    refused(go(p, CALLBACK, {"error": "access_denied", "state": state}), "cancelled")
    refused(sign_in(p, state_cookie="")[1], "timed out or came from another tab")                # no state cookie
    refused(sign_in(p, state_cookie="autocam_signin=someone-elses")[1], "timed out")
    out = go(p, START)
    state = parse_qs(urlsplit(out.headers["Location"]).query)["state"][0]
    kept = out.headers["Set-Cookie"].split(";")[0]
    assert go(p, CALLBACK, {"code": "maya", "state": state}, cookie=kept).status == 302
    refused(go(p, CALLBACK, {"code": "maya", "state": state}, cookie=kept), "timed out")          # used once only
    out = go(p, START)
    state = parse_qs(urlsplit(out.headers["Location"]).query)["state"][0]
    p.when[0] += timedelta(minutes=11)
    refused(go(p, CALLBACK, {"code": "maya", "state": state}, cookie=out.headers["Set-Cookie"].split(";")[0]),
            "timed out")
    refused(sign_in(p, code="nobody")[1], "Slack didn't sign you in")
    p.slack.down = True
    refused(sign_in(p)[1], "Couldn't reach Slack")
    p.slack.down = False
    status, d, _ = shop(p, "autocam_shop=made-up")
    assert (status, d["kind"]) == (401, "signin")


def test_before_the_team_id_is_set_a_sign_in_says_what_it_is_and_lets_nobody_in(tmp_path):
    p = signin_panel(tmp_path, team="")
    back = sign_in(p)[1]
    assert back.status == 200 and b"AUTOCAM_SLACK_TEAM_ID=T0TEAM" in back.body and "Set-Cookie" not in back.headers
    assert shop(p, key=shop_key(KEY))[0] == 200                                     # the key link still works
    assert "signIn" not in shop(p, key=shop_key(KEY))[1]                             # and isn't asked to sign in


def test_the_old_key_link_works_for_a_week_after_the_first_sign_in(tmp_path):
    p = signin_panel(tmp_path)
    key = shop_key(KEY)
    assert go(p, "/shop", {"key": key}).status == 200 and b"shop.js" in go(p, "/shop", {"key": key}).body
    status, d, _ = shop(p, key=key)
    assert status == 200 and d["signIn"] == {"until": ""} and d["signedIn"] == ""   # asked, no end yet
    cookie = session_of(sign_in(p)[1])                                             # the first sign-in: a week from now
    assert shop(p, key=key)[1]["signIn"] == {"until": "2026-11-11"}
    p.when[0] = NOW + timedelta(days=6, hours=23)
    assert shop(p, key=key)[0] == 200
    p.when[0] = NOW + timedelta(days=7, minutes=1)
    status, d, _ = shop(p, key=key)
    assert (status, d["kind"]) == (401, "signin")
    page = go(p, "/shop", {"key": key})
    assert page.status == 200 and html.escape("doesn't open the shop any more").encode() in page.body and b"shop.js" not in page.body
    assert shop(p, cookie)[0] == 200                                               # signed in is still fine


def test_without_slack_set_up_the_key_link_is_how_in(tmp_path):
    p = shaft_panel(tmp_path)
    assert p.app.handle("GET", "/shop", {}, {}).status == 403
    assert p.app.handle("GET", START, {}, {}).status == 404
    assert p.call("GET", "/api/shop", {"device": "phone-aaaa"}, key=shop_key(KEY))[0] == 200


def test_a_session_lasts_from_its_last_use_and_signing_out_ends_it(tmp_path):
    p = signin_panel(tmp_path)
    cookie = session_of(sign_in(p)[1])
    for days in (59, 118):                                    # used every 59 days: still signed in
        p.when[0] = NOW + timedelta(days=days)
        assert shop(p, cookie)[0] == 200
    p.when[0] = NOW + timedelta(days=118 + 61)
    assert shop(p, cookie)[0] == 401                          # 61 days without it: sign in again
    p.when[0] = NOW
    cookie = session_of(sign_in(p)[1])
    status, _, r = shop(p, cookie, method="POST", action="sign-out")
    assert status == 200 and "Max-Age=0" in r.headers["Set-Cookie"]
    assert shop(p, cookie)[0] == 401


def test_a_mentor_can_sign_out_one_person_or_everyone(tmp_path):
    p = signin_panel(tmp_path)
    maya = [session_of(sign_in(p)[1]) for _ in range(2)]      # two phones
    sam = session_of(sign_in(p, code="sam")[1])
    assert end_sessions(p.sessions, "maya") == 2              # any case
    assert [shop(p, c)[0] for c in maya + [sam]] == [401, 401, 200]   # the service sees the file change
    assert end_sessions(p.sessions) == 1 and shop(p, sam)[0] == 401


def test_names_come_from_slack(tmp_path):
    p = signin_panel(tmp_path)
    p.people.set_me("phone-old1", "maya")                     # typed before sign-in existed, with work assigned
    first = session_of(sign_in(p)[1])
    assert shop(p, first)[1]["me"] == "maya"                  # Slack's Maya takes it over
    other = session_of(sign_in(p, code="maya2")[1])
    assert shop(p, other, device="phone-bbbb")[1]["me"] == "Maya K."     # a second Maya
    again = session_of(sign_in(p, code="maya2")[1])
    assert shop(p, again, device="phone-cccc")[1]["me"] == "Maya K."     # the same person on another phone
    status, d, _ = shop(p, other, device="phone-bbbb", method="POST", action="me",
                        body={"device": "phone-bbbb", "name": "Someone"})
    assert (status, d["me"]) == (200, "Maya K.") and "Someone" not in p.people.names()   # not picked any more
    p.slack.codes["odd"] = {"user": "U0ODD", "given_name": "", "family_name": ""}
    p.slack.codes["odd"]["given_name"] = "🙂"
    assert shop(p, session_of(sign_in(p, code="odd")[1]), device="phone-dddd")[1]["me"] == "Teammate"


def test_cookies_are_read_even_with_odd_ones_beside_them():
    assert cookies('a=1; autocam_shop=x_y-z; bad; =q; theme="dark"') == {"a": "1", "autocam_shop": "x_y-z", "theme": '"dark"'}
    assert cookies("") == {}


def test_the_service_turns_sign_in_on_only_with_the_slack_app_in_env(tmp_path):
    import copy
    from autocam_service.app import shop_signin
    from autocam_service.config import parse_config
    from autocam_service.credentials import Credentials
    from test_runner_e2e import RAW, REPO_ROOT
    raw = copy.deepcopy(RAW)
    raw["panel"] = {**raw.get("panel", {}), "public_url": "https://autocam.team5940.org", "enabled": True}
    cfg = parse_config(raw, root=REPO_ROOT)
    assert cfg.panel.shop_signin == "slack" and cfg.panel.shop_old_link_days == 7
    assert shop_signin(cfg, Credentials({}), None) is None
    s = shop_signin(cfg, Credentials({"AUTOCAM_SLACK_CLIENT_ID": "1.2", "AUTOCAM_SLACK_CLIENT_SECRET": SECRET}), None)
    assert s.ready and not s.on and s.redirect_uri == "https://autocam.team5940.org/shop/signed-in"
    assert SECRET not in repr(s)
    s = shop_signin(cfg, Credentials({"AUTOCAM_SLACK_CLIENT_ID": "1.2", "AUTOCAM_SLACK_CLIENT_SECRET": SECRET,
                                      "AUTOCAM_SLACK_TEAM_ID": TEAM}), None)
    assert s.on
    raw["panel"]["shop_signin"] = "google"
    with pytest.raises(Exception):
        parse_config(raw, root=REPO_ROOT)


def test_the_qr_page_has_the_plain_link_once_slack_is_set_up(tmp_path, capsys):
    pytest.importorskip("segno")
    from autocam_service.cli import main
    env = tmp_path / ".env"
    env.write_text(f"AUTOCAM_PANEL_KEY={KEY}\nAUTOCAM_SLACK_CLIENT_ID=1.2\nAUTOCAM_SLACK_CLIENT_SECRET={SECRET}\n"
                   f"AUTOCAM_SLACK_TEAM_ID={TEAM}\n", encoding="utf-8")
    import segno
    made = []
    real = segno.make
    segno.make = lambda link, **kw: made.append(link) or real(link, **kw)
    try:
        assert main(["--env-file", str(env), "shop-qr", "--out", str(tmp_path / "qr.html")]) == 0
    finally:
        segno.make = real
    assert made == ["https://autocam.team5940.org/shop"]
