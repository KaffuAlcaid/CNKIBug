from types import SimpleNamespace

from cnkibug import keyword_scraper
from cnkibug.cnki_guard import VERIFY_NONE, VERIFY_PASSED, VERIFY_TIMEOUT
from cnkibug.cnki_results import PageParseResult
from cnkibug.scrape_report import STATUS_EMPTY, STATUS_FAILED, STATUS_SUCCESS
from cnkibug.scrape_session import ScrapeSession
from cnkibug.settings import ScraperSettings


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
        log_keywords=False,
        log_scraped_records=False,
    )


def _patch_search_setup(monkeypatch):
    monkeypatch.setattr(keyword_scraper, "_open_home_page", lambda page, settings: None)
    monkeypatch.setattr(keyword_scraper, "_open_search_page", lambda page, settings: None)
    monkeypatch.setattr(keyword_scraper, "_submit_search", lambda page, keyword, settings: None)
    monkeypatch.setattr(keyword_scraper, "get_result_page_numbers", lambda page: (None, None))
    monkeypatch.setattr(keyword_scraper.time, "sleep", lambda seconds: None)


def test_wait_search_outcome_detects_verify_url():
    class Result:
        def json_value(self):
            return "verify"

    class Page:
        def wait_for_function(self, script, **kwargs):
            assert "location.pathname.includes('/verify')" in script
            return Result()

    outcome = keyword_scraper._wait_search_outcome(
        Page(),
        SimpleNamespace(timeout_selector_ms=10),
    )

    assert outcome == "verify"


def test_verify_progress_callback_pauses_and_resumes(monkeypatch):
    page = SimpleNamespace(url="https://kns.cnki.net/verify")
    events = []
    monkeypatch.setattr(
        keyword_scraper,
        "handle_verify",
        lambda page, settings: VERIFY_PASSED,
    )

    result = keyword_scraper._handle_verify_with_progress(
        page,
        _settings(),
        events.append,
    )

    assert result == VERIFY_PASSED
    assert events == [True, False]


def test_verify_timeout_keeps_progress_paused(monkeypatch):
    page = SimpleNamespace(url="https://kns.cnki.net/verify")
    events = []
    monkeypatch.setattr(
        keyword_scraper,
        "handle_verify",
        lambda page, settings: VERIFY_TIMEOUT,
    )

    result = keyword_scraper._handle_verify_with_progress(
        page,
        _settings(),
        events.append,
    )

    assert result == VERIFY_TIMEOUT
    assert events == [True]


def test_scrape_keyword_waits_for_delayed_verify(monkeypatch):
    _patch_search_setup(monkeypatch)
    outcomes = iter(["verify", "no_content"])
    verify_calls = 0

    def handle_verify(page, settings):
        nonlocal verify_calls
        verify_calls += 1
        return VERIFY_PASSED if verify_calls == 4 else VERIFY_NONE

    monkeypatch.setattr(keyword_scraper, "handle_verify", handle_verify)
    monkeypatch.setattr(
        keyword_scraper,
        "_wait_search_outcome",
        lambda page, settings: next(outcomes),
    )
    session = ScrapeSession()
    session.page = object()

    result = keyword_scraper.scrape_keyword(session, "焊接", 1, _settings())

    assert result.status == STATUS_EMPTY
    assert verify_calls == 4


def test_scrape_keyword_marks_partial_page_failure_as_failed(monkeypatch, caplog):
    _patch_search_setup(monkeypatch)
    monkeypatch.setattr(keyword_scraper, "handle_verify", lambda page, settings: VERIFY_NONE)
    monkeypatch.setattr(keyword_scraper, "_wait_search_outcome", lambda page, settings: "has_results")
    monkeypatch.setattr(
        keyword_scraper,
        "parse_result_rows",
        lambda page, seen, stats: PageParseResult(records=[["标题", "", "", ""]], rows_seen=1),
    )
    monkeypatch.setattr(keyword_scraper, "get_first_result_href", lambda page: "/detail/1")
    monkeypatch.setattr(keyword_scraper, "get_result_page_numbers", lambda page: (1, 2))
    confirm_calls = []
    monkeypatch.setattr(
        keyword_scraper,
        "wait_result_page_advanced",
        lambda *args, **kwargs: confirm_calls.append(True) or False,
    )

    class NextButton:
        def get_attribute(self, name):
            return "1"

        def click(self, **kwargs):
            return None

    monkeypatch.setattr(
        keyword_scraper,
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
    monkeypatch.setattr(keyword_scraper, "handle_verify", lambda page, settings: VERIFY_NONE)
    monkeypatch.setattr(keyword_scraper, "_wait_search_outcome", lambda page, settings: "has_results")
    monkeypatch.setattr(keyword_scraper, "get_result_page_numbers", lambda page: (1, 1))
    monkeypatch.setattr(keyword_scraper, "query_first", lambda page, group: None)
    monkeypatch.setattr(
        keyword_scraper,
        "parse_result_rows",
        lambda page, seen, stats: PageParseResult(
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
    monkeypatch.setattr(keyword_scraper, "handle_verify", lambda page, settings: VERIFY_NONE)
    monkeypatch.setattr(keyword_scraper, "_wait_search_outcome", lambda page, settings: "has_results")
    monkeypatch.setattr(keyword_scraper, "get_result_page_numbers", lambda page: (1, None))
    monkeypatch.setattr(keyword_scraper, "query_first", lambda page, group: None)
    monkeypatch.setattr(
        keyword_scraper,
        "parse_result_rows",
        lambda page, seen, stats: PageParseResult(
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
    monkeypatch.setattr(keyword_scraper, "handle_verify", lambda page, settings: VERIFY_NONE)
    monkeypatch.setattr(keyword_scraper, "_wait_search_outcome", lambda page, settings: "has_results")
    monkeypatch.setattr(keyword_scraper, "get_result_page_numbers", lambda page: (1, 1))
    monkeypatch.setattr(
        keyword_scraper,
        "parse_result_rows",
        lambda page, seen, stats: PageParseResult(rows_seen=2, skipped_no_title=2),
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
    monkeypatch.setattr(keyword_scraper, "handle_verify", lambda page, settings: VERIFY_NONE)
    monkeypatch.setattr(keyword_scraper, "_wait_search_outcome", lambda page, settings: "has_results")
    monkeypatch.setattr(keyword_scraper, "get_first_result_title", lambda page: "旧标题")
    positioned = []
    monkeypatch.setattr(
        keyword_scraper,
        "_position_after_checkpoint",
        lambda session, completed_page, settings, keyword_ref, on_verify_state: positioned.append(completed_page) or True,
    )
    monkeypatch.setattr(
        keyword_scraper,
        "parse_result_rows",
        lambda page, seen, stats: PageParseResult(
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


def test_scrape_keyword_logs_and_restarts_when_checkpoint_anchor_changes(monkeypatch, caplog):
    _patch_search_setup(monkeypatch)
    monkeypatch.setattr(keyword_scraper, "handle_verify", lambda page, settings: VERIFY_NONE)
    monkeypatch.setattr(keyword_scraper, "_wait_search_outcome", lambda page, settings: "has_results")
    monkeypatch.setattr(keyword_scraper, "get_first_result_title", lambda page: "新首页标题")
    monkeypatch.setattr(keyword_scraper, "get_result_page_numbers", lambda page: (1, 1))
    monkeypatch.setattr(keyword_scraper, "query_first", lambda page, group: None)
    monkeypatch.setattr(
        keyword_scraper,
        "parse_result_rows",
        lambda page, seen, stats: PageParseResult(
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
