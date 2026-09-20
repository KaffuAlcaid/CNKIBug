import json

import pytest

from cnkibug.fileio import keyword_input as keyword_import
from cnkibug.fileio.keyword_input import KeywordImportError, dedupe_keywords, load_keywords_txt
from cnkibug.fileio.search_plans import read_search_plan, write_search_plan
from cnkibug.core.search_query import AdvancedQuery, SearchCondition, SearchOptions


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


def test_search_plan_roundtrip_preserves_conditions_and_output_options(tmp_path):
    query = AdvancedQuery((SearchCondition("TI", "焊接"),), date_from="2020-01-01")
    options = SearchOptions(resources=("学术期刊",), page_size=50)
    task = {
        "keywords": ["增材制造", "高级检索 1"], "max_pages": 4, "save_mode": "multi_csv",
        "include_citation": True, "include_details": True, "detail_txt_export": True,
        "output_dir": str(tmp_path), "advanced_queries": {"高级检索 1": query.to_dict()},
        "search_options": options.to_dict(), "completed": {"old": "ignored"},
    }
    path = tmp_path / "search.json"
    write_search_plan(path, task)
    loaded = read_search_plan(path)
    assert loaded["keywords"] == task["keywords"]
    assert AdvancedQuery.from_dict(loaded["advanced_queries"]["高级检索 1"]) == query
    assert SearchOptions.from_dict(loaded["search_options"]) == options
    assert loaded["save_mode"] == "multi_csv" and loaded["detail_txt_export"]
    assert loaded["output_dir"] == str(tmp_path)
    assert "completed" not in loaded


@pytest.mark.parametrize("changes", [
    {"keywords": ["a", "a"]}, {"max_pages": 0}, {"save_mode": []},
    {"detail_txt_export": True, "include_details": False},
    {"advanced_queries": {"missing": {}}},
])
def test_invalid_search_plan_does_not_replace_existing_file(tmp_path, changes):
    path = tmp_path / "search.json"
    path.write_text("previous", encoding="utf-8")
    task = {"keywords": ["a"], "max_pages": 1, "save_mode": "single", **changes}
    with pytest.raises(ValueError):
        write_search_plan(path, task)
    assert path.read_text(encoding="utf-8") == "previous"


def test_search_plan_rejects_unrecognized_formats(tmp_path):
    path = tmp_path / "search.json"
    path.write_text(json.dumps({"format": "cnkibug-search-plan", "version": 99, "task": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        read_search_plan(path)
