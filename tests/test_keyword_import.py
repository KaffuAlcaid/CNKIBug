import pytest

from cnkibug.fileio import keyword_input as keyword_import
from cnkibug.fileio.keyword_input import KeywordImportError, dedupe_keywords, load_keywords_txt


def test_dedupe_keywords_trims_ends_and_preserves_order_and_internal_spaces():
    result = dedupe_keywords(["  焊接  ", "", "增材  制造", "焊接"])

    assert result.keywords == ["焊接", "增材  制造"]
    assert result.total_lines == 4
    assert result.blank_lines == 1
    assert result.duplicates == ["焊接"]


def test_load_keywords_txt_accepts_utf8_bom_and_quoted_path(tmp_path):
    path = tmp_path / "关键词 列表.txt"
    path.write_bytes("\ufeff机器学习\r\n\r\n机器学习\r\n增材制造".encode("utf-8"))

    result = load_keywords_txt(f'"{path}"')

    assert result.keywords == ["机器学习", "增材制造"]
    assert result.total_lines == 4
    assert result.blank_lines == 1
    assert result.duplicate_count == 1


@pytest.mark.parametrize("content", [b"\xff\xfe", b"keyword\x00binary"])
def test_load_keywords_txt_rejects_non_utf8_and_binary(tmp_path, content):
    path = tmp_path / "bad.txt"
    path.write_bytes(content)

    with pytest.raises(KeywordImportError):
        load_keywords_txt(str(path))


def test_load_keywords_txt_enforces_file_and_keyword_limits(monkeypatch, tmp_path):
    oversized = tmp_path / "oversized.txt"
    oversized.write_text("abcd", encoding="utf-8")
    monkeypatch.setattr(keyword_import, "MAX_IMPORT_BYTES", 3)
    with pytest.raises(KeywordImportError, match="1 MiB"):
        load_keywords_txt(str(oversized))

    monkeypatch.setattr(keyword_import, "MAX_KEYWORDS", 2)
    with pytest.raises(KeywordImportError, match="2"):
        dedupe_keywords(["a", "b", "c"])
