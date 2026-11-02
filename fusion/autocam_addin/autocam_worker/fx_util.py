"""UNTESTED IN FUSION. Small helpers shared by the fx_* modules (Fusion API units are cm)."""

import os
import subprocess
import time
from pathlib import Path

import adsk.core

from .adapter import AdapterError

IN = 2.54


def to_in(cm: float) -> float:
    return cm / IN


def to_cm(inches: float) -> float:
    return inches * IN


def vi(inches: float):
    # Explicit units: some API calls read bare numbers in document units instead of cm.
    return adsk.core.ValueInput.createByString(f"{inches:.5f} in")


def items(coll):
    if coll is None:
        return []
    if hasattr(coll, "count") and hasattr(coll, "item"):
        return [coll.item(i) for i in range(coll.count)]
    return list(coll)


def collection(things):
    coll = adsk.core.ObjectCollection.create()
    for t in things:
        coll.add(t)
    return coll


def param(obj, name: str):
    p = obj.parameters.itemByName(name)
    if p is None:
        raise AdapterError(f"{getattr(obj, 'name', obj)} has no parameter {name}")
    return p


def set_expr(obj, name: str, expression: str) -> None:
    p = param(obj, name)
    p.expression = expression
    if p.expression != expression:
        raise AdapterError(f"{name} = {expression} didn't stick (reads {p.expression})")


CLOSE_RECOVERED = Path(__file__).with_name("close_recovered.ps1")


def start_closing_recovered(wait_s: int = 0, tries: int = 1):
    """Fusion's "Recovered Documents" window (shown over the first document after a crash) silently stops toolpaths
    generating until it's closed (2026-11-02, r055-r059). A PowerShell UI Automation script clicks its Close; it
    runs on its own while the caller keeps Fusion's events going (Fusion must answer it). None if it can't start."""
    try:
        return subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                 str(CLOSE_RECOVERED), "-FusionPid", str(os.getpid()), "-WaitSeconds", str(int(wait_s)),
                                 "-Tries", str(int(tries))], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, creationflags=0x08000000)   # no window
    except OSError:
        return None


def wait_for(done, timeout_s: float, what: str, unblock_after_s=None, said=None) -> float:
    """Wait, keeping Fusion's events going. unblock_after_s: still not done after this long, close Fusion's Recovered
    Documents window if it's up (said gets what the script printed)."""
    t0 = time.time()
    proc = None
    try:
        while not done():
            adsk.doEvents()
            time.sleep(0.1)
            if unblock_after_s is not None and proc is None and time.time() - t0 > unblock_after_s:
                proc = start_closing_recovered()
                unblock_after_s = None
            if proc is not None and proc.poll() is not None:
                if said is not None:
                    said((proc.stdout.read() if proc.stdout else "").strip())
                proc = None
            if time.time() - t0 > timeout_s:
                raise AdapterError(f"timed out after {timeout_s:g} s waiting for {what}")
        return time.time() - t0
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()


def vec(v):
    return round(v.x, 4), round(v.y, 4), round(v.z, 4)


def normal(face):
    _, n = face.evaluator.getNormalAtPoint(face.pointOnFace)
    n.normalize()
    return n


def call(what: str, fn, *args):
    """Run one Fusion call; any failure becomes an AdapterError naming the call."""
    try:
        return fn(*args)
    except AdapterError:
        raise
    except Exception as e:  # noqa: BLE001 - Fusion raises RuntimeError and friends
        raise AdapterError(f"{what}: {type(e).__name__}: {e}") from None
