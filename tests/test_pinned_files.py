"""The shop's machine files must stay byte-exact."""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
POST = REPO / "fusion" / "posts" / "shopsabre_automatic_mist.cps"
# The machine-proven post. Never edit it; configure it through post properties only.
PINNED_POST_SHA256 = "9bfcc6beb01b5123fc86495ce1907b7de72cccd5167638ca568591129de9741f"
MACHINE_FILES = sorted(p for ext in ("*.cps", "*.tap") for p in (REPO / "fusion").rglob(ext))


def test_post_is_byte_identical_to_the_pinned_copy():
    assert hashlib.sha256(POST.read_bytes()).hexdigest() == PINNED_POST_SHA256, (
        "fusion/posts/shopsabre_automatic_mist.cps changed. It is the shop's machine-proven post "
        "and must never be edited.")


def test_machine_files_exist():
    names = {p.name for p in MACHINE_FILES}
    assert {"shopsabre_automatic_mist.cps", "pause_air_test.tap"} <= names


@pytest.mark.parametrize("path", MACHINE_FILES, ids=lambda p: p.name)
def test_machine_files_use_crlf_only(path):
    data = path.read_bytes()
    assert data.count(b"\n") == data.count(b"\r\n"), f"{path.name} has bare LF line endings"
    assert data.count(b"\r") == data.count(b"\r\n"), f"{path.name} has bare CR characters"


def test_gitattributes_keep_machine_files_byte_exact():
    rules = [" ".join(line.split("#")[0].split()) for line in (REPO / ".gitattributes").read_text().splitlines()]
    rules = [r for r in rules if r]
    assert {"*.cps -text", "*.tap -text", "*.tools binary"} <= set(rules)
    # A later matching line overrides an earlier one, so the catch-all must come before the machine rules.
    catch_all = rules.index("* text=auto")
    assert catch_all < rules.index("*.cps -text") and catch_all < rules.index("*.tap -text")


def _git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
@pytest.mark.parametrize("rel", ["fusion/posts/shopsabre_automatic_mist.cps", "fusion/tests/pause_air_test.tap"])
def test_git_never_converts_machine_file_line_endings(rel):
    try:
        out = _git("check-attr", "text", "--", rel)
    except subprocess.CalledProcessError:
        pytest.skip("not inside a git checkout")
    assert out.strip().endswith("text: unset"), out
