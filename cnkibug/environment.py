"""平台 / 环境检测 —— Edge 安装检查、桌面路径解析、运行环境校验。

==== 💩 山警告（原样搬入，本次未作任何修改）====
"""

import sys
import os
import shutil
import winreg

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
    if sys.platform != "win32":
        return os.path.join(os.path.expanduser("~"), "Desktop")
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


def check_env():
    if sys.platform != "win32":
        playwright_path = os.path.join(
            os.path.expanduser("~"), "AppData", "Local", "ms-playwright"
        )
        if not os.path.exists(playwright_path):
            _console.print("\n[yellow][环境缺失] 请先在终端运行: playwright install chromium[/yellow]\n")
            sys.exit(0)
        return

    if not _edge_installed():
        _popup_error([
            "==============================================",
            " [环境缺失] 未检测到 Microsoft Edge 浏览器！",
            "----------------------------------------------",
            " 本程序需要使用 Microsoft Edge 来抓取网页数据。",
            " Windows 10/11 通常已预装，若您已卸载请重新安装。",
            "",
            " 请用浏览器打开以下地址，下载并安装 Edge：",
            "",
            "   https://www.microsoft.com/zh-cn/edge/download",
            "",
            " 安装完成后，关闭此窗口，重新双击程序即可！",
            "==============================================",
        ])
        sys.exit(0)
