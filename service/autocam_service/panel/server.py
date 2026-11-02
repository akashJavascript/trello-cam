"""The Onshape panel's HTTP server and its Cloudflare tunnel, started by `autocam run` beside the service loop.

- The server listens on 127.0.0.1:[panel] port only, in threads of the service's own process. It hands every
  request to PanelApp.handle and never logs query strings (the key is in the page's).
- The tunnel ([panel] tunnel_dir: cloudflared.exe and config.yml, made once by hand; docs/panel.md) makes it
  https://autocam.team5940.org. It runs as its own process with no window. It's left running when the service
  restarts for new code (state/panel_tunnel.pid says which process it is), so the panel doesn't blink; a check
  every minute starts it again if it stopped.
"""

import json
import logging
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import parse_qsl, urlsplit

from .api import PanelApp, body_limit
from .hooks import HOOK_PREFIX

log = logging.getLogger("autocam.panel")

CREATE_NO_WINDOW = 0x08000000
TUNNEL_CHECK_S = 60.0
BIND_TRIES = 30                  # the old service may still hold the port for a moment after a restart


def _handler(app: PanelApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AutoCAM"
        sys_version = ""

        def _serve(self, method: str) -> None:
            parts = urlsplit(self.path)
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(length, body_limit(parts.path) + 1)) if length else b""
            headers = {k.lower(): v for k, v in self.headers.items()}
            resp = app.handle(method, parts.path, query, headers, body)
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(resp.body)))
            for k, v in resp.headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(resp.body)

        def do_GET(self) -> None:  # noqa: N802 - http.server's names
            self._serve("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._serve("POST")

        def log_message(self, fmt: str, *args) -> None:
            path = urlsplit(self.path).path if hasattr(self, "path") else "?"
            if path.startswith(HOOK_PREFIX):
                path = HOOK_PREFIX + "<secret>"                # the webhook's address is its password
            log.debug("panel http: %s %s", path, args[1] if len(args) > 1 else "")

    return Handler


def start_server(app: PanelApp, port: int, sleep: Callable[[float], None] = time.sleep) -> threading.Thread:
    """Serve in a background thread (daemon: it ends with the service)."""
    def serve() -> None:
        for attempt in range(BIND_TRIES):
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", port), _handler(app))
                break
            except OSError as e:
                if attempt == BIND_TRIES - 1:
                    log.error("panel: can't listen on 127.0.0.1:%s (%s); the Onshape panel is off", port, e)
                    return
                sleep(2.0)
        httpd.daemon_threads = True
        log.info("panel: serving on 127.0.0.1:%s", port)
        httpd.serve_forever()

    thread = threading.Thread(target=serve, name="panel-http", daemon=True)
    thread.start()
    return thread


class Tunnel:
    def __init__(self, folder: Path, state_dir: Path, logs_dir: Path,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 is_running: Optional[Callable[[int], bool]] = None):
        self.folder = Path(folder)
        self.pid_file = Path(state_dir) / "panel_tunnel.pid"
        self.log_file = Path(logs_dir) / "cloudflared.log"
        self.popen = popen
        self.is_running = is_running or cloudflared_running

    def command(self) -> List[str]:
        return [str(self.folder / "cloudflared.exe"), "tunnel", "--config", str(self.folder / "config.yml"),
                "--logfile", str(self.log_file), "run"]

    def ready(self) -> Optional[str]:
        """None if the tunnel can be started, else why not."""
        for name in ("cloudflared.exe", "config.yml"):
            if not (self.folder / name).is_file():
                return f"{self.folder / name} is missing (docs/panel.md says how to set it up)"
        return None

    def ensure(self) -> bool:
        """Start the tunnel unless it's already running. True if it's running now."""
        try:
            pid = int(json.loads(self.pid_file.read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            pid = 0
        if pid and self.is_running(pid):
            return True
        problem = self.ready()
        if problem:
            log.warning("panel: no tunnel: %s", problem)
            return False
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        flags = CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = self.popen(self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(json.dumps({"pid": proc.pid}) + "\n", encoding="utf-8")
        log.info("panel: started the tunnel (cloudflared, pid %s)", proc.pid)
        return True

    def keep_running(self, sleep: Callable[[float], None] = time.sleep) -> threading.Thread:
        def loop() -> None:
            while True:
                try:
                    self.ensure()
                except Exception:  # noqa: BLE001 - try again next minute
                    log.exception("panel: starting the tunnel failed")
                sleep(TUNNEL_CHECK_S)

        thread = threading.Thread(target=loop, name="panel-tunnel", daemon=True)
        thread.start()
        return thread


def cloudflared_running(pid: int) -> bool:
    """Is process `pid` a cloudflared? (A pid can be reused by something else after a reboot.)"""
    if os.name != "nt":
        try:
            return "cloudflared" in Path(f"/proc/{pid}/cmdline").read_text(errors="replace")
        except OSError:
            return False
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, timeout=20, creationflags=CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "cloudflared" in out.lower()
