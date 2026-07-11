from __future__ import annotations

import re
import sys
from pathlib import Path

from cnkibug.version import read_project_version


def build_version_info(version: str) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if match is None:
        raise ValueError(f"Windows 版本资源要求 x.y.z 格式，实际为 {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        '080404b0',
        [StringStruct('CompanyName', 'Kaffu_Alcaid'),
        StringStruct('FileDescription', '中国知网论文标题爬虫工具'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'CNKIBug'),
        StringStruct('LegalCopyright', '©2026 Kaffu_Alcaid. All rights reserved.'),
        StringStruct('OriginalFilename', 'CNKIBug.exe'),
        StringStruct('ProductName', 'CNKIBug'),
        StringStruct('ProductVersion', '{version}')])
      ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def main() -> None:
    root = Path(__file__).resolve().parent
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "version.txt"
    project_version = read_project_version(root / "pyproject.toml")
    destination.write_text(build_version_info(project_version), encoding="utf-8")


if __name__ == "__main__":
    main()
