from dataclasses import replace
from urllib.parse import quote

import pytest
from playwright.sync_api import sync_playwright

from cnkibug.app.runtime import DEFAULT_CONFIG
from cnkibug.cnki.details import ArticleDetailFetcher
from cnkibug.cnki.details import ArticleDetails
from cnkibug.cnki.pages import _append_page_details
from cnkibug.cnki.results import PageParseResult
from cnkibug.browser.session import ScrapeSession
from cnkibug.core.events import EventSink
from cnkibug.core.settings import get_scraper_settings


@pytest.fixture(scope="module")
def browser_context():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        yield context
        context.close()
        browser.close()


def _settings():
    return replace(
        get_scraper_settings(DEFAULT_CONFIG),
        timeout_goto_ms=2000,
        timeout_selector_ms=2000,
    )


def _data_url(body: str) -> str:
    return "data:text/html;charset=utf-8," + quote(body)


def test_fetch_extracts_keywords_and_full_hidden_abstract(browser_context):
    fetcher = ArticleDetailFetcher(browser_context, _settings())
    result = fetcher.fetch(
        _data_url("""
            <div class="brief"><h1>中文论文</h1></div>
            <input id="abstract_text"
                   value="完整摘要 Mg&lt;sub&gt;3&lt;/sub&gt; 内容">
            <span id="ChDivSummary">截断摘要...</span>
            <p class="keywords"><a>铝合金;</a><a>晶粒组织；</a></p>
        """),
        log_ref="page=1 row=1",
    )

    assert result.keywords == ["铝合金", "晶粒组织"]
    assert result.abstract == "完整摘要 Mg3 内容"
    assert result.failed is False


def test_fetch_treats_english_page_without_keywords_as_normal(browser_context):
    fetcher = ArticleDetailFetcher(browser_context, _settings())
    result = fetcher.fetch(
        _data_url("""
            <div class="brief"><h1>English paper</h1></div>
            <input id="abstract_text" value="Full English abstract.">
            <span id="ChDivSummary">Full English...</span>
        """),
        log_ref="page=1 row=2",
    )

    assert result.keywords == []
    assert result.abstract == "Full English abstract."
    assert result.failed is False


def test_fetch_returns_empty_and_reuses_page_after_failure(browser_context, caplog):
    fetcher = ArticleDetailFetcher(browser_context, _settings())
    failed = fetcher.fetch(
        _data_url("<p>详情结构缺失</p>"),
        log_ref="page=1 row=3",
    )

    assert failed.failed is True
    assert failed.keywords == []
    assert failed.abstract == ""
    assert "论文详情抓取失败" in caplog.text

    recovered = fetcher.fetch(
        _data_url("""
            <div class="brief"><h1>Recovered</h1></div>
            <span id="ChDivSummary">Fallback abstract.</span>
        """),
        log_ref="page=1 row=4",
    )
    assert recovered.abstract == "Fallback abstract."
    assert recovered.failed is False


class _RecordingEvents(EventSink):
    def __init__(self):
        self.items = []

    def emit(self, name, **payload):
        self.items.append((name, payload))


class _DetailFetcher:
    def __init__(self, results):
        self.results = iter(results)

    def fetch(self, url, *, log_ref):
        return next(self.results)


def test_page_enrichment_preserves_record_order_and_raw_keywords():
    events = _RecordingEvents()
    session = ScrapeSession(events)
    parsed = PageParseResult(records=[
        ["论文一", "", "", "", "url1"],
        ["论文二", "", "", "", "url2"],
    ])
    fetcher = _DetailFetcher([
        ArticleDetails(["关键词甲", "关键词乙"], "摘要一"),
        ArticleDetails([], "摘要二"),
    ])

    completed = _append_page_details(
        session,
        parsed,
        fetcher,
        keyword_ref="keyword_index=1/1",
        current_page=1,
        log_titles=False,
    )

    assert completed is True
    assert parsed.records == [
        ["论文一", "", "", "", "url1", "关键词甲\n关键词乙", "摘要一"],
        ["论文二", "", "", "", "url2", "", "摘要二"],
    ]
    assert parsed.detail_success == 2
    assert parsed.keywords_present == 1
    assert parsed.abstracts_present == 2
    assert events.items[-1] == (
        "progress_updated",
        {"detail_index": 0, "detail_total": 0},
    )


def test_page_enrichment_stops_without_checkpoint_data_on_verify_timeout():
    session = ScrapeSession(_RecordingEvents())
    parsed = PageParseResult(records=[["论文", "", "", "", "url"]])
    fetcher = _DetailFetcher([
        ArticleDetails([], "", failed=True, verify_timeout=True),
    ])

    completed = _append_page_details(
        session,
        parsed,
        fetcher,
        keyword_ref="keyword_index=1/1",
        current_page=1,
        log_titles=False,
    )

    assert completed is False
    assert session.stop_requested is True
    assert session.verify_timeout is True
    assert parsed.records == [["论文", "", "", "", "url"]]
