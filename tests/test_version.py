from pathlib import Path

import pytest

from cnkibug.core.version import APP_VERSION, read_project_version
from generate_version_info import build_version_info


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_and_windows_resource_use_project_version():
    project_version = read_project_version(ROOT / "pyproject.toml")
    generated = build_version_info(project_version)

    assert APP_VERSION == project_version
    assert f"filevers=({', '.join(project_version.split('.'))}, 0)" in generated
    assert f"FileVersion', '{project_version}'" in generated
    assert f"ProductVersion', '{project_version}'" in generated


def test_windows_resource_rejects_non_numeric_version():
    with pytest.raises(ValueError, match="x.y.z"):
        build_version_info("1.2.3rc1")
