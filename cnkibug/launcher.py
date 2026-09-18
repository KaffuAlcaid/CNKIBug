from __future__ import annotations

import os
import sys
from pathlib import Path


def user_program_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share").expanduser()


def cli_main() -> None:
    from .app.cli import main
    from .core.version import APP_VERSION

    if sys.argv[1:] == ["--self-check"]:
        print(f"CNKIBug self-check OK: {APP_VERSION}")
        return
    main(program_dir=user_program_dir())


def gui_main() -> None:
    if sys.argv[1:] == ["--install-system-deps"]:
        from .browser.environment import run_system_dependency_installer

        raise SystemExit(run_system_dependency_installer())
    try:
        from .gui.app import main
    except ImportError as error:
        raise SystemExit('GUI requires cnkibug[gui] and Python Tk support.') from error
    from .core.version import APP_VERSION

    if sys.argv[1:] == ["--self-check"]:
        print(f"CNKIBug GUI self-check OK: {APP_VERSION}")
        return
    main(program_dir=user_program_dir())
