
# noinspection PyDeprecation

import sys
import os
import shutil

from .ui import _console

_EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
]


def _edge_installed() -> bool:
    if any(os.path.isfile(p) for p in _EDGE_PATHS):
        return True
    return shutil.which("msedge") is not None # noqa


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
