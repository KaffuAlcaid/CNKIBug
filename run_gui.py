import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path


def _write_message(message: str) -> None:
    if sys.stdout is not None:
        print(message)


def _write_startup_error_log(error: BaseException) -> Path | None:
    log_paths = (
        _entry_directory() / "CNKIBug-data" / "log" / "gui_startup_error.log",
        Path(tempfile.gettempdir()) / "CNKIBug" / "gui_startup_error.log",
    )
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    details = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    entry = f"[{timestamp}] GUI startup import failure\n{details}\n"

    for log_path in log_paths:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(entry)
        except OSError:
            continue
        return log_path
    return None


def _show_startup_error_dialog(message: str) -> None:
    root = None
    shown = False
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("CNKIBug", message, parent=root)
        shown = True
    except Exception:
        pass
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass

    if shown or sys.platform != "win32":
        return

    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "CNKIBug", 0x10)
    except Exception:
        pass


def _report_startup_import_error(error: BaseException) -> None:
    log_path = _write_startup_error_log(error)
    failure = f"CNKIBug GUI 启动失败：{error}"
    log_message = f"日志路径：{log_path}" if log_path else "日志写入失败。"
    _write_message(failure)
    _write_message('请运行：pip install -e ".[gui]"')
    _write_message(log_message)
    _show_startup_error_dialog(f"{failure}\n\n{log_message}")


def _run_self_check(*, check_browser: bool = False) -> int:
    try:
        import ttkbootstrap
        import cnkibug.core.memory
        from cnkibug.core.version import APP_VERSION
        import cnkibug.gui.app
        import cnkibug.gui.events
        import cnkibug.workflow.runner
    except ImportError as error:
        _write_message(f"CNKIBug GUI self-check failed: {error}")
        return 1
    if not _resource_path("icon.ico").is_file():
        _write_message("CNKIBug GUI self-check failed: icon.ico missing")
        return 1
    update_script = "apply_update.ps1" if sys.platform == "win32" else "apply_update.sh"
    if not _resource_path(f"cnkibug/gui/{update_script}").is_file():
        _write_message("CNKIBug GUI self-check failed: update script missing")
        return 1
    if check_browser:
        from threading import Event
        from cnkibug.browser.environment import check_environment

        root = None
        try:
            root = ttkbootstrap.Window(themename="litera", iconphoto=None)
            root.withdraw()
            root.update()
            with tempfile.TemporaryDirectory(prefix="cnkibug-browser-check-") as output_dir:
                results = check_environment(
                    _entry_directory() / "CNKIBug-data", Path(output_dir), Event(),
                    lambda item: _write_message(f"{item.label}: {item.status}\n{item.detail}"),
                )
            if not results or any(item.status != "ready" for item in results):
                return 1
        except Exception as error:
            _write_message(f"CNKIBug browser check failed: {error}")
            return 1
        finally:
            if root is not None:
                root.destroy()
        _write_message(f"CNKIBug browser startup OK: {APP_VERSION}")
    _write_message(f"CNKIBug GUI self-check OK: {APP_VERSION}")
    return 0


def _run() -> None:
    try:
        from cnkibug.gui.app import main
    except Exception as error:
        _report_startup_import_error(error)
        raise SystemExit(1) from error
    main(_entry_directory(), icon_path=_resource_path("icon.ico"))


def _entry_directory() -> Path:
    from cnkibug.core.runtime import appimage_path

    if appimage_path():
        from cnkibug.launcher import user_program_dir

        return user_program_dir()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resource_path(filename: str) -> Path:
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and bundle_dir:
        return Path(bundle_dir) / filename
    return Path(__file__).resolve().parent / filename


if __name__ == "__main__":
    if sys.argv[1:] == ["--install-system-deps"]:
        from cnkibug.browser.environment import run_system_dependency_installer

        raise SystemExit(run_system_dependency_installer())
    if sys.argv[1:] == ["--self-check"]:
        raise SystemExit(_run_self_check())
    if sys.argv[1:] == ["--self-check-browser"]:
        raise SystemExit(_run_self_check(check_browser=True))
    if sys.argv[1:] == ["--install-browser"]:
        from threading import Event
        from cnkibug.browser.environment import install_chromium

        try:
            install_chromium(Event(), _write_message)
        except Exception as error:
            _write_message(str(error))
            raise SystemExit(1) from error
        raise SystemExit(0)
    _run()
