from __future__ import annotations

import importlib
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from tempfile import TemporaryFile
from threading import Event, Thread

from ..core.runtime import appimage_path


@dataclass(frozen=True)
class EnvironmentCheck:
    key: str
    label: str
    status: str
    detail: str


class EnvironmentCancelled(Exception):
    pass


def browser_channels() -> tuple[str | None, ...]:
    return (None,) if sys.platform == "linux" else ("msedge", None)


def browser_launch_options(channel: str | None) -> dict:
    options = {"headless": False}
    if channel:
        options["channel"] = channel
    if sys.platform == "linux" and getattr(sys, "frozen", False):
        options["env"] = system_process_environment()
    return options


def edge_executable() -> Path | None:
    if sys.platform == "win32":
        roots = (
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")),
        )
        for root in roots:
            candidate = Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidate.is_file():
                return candidate
    candidate = shutil.which("msedge") or shutil.which("microsoft-edge")
    return Path(candidate) if candidate else None


def browser_cache_directory() -> Path:
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override == "0":
        import playwright

        return Path(playwright.__file__).parent / "driver" / "package" / ".local-browsers"
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))).expanduser() / "ms-playwright"


def system_process_environment() -> dict[str, str]:
    env = os.environ.copy()
    # System utilities must use host libraries rather than the frozen Python bundle's libraries.
    if sys.platform == "linux" and getattr(sys, "frozen", False):
        original = env.pop("LD_LIBRARY_PATH_ORIG", "")
        if original:
            env["LD_LIBRARY_PATH"] = original
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


def playwright_install_command(*, system_dependencies: bool = False) -> tuple[list[str], dict[str, str]]:
    from playwright._impl._driver import compute_driver_executable, get_driver_env

    driver, cli = compute_driver_executable()
    command = [driver, cli, "install-deps", "chromium"] if system_dependencies else [driver, cli, "install", "chromium", "--no-shell"]
    env = get_driver_env()
    if sys.platform == "linux" and getattr(sys, "frozen", False):
        host_env = system_process_environment()
        for key in ("LD_LIBRARY_PATH", "LD_LIBRARY_PATH_ORIG"):
            if key in host_env:
                env[key] = host_env[key]
            else:
                env.pop(key, None)
    return command, env


def system_dependency_command() -> str:
    image = appimage_path()
    if image:
        return shlex.join([str(image), "--install-system-deps"])
    if getattr(sys, "frozen", False):
        return shlex.join([sys.executable, "--install-system-deps"])
    return shlex.join([sys.executable, "-m", "playwright", "install-deps", "chromium"])


def run_system_dependency_installer() -> int:
    if sys.platform != "linux":
        raise RuntimeError("系统组件安装入口适用于 Linux。")
    command, env = playwright_install_command(system_dependencies=True)
    return subprocess.call(command, env=env)


def chromium_available() -> bool:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        return Path(playwright.chromium.executable_path).is_file()


def check_environment(data_dir: Path, output_dir: Path, cancelled: Event,
                      on_result: Callable[[EnvironmentCheck], None], *, launch_browser: bool = True) -> list[EnvironmentCheck]:
    results: list[EnvironmentCheck] = []

    def record(key: str, label: str, status: str, detail: str) -> None:
        if cancelled.is_set():
            raise EnvironmentCancelled()
        result = EnvironmentCheck(key, label, status, detail)
        results.append(result)
        on_result(result)

    try:
        for module in ("tkinter", "ttkbootstrap", "playwright", "openpyxl", "PIL", "psutil"):
            importlib.import_module(module)
        record("runtime", "程序运行环境", "ready", f"Python {platform.python_version()}；程序组件已加载")
    except ImportError as error:
        record("runtime", "程序运行环境", "error", str(error))

    for key, label, directory in (("data", "用户数据目录", data_dir), ("output", "论文保存目录", output_dir)):
        try:
            directory = directory.expanduser().resolve()
            directory.mkdir(parents=True, exist_ok=True)
            with TemporaryFile(dir=directory):
                pass
            record(key, label, "ready", str(directory))
        except (OSError, ValueError) as error:
            record(key, label, "error", str(error))

    try:
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
    except ImportError as error:
        record("system", "系统组件", "pending", "程序组件加载后检查")
        record("browser", "浏览器", "error", str(error))
        return results

    try:
        with sync_playwright() as playwright:
            edge = edge_executable() if sys.platform == "win32" else None
            executable = edge or Path(playwright.chromium.executable_path)
            if not executable.is_file():
                record("system", "系统组件", "pending", "浏览器安装后检查" if sys.platform == "linux" else platform.platform())
                record("browser", "浏览器", "missing", "需要安装 Chromium" if sys.platform == "linux" else "请安装 Microsoft Edge 或 Playwright Chromium")
                return results
            if sys.platform == "linux":
                command = shutil.which("ldd")
                if command is None:
                    record("system", "系统组件", "error", "未找到 ldd，无法检查浏览器共享库")
                    record("browser", "浏览器", "pending", "系统组件检查完成后启动")
                    return results
                env = system_process_environment()
                env["LC_ALL"] = "C"
                libraries = subprocess.run([command, str(executable)], capture_output=True, text=True, timeout=15, env=env)
                missing = re.findall(r"^\s*(\S+)\s+=>\s+not found", libraries.stdout, re.MULTILINE)
                if missing:
                    record("system", "系统组件", "error", "缺少：" + "、".join(missing) + "\n安装命令：\n" + system_dependency_command())
                    record("browser", "浏览器", "error", "Chromium 已安装，等待补齐系统组件")
                    return results
                if libraries.returncode:
                    record("system", "系统组件", "error", libraries.stderr.strip() or libraries.stdout.strip())
                    record("browser", "浏览器", "pending", "系统组件检查完成后启动")
                    return results
            record("system", "系统组件", "ready", platform.platform())
            if launch_browser:
                for channel in browser_channels():
                    try:
                        browser = playwright.chromium.launch(
                            timeout=15000, **browser_launch_options(channel),
                        )
                        edge = edge if channel == "msedge" else None
                        executable = edge or Path(playwright.chromium.executable_path)
                        break
                    except PlaywrightError:
                        if channel != "msedge":
                            raise
                try:
                    page = browser.new_page()
                    page.goto("about:blank", timeout=15000)
                finally:
                    browser.close()
            record("browser", "浏览器", "ready", ("Microsoft Edge" if edge else "Chromium") + "\n" + str(executable))
    except (OSError, PlaywrightError, subprocess.TimeoutExpired) as error:
        record("browser", "浏览器", "error", str(error))
    return results


def install_chromium(cancelled: Event, on_output: Callable[[str], None]) -> None:
    if sys.platform != "linux":
        raise RuntimeError("浏览器自动安装适用于 Linux。")
    browser_cache_directory().mkdir(parents=True, exist_ok=True)
    command, env = playwright_install_command()
    lines: Queue[str] = Queue()
    recent: list[str] = []
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", start_new_session=True)

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line.rstrip())

    reader = Thread(target=read_output, daemon=True)
    reader.start()
    try:
        while process.poll() is None or reader.is_alive() or not lines.empty():
            if cancelled.is_set():
                raise EnvironmentCancelled()
            try:
                line = lines.get(timeout=0.2)
            except Empty:
                continue
            if line:
                recent.append(line)
                recent = recent[-12:]
                on_output(line)
        if process.wait() != 0:
            raise RuntimeError("Chromium 安装失败：\n" + "\n".join(recent))
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        reader.join(timeout=1)
        if process.stdout is not None:
            process.stdout.close()
