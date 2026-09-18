from __future__ import annotations

import ctypes
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import psutil

from ..core.version import APP_VERSION
from ..core.settings import UPDATE_SOURCES
from ..core.runtime import appimage_path
from ..browser.environment import system_process_environment


REPOSITORY = "KaffuAlcaid/CNKIBug"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MANIFEST_URL = f"https://cdn.jsdmirror.com/gh/{REPOSITORY}@updates/latest.json"
GUI_ASSET = "CNKIBug-GUI.exe"
LINUX_GUI_ASSET = "CNKIBug-GUI-x86_64.AppImage"
UPDATE_SCRIPT = Path(__file__).with_name("apply_update.ps1")
SOURCE_LABELS = {source: source for source in UPDATE_SOURCES}
SOURCE_LABELS.update(auto="自动", direct="无加速（系统代理）")


class UpdateError(RuntimeError):
    pass


class UpdateCancelled(Exception):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    page_url: str
    published_at: str
    notes: str
    newer: bool
    download_url: str = ""
    size: int = 0
    sha256: str = ""
    asset_name: str = GUI_ASSET

    @property
    def ready(self) -> bool:
        return bool(self.download_url and self.size > 0 and self.sha256)


def version_numbers(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        raise UpdateError(f"无法识别版本号：{value}")
    return tuple(int(part) for part in match.groups())


def gui_asset_name() -> str:
    if sys.platform == "linux":
        return LINUX_GUI_ASSET if platform.machine().lower() in ("x86_64", "amd64") else ""
    return GUI_ASSET


def parse_release(payload: dict, current_version: str, *, asset_name: str | None = None) -> ReleaseInfo:
    if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
        raise UpdateError("未找到可用的正式发布。")
    tag = payload.get("tag_name")
    if not isinstance(tag, str):
        raise UpdateError("发布信息缺少版本号。")
    newer = version_numbers(tag) > version_numbers(current_version)
    release_root = f"https://github.com/{REPOSITORY}/releases"
    selected_asset = gui_asset_name() if asset_name is None else asset_name
    download_url = f"{release_root}/download/{quote(tag, safe='')}/{selected_asset}"
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("发布信息缺少附件列表。")
    asset = next((item for item in assets
                  if isinstance(item, dict) and item.get("name") == selected_asset), {})
    size = asset.get("size", 0)
    digest = asset.get("digest", "")
    ready = (
        asset.get("state") == "uploaded"
        and asset.get("browser_download_url") == download_url
        and type(size) is int and size > 0
        and isinstance(digest, str)
        and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest) is not None
    )
    return ReleaseInfo(
        version=tag.removeprefix("v"),
        page_url=f"{release_root}/tag/{quote(tag, safe='')}",
        published_at=str(payload.get("published_at") or ""),
        notes=str(payload.get("body") or "暂无发布说明。"),
        newer=newer,
        download_url=download_url if ready else "",
        size=size if ready else 0,
        sha256=digest[7:].lower() if ready else "",
        asset_name=selected_asset,
    )


def _read_release(url: str, current_version: str, timeout: int) -> ReleaseInfo:
    request = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"CNKIBug-GUI/{current_version}",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        return parse_release(payload, current_version)
    except HTTPError as error:
        if error.code in (403, 429):
            raise UpdateError("服务暂时限制了请求，请稍后再试。") from error
        if error.code == 404:
            raise UpdateError("未找到可用的正式发布。") from error
        raise UpdateError(f"更新检查失败：HTTP {error.code}") from error
    except (URLError, OSError, http.client.HTTPException) as error:
        raise UpdateError(f"连接失败：{error}") from error
    except (ValueError, TypeError) as error:
        raise UpdateError("无法读取服务返回的发布信息。") from error


def download_sources(source: str) -> tuple[str, ...]:
    if source not in UPDATE_SOURCES:
        raise UpdateError(f"未知更新线路：{source}")
    return UPDATE_SOURCES[1:] if source == "auto" else (source,)


def _metadata_sources(source: str) -> tuple[tuple[str, str], ...]:
    download_sources(source)
    direct = ("GitHub", RELEASE_API)
    return (direct,) if source == "direct" else (("JSDMirror", MANIFEST_URL), direct)


def check_release(
    current_version: str = APP_VERSION,
    *,
    source: str = "direct",
    cancelled: Event | None = None,
    on_source: Callable[[str], None] | None = None,
) -> ReleaseInfo:
    failures = []
    for name, url in _metadata_sources(source):
        if cancelled is not None and cancelled.is_set():
            raise UpdateCancelled()
        if on_source is not None:
            on_source(name)
        try:
            return _read_release(url, current_version, timeout=8)
        except UpdateError as error:
            failures.append(f"{name}：{error}")
    raise UpdateError("无法取得更新信息。\n" + "\n".join(failures))


def _download_url(original: str, source: str) -> str:
    return original if source == "direct" else f"https://{source}/{original}"


def probe_connections(source: str, cancelled: Event, on_result: Callable[[str], None]) -> None:
    release = None
    for name, url in _metadata_sources(source):
        if cancelled.is_set():
            raise UpdateCancelled()
        started = time.monotonic()
        try:
            release = _read_release(url, APP_VERSION, timeout=5)
        except UpdateError as error:
            on_result(f"{name}（更新信息）：不可用，{error}")
        else:
            on_result(f"{name}（更新信息）：可用，延迟 {round((time.monotonic() - started) * 1000)} ms")
            break
    if release is None or not release.ready:
        on_result("未取得可用的 GUI 发布文件，无法检查文件下载。")
        return
    for name in download_sources(source):
        if cancelled.is_set():
            raise UpdateCancelled()
        request = Request(_download_url(release.download_url, name), headers={
            "Range": "bytes=0-1023", "Accept-Encoding": "identity", "User-Agent": f"CNKIBug-GUI/{APP_VERSION}",
        })
        started = time.monotonic()
        try:
            with urlopen(request, timeout=5) as response:
                data = response.read(1024)
                expected_type = (data.startswith(b"\x7fELF") and data[8:11] == b"AI\x02") if release.asset_name.endswith(".AppImage") else data.startswith(b"MZ")
                if response.status not in (200, 206) or not expected_type:
                    raise UpdateError("返回文件与当前平台不匹配。")
        except (URLError, OSError, http.client.HTTPException, UpdateError) as error:
            on_result(f"{SOURCE_LABELS[name]}（文件下载）：不可用，{error}")
        else:
            elapsed = round((time.monotonic() - started) * 1000)
            on_result(f"{SOURCE_LABELS[name]}（文件下载）：可用，延迟 {elapsed} ms")


def can_install_update() -> bool:
    if sys.platform == "win32":
        return bool(getattr(sys, "frozen", False))
    image = appimage_path()
    return bool(image and gui_asset_name() and image.is_file() and os.access(image.parent, os.W_OK))


def download_release(
    release: ReleaseInfo,
    data_dir: Path,
    cancelled: Event,
    on_progress: Callable[[int, int], None],
    *,
    source: str = "direct",
    on_source: Callable[[str], None] | None = None,
) -> Path:
    if not release.newer or not release.ready:
        raise UpdateError("该版本的 GUI 文件或校验信息尚未就绪。")
    sources = download_sources(source)
    update_dir = data_dir / "update"
    update_dir.mkdir(parents=True, exist_ok=True)
    job_dir = Path(tempfile.mkdtemp(prefix="pending-", dir=update_dir))
    partial = job_dir / f"{release.asset_name}.part"
    candidate = job_dir / release.asset_name
    failures = []
    try:
        for name in sources:
            if cancelled.is_set():
                raise UpdateCancelled()
            if on_source is not None:
                on_source(SOURCE_LABELS[name])
            on_progress(0, release.size)
            if cancelled.is_set():
                raise UpdateCancelled()
            digest = hashlib.sha256()
            received = 0
            request = Request(_download_url(release.download_url, name), headers={
                "User-Agent": f"CNKIBug-GUI/{APP_VERSION}", "Accept-Encoding": "identity",
            })
            try:
                with urlopen(request, timeout=15) as response, partial.open("wb") as output:
                    while True:
                        if cancelled.is_set():
                            raise UpdateCancelled()
                        try:
                            chunk = response.read(256 * 1024)
                        except (OSError, http.client.HTTPException) as error:
                            raise URLError(error) from error
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > release.size:
                            raise UpdateError(f"{SOURCE_LABELS[name]}：文件大小与发布信息不一致。")
                        output.write(chunk)
                        digest.update(chunk)
                        on_progress(received, release.size)
            except (URLError, TimeoutError, http.client.HTTPException) as error:
                failures.append(f"{SOURCE_LABELS[name]}：{error}")
                partial.unlink(missing_ok=True)
                continue
            if cancelled.is_set():
                raise UpdateCancelled()
            if received != release.size or digest.hexdigest() != release.sha256:
                raise UpdateError(f"{SOURCE_LABELS[name]}：文件校验失败，当前程序未被替换。")
            partial.replace(candidate)
            return candidate
        if cancelled.is_set():
            raise UpdateCancelled()
        raise UpdateError("所有选定线路均下载失败。\n" + "\n".join(failures))
    except OSError as error:
        raise UpdateError(f"无法写入更新文件：{error}") from error
    finally:
        partial.unlink(missing_ok=True)
        if not candidate.exists():
            job_dir.rmdir()


def start_installer(candidate: Path, release: ReleaseInfo) -> None:
    if not can_install_update():
        raise UpdateError("当前运行方式或程序所在目录不支持自动替换。")
    if appimage_path() is not None:
        _start_appimage_installer(candidate, release)
        return
    target = Path(sys.executable).resolve()
    job_dir = candidate.parent.resolve()
    update_dir = job_dir.parent
    process_ids = [os.getpid()]
    parent = psutil.Process().parent()
    if parent is not None and Path(parent.exe()).resolve() == target:
        process_ids.append(parent.pid)
    script_path = update_dir / "apply_update.ps1"
    script_path.write_bytes(UPDATE_SCRIPT.read_bytes())
    plan_path = job_dir / "install.json"
    ready_path = job_dir / "ready"
    plan = {
        "target": str(target), "candidate": str(candidate.resolve()),
        "backup": str(update_dir / f"{target.name}.bak"),
        "process_ids": process_ids, "size": release.size, "sha256": release.sha256,
        "error_title": "CNKIBug 更新失败",
        "error_message": "未能完成更新。请保留当前程序，稍后重试。",
    }
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8", newline="\n")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    # PowerShell must load system DLLs instead of inheriting the frozen app's DLL directory.
    kernel32 = ctypes.windll.kernel32
    kernel32.SetDllDirectoryW(None)
    try:
        process = subprocess.Popen(
            [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(script_path), "-PlanPath", str(plan_path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    finally:
        kernel32.SetDllDirectoryW(str(sys._MEIPASS))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            error_path = job_dir / "error.log"
            details = error_path.read_text(encoding="utf-8-sig") if error_path.is_file() else ""
            raise UpdateError(f"更新脚本未能启动，当前程序仍在运行。\n{details}".strip())
        if ready_path.is_file():
            return
        time.sleep(0.1)
    process.terminate()
    process.wait(timeout=5)
    raise UpdateError("更新脚本启动超时，当前程序仍在运行。")


def save_appimage(candidate: Path, destination: Path) -> Path:
    destination = destination.expanduser().resolve()
    descriptor, temporary = tempfile.mkstemp(prefix=".cnkibug-update-", suffix=".AppImage", dir=destination.parent)
    os.close(descriptor)
    staged = Path(temporary)
    try:
        shutil.copyfile(candidate, staged)
        staged.chmod(0o755)
        staged.replace(destination)
        candidate.unlink()
        return destination
    finally:
        staged.unlink(missing_ok=True)


def _start_appimage_installer(candidate: Path, release: ReleaseInfo) -> None:
    target = appimage_path()
    assert target is not None
    job_dir = candidate.parent.resolve()
    script_path = job_dir / "apply_update.sh"
    script_path.write_bytes(Path(__file__).with_name("apply_update.sh").read_bytes())
    descriptor, temporary = tempfile.mkstemp(prefix=".cnkibug-update-", suffix=".AppImage", dir=target.parent)
    os.close(descriptor)
    staged = Path(temporary)
    process = None
    handed_off = False
    try:
        shutil.copyfile(candidate, staged)
        staged.chmod((target.stat().st_mode & 0o777) | 0o100)
        process_ids = [os.getpid()]
        parent = psutil.Process().parent()
        if parent is not None and Path(parent.exe()).resolve() == Path(sys.executable).resolve():
            process_ids.append(parent.pid)
        process = subprocess.Popen(
            ["/bin/sh", str(script_path), str(target), str(staged), str(candidate.resolve()),
             str(job_dir.parent / f"{target.name}.bak"), " ".join(map(str, process_ids)),
             str(release.size), release.sha256],
            env=system_process_environment(), cwd=target.parent,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                error_file = job_dir / "error.log"
                details = error_file.read_text(encoding="utf-8", errors="replace") if error_file.exists() else ""
                raise UpdateError(f"更新脚本未能启动，当前程序仍在运行。\n{details}".strip())
            if (job_dir / "ready").is_file():
                handed_off = True
                return
            time.sleep(0.1)
        raise UpdateError("更新脚本启动超时，当前程序仍在运行。")
    finally:
        if not handed_off:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            staged.unlink(missing_ok=True)
