"""Unit conversion at the Fusion edge. Everything outside the Fusion adapters is inches."""

CM_PER_IN = 2.54
MM_PER_IN = 25.4


def in_to_cm(x: float) -> float:
    return x * CM_PER_IN


def cm_to_in(x: float) -> float:
    return x / CM_PER_IN


def mm_to_in(x: float) -> float:
    return x / MM_PER_IN


def close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol
