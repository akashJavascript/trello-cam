"""Signing in to the shop's phone page with Slack (2026-11-04; the user: "should still build auth". Everyone on the
team is on the team's Slack, and the user can make Slack apps there).

Only the phone page (/shop and /api/shop...). The Onshape panel keeps its key: it lives in Onshape's frame, where a
sign-in page can't run.

Slack's OpenID Connect ("Sign in with Slack", docs.slack.dev/authentication/sign-in-with-slack):
    GET /shop/sign-in     -> Slack's authorize page, scope "openid profile" (the person's name, never their email),
                             with a one-time state that is also put in a short cookie only the way back sees
    GET /shop/signed-in   <- Slack, with ?code&state. The code is swapped for a token (server to server, with the
                             app's secret), and the token for who it is (openid.connect.userInfo). Someone in the
                             team's workspace (AUTOCAM_SLACK_TEAM_ID) gets a session cookie. The token isn't kept.

Sessions are kept in state/shop_sessions.json on the laptop, never in the repo. For each session the file holds
the sha256 of its cookie (never the cookie itself), the Slack user id and the name the shop knows them by, and
when it ends. A session lasts [panel] shop_session_days from its last use, so people who use the page stay signed
in. `autocam shop-sign-out` ends one person's sessions or everyone's.

Names: the first name from Slack, kept on the shop's list in people.json. If someone already typed "Maya" before
sign-in existed, the Maya who signs in takes that name over, along with what was assigned to it. A second Maya on
Slack gets "Maya K." A signed-in phone's "who are you" is that name; it isn't picked any more.

The old key link keeps working for [panel] shop_old_link_days after the first Slack sign-in, so nobody is locked
out mid-build. After that, only a sign-in opens the page. Until Slack is set up (AUTOCAM_SLACK_CLIENT_ID, _SECRET
and _TEAM_ID in .env), the key link is the only way in. With the client id and secret but no team id yet, a
sign-in shows the workspace's id to put in .env, and lets nobody in.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from autocam_core.hotfolder import write_atomic

from ..shoplist import ListError

log = logging.getLogger("autocam.panel")

AUTHORIZE = "https://slack.com/openid/connect/authorize"
TOKEN = "https://slack.com/api/openid.connect.token"
USERINFO = "https://slack.com/api/openid.connect.userInfo"
START = "/shop/sign-in"
CALLBACK = "/shop/signed-in"
COOKIE = "autocam_shop"
STATE_COOKIE = "autocam_signin"
STATE_S = 600                          # a sign-in started more than 10 minutes ago is refused
MAX_PENDING = 500
MAX_SESSIONS = 500
TAB_RE = re.compile(r"^(shafts|parts|plates|prints)$")
TEAM_RE = re.compile(r"^[TE][A-Z0-9]{2,20}$")          # a Slack workspace (T...) or Enterprise Grid org (E...)
USER_RE = re.compile(r"^[UW][A-Z0-9]{2,20}$")          # a Slack user id
NAME_CHARS = re.compile(r"[^\w .'-]")


class SignInRefused(Exception):
    """A sign-in that didn't work: what to show the person (never a secret)."""


class SlackError(Exception):
    """Slack couldn't be reached, or answered something unreadable (never carries the token or the secret)."""


def slack_post(url: str, data: Optional[Dict[str, str]] = None, token: str = "", timeout_s: float = 10.0) -> Dict[str, Any]:
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, data=urlencode(data or {}).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout_s) as r:  # noqa: S310 - a fixed https://slack.com address
            out = json.loads(r.read().decode("utf-8"))
    except (URLError, OSError, ValueError) as e:
        raise SlackError(type(e).__name__) from None
    if not isinstance(out, dict):
        raise SlackError("not an object")
    return out


def cookies(header: str) -> Dict[str, str]:
    """The cookies in a Cookie header. Written by hand because http.cookies drops every cookie when one is odd."""
    out: Dict[str, str] = {}
    for part in (header or "").split(";"):
        k, sep, v = part.strip().partition("=")
        if sep and k and k not in out:
            out[k] = v.strip()
    return out


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean(raw: Any) -> str:
    """A name part from Slack, cut down to what the shop's names allow (people.NAME_RE)."""
    text = " ".join(NAME_CHARS.sub("", str(raw or "")).split())
    while text and not (text[0].isalpha()):
        text = text[1:]
    return text[:24].strip()


@dataclass(frozen=True)
class Session:
    user: str           # Slack user id
    name: str           # as on the shop's list


class ShopSignIn:
    def __init__(self, path: Path, client_id: str, client_secret: str, team_id: str, redirect_uri: str,
                 session_days: int = 60, old_link_days: int = 7, people: Any = None,
                 post: Callable[..., Dict[str, Any]] = slack_post,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 token: Callable[[], str] = lambda: secrets.token_urlsafe(32)):
        self.path = Path(path)
        self._client_id = client_id
        self._client_secret = client_secret
        self.team_id = team_id if TEAM_RE.match(team_id or "") else ""
        self.redirect_uri = redirect_uri
        self.session_days = session_days
        self.old_link_days = old_link_days
        self.people = people
        self.post = post
        self.now = now
        self.token = token
        self._pending: Dict[str, Tuple[datetime, str]] = {}      # state -> (started, tab)
        self._lock = threading.RLock()
        self._cache: Optional[Dict[str, Any]] = None
        self._mtime = -1

    def __repr__(self) -> str:                  # never the secret
        return f"ShopSignIn(team={'<set>' if self.team_id else '<missing>'}, {self.path.name})"

    # ---- what's set up
    @property
    def ready(self) -> bool:
        """A sign-in can be tried (the Slack app's id and secret are in .env)."""
        return bool(self._client_id and self._client_secret and self.redirect_uri)

    @property
    def on(self) -> bool:
        """People can get in: the team's workspace is known too."""
        return self.ready and bool(self.team_id)

    def old_link_until(self) -> Optional[datetime]:
        """When the key link stops opening the page: old_link_days after the first Slack sign-in. None: it still
        works with no end set (Slack isn't set up, or nobody has signed in yet)."""
        if not self.on:
            return None
        first = self._load().get("first")
        try:
            return datetime.fromisoformat(first) + timedelta(days=self.old_link_days) if first else None
        except ValueError:
            return None

    def old_link_open(self) -> bool:
        until = self.old_link_until()
        return until is None or self.now() < until

    # ---- the file (reread when another process changed it: `autocam shop-sign-out`)
    def _load(self) -> Dict[str, Any]:
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime_ns
            except OSError:
                mtime = 0
            if self._cache is None or mtime != self._mtime:
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = {}
                data = data if isinstance(data, dict) else {}
                self._cache = {"first": str(data.get("first") or ""),
                               "users": {k: v for k, v in dict(data.get("users") or {}).items() if isinstance(v, str)},
                               "sessions": {k: v for k, v in dict(data.get("sessions") or {}).items()
                                            if isinstance(v, dict) and v.get("user") and v.get("until")}}
                self._mtime = mtime
            return self._cache

    def _save(self, data: Dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(self.path, (json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
            self._cache = data
            try:
                self._mtime = self.path.stat().st_mtime_ns
            except OSError:
                self._mtime = -1

    def _stamp(self, t: datetime) -> str:
        return t.astimezone(timezone.utc).isoformat(timespec="seconds")

    # ---- the cookies
    def cookie(self, value: str) -> str:
        return (f"{COOKIE}={value}; Path=/; Max-Age={self.session_days * 86400}; HttpOnly; Secure; SameSite=Lax")

    @staticmethod
    def clear_cookie() -> str:
        return f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"

    # ---- signing in
    def start(self, tab: str = "") -> Tuple[str, str]:
        """Slack's authorize address, and the state cookie to set with it."""
        if not self.ready:
            raise SignInRefused("Signing in with Slack isn't set up yet. Ask a mentor.")
        state = self.token()
        now = self.now()
        with self._lock:
            for k in [k for k, (t, _) in self._pending.items() if (now - t).total_seconds() > STATE_S]:
                del self._pending[k]
            while len(self._pending) >= MAX_PENDING:
                del self._pending[next(iter(self._pending))]
            self._pending[state] = (now, tab if TAB_RE.match(tab or "") else "")
        query = {"response_type": "code", "scope": "openid profile", "client_id": self._client_id,
                 "state": state, "redirect_uri": self.redirect_uri}
        if self.team_id:
            query["team"] = self.team_id                   # straight to the team's workspace
        state_cookie = f"{STATE_COOKIE}={state}; Path={CALLBACK}; Max-Age={STATE_S}; HttpOnly; Secure; SameSite=Lax"
        return f"{AUTHORIZE}?{urlencode(query)}", state_cookie

    def finish(self, query: Dict[str, str], cookie_header: str) -> Tuple[str, Session, str]:
        """Slack's answer -> (the session cookie's value, who it is, the tab to open). Raises SignInRefused."""
        if query.get("error"):
            log.info("shop sign-in: Slack said %s", re.sub(r"[^a-z_]", "", query["error"])[:40])
            raise SignInRefused("Sign-in was cancelled.")
        state = query.get("state", "")
        kept = cookies(cookie_header).get(STATE_COOKIE, "")
        with self._lock:
            started = self._pending.get(state) if state else None
            if started is None or not kept or not hmac.compare_digest(kept, state) \
                    or (self.now() - started[0]).total_seconds() > STATE_S:
                raise SignInRefused("That sign-in timed out or came from another tab. Try again.")
            del self._pending[state]
        code = query.get("code", "")
        if not code or len(code) > 500:
            raise SignInRefused("Slack didn't sign you in. Try again.")
        try:
            got = self.post(TOKEN, {"client_id": self._client_id, "client_secret": self._client_secret, "code": code,
                                    "redirect_uri": self.redirect_uri, "grant_type": "authorization_code"})
            if not got.get("ok") or not got.get("access_token"):
                log.warning("shop sign-in: Slack refused the code (%s)", re.sub(r"[^a-z_]", "", str(got.get("error")))[:40])
                raise SignInRefused("Slack didn't sign you in. Try again.")
            info = self.post(USERINFO, token=str(got["access_token"]))
        except SlackError as e:
            log.warning("shop sign-in: couldn't reach Slack (%s)", e)
            raise SignInRefused("Couldn't reach Slack. Try again in a minute.") from None
        if not info.get("ok"):
            log.warning("shop sign-in: Slack wouldn't say who it is (%s)", re.sub(r"[^a-z_]", "", str(info.get("error")))[:40])
            raise SignInRefused("Slack didn't sign you in. Try again.")
        team = str(info.get("https://slack.com/team_id") or "")
        user = str(info.get("https://slack.com/user_id") or info.get("sub") or "")
        if not USER_RE.match(user) or not TEAM_RE.match(team):
            raise SignInRefused("Slack didn't say who you are. Try again.")
        if not self.team_id:
            log.warning("shop sign-in: AUTOCAM_SLACK_TEAM_ID isn't in .env yet; this sign-in's workspace is %s", team)
            raise SignInRefused(f"Almost set up: put AUTOCAM_SLACK_TEAM_ID={team} in the laptop's .env, then sign in again.")
        if team != self.team_id:
            log.warning("shop sign-in: refused someone from another Slack workspace")
            raise SignInRefused("That Slack account isn't in the team's Slack. Sign in with your team account.")
        with self._lock:
            data = self._load()
            name = self._name(data, user, info)
            value = self.token()
            now = self.now()
            data["sessions"][_hash(value)] = {"user": user, "since": self._stamp(now), "seen": self._stamp(now),
                                              "until": self._stamp(now + timedelta(days=self.session_days))}
            if len(data["sessions"]) > MAX_SESSIONS:
                for k in sorted(data["sessions"], key=lambda k: data["sessions"][k].get("seen", ""))[:-MAX_SESSIONS]:
                    del data["sessions"][k]
            data["first"] = data["first"] or self._stamp(now)
            self._save(data)
        log.info("shop sign-in: %s signed in with Slack", name)
        return value, Session(user, name), started[1]

    def _name(self, data: Dict[str, Any], user: str, info: Dict[str, Any]) -> str:
        """The shop's name for this Slack user: the one they had, else their first name, else first name and
        initial when another Slack user already has the first name."""
        users = data["users"]
        if user not in users:
            first = _clean(info.get("given_name")) or _clean(str(info.get("name") or "").split(" ")[0]) or "Teammate"
            first = first.split(" ")[0][:20]
            family = _clean(info.get("family_name"))
            taken = {n.casefold() for u, n in users.items() if u != user}
            tries = [first] + ([f"{first} {family[0].upper()}."] if family else []) + [f"{first} {i}" for i in range(2, 100)]
            users[user] = next(n for n in tries if n.casefold() not in taken)
        name = users[user]
        if self.people is not None:
            try:
                kept = self.people.add(name)               # on the shop's list (an earlier typed "maya" is theirs)
                if kept:
                    users[user] = name = kept
            except ListError as e:
                log.warning("shop sign-in: %s", e)
        return name

    # ---- a signed-in phone
    def session(self, cookie_header: str) -> Optional[Session]:
        value = cookies(cookie_header).get(COOKIE, "")
        if not value or len(value) > 200 or not self.on:
            return None
        key = _hash(value)
        with self._lock:
            data = self._load()
            s = data["sessions"].get(key)
            if s is None:
                return None
            now = self.now()
            try:
                ended = datetime.fromisoformat(s["until"]) <= now
            except (TypeError, ValueError):
                ended = True
            if ended:
                del data["sessions"][key]
                self._save(data)
                return None
            name = data["users"].get(s["user"], "")
            if not name:
                return None
            if str(s.get("seen", ""))[:10] != self._stamp(now)[:10]:       # used today: it lasts from today
                s["seen"] = self._stamp(now)
                s["until"] = self._stamp(now + timedelta(days=self.session_days))
                self._save(data)
            return Session(s["user"], name)

    def sign_out(self, cookie_header: str) -> None:
        value = cookies(cookie_header).get(COOKIE, "")
        if not value:
            return
        with self._lock:
            data = self._load()
            if data["sessions"].pop(_hash(value), None) is not None:
                self._save(data)


def end_sessions(path: Path, name: str = "") -> int:
    """`autocam shop-sign-out`: end everyone's sessions, or one name's (any case). How many ended."""
    s = ShopSignIn(path, "", "", "", "")
    with s._lock:
        data = s._load()
        if name:
            users = {u for u, n in data["users"].items() if n.casefold() == name.strip().casefold()}
            gone = [k for k, v in data["sessions"].items() if v.get("user") in users]
        else:
            gone = list(data["sessions"])
        for k in gone:
            del data["sessions"][k]
        if gone:
            s._save(data)
        return len(gone)
