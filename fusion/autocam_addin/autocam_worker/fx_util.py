"""UNTESTED IN FUSION. Small helpers shared by the fx_* modules (Fusion API units are cm)."""

import time

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


def wait_for(done, timeout_s: float, what: str) -> float:
    t0 = time.time()
    while not done():
        adsk.doEvents()
        time.sleep(0.1)
        if time.time() - t0 > timeout_s:
            raise AdapterError(f"timed out after {timeout_s:g} s waiting for {what}")
    return time.time() - t0


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
