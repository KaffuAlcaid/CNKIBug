from __future__ import annotations

import os
import sys


def clear_screen() -> None:
    try:
        if not sys.stdout.isatty():
            return
    except Exception:
        return
    if sys.platform == "win32":
        os.system("cls")  # noqa: S605, S607
        sys.stdout.write("\033[3J")
        sys.stdout.flush()
    else:
        sys.stdout.write("\033c")
        sys.stdout.flush()


def safe_input(prompt: str = "") -> str:
    try:
        return input(prompt)
    except EOFError:
        print("\n[*] 检测到输入流结束（EOF），程序退出。")
        raise SystemExit(0)
