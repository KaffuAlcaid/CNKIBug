import sys
from pathlib import Path

from cnkibug.app.errors import handle_import_error
from cnkibug.core.version import APP_VERSION


def _run_self_check() -> int:
    try:
        import cnkibug.app.runtime
        import cnkibug.browser.runtime
        import cnkibug.cnki.details
        import cnkibug.cnki.keyword
        import cnkibug.fileio.exporter
        import cnkibug.workflow.runner
        import cnkibug.workflow.state
    except ImportError as error:
        print(f"CNKIBug self-check failed: {error}")
        return 1
    print(f"CNKIBug self-check OK: {APP_VERSION}")
    return 0


def _run() -> None:
    try:
        from cnkibug.app.cli import main
    except ImportError as error:
        handle_import_error(error)
        raise SystemExit(1) from error
    main(_entry_directory())


def _entry_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-check"]:
        raise SystemExit(_run_self_check())
    _run()
