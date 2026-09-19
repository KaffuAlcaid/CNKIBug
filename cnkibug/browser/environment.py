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


@dataclass(frozen=True)
class BrowserCandidate:
    name: str
    executable: Path | None
    channel: str | None = None


def browser_candidates(playwright) -> tuple[BrowserCandidate, ...]:
    candidates = []
    if sys.platform == "linux":
        found = set()
        for command, name in (("google-chrome", "Google Chrome"), ("google-chrome-stable", "Google Chrome"),
                              ("chromium", "系统 Chromium"), ("chromium-browser", "系统 Chromium")):
            executable = shutil.which(command)
            if executable and Path(executable).resolve() not in found:
                found.add(Path(executable).resolve())
                candidates.append(BrowserCandidate(name, Path(executable)))
    else:
        candidates.append(BrowserCandidate("Microsoft Edge", edge_executable(), "msedge"))
    candidates.append(BrowserCandidate("Playwright Chromium", Path(playwright.chromium.executable_path)))
    return tuple(candidates)


def browser_launch_options(candidate: BrowserCandidate) -> dict:
    options = {"headless": False}
    if candidate.channel:
        options["channel"] = candidate.channel
    elif candidate.executable:
        options["executable_path"] = str(candidate.executable)
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


def linux_distribution() -> set[str]:
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        return set()
    return {release.get("ID", ""), *release.get("ID_LIKE", "").split()}


def system_dependency_command() -> str | None:
    distribution = linux_distribution()
    if "fedora" in distribution:
        return "sudo dnf install chromium"
    if not distribution.intersection({"debian", "ubuntu"}):
        return None
    image = appimage_path()
    if image:
        return shlex.join([str(image), "--install-system-deps"])
    if getattr(sys, "frozen", False):
        return shlex.join([sys.executable, "--install-system-deps"])
    return shlex.join([sys.executable, "-m", "playwright", "install-deps", "chromium"])


def run_system_dependency_installer() -> int:
    if sys.platform != "linux":
        raise RuntimeError("系统组件安装入口适用于 Linux。")
    if not linux_distribution().intersection({"debian", "ubuntu"}):
        command = system_dependency_command()
        print(f"请在终端执行：{command}" if command else "请通过系统软件包管理器安装 Chromium 及其运行组件。")
        return 1
    command, env = playwright_install_command(system_dependencies=True)
    return subprocess.call(command, env=env)


def browser_installed() -> bool:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        return any(candidate.executable and candidate.executable.is_file() for candidate in browser_candidates(playwright))


def _missing_shared_libraries(executable: Path) -> list[str]:
    command = shutil.which("ldd")
    if not command:
        return []
    with executable.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            return []
    env = system_process_environment()
    env["LC_ALL"] = "C"
    libraries = subprocess.run([command, str(executable)], capture_output=True, text=True, timeout=15, env=env)
    return re.findall(r"^\s*(\S+)\s+=>\s+not found", libraries.stdout, re.MULTILINE)


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
            installed = [candidate for candidate in browser_candidates(playwright)
                         if candidate.executable and candidate.executable.is_file()]
            failures, missing = [], []
            for candidate in installed:
                if cancelled.is_set():
                    raise EnvironmentCancelled()
                try:
                    if launch_browser:
                        browser = playwright.chromium.launch(timeout=15000, **browser_launch_options(candidate))
                        try:
                            page = browser.new_page()
                            page.goto("about:blank", timeout=15000)
                            page.set_content("<title>CNKIBug</title>")
                            if page.title() != "CNKIBug":
                                raise RuntimeError("浏览器页面检查失败")
                        finally:
                            browser.close()
                    record("system", "系统组件", "ready" if launch_browser else "pending", platform.platform())
                    record("browser", "浏览器", "ready" if launch_browser else "pending",
                           candidate.name + "\n" + str(candidate.executable))
                    return results
                except (OSError, PlaywrightError, RuntimeError) as error:
                    failures.append(f"{candidate.name}：{error}")
                    if sys.platform == "linux":
                        try:
                            missing.extend(_missing_shared_libraries(candidate.executable))
                        except (OSError, subprocess.TimeoutExpired):
                            pass
            guidance = "请安装 Microsoft Edge 或 Playwright Chromium。"
            if sys.platform == "linux":
                command = system_dependency_command()
                guidance = "请安装系统 Chrome、Chromium，或点击安装 Chromium。"
                guidance += f"\n系统安装命令：\n{command}" if command else "\n系统组件可通过软件包管理器安装。"
            record("system", "系统组件", "error" if installed else "pending",
                   "缺少：" + "、".join(dict.fromkeys(missing)) if missing else "浏览器启动后确认")
            cached = Path(playwright.chromium.executable_path).is_file()
            record("browser", "浏览器", "error" if cached else "missing", "\n\n".join([*failures, guidance]))
    except (OSError, PlaywrightError) as error:
        record("system", "系统组件", "pending", "浏览器启动后确认")
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
