from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cnkibug.cnki import journals
from cnkibug.cnki.models import Paper
from cnkibug.core.events import EventSink


class Events(EventSink):
    def __init__(self):
        self.items = []

    def emit(self, name, **payload):
        self.items.append((name, payload))


def test_journal_indexing_only_uses_explicit_section_and_preserves_years():
    snapshot = {
        "name": "期刊", "indexing": "CSSCI来源期刊（2023-2024年度）",
        "lines": ["SCI 数据库介绍", "ISSN：1000-1234", "CN：11-1234/T", "（2025版）",
                  "复合影响因子：1.234", "综合影响因子：", "0.678"],
    }
    info = journals.parse_journal_snapshot(snapshot, "https://navi.cnki.net/knavi/detail/test")
    assert info["indexing"] == "CSSCI来源期刊（2023-2024年度）"
    assert info["issn"] == "1000-1234" and info["cn"] == "11-1234/T"
    assert info["metrics"] == ["（2025版） 复合影响因子：1.234", "综合影响因子：0.678"]
    assert info["source_url"].endswith("/test") and info["queried_at"]


def test_missing_indexing_is_not_inferred_from_other_page_text():
    info = journals.parse_journal_snapshot({"name": "期刊", "indexing": "", "lines": ["SCI", "CSSCI", "北大核心"]}, "url")
    assert info["indexing"] == ""


def test_journal_selection_requires_one_exact_name_and_deduplicates_links():
    candidate = {"name": "期刊", "url": "https://navi.cnki.net/knavi/detail/a"}
    assert journals.choose_journal([candidate, candidate, {"name": "其他期刊", "url": "other"}], "《期刊》") == candidate
    with pytest.raises(ValueError, match="多个同名"):
        journals.choose_journal([candidate, {**candidate, "url": "other"}], "期刊")
    with pytest.raises(ValueError, match="完全匹配"):
        journals.choose_journal([candidate], "期刊（英文版）")


def test_journal_batch_queries_each_source_once(monkeypatch):
    events = Events()
    page = Mock(is_closed=lambda: False)
    monkeypatch.setattr(journals, "open_browser_context", lambda *args: nullcontext(SimpleNamespace(new_page=lambda: page)))
    lookup = Mock(return_value={"name": "期刊"})
    monkeypatch.setattr(journals, "lookup_journal", lookup)
    papers = [
        (0, Paper(source="期刊", document_type="期刊")),
        (1, Paper(source="期刊", document_type="学术期刊")),
        (2, Paper(source="单位", document_type="专利")),
    ]
    message = journals.fetch_journal_info(papers, object(), object(), events)
    lookup.assert_called_once()
    assert any(payload["indices"] == [0, 1] for name, payload in events.items if name == "journal_result")
    assert "成功 1 种" in message and "跳过 1 篇" in message


def test_journal_verification_stop_does_not_start_next_query(monkeypatch):
    events = Events()
    page = Mock(url="https://navi.cnki.net/knavi", is_closed=lambda: False)
    monkeypatch.setattr(journals, "open_browser_context", lambda *args: nullcontext(SimpleNamespace(new_page=lambda: page)))
    lookup = Mock(side_effect=journals.JournalQueryStopped("Verification timeout"))
    monkeypatch.setattr(journals, "lookup_journal", lookup)
    papers = [(0, Paper(source="期刊一", document_type="期刊")), (1, Paper(source="期刊二", document_type="期刊"))]
    message = journals.fetch_journal_info(papers, object(), object(), events)
    lookup.assert_called_once()
    assert "失败 1 种，未查询 1 种" in message
