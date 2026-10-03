"""The service picks up new code by itself: `autocam run` is a small supervisor around the service loop.

The supervisor runs the loop in a child process and starts it again when it exits with EXIT_RESTART. Between
passes, the loop (CodeWatch) looks at the service and core code, the config and .env. When they've changed
and then stayed the same for one more pass (a `git pull` isn't half-written), it checks that the new code
imports and the config loads, in a separate process, and only then exits so the supervisor starts it on the
new code. If the check fails, the old code keeps running and the error is logged; that version isn't tried
again. An empty `state/restart_service` file asks for a restart now (it's deleted).

Restarting between passes is safe at any point of a run: every run is saved before it does anything, and
every Trello write is recorded (run_state.py), so the new process carries on where the old one stopped.
The supervisor itself is loaded once; a change to this file needs one manual restart.
"""

import hashlib
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Sequence

log = logging.getLogger("autocam.restart")

EXIT_RESTART = 3
CRASH_WAIT_S = 60
_CHECK = ("import sys; import autocam_service.app, autocam_service.runner; "
          "from autocam_service.cli import main; sys.exit(main(sys.argv[1:]))")


def code_files(root: Path) -> List[Path]:
    root = Path(root)
    return sorted([*(root / "service" / "autocam_service").rglob("*.py"), *(root / "core" / "autocam_core").rglob("*.py")])


def new_code_loads(config: Path, env_file: Optional[Path]) -> Optional[str]:
    """None if the code on disk imports and the config loads (in a fresh Python), else what went wrong."""
    cmd = [sys.executable, "-c", _CHECK, "--config", str(config)]
    if env_file is not None:
        cmd += ["--env-file", str(env_file)]
    try:
        r = subprocess.run(cmd + ["config-check"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return f"couldn't run the check: {e}"
    return None if r.returncode == 0 else (r.stdout + r.stderr).strip()[-2000:] or f"exit code {r.returncode}"


class CodeWatch:
    def __init__(self, root: Path, flag: Path, extra: Sequence[Path] = (),
                 validate: Callable[[], Optional[str]] = lambda: None):
        self.root = Path(root)
        self.flag = Path(flag)
        self.extra = [Path(p) for p in extra]
        self.validate = validate
        self.current = self.fingerprint()
        self.pending: Optional[str] = None      # a change seen once, waiting one pass to settle
        self.rejected: Optional[str] = None     # a version whose check failed

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        for f in code_files(self.root) + self.extra:
            try:
                st = f.stat()
                h.update(f"{f}|{st.st_mtime_ns}|{st.st_size}\n".encode())
            except OSError:
                h.update(f"{f}|gone\n".encode())
        return h.hexdigest()

    def should_restart(self) -> bool:
        if self.flag.exists():
            try:
                self.flag.unlink()
            except OSError:
                pass
            return self._loads("a restart was asked for")
        now = self.fingerprint()
        if now in (self.current, self.rejected):
            self.pending = None
            return False
        if now != self.pending:
            self.pending = now           # changed since the last pass: let it settle
            return False
        return self._loads("the code or config changed")

    def _loads(self, why: str) -> bool:
        problem = self.validate()
        if problem is None:
            log.info("%s: restarting into it", why)
            return True
        self.rejected, self.pending = self.fingerprint(), None
        log.error("%s, but the new version doesn't load, so the old one keeps running:\n%s", why, problem)
        return False


def supervise(child_cmd: List[str], spawn: Callable[[List[str]], int] = subprocess.call,
              sleep: Callable[[float], None] = time.sleep, say: Callable[[str], None] = print) -> int:
    """Run the service loop in a child process; start it again for new code, or a minute after a crash."""
    while True:
        try:
            rc = spawn(child_cmd)
        except KeyboardInterrupt:
            return 0
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if rc == EXIT_RESTART:
            say(f"{stamp} restarting the service with the new code")
            continue
        if rc == 0:
            return 0                     # stopped with Ctrl+C
        say(f"{stamp} the service stopped (exit code {rc}); starting it again in {CRASH_WAIT_S} s. Ctrl+C to quit.")
        try:
            sleep(CRASH_WAIT_S)
        except KeyboardInterrupt:
            return 0
