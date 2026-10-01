"""Names that survive the post and WinCNC.

The post writes comments as `[text]`, keeping only the characters below and cutting the
text to 118 characters. Program, job and operation names use letters, digits, `-` and `_`.
"""

import re

COMMENT_CHARS = frozenset(" abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,=_-:()")
COMMENT_MAX = 120 - 2  # the post's maximumLineLength minus the brackets
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
OUTER_TAG = "[outer]"


def is_safe_name(name: str) -> bool:
    return bool(SAFE_NAME_RE.match(name))


def safe_token(text: str) -> str:
    """Collapse anything outside [A-Za-z0-9_-] to single underscores."""
    token = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")
    return token or "x"


def thickness_token(thickness_in: float) -> str:
    """0.125 -> '0p125', 0.25 -> '0p25' (no '.' or '/' in program names)."""
    text = f"{thickness_in:.4f}".rstrip("0").rstrip(".")
    return text.replace(".", "p")


def program_name(prefix: str, thickness_in: float, run_id: str, sheet_index: int) -> str:
    """e.g. 6061_0p125_r017_S1"""
    name = f"{prefix}_{thickness_token(thickness_in)}_{run_id}_S{sheet_index}"
    if not is_safe_name(name):
        raise ValueError(f"program name {name!r} has characters the post would strip")
    return name


def filter_comment(text: str) -> str:
    """What the post keeps of a comment (without the brackets)."""
    return "".join(c for c in text if c in COMMENT_CHARS)[:COMMENT_MAX]


def comment_line(text: str) -> str:
    """The exact line the post writes for writeComment(text), or '' if nothing survives."""
    kept = filter_comment(text)
    return f"[{kept}]" if kept else ""


def instance_id(part_key: str, copy: int) -> str:
    """One physical copy of a part on a sheet, e.g. p03-2."""
    return f"{part_key}-{copy}"


def outer_op_name(instance: str) -> str:
    """Fusion op name for one part's outline. Posts as the comment line `[outer p03-2]`."""
    if not is_safe_name(instance):
        raise ValueError(f"instance id {instance!r} must be letters, digits, '-' or '_'")
    return f"{OUTER_TAG} {instance}"


def outer_comment_line(instance: str) -> str:
    return comment_line(outer_op_name(instance))
