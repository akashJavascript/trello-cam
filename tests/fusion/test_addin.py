"""The add-in's self-reload (autocam_addin.py) on a copy of the repo's config and a fake adsk. Python mistakes
and the reload rules only; the custom event and the polling thread need Fusion (docs/manual-tests.md M2)."""

import importlib.util
import shutil
import sys
import types
from pathlib import Path

import pytest

import fakebrep

REPO = Path(__file__).resolve().parents[2]
AUTOCAM = ("autocam_core", "autocam_worker")


def _ours(name):
    return name.split(".")[0] in AUTOCAM


@pytest.fixture
def addin(tmp_path):
    saved = {n: m for n, m in sys.modules.items() if _ours(n)}   # the reload swaps these; put the test run's back
    fakebrep.install()
    spec = importlib.util.spec_from_file_location("autocam_addin_under_test",
                                                  REPO / "fusion" / "autocam_addin" / "autocam_addin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "config").mkdir()
    shutil.copy(REPO / "config" / "autocam.toml", tmp_path / "config")
    (tmp_path / "core" / "autocam_core").mkdir(parents=True)
    module.REPO = tmp_path
    module._app = types.SimpleNamespace(version="2705.1.15")
    module._worker, settings = module._make_worker()
    module._reload_flag = settings.queue / "reload_addin"
    yield module
    fakebrep.uninstall()
    for name in [n for n in sys.modules if _ours(n)]:
        del sys.modules[name]
    sys.modules.update(saved)


def _repo_version(module, version):
    (module.REPO / "core" / "autocam_core" / "__init__.py").write_text(f'CORE_VERSION = "{version}"\n')


def test_reloads_when_the_repo_has_a_new_core_version(addin):
    loaded = addin._loaded
    _repo_version(addin, loaded)
    assert not addin._wants_reload()
    _repo_version(addin, "9.9.9")
    assert addin._wants_reload()
    old = addin._worker
    addin._reload()
    assert addin._worker is not old and addin._loaded == loaded     # the real core is what got imported
    assert addin._failed is None
    assert (addin.REPO / "queue" / "worker_heartbeat.json").exists()


def test_the_flag_file_forces_one_reload(addin):
    _repo_version(addin, addin._loaded)
    addin._reload_flag.write_text("")
    assert addin._wants_reload()
    addin._reload()
    assert not addin._reload_flag.exists() and not addin._wants_reload()


def test_never_while_a_job_runs(addin):
    _repo_version(addin, "9.9.9")
    addin._worker.busy = True
    assert not addin._wants_reload()
    addin._worker.busy = False
    sys._autocam_job_running = "manual job"
    try:
        assert not addin._wants_reload()
    finally:
        del sys._autocam_job_running


def test_broken_new_code_keeps_the_old_worker_and_is_not_retried(addin, monkeypatch):
    _repo_version(addin, "9.9.9")
    old = addin._worker

    def broken():
        raise SyntaxError("half-pulled file")
    monkeypatch.setattr(addin, "_make_worker", broken)
    addin._reload()
    assert addin._worker is old and addin._failed == "9.9.9"
    assert not addin._wants_reload()                 # not every 10 s
    addin._reload_flag.write_text("")
    assert addin._wants_reload()                     # but the flag file asks again
