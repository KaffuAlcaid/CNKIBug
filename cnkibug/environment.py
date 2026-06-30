"""平台 / 环境检测 —— Edge 安装检查、桌面路径解析、运行环境校验。

跨平台说明：winreg 是 Windows 专属标准库，原先在文件顶层 import，会导致
Linux / macOS 上 `import cnkibug.environment` 直接抛 ModuleNotFoundError，
进而被 run.py 的依赖守卫当成「缺依赖」而退出。现已下沉到
get_real_desktop_path 的 Windows 分支内按需导入；非 Windows 平台的桌面路径
改走 XDG 用户目录解析（见 _xdg_desktop_path）。Windows 行为保持不变。
"""
# noinspection PyDeprecation

import sys
import os
import shutil
import subprocess

from .errors import _popup_error
from .ui import _console

_EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
]


def _edge_installed() -> bool:
    if any(os.path.isfile(p) for p in _EDGE_PATHS):
        return True
    return shutil.which("msedge") is not None


def get_real_desktop_path() -> str:
    """返回当前用户的桌面目录。

    Windows：查注册表 User Shell Folders，支持桌面被重定向（如 OneDrive）。
    非 Windows：交给 _xdg_desktop_path 解析，能正确处理本地化目录名
    （如中文桌面的 ~/桌面），而不是写死 ~/Desktop。
    """
    if sys.platform == "win32":
        import winreg
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            )
            val, _ = winreg.QueryValueEx(key, "Desktop")
            winreg.CloseKey(key)
            return os.path.expandvars(val)
        except Exception:
            return os.path.join(os.path.expanduser("~"), "Desktop")

    return _xdg_desktop_path()


def _xdg_desktop_path() -> str:
    """非 Windows（Linux / macOS）平台解析桌面目录，逐级回退到 ~/Desktop。

    依次尝试：XDG_DESKTOP_DIR 环境变量 → xdg-user-dir 命令 →
    ~/.config/user-dirs.dirs 配置文件 → ~/Desktop 兜底。
    """
    home = os.path.expanduser("~")
    fallback = os.path.join(home, "Desktop")

    env_desktop = os.environ.get("XDG_DESKTOP_DIR")
    if env_desktop:
        return os.path.expandvars(env_desktop)

    xdg_bin = shutil.which("xdg-user-dir")
    if xdg_bin:
        try:
            out = subprocess.run(
                [xdg_bin, "DESKTOP"],
                capture_output=True, text=True, timeout=3,
            )
            path = out.stdout.strip()
            if path and path != home and os.path.isdir(path):
                return path
        except Exception:
            pass

    config = os.path.join(home, ".config", "user-dirs.dirs")
    try:
        with open(config, encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if line.startswith("XDG_DESKTOP_DIR"):
                    val: str = line.split("=", 1)[1].strip().strip('"')
                    val = val.replace("$HOME", home)
                    return os.path.expandvars(val) # noqa
    except OSError:
        pass

    return fallback


def check_env():
    if sys.platform != "win32":
        _home = os.path.expanduser("~")
        if sys.platform == "darwin":
            playwright_path = os.path.join(_home, "Library", "Caches", "ms-playwright")
        else:
            playwright_path = os.path.join(_home, ".cache", "ms-playwright")
        _override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        if _override and _override != "0":
            playwright_path = _override
        if not os.path.exists(playwright_path):
            _console.print("\n[yellow][环境缺失] 请先在终端运行: playwright install chromium[/yellow]\n")
            # 缺少必需依赖属于失败退出，用非 0 退出码，便于脚本化/CI 正确判断。
            sys.exit(1)
        return

    if not _edge_installed():
        _console.print(
            "\n[yellow][环境提示] 未检测到 Microsoft Edge，"
            "将尝试使用 Playwright Chromium。[/yellow]"
        )
        _console.print(
            "[dim]若后续浏览器启动失败，请安装 Edge 或运行："
            "playwright install chromium[/dim]\n"
        )
