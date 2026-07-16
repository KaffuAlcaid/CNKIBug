
import sys


def _popup_error(lines: list[str]):
    message = "\n".join(lines)

    if sys.platform == "win32":
        try:
            import ctypes
            style = 0x10 | 0x10000 | 0x40000
            ctypes.windll.user32.MessageBoxW(0, message, "CNKIBug - 错误", style) # noqa
            return
        except Exception: # noqa
            pass

    print(message, file=sys.stderr)


def handle_import_error(error: ImportError) -> None:
    if sys.platform == "win32":
        _popup_error([
            "==============================================",
            " [致命错误] 程序核心组件加载失败！",
            "----------------------------------------------",
            f" 缺失模块: {error}",
            "",
            " 可能原因：您运行的不是完整的 exe 文件，",
            " 或 exe 文件已损坏。",
            "",
            " 解决方法：",
            "   请重新下载完整的 CNKIBug.exe 文件，",
            "   不要解压、不要移动内部文件，直接双击运行。",
            "==============================================",
        ])
    else:
        print(f"[FATAL] 缺少依赖: {error}")
        print("请运行: pip install playwright openpyxl rich && playwright install chromium")
