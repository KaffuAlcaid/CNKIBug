"""错误弹窗 —— 仅依赖标准库（必须保持零三方依赖，否则 run.py 的依赖守卫失效）。"""

import sys


def _popup_error(lines: list[str]):
    message = "\n".join(lines)

    if sys.platform == "win32":
        try:
            import ctypes
            # MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST（MB_OK=0x0 省略）
            style = 0x10 | 0x10000 | 0x40000
            ctypes.windll.user32.MessageBoxW(0, message, "CNKIBug - 错误", style)
            return
        except Exception:
            pass

    print(message, file=sys.stderr)
