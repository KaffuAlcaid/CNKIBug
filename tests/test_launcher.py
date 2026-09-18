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


def test_appimage_entry_keeps_data_outside_mounted_bundle(monkeypatch, tmp_path):
    import run_gui

    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(launcher.sys, "executable", str(tmp_path / "mount" / "CNKIBug-GUI"))
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "Applications" / "CNKIBug.AppImage"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user-data"))

    assert run_gui._entry_directory() == tmp_path / "user-data"


def test_windows_executable_keeps_data_next_to_program(monkeypatch, tmp_path):
    import run_gui

    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(launcher.sys, "executable", str(tmp_path / "CNKIBug-GUI.exe"))
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "CNKIBug.AppImage"))

    assert run_gui._entry_directory() == tmp_path
