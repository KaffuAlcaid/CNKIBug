from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def read_project_version(pyproject_path: Path) -> str:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def get_app_version() -> str:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject_path.is_file():
        try:
            return read_project_version(pyproject_path)
        except (KeyError, OSError, tomllib.TOMLDecodeError):
            pass
    try:
        return version("cnkibug")
    except PackageNotFoundError:
        return "0+unknown"


APP_VERSION = get_app_version()
