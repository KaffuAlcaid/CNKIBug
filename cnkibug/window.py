"""窗口置顶（第二层）—— Windows 专属 ctypes / user32 实现。

用途：检测到知网安全验证(/verify)时，把浏览器窗口拽到最前抢用户注意力。
只抢一次：置顶后立即取消 TOPMOST，避免常驻压住浏览器导致用户无法操作滑块
（呼应「砍掉控制台常驻置顶」的决定）。

诚实边界：受 Windows 前台锁定策略限制，SetForegroundWindow 不保证 100% 抢到
焦点，系统策略严格时可能只是任务栏闪烁——这是 Win32 硬限制，不是代码缺陷。
非 Windows 平台所有函数为 no-op，直接返回 False，绝不抛错、不影响抓取主流程。
"""

import sys
import logging

logger = logging.getLogger(__name__)

_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_SHOWWINDOW = 0x0040
_SW_RESTORE = 9


def bring_to_front(title_hints=("安全验证", "中国知网", "知网")) -> bool:
    """把标题命中 title_hints 任一子串的顶层窗口置顶并抢焦点。

    命中多个时优先选标题靠前 hint 的窗口（验证时标题为「安全验证」，最该被选中）。
    返回 True 表示找到并尝试置顶；False 表示非 Windows、无匹配窗口或调用失败。
    """
    if sys.platform != "win32":
        return False

    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        logger.debug("导入 ctypes 失败，跳过窗口置顶", exc_info=True)
        return False

    try:
        user32 = ctypes.windll.user32
    except Exception:
        logger.debug("获取 user32 句柄失败，跳过窗口置顶", exc_info=True)
        return False

    matches = []  # [(hwnd, title)]

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    def _cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if any(h in title for h in title_hints):
                matches.append((hwnd, title))
        except Exception:
            logger.debug("枚举窗口回调处理异常，忽略该窗口", exc_info=True)
        return True

    try:
        user32.EnumWindows(EnumWindowsProc(_cb), 0)
    except Exception:
        logger.debug("EnumWindows 调用失败，跳过窗口置顶", exc_info=True)
        return False

    if not matches:
        return False

    def _rank(item):
        title = item[1]
        for i, h in enumerate(title_hints):
            if h in title:
                return i
        return len(title_hints)

    matches.sort(key=_rank)
    hwnd = matches[0][0]

    try:
        user32.ShowWindow(hwnd, _SW_RESTORE)  # 若最小化先还原
        flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_SHOWWINDOW
        # 先置顶抢注意力，随即取消 TOPMOST，不常驻压住浏览器
        user32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0, flags)
        user32.SetWindowPos(hwnd, _HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        logger.debug("置顶窗口操作失败", exc_info=True)
        return False
