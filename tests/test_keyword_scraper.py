from threading import Event
from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from cnkibug.browser.session import ScrapeSession
from cnkibug.cnki import guard, keyword as keyword_scraper, pages, search
from cnkibug.cnki.details import ArticleDetails
from cnkibug.cnki.guard import (
    VERIFY_CANCELLED,
    VERIFY_NONE,
    VERIFY_PAGE_CLOSED,
    VERIFY_PASSED,
    VERIFY_TIMEOUT,
)
from cnkibug.cnki.models import STATUS_EMPTY, STATUS_FAILED, STATUS_STOPPED, STATUS_SUCCESS
from cnkibug.cnki.results import PageParseResult
from cnkibug.cnki.search import SEARCH_RESULTS, SEARCH_STOPPED, SearchResult
from cnkibug.core.events import EventSink
from cnkibug.core.settings import ScraperSettings


def _settings(max_advance_fail=1):
    return ScraperSettings(
        timeout_goto_ms=1,
        timeout_load_ms=1,
        timeout_selector_ms=1,
        verify_wait_timeout_sec=1,
        verify_notice_interval_sec=1,
        max_advance_fail=max_advance_fail,
        session_cache_enabled=False,
        session_cache_ttl_hours=1,
        log_save_path=True,
        log_keywords=False,
        log_scraped_records=False,
    )


def _patch_search_setup(monkeypatch):
    monkeypatch.setattr(
        keyword_scraper,
        "run_keyword_search",
        lambda *args, **kwargs: SearchResult(SEARCH_RESULTS),
    )
    monkeypatch.setattr(
        ScrapeSession,
        "wait_interruptibly",
        lambda self, seconds: not self.stop_requested,
    )


def test_session_wait_returns_immediately_when_cancelled():
    cancel_event = Event()
    cancel_event.set()
    session = ScrapeSession(cancel_event=cancel_event)

    assert session.wait_interruptibly(60) is False


def test_keyword_search_stops_after_verify_is_cancelled(monkeypatch):
    cancel_event = Event()
    session = ScrapeSession(cancel_event=cancel_event)
    session.page = object()

    monkeypatch.setattr(search, "open_home_page", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        search,
        "handle_verify_with_progress",
        lambda *args, **kwargs: cancel_event.set() or VERIFY_CANCELLED,
    )
    monkeypatch.setattr(
        search,
        "open_search_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("cancelled search must not open the search page")
        ),
    )

    result = search.run_keyword_search(session, "焊接", _settings(), "keyword=<hidden>")

    assert result.status == SEARCH_STOPPED


@pytest.mark.parametrize("closed_step", ["home", "search", "submit"])
def test_keyword_search_stops_when_navigation_page_is_closed(monkeypatch, closed_step):
    class Page:
        closed = False

        def is_closed(self):
            return self.closed

    page = Page()
    session = ScrapeSession()
    session.page = page

    def close_page(*args, **kwargs):
        page.closed = True
        raise PlaywrightError("Target page, context or browser has been closed")

    monkeypatch.setattr(
        search,
        "open_home_page",
        close_page if closed_step == "home" else lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        search,
        "handle_verify_with_progress",
        lambda *args, **kwargs: VERIFY_NONE,
    )
    monkeypatch.setattr(
        search,
        "open_search_page",
        close_page if closed_step == "search" else lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        search,
        "submit_search",
        close_page if closed_step == "submit" else lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(search, "wait_search_outcome", lambda *args, **kwargs: "no_content")

    result = search.run_keyword_search(session, "焊接", _settings(), "keyword=<hidden>")

    assert result.status == SEARCH_STOPPED
    assert result.reason == "浏览器页面已关闭"
    assert session.stop_requested is True


@pytest.mark.parametrize("closed_step", ["home", "search", "outcome"])
def test_keyword_search_treats_closed_page_timeout_as_stop(monkeypatch, closed_step):
    class Page:
        closed = False

        def is_closed(self):
            return self.closed

    page = Page()
    session = ScrapeSession()
    session.page = page

    def close_page(*args, **kwargs):
        page.closed = True
        raise PlaywrightTimeoutError("Timeout while page was closing")

    monkeypatch.setattr(
        search,
        "open_home_page",
        close_page if closed_step == "home" else lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        search,
        "handle_verify_with_progress",
        lambda *args, **kwargs: VERIFY_NONE,
    )
    monkeypatch.setattr(
        search,
        "open_search_page",
        close_page if closed_step == "search" else lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(search, "submit_search", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        search,
        "wait_search_outcome",
        close_page if closed_step == "outcome" else lambda *args, **kwargs: "no_content",
    )

    result = search.run_keyword_search(session, "焊接", _settings(), "keyword=<hidden>")

    assert result.status == SEARCH_STOPPED
    assert result.reason == "浏览器页面已关闭"
    assert session.stop_requested is True


def test_wait_search_outcome_detects_verify_url():
    class Result:
        def json_value(self):
            return "verify"

    class Page:
        def wait_for_function(self, script, **kwargs):
            assert "location.pathname.includes('/verify')" in script
            return Result()

    outcome = search.wait_search_outcome(
        Page(),
        SimpleNamespace(timeout_selector_ms=10),
    )

    assert outcome == "verify"


def test_verify_progress_callback_pauses_and_resumes(monkeypatch):
    page = SimpleNamespace(url="https://kns.cnki.net/verify")
    recorded = []

    class Events(EventSink):
        def emit(self, name, **payload):
            recorded.append(name)

    monkeypatch.setattr(
        guard.time,
        "sleep",
        lambda seconds: setattr(page, "url", "https://kns.cnki.net/"),
    )

    result = guard.handle_verify_with_progress(
        page,
        _settings(),
        Events(),
    )

    assert result == VERIFY_PASSED
    assert recorded == [
        "progress_paused",
        "verify_required",
        "verify_passed",
        "progress_resumed",
    ]


def test_verify_timeout_keeps_progress_paused(monkeypatch):
    page = SimpleNamespace(url="https://kns.cnki.net/verify")
    recorded = []

    class Events(EventSink):
        def emit(self, name, **payload):
            recorded.append(name)

    monkeypatch.setattr(guard.time, "sleep", lambda seconds: None)

    result = guard.handle_verify_with_progress(
        page,
        _settings(),
        Events(),
    )

    assert result == VERIFY_TIMEOUT
    assert recorded == ["progress_paused", "verify_required", "verify_timeout"]


def test_verify_wait_stops_promptly_when_gui_requests_cancellation(monkeypatch):
    page = SimpleNamespace(url="https://kns.cnki.net/verify")
    recorded = []

    class Events(EventSink):
        def emit(self, name, **payload):
            recorded.append(name)

        def cancel_requested(self):
            return True

    monkeypatch.setattr(
        guard.time,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(AssertionError("must not sleep")),
    )

    result = guard.handle_verify_with_progress(page, _settings(), Events())

    assert result == VERIFY_CANCELLED
    assert recorded == ["progress_paused", "verify_required", "progress_resumed"]


def test_verify_wait_stops_when_page_is_closed(monkeypatch):
    recorded = []

    class Page:
        url = "https://kns.cnki.net/verify"
        closed = False

        def is_closed(self):
            return self.closed

    class Events(EventSink):
        def emit(self, name, **payload):
            recorded.append(name)

    page = Page()
    monkeypatch.setattr(guard.time, "sleep", lambda seconds: setattr(page, "closed", True))

    result = guard.handle_verify_with_progress(page, _settings(), Events())

    assert result == VERIFY_PAGE_CLOSED
    assert recorded == ["progress_paused", "verify_required", "progress_resumed"]


def test_scrape_keyword_waits_for_delayed_verify(monkeypatch):
    outcomes = iter(["verify", "no_content"])
    verify_calls = 0

    def handle_verify(page, settings, events=None):
        nonlocal verify_calls
        verify_calls += 1
        return VERIFY_PASSED if verify_calls == 4 else VERIFY_NONE

    monkeypatch.setattr(search, "open_home_page", lambda page, settings, events=None: None)
    monkeypatch.setattr(search, "open_search_page", lambda page, settings, events=None: None)
    monkeypatch.setattr(
        search,
        "submit_search",
        lambda page, keyword, settings, events=None, wait_interruptibly=None: None,
    )
    monkeypatch.setattr(search, "wait_search_outcome", lambda page, settings: next(outcomes))
    monkeypatch.setattr(guard, "handle_verify", handle_verify)
    session = ScrapeSession()
    session.page = object()

    result = keyword_scraper.scrape_keyword(session, "焊接", 1, _settings())

    assert result.status == STATUS_EMPTY
    assert verify_calls == 4


def test_scrape_keyword_marks_partial_page_failure_as_failed(monkeypatch, caplog):
    _patch_search_setup(monkeypatch)
    monkeypatch.setattr(guard, "handle_verify", lambda page, settings, events=None: VERIFY_NONE)
    monkeypatch.setattr(
        pages,
        "parse_result_rows",
        lambda page, seen, stats, **kwargs: PageParseResult(
            records=[["标题", "", "", ""]],
            rows_seen=1,
        ),
    )
    monkeypatch.setattr(pages, "get_first_result_href", lambda page: "/detail/1")
    monkeypatch.setattr(pages, "get_result_page_numbers", lambda page: (1, 2))
    confirm_calls = []
    monkeypatch.setattr(
        pages,
        "wait_result_page_advanced",
        lambda *args, **kwargs: confirm_calls.append(True) or False,
    )

    class NextButton:
        def get_attribute(self, name):
            return "1"

        def click(self, **kwargs):
            return None

    monkeypatch.setattr(
        pages,
        "query_first",
        lambda page, group: NextButton() if group == "next_page" else None,
    )

    class Page:
        url = "https://kns.cnki.net/kns8s/"

        def wait_for_selector(self, *args, **kwargs):
            return None

        def is_closed(self):
            return False

        def title(self):
            return "results"

    session = ScrapeSession()
    session.page = Page()

    checkpoints = []
    result = keyword_scraper.scrape_keyword(
        session,
        "焊接",
        2,
        _settings(max_advance_fail=2),
        on_page_complete=lambda page, records: checkpoints.append((page, records)),
    )

    assert result.status == STATUS_FAILED
    assert result.records == [["标题", "", "", ""]]
    assert "翻页结果未确认" in result.reason
    assert checkpoints == [(1, result.records)]
    assert len(confirm_calls) == 2
    assert "关键词部分完成，将在恢复时重试" in caplog.text


def test_scrape_keyword_accepts_missing_next_button_on_confirmed_last_page(monkeypatch):
    _patch_search_setup(monkeypatch)
    monkeypatch.setattr(guard, "handle_verify", lambda page, settings, events=None: VERIFY_NONE)
    monkeypatch.setattr(pages, "get_result_page_numbers", lambda page: (1, 1))
    monkeypatch.setattr(pages, "query_first", lambda page, group: None)
    monkeypatch.setattr(
        pages,
        "parse_result_rows",
        lambda page, seen, stats, **kwargs: PageParseResult(
            records=[["标题", "", "", "", "https://example.test/1"]],
            rows_seen=1,
        ),
    )

    class Page:
        url = "https://kns.cnki.net/kns8s/"

        def wait_for_selector(self, *args, **kwargs):
            return None

    session = ScrapeSession()
    session.page = Page()

    result = keyword_scraper.scrape_keyword(session, "焊接", 2, _settings())

    assert result.status == STATUS_SUCCESS
    assert len(result.records) == 1


def test_scrape_keyword_rejects_missing_next_button_without_last_page_proof(monkeypatch):
    _patch_search_setup(monkeypatch)
    monkeypatch.setattr(guard, "handle_verify", lambda page, settings, events=None: VERIFY_NONE)
    monkeypatch.setattr(pages, "get_result_page_numbers", lambda page: (1, None))
    monkeypatch.setattr(pages, "query_first", lambda page, group: None)
    monkeypatch.setattr(
        pages,
        "parse_result_rows",
        lambda page, seen, stats, **kwargs: PageParseResult(
            records=[["标题", "", "", "", "https://example.test/1"]],
            rows_seen=1,
        ),
    )

    class Page:
        url = "https://kns.cnki.net/kns8s/"

        def wait_for_selector(self, *args, **kwargs):
            return None

        def title(self):
            return "results"

    checkpoints = []
    session = ScrapeSession()
    session.page = Page()

    result = keyword_scraper.scrape_keyword(
        session,
        "焊接",
        2,
        _settings(),
        on_page_complete=lambda page, records: checkpoints.append((page, records)),
    )

    assert result.status == STATUS_FAILED
    assert "无法确认末页" in result.reason
    assert checkpoints == [(1, result.records)]


def test_scrape_keyword_rejects_page_when_all_titles_are_unreadable(monkeypatch):
    _patch_search_setup(monkeypatch)
    monkeypatch.setattr(guard, "handle_verify", lambda page, settings, events=None: VERIFY_NONE)
    monkeypatch.setattr(pages, "get_result_page_numbers", lambda page: (1, 1))
    monkeypatch.setattr(
        pages,
        "parse_result_rows",
        lambda page, seen, stats, **kwargs: PageParseResult(
            rows_seen=2,
            skipped_no_title=2,
        ),
    )

    class Page:
        url = "https://kns.cnki.net/kns8s/"

        def wait_for_selector(self, *args, **kwargs):
            return None

    checkpoints = []
    session = ScrapeSession()
    session.page = Page()

    result = keyword_scraper.scrape_keyword(
        session,
        "焊接",
        1,
        _settings(),
        on_page_complete=lambda page, records: checkpoints.append((page, records)),
    )

    assert result.status == STATUS_FAILED
    assert "全部结果均无法解析标题" in result.reason
    assert checkpoints == []


def test_scrape_keyword_resumes_after_completed_page(monkeypatch):
    _patch_search_setup(monkeypatch)
    monkeypatch.setattr(guard, "handle_verify", lambda page, settings, events=None: VERIFY_NONE)
    monkeypatch.setattr(keyword_scraper, "get_first_result_title", lambda page: "旧标题")
    monkeypatch.setattr(
        keyword_scraper,
        "get_first_result_href",
        lambda page: "https://example.test/old",
    )
    positioned = []
    monkeypatch.setattr(
        keyword_scraper,
        "position_after_checkpoint",
        lambda session, completed_page, settings, keyword_ref: positioned.append(completed_page) or True,
    )
    monkeypatch.setattr(pages, "get_result_page_numbers", lambda page: (2, 2))
    monkeypatch.setattr(
        pages,
        "parse_result_rows",
        lambda page, seen, stats, **kwargs: PageParseResult(
            records=[["新标题", "", "", "", "https://example.test/new"]],
            rows_seen=1,
        ),
    )

    class Page:
        url = "https://kns.cnki.net/kns8s/"

        def wait_for_selector(self, *args, **kwargs):
            return None

    checkpoints = []
    session = ScrapeSession()
    session.page = Page()
    old_record = ["旧标题", "", "", "", "https://example.test/old"]

    result = keyword_scraper.scrape_keyword(
        session,
        "焊接",
        2,
        _settings(),
        start_page=2,
        initial_records=[old_record],
        on_page_complete=lambda page, records: checkpoints.append((page, records)),
    )

    assert result.status == STATUS_SUCCESS
    assert result.records == [
        old_record,
        ["新标题", "", "", "", "https://example.test/new"],
    ]
    assert positioned == [1]
    assert checkpoints == [(2, result.records)]


def test_scrape_keyword_finishes_from_last_page_checkpoint_without_network():
    session = ScrapeSession()
    session.page = object()
    records = [["标题", "", "", "", "https://example.test/1"]]

    result = keyword_scraper.scrape_keyword(
        session,
        "焊接",
        2,
        _settings(),
        start_page=3,
        initial_records=records,
    )

    assert result.status == STATUS_SUCCESS
    assert result.records == records


@pytest.mark.parametrize(
    ("current_title", "current_href"),
    [
        ("新首页标题", "https://example.test/old"),
        ("", "https://example.test/old"),
        ("旧首页标题", ""),
        ("旧首页标题", "https://example.test/changed"),
    ],
)
def test_scrape_keyword_logs_and_restarts_when_checkpoint_anchor_is_invalid(
    monkeypatch,
    caplog,
    current_title,
    current_href,
):
    _patch_search_setup(monkeypatch)
    monkeypatch.setattr(guard, "handle_verify", lambda page, settings, events=None: VERIFY_NONE)
    monkeypatch.setattr(
        keyword_scraper,
        "get_first_result_title",
        lambda page: current_title,
    )
    monkeypatch.setattr(
        keyword_scraper,
        "get_first_result_href",
        lambda page: current_href,
    )
    monkeypatch.setattr(pages, "get_result_page_numbers", lambda page: (1, 1))
    monkeypatch.setattr(pages, "query_first", lambda page, group: None)
    monkeypatch.setattr(
        pages,
        "parse_result_rows",
        lambda page, seen, stats, **kwargs: PageParseResult(
            records=[["新首页标题", "", "", "", "https://example.test/fresh"]],
            rows_seen=1,
        ),
    )

    class Page:
        url = "https://kns.cnki.net/kns8s/"

        def wait_for_selector(self, *args, **kwargs):
            return None

    checkpoints = []
    session = ScrapeSession()
    session.page = Page()

    result = keyword_scraper.scrape_keyword(
        session,
        "焊接",
        2,
        _settings(),
        start_page=2,
        initial_records=[["旧首页标题", "", "", "", "https://example.test/old"]],
        on_page_complete=lambda page, records: checkpoints.append((page, records)),
    )

    assert result.status == STATUS_SUCCESS
    assert result.records == [["新首页标题", "", "", "", "https://example.test/fresh"]]
    assert checkpoints[0][0] == 0
    assert checkpoints[1] == (1, result.records)
    assert "页级恢复首页锚点变化" in caplog.text


def test_detail_cancellation_discards_current_page_and_checkpoint(monkeypatch):
    _patch_search_setup(monkeypatch)
    monkeypatch.setattr(guard, "handle_verify", lambda page, settings, events=None: VERIFY_NONE)
    monkeypatch.setattr(pages, "get_result_page_numbers", lambda page: (1, 1))
    monkeypatch.setattr(
        pages,
        "parse_result_rows",
        lambda page, seen, stats, **kwargs: PageParseResult(
            records=[
                ["论文一", "", "", "", "https://example.test/1"],
                ["论文二", "", "", "", "https://example.test/2"],
            ],
            rows_seen=2,
        ),
    )

    class Page:
        url = "https://kns.cnki.net/kns8s/"

        def wait_for_selector(self, *args, **kwargs):
            return None

    session = ScrapeSession()
    session.page = Page()

    class DetailFetcher:
        calls = 0

        def fetch(self, url, *, log_ref):
            self.calls += 1
            if self.calls == 2:
                session.request_stop("用户停止")
            return ArticleDetails(["关键词"], "摘要")

    checkpoints = []
    result = keyword_scraper.scrape_keyword(
        session,
        "焊接",
        1,
        _settings(),
        detail_fetcher=DetailFetcher(),
        on_page_complete=lambda page, records: checkpoints.append((page, records)),
    )

    assert result.status == STATUS_STOPPED
    assert result.records == []
    assert checkpoints == []


def test_closed_page_during_page_parse_discards_current_page_and_checkpoint(monkeypatch):
    _patch_search_setup(monkeypatch)
    monkeypatch.setattr(
        guard,
        "handle_verify",
        lambda page, settings, events=None: VERIFY_NONE,
    )
    monkeypatch.setattr(pages, "get_result_page_numbers", lambda page: (1, 1))

    class Page:
        url = "https://kns.cnki.net/kns8s/"
        closed = False

        def wait_for_selector(self, *args, **kwargs):
            return None

        def is_closed(self):
            return self.closed

    page = Page()

    def parse_result_rows(page, seen, stats, *, stop_requested, **kwargs):
        page.closed = True
        assert stop_requested() is True
        return PageParseResult(
            records=[["不应提交", "", "", "", "https://example.test/1"]],
            rows_seen=1,
            cancelled=True,
        )

    monkeypatch.setattr(pages, "parse_result_rows", parse_result_rows)
    session = ScrapeSession()
    session.page = page
    checkpoints = []

    result = keyword_scraper.scrape_keyword(
        session,
        "焊接",
        1,
        _settings(),
        include_citation=True,
        on_page_complete=lambda page, records: checkpoints.append((page, records)),
    )

    assert result.status == STATUS_STOPPED
    assert result.reason == "浏览器页面已关闭"
    assert result.records == []
    assert checkpoints == []


def test_closed_page_after_page_parse_discards_current_page_and_checkpoint(monkeypatch):
    _patch_search_setup(monkeypatch)
    monkeypatch.setattr(guard, "handle_verify", lambda page, settings, events=None: VERIFY_NONE)
    monkeypatch.setattr(pages, "get_result_page_numbers", lambda page: (1, 1))

    class Page:
        url = "https://kns.cnki.net/kns8s/"
        closed = False

        def wait_for_selector(self, *args, **kwargs):
            return None

        def is_closed(self):
            return self.closed

    page = Page()

    def parse_result_rows(*args, **kwargs):
        page.closed = True
        return PageParseResult(
            records=[["不应提交", "", "", "", "https://example.test/1"]],
            rows_seen=1,
        )

    monkeypatch.setattr(pages, "parse_result_rows", parse_result_rows)
    session = ScrapeSession()
    session.page = page
    checkpoints = []

    result = keyword_scraper.scrape_keyword(
        session,
        "焊接",
        1,
        _settings(),
        on_page_complete=lambda page, records: checkpoints.append((page, records)),
    )

    assert result.status == STATUS_STOPPED
    assert result.reason == "浏览器页面已关闭"
    assert result.records == []
    assert checkpoints == []
