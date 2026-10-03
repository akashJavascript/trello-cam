"""The service restarting itself into new code (restart.py)."""

import types

import pytest

from autocam_service import app, cli, restart
from autocam_service.config import DEFAULT_CONFIG
from autocam_service.restart import EXIT_RESTART, CodeWatch, new_code_loads, supervise


@pytest.fixture
def tree(tmp_path):
    for rel in ("service/autocam_service/runner.py", "core/autocam_core/tapguard.py", "config/autocam.toml"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("v1\n")
    return tmp_path


def watch(tree, problems=()):
    calls = []
    answers = list(problems)

    def validate():
        calls.append(1)
        return answers.pop(0) if answers else None
    w = CodeWatch(tree, tree / "state" / "restart_service", extra=[tree / "config" / "autocam.toml"], validate=validate)
    return w, calls


def test_restarts_once_a_change_has_settled(tree):
    w, calls = watch(tree)
    assert not w.should_restart()
    (tree / "service/autocam_service/runner.py").write_text("v2 longer\n")
    assert not w.should_restart()          # just changed: maybe a git pull still writing files
    assert w.should_restart() and calls == [1]


def test_config_changes_count_too(tree):
    w, _ = watch(tree)
    (tree / "config/autocam.toml").write_text("changed = 1\n")
    w.should_restart()
    assert w.should_restart()


def test_new_code_that_does_not_load_is_not_tried_again(tree):
    w, calls = watch(tree, problems=["SyntaxError: oops"])
    (tree / "core/autocam_core/tapguard.py").write_text("broken (\n")
    w.should_restart()
    assert not w.should_restart() and calls == [1]
    assert not w.should_restart() and not w.should_restart() and calls == [1]
    (tree / "core/autocam_core/tapguard.py").write_text("fixed again\n")      # the next push
    w.should_restart()
    assert w.should_restart() and calls == [1, 1]


def test_the_flag_file_restarts_right_away(tree):
    w, _ = watch(tree)
    w.flag.parent.mkdir()
    w.flag.write_text("")
    assert w.should_restart() and not w.flag.exists()


def test_supervisor_restarts_for_new_code_and_after_crashes():
    codes, slept, said = [EXIT_RESTART, 1, EXIT_RESTART, 0], [], []
    assert supervise(["child"], spawn=lambda cmd: codes.pop(0), sleep=slept.append, say=said.append) == 0
    assert codes == [] and slept == [restart.CRASH_WAIT_S] and len(said) == 3


def test_run_forever_hands_back_to_the_supervisor():
    ticks = []
    runner = types.SimpleNamespace(tick=lambda: ticks.append(1))
    answers = [False, False, True]
    code = types.SimpleNamespace(should_restart=lambda: answers.pop(0))
    assert app.run_forever(runner, 60, code, sleep=lambda s: None) == EXIT_RESTART
    assert len(ticks) == 3


def test_the_real_code_and_config_pass_the_check():
    assert new_code_loads(DEFAULT_CONFIG, None) is None


def test_autocam_run_starts_the_supervisor(monkeypatch):
    seen = []
    monkeypatch.setattr(restart, "supervise", lambda cmd: seen.append(cmd) or 0)
    assert cli.main(["run", "--verbose"]) == 0
    assert seen[0][1:] == ["-m", "autocam_service", "run", "--verbose", "--child"]


def test_a_broken_config_fails_the_check(tmp_path):
    bad = tmp_path / "autocam.toml"
    bad.write_text(DEFAULT_CONFIG.read_text(encoding="utf-8").replace("[trello]", "[trello]\nnot_a_setting = 1", 1))
    problem = new_code_loads(bad, None)
    assert problem is not None and "not_a_setting: unknown key" in problem
