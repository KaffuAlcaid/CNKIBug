
import sys


def _popup_error(lines: list[str]):
    message = "\n".join(lines)

    if sys.platform == "win32":
        try:
            import ctypes
            style = 0x10 | 0x10000 | 0x40000
            ctypes.windll.user32.MessageBoxW(0, message, "CNKIBug - 错误", style)
            return
        except Exception:
            pass

    print(message, file=sys.stderr)
