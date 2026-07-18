import sys
from pathlib import Path

from cnkibug.core.version import APP_VERSION


def _write_message(message: str) -> None:
    if sys.stdout is not None:
        print(message)


def _run_self_check() -> int:
    try:
        import ttkbootstrap
        import cnkibug.gui.app
        import cnkibug.gui.events
        import cnkibug.workflow.runner
    except ImportError as error:
        _write_message(f"CNKIBug GUI self-check failed: {error}")
        return 1
    if not _resource_path("icon.ico").is_file():
        _write_message("CNKIBug GUI self-check failed: icon.ico missing")
        return 1
    _write_message(f"CNKIBug GUI self-check OK: {APP_VERSION}")
    return 0


def _run() -> None:
    try:
        from cnkibug.gui.app import main
    except ImportError as error:
        _write_message(f"CNKIBug GUI 启动失败：{error}")
        _write_message('请运行：pip install -e ".[gui]"')
        raise SystemExit(1) from error
    main(_entry_directory(), icon_path=_resource_path("icon.ico"))


def _entry_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resource_path(filename: str) -> Path:
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and bundle_dir:
        return Path(bundle_dir) / filename
    return Path(__file__).resolve().parent / filename


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-check"]:
        raise SystemExit(_run_self_check())
    _run()
