from pathlib import Path

from cnkibug import launcher


def test_installed_entry_uses_xdg_data_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user-data"))
    assert launcher.user_program_dir() == tmp_path / "user-data"


def test_installed_entry_defaults_to_user_home_on_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert launcher.user_program_dir() == tmp_path / ".local" / "share"
