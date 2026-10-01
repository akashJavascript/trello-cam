"""The only code that talks to the Onshape API (CLAUDE.md: never from tests or scratch scripts).

- Every request goes through the ledger (pending before it's sent, done after).
- A per-run cap and the 402 latch are checked before anything is sent.
- Redirects are followed by hand so each hop is logged. Onshape keys are only ever sent to
  the configured Onshape host; a redirect elsewhere (e.g. a download URL) goes without them.
- 429: wait out Retry-After if it's short; otherwise stop the run (the next trigger resumes).
- Auth: HMAC request signing with API keys (Onshape's documented scheme; verify on the first
  supervised run, M3).
"""

import base64
import hashlib
import hmac
import json
import secrets
import string
import time
from dataclasses import dataclass
from email.utils import formatdate
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.parse import urlencode, urljoin, urlsplit

from .ledger import Ledger

API = "/api/v10"
MAX_REDIRECTS = 3
DEFAULT_RETRY_AFTER_S = 30.0


class OnshapeError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class BudgetExceeded(OnshapeError):
    """Our own limits: the per-run cap or the 402 latch. Nothing was sent."""


class QuotaExhausted(OnshapeError):
    """Onshape said 402: the enterprise is out of API calls."""


class RateLimited(OnshapeError):
    """Onshape said 429 with a long Retry-After; stop the run and resume on the next trigger."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]   # lower-case names
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class RequestsTransport:
    """The real HTTP transport (never used in tests; the suite blocks sockets)."""

    def __init__(self, timeout_s: float = 60.0):
        import requests
        self.session = requests.Session()
        self.timeout_s = timeout_s

    def send(self, method: str, url: str, headers: Dict[str, str], body: Optional[bytes]) -> HttpResponse:
        r = self.session.request(method, url, headers=headers, data=body, allow_redirects=False, timeout=self.timeout_s)
        return HttpResponse(r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content)


def _nonce() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(25))


def sign(*, access_key: str, secret_key: str, method: str, url: str, content_type: str,
         nonce: str, date: str) -> Dict[str, str]:
    parts = urlsplit(url)
    text = "\n".join([method, nonce, date, content_type, parts.path, parts.query, ""]).lower()
    digest = hmac.new(secret_key.encode("utf-8"), text.encode("utf-8"), hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode("ascii")
    return {"Authorization": f"On {access_key}:HmacSHA256:{signature}", "On-Nonce": nonce, "Date": date,
            "Content-Type": content_type}


class OnshapeClient:
    def __init__(self, *, base_url: str, access_key: str, secret_key: str, ledger: Ledger, transport,
                 run_id: str, max_calls: int, retry_after_max_wait_s: float,
                 sleep: Callable[[float], None] = time.sleep,
                 nonce: Callable[[], str] = _nonce, date: Callable[[], str] = lambda: formatdate(usegmt=True),
                 monotonic: Callable[[], float] = time.monotonic):
        self.base_url = base_url.rstrip("/")
        self.host = urlsplit(self.base_url).hostname
        self._access_key = access_key
        self._secret_key = secret_key
        self.ledger = ledger
        self.transport = transport
        self.run_id = run_id
        self.max_calls = max_calls
        self.retry_after_max_wait_s = retry_after_max_wait_s
        self.sleep = sleep
        self._nonce = nonce
        self._date = date
        self._monotonic = monotonic
        self.calls = 0

    def __repr__(self) -> str:
        return f"OnshapeClient({self.base_url}, calls={self.calls}/{self.max_calls})"

    # ---- public
    def get_json(self, path: str, *, query: Optional[Dict[str, Any]] = None, purpose: str,
                 part_key: Optional[str] = None) -> Any:
        return self._request("GET", path, query, None, purpose, part_key, "application/json").json()

    def post_json(self, path: str, body: Dict[str, Any], *, purpose: str, part_key: Optional[str] = None) -> Any:
        data = json.dumps(body).encode("utf-8")
        return self._request("POST", path, None, data, purpose, part_key, "application/json").json()

    def get_bytes(self, path: str, *, purpose: str, part_key: Optional[str] = None) -> bytes:
        return self._request("GET", path, None, None, purpose, part_key, "application/octet-stream").body

    # ---- internals
    def _request(self, method, path, query, body, purpose, part_key, accept) -> HttpResponse:
        url = self.base_url + path + (("?" + urlencode(query)) if query else "")
        hops = 0
        while True:
            resp = self._send(method, url, body, purpose, part_key, accept)
            if 300 <= resp.status < 400 and resp.headers.get("location"):
                hops += 1
                if hops > MAX_REDIRECTS:
                    raise OnshapeError(f"too many redirects for {path}", resp.status)
                url = urljoin(url, resp.headers["location"])
                method, body = "GET", None
                continue
            if resp.status == 402:
                self.ledger.latch(f"402 from {path} during run {self.run_id}")
                raise QuotaExhausted("Onshape says the enterprise is out of API calls (402); calls are stopped", 402)
            if resp.status == 429:
                wait = self._retry_after(resp)
                if wait > self.retry_after_max_wait_s:
                    raise RateLimited(f"Onshape asked to wait {wait:.0f} s (429); stopping this run", 429)
                self.sleep(wait)
                continue
            if resp.status >= 400:
                excerpt = resp.body[:300].decode("utf-8", errors="replace")
                raise OnshapeError(f"Onshape returned {resp.status} for {path}: {excerpt}", resp.status)
            return resp

    def _send(self, method, url, body, purpose, part_key, accept) -> HttpResponse:
        if self.ledger.latched():
            self.ledger.blocked(run_id=self.run_id, method=method, url=url, purpose=purpose, reason="402 latch")
            raise BudgetExceeded("Onshape calls are stopped after a 402; a mentor must reset the latch")
        if self.calls >= self.max_calls:
            self.ledger.blocked(run_id=self.run_id, method=method, url=url, purpose=purpose, reason="per-run cap")
            raise BudgetExceeded(f"this run reached its limit of {self.max_calls} Onshape calls")
        onshape_host = urlsplit(url).hostname == self.host
        headers = {"Accept": accept}
        if onshape_host:
            headers.update(sign(access_key=self._access_key, secret_key=self._secret_key, method=method, url=url,
                                content_type="application/json", nonce=self._nonce(), date=self._date()))
        call_id = self.ledger.begin(run_id=self.run_id, method=method, url=url, purpose=purpose, part_key=part_key)
        self.calls += 1
        t0 = self._monotonic()
        try:
            resp = self.transport.send(method, url, headers, body)
        except Exception as e:
            # No response: we can't know whether Onshape counted it, so the ledger assumes it did.
            self.ledger.finish(call_id, 0, int((self._monotonic() - t0) * 1000), billable=onshape_host)
            raise OnshapeError(f"request to Onshape failed: {type(e).__name__}: {e}") from e
        self.ledger.finish(call_id, resp.status, int((self._monotonic() - t0) * 1000),
                           billable=onshape_host and 200 <= resp.status < 400)
        return resp

    @staticmethod
    def _retry_after(resp: HttpResponse) -> float:
        try:
            return max(0.0, float(resp.headers.get("retry-after", DEFAULT_RETRY_AFTER_S)))
        except ValueError:
            return DEFAULT_RETRY_AFTER_S
