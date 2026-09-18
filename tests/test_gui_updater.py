import hashlib
import io
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import URLError

import pytest

from cnkibug.gui import updater
from cnkibug.gui.update_dialog import UpdateDialog
from scripts.publish_update import build_manifest


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
    ]}, current, asset_name=updater.GUI_ASSET)
    assert release.newer is newer
    assert release.ready
    assert release.download_url == url


def test_release_without_gui_asset_is_not_ready_and_unknown_versions_fail():
    assert not updater.parse_release({"tag_name": "v0.6.0", "assets": []}, "0.5.0").ready
    with pytest.raises(updater.UpdateError):
        updater.parse_release({"tag_name": "nightly", "assets": []}, "0.5.0")
    with pytest.raises(updater.UpdateError):
        updater.parse_release({"tag_name": "v0.6.0", "assets": []}, "0+unknown")


@pytest.mark.parametrize("platform_name,machine,expected", [
    ("win32", "AMD64", updater.GUI_ASSET),
    ("linux", "x86_64", updater.LINUX_GUI_ASSET),
    ("linux", "aarch64", ""),
])
def test_update_selects_asset_for_current_platform(monkeypatch, platform_name, machine, expected):
    monkeypatch.setattr(updater.sys, "platform", platform_name)
    monkeypatch.setattr(updater.platform, "machine", lambda: machine)
    assets = [
        {"name": name, "state": "uploaded", "size": 10, "digest": "sha256:" + "a" * 64,
         "browser_download_url": f"https://github.com/{updater.REPOSITORY}/releases/download/v0.7.0/{name}"}
        for name in (updater.GUI_ASSET, updater.LINUX_GUI_ASSET)
    ]

    manifest = build_manifest({"tag_name": "v0.7.0", "published_at": "2026-09-18T00:00:00Z", "assets": assets})
    release = updater.parse_release(manifest, "0.6.0")

    assert release.asset_name == expected
    assert release.ready is bool(expected)
    if expected:
        assert release.download_url.endswith("/" + expected)


@pytest.mark.parametrize("asset_name", [updater.GUI_ASSET, updater.LINUX_GUI_ASSET])
def test_download_finishes_only_after_matching_release_digest(monkeypatch, tmp_path, asset_name):
    content = b"downloaded executable"
    monkeypatch.setattr(updater, "urlopen", lambda *args, **kwargs: io.BytesIO(content))
    progress = []
    candidate = updater.download_release(_release(content, asset_name=asset_name), tmp_path, Event(),
                                         lambda *counts: progress.append(counts))
    assert candidate.read_bytes() == content
    assert candidate.name == asset_name
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
        updater.download_release(_release(content), tmp_path, cancelled,
                                 lambda received, total: cancelled.set() if received else None)
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


def test_auto_download_restarts_on_the_next_source_after_network_failure(monkeypatch, tmp_path):
    content = b"executable"
    calls = []
    sources = []

    def open_url(request, **kwargs):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise URLError("offline")
        return io.BytesIO(content)

    monkeypatch.setattr(updater, "urlopen", open_url)
    candidate = updater.download_release(_release(content), tmp_path, Event(), lambda *args: None,
                                         source="auto", on_source=sources.append)
    assert candidate.read_bytes() == content
    assert sources == ["ghproxy.net", "ghfast.top"]
    assert calls == ["https://ghproxy.net/https://example.test/gui.exe",
                     "https://ghfast.top/https://example.test/gui.exe"]


@pytest.mark.parametrize("source", ["ghfast.top", "direct"])
def test_fixed_source_does_not_fall_back_to_other_download_routes(monkeypatch, tmp_path, source):
    requests = []

    def fail(request, **kwargs):
        requests.append(request.full_url)
        raise URLError("offline")

    monkeypatch.setattr(updater, "urlopen", fail)
    with pytest.raises(updater.UpdateError):
        updater.download_release(_release(), tmp_path, Event(), lambda *args: None, source=source)
    assert len(requests) == 1
    if source == "direct":
        assert requests[0] == "https://example.test/gui.exe"


def test_checksum_failure_stops_automatic_source_selection(monkeypatch, tmp_path):
    open_url = Mock(side_effect=lambda *args, **kwargs: io.BytesIO(b"wrong-data"))
    monkeypatch.setattr(updater, "urlopen", open_url)
    with pytest.raises(updater.UpdateError, match="校验失败"):
        updater.download_release(_release(b"valid-data"), tmp_path, Event(), lambda *args: None, source="auto")
    assert open_url.call_count == 1


def test_published_manifest_round_trips_through_the_updater():
    release = {
        "tag_name": "v0.7.0", "published_at": "2026-09-16T00:00:00Z", "body": "Release notes",
        "assets": [{"name": updater.GUI_ASSET, "state": "uploaded", "size": 10,
                    "digest": "sha256:" + "a" * 64,
                    "browser_download_url": f"https://github.com/{updater.REPOSITORY}/releases/download/v0.7.0/{updater.GUI_ASSET}"}],
    }
    manifest = build_manifest(release)
    restored = updater.parse_release(json.loads(json.dumps(manifest)), "0.6.0", asset_name=updater.GUI_ASSET)
    assert restored.newer and restored.ready
    assert restored.version == "0.7.0"
    assert "/download/v0.7.0/" in restored.download_url

    linux_release = updater.parse_release(manifest, "0.6.0", asset_name=updater.LINUX_GUI_ASSET)
    assert not linux_release.ready


def test_appimage_save_preserves_existing_user_data(tmp_path):
    candidate = tmp_path / "download" / updater.LINUX_GUI_ASSET
    candidate.parent.mkdir()
    candidate.write_bytes(b"new version")
    config = tmp_path / "config.json"
    config.write_bytes(b"user configuration")
    destination = tmp_path / "CNKIBug.AppImage"

    assert updater.save_appimage(candidate, destination) == destination
    assert destination.read_bytes() == b"new version"
    assert config.read_bytes() == b"user configuration"
    assert not candidate.exists()
    assert not list(tmp_path.glob(".cnkibug-update-*"))


def test_direct_update_check_uses_only_github_api(monkeypatch):
    read = Mock(return_value=_release())
    monkeypatch.setattr(updater, "_read_release", read)
    updater.check_release(source="direct")
    assert read.call_args.args[0] == updater.RELEASE_API
    assert read.call_count == 1


def test_connection_probe_reads_only_the_beginning_of_the_executable(monkeypatch):
    monkeypatch.setattr(updater, "_read_release", lambda *args, **kwargs: _release())
    requests = []
    reads = []

    class Response(io.BytesIO):
        status = 206

        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    def open_url(request, **kwargs):
        requests.append(request)
        return Response(b"MZ" + b"x" * 2048)

    monkeypatch.setattr(updater, "urlopen", open_url)
    results = []
    updater.probe_connections("direct", Event(), results.append)
    assert requests[0].get_header("Range") == "bytes=0-1023"
    assert reads == [1024]
    assert "文件下载）：可用" in results[-1]
