from pathlib import Path

import run
from cnkibug.app import cli
from cnkibug.core.version import APP_VERSION


def test_self_check_reports_app_version(capsys):
    assert run._run_self_check() == 0
    assert capsys.readouterr().out.strip() == f"CNKIBug self-check OK: {APP_VERSION}"


def test_source_entry_directory_is_run_py_directory():
    assert run._entry_directory() == Path(run.__file__).resolve().parent


def test_frozen_entry_directory_is_executable_directory(monkeypatch, tmp_path):
    executable = tmp_path / "CNKIBug.exe"
    monkeypatch.setattr(run.sys, "frozen", True, raising=False)
    monkeypatch.setattr(run.sys, "executable", str(executable))

    assert run._entry_directory() == tmp_path


def test_run_passes_entry_directory_to_cli(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(run, "_entry_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "main", captured.append)

    run._run()

    assert captured == [tmp_path]
