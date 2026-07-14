from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MAX_IMPORT_BYTES = 1024 * 1024
MAX_KEYWORDS = 1000


class KeywordImportError(ValueError):
    pass


@dataclass(frozen=True)
class KeywordImportResult:
    keywords: list[str]
    total_lines: int
    blank_lines: int
    duplicates: list[str]

    @property
    def duplicate_count(self) -> int:
        return len(self.duplicates)


def dedupe_keywords(lines: Iterable[str]) -> KeywordImportResult:
    keywords: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    total_lines = 0
    blank_lines = 0

    for raw_line in lines:
        total_lines += 1
        keyword = raw_line.strip()
        if not keyword:
            blank_lines += 1
            continue
        if keyword in seen:
            duplicates.append(keyword)
            continue
        seen.add(keyword)
        keywords.append(keyword)
        if len(keywords) > MAX_KEYWORDS:
            raise KeywordImportError(f"去重后关键词不能超过 {MAX_KEYWORDS} 个。")

    return KeywordImportResult(
        keywords=keywords,
        total_lines=total_lines,
        blank_lines=blank_lines,
        duplicates=duplicates,
    )


def load_keywords_txt(path_text: str) -> KeywordImportResult:
    path = _parse_path(path_text)
    try:
        if not path.is_file():
            raise KeywordImportError(f"文件不存在或不是普通文件：{path}")
        if path.stat().st_size > MAX_IMPORT_BYTES:
            raise KeywordImportError("TXT 文件不能超过 1 MiB。")
        raw = path.read_bytes()
    except KeywordImportError:
        raise
    except OSError as exc:
        raise KeywordImportError(f"无法读取文件：{exc}") from exc

    if b"\x00" in raw:
        raise KeywordImportError("文件包含二进制内容，无法作为 TXT 关键词列表读取。")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise KeywordImportError("文件必须使用 UTF-8 编码。") from exc

    result = dedupe_keywords(text.splitlines())
    if not result.keywords:
        raise KeywordImportError("文件中没有可用关键词。")
    return result


def _parse_path(path_text: str) -> Path:
    value = path_text.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    if not value:
        raise KeywordImportError("文件路径不能为空。")
    return Path(os.path.expandvars(os.path.expanduser(value)))
