import hashlib
import io
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cnkibug.gui import updater
from cnkibug.gui.update_dialog import UpdateDialog


def _release(content=b"executable", **changes):
    values = dict(version="0.6.0", page_url="https://github.com/KaffuAlcaid/CNKIBug/releases/tag/v0.6.0",
                  published_at="2026-09-14T00:00:00Z", notes="Release notes", newer=True,
                  download_url="https://example.test/gui.exe", size=len(content),
                  sha256=hashlib.sha256(content).hexdigest())
    values.update(changes)
    return updater.ReleaseInfo(**values)


@pytest.mark.parametrize("tag,current,newer", [
    ("v0.10.0", "0.9.0", True), ("v0.5.0", "0.5.0", False), ("v0.5.0", "0.6.0", False),
])
def test_release_comparison_uses_versions_and_selects_only_gui(tag, current, newer):
    url = f"https://github.com/{updater.REPOSITORY}/releases/download/{tag}/{updater.GUI_ASSET}"
    asset = {"name": updater.GUI_ASSET, "state": "uploaded", "size": 10,
             "digest": "sha256:" + "a" * 64, "browser_download_url": url}
    release = updater.parse_release({"tag_name": tag, "assets": [
        {**asset, "name": "CNKIBug.exe"}, asset,
    ]}, current)
    assert release.newer is newer
    assert release.ready
    assert release.download_url == url


def test_release_without_gui_asset_is_not_ready_and_unknown_versions_fail():
    assert not updater.parse_release({"tag_name": "v0.6.0", "assets": []}, "0.5.0").ready
    with pytest.raises(updater.UpdateError):
        updater.parse_release({"tag_name": "nightly", "assets": []}, "0.5.0")
    with pytest.raises(updater.UpdateError):
        updater.parse_release({"tag_name": "v0.6.0", "assets": []}, "0+unknown")


def test_download_finishes_only_after_matching_release_digest(monkeypatch, tmp_path):
    content = b"downloaded executable"
    monkeypatch.setattr(updater, "urlopen", lambda *args, **kwargs: io.BytesIO(content))
    progress = []
    candidate = updater.download_release(_release(content), tmp_path, Event(),
                                         lambda *counts: progress.append(counts))
    assert candidate.read_bytes() == content
    assert candidate.name == updater.GUI_ASSET
    assert progress[-1] == (len(content), len(content))
    assert not list(candidate.parent.glob("*.part"))


@pytest.mark.parametrize("received", [b"short", b"wrong-data", b"excess-length-data"])
def test_bad_download_keeps_existing_executable_and_removes_partial(monkeypatch, tmp_path, received):
    target = tmp_path / "CNKIBug-GUI.exe"
    target.write_bytes(b"original")
    monkeypatch.setattr(updater, "urlopen", lambda *args, **kwargs: io.BytesIO(received))
    with pytest.raises(updater.UpdateError):
        updater.download_release(_release(b"valid-data"), tmp_path, Event(), lambda *args: None)
    assert target.read_bytes() == b"original"
    assert not list((tmp_path / "update").glob("pending-*"))


def test_cancelled_download_cannot_be_installed(monkeypatch, tmp_path):
    cancelled = Event()
    content = b"executable"
    monkeypatch.setattr(updater, "urlopen", lambda *args, **kwargs: io.BytesIO(content))
    with pytest.raises(updater.UpdateCancelled):
        updater.download_release(_release(content), tmp_path, cancelled, lambda *args: cancelled.set())
    assert not list((tmp_path / "update").glob("pending-*"))


@pytest.mark.parametrize("helper_ready", [True, False])
def test_installer_handoff_waits_for_ready_and_keeps_current_executable(monkeypatch, tmp_path, helper_ready):
    job = tmp_path / "update" / "pending-test"
    job.mkdir(parents=True)
    candidate = job / updater.GUI_ASSET
    candidate.write_bytes(b"executable")
    target = tmp_path / "renamed app's.exe"
    target.write_bytes(b"original")
    calls = []
    polls = []

    def poll():
        polls.append(True)
        if not helper_ready:
            return 1
        if len(polls) == 2:
            (job / "ready").write_text("ready", encoding="utf-8")
        return None

    def start(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(poll=poll)

    monkeypatch.setattr(updater, "can_install_update", lambda: True)
    monkeypatch.setattr(updater.sys, "executable", str(target))
    monkeypatch.setattr(updater.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(updater.psutil, "Process", lambda: SimpleNamespace(parent=lambda: None))
    monkeypatch.setattr(updater.ctypes, "windll", Mock(), raising=False)
    monkeypatch.setattr(updater.subprocess, "Popen", start)
    monkeypatch.setattr(updater.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(updater.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("SystemRoot", str(tmp_path))

    if helper_ready:
        updater.start_installer(candidate, _release())
        assert len(polls) == 2
    else:
        with pytest.raises(updater.UpdateError, match="更新脚本未能启动"):
            updater.start_installer(candidate, _release())

    args, options = calls[0]
    plan = json.loads(Path(args[-1]).read_text(encoding="utf-8"))
    assert plan["target"] == str(target.resolve())
    assert plan["candidate"] == str(candidate.resolve())
    assert args[-2] == "-PlanPath"
    assert "shell" not in options
    assert target.read_bytes() == b"original"


@pytest.mark.parametrize("newer,ready,label", [(False, True, "确定"), (True, True, "更新"), (True, False, "更新")])
def test_update_result_buttons_match_release_state(monkeypatch, newer, ready, label):
    monkeypatch.setattr(updater, "can_install_update", lambda: True)
    dialog = UpdateDialog.__new__(UpdateDialog)
    for name in ("_status", "_progress", "_notes", "_primary", "_secondary"):
        setattr(dialog, name, Mock())
    release = _release(newer=newer, download_url="https://example.test/gui.exe" if ready else "")

    dialog._show_release(release)

    assert dialog._primary.configure.call_args.kwargs["text"] == label
    assert dialog._primary.configure.call_args.kwargs["state"] == ("disabled" if newer and not ready else "normal")
    if newer:
        dialog._secondary.pack.assert_called_once()
    else:
        dialog._secondary.pack_forget.assert_called_once()
