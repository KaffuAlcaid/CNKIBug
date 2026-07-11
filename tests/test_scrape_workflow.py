from pathlib import Path
from types import SimpleNamespace

from cnkibug import scrape_workflow, task_state
from cnkibug.exporter import SaveResult
from cnkibug.scrape_report import STATUS_FAILED, STATUS_SUCCESS, make_keyword_result


class _PlaywrightContext:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _BrowserContext:
    def new_page(self):
        return object()

    def close(self):
        return None


class _Browser:
    def close(self):
        return None


def _patch_workflow(monkeypatch, saved_results, deleted):
    monkeypatch.setattr(scrape_workflow, "sync_playwright", lambda: _PlaywrightContext())
    monkeypatch.setattr(scrape_workflow, "launch_browser", lambda playwright: _Browser())
    monkeypatch.setattr(
        scrape_workflow,
        "create_browser_context",
        lambda browser, settings: _BrowserContext(),
    )
    monkeypatch.setattr(scrape_workflow, "warmup", lambda session, settings: True)
    monkeypatch.setattr(scrape_workflow, "save_cookie_state", lambda context, enabled: None)
    monkeypatch.setattr(scrape_workflow, "print_browser_banner", lambda: None)
    monkeypatch.setattr(scrape_workflow, "print_task_report", lambda report, results: None)
    monkeypatch.setattr(scrape_workflow.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        scrape_workflow,
        "get_scraper_settings",
        lambda: SimpleNamespace(log_keywords=False, session_cache_enabled=False),
    )
    monkeypatch.setattr(
        scrape_workflow,
        "save_last_task",
        lambda state: Path("/tmp/last_task.json"),
    )
    monkeypatch.setattr(
        scrape_workflow,
        "delete_last_task",
        lambda: deleted.append(True),
    )

    def save_all(save_mode, keywords, all_results, ts, announce):
        saved_results.append({key: list(value) for key, value in all_results.items()})
        return SaveResult()

    monkeypatch.setattr(scrape_workflow, "save_all", save_all)


def test_resume_retries_failed_keyword_and_deletes_finished_task(monkeypatch):
    saved_results = []
    deleted = []
    _patch_workflow(monkeypatch, saved_results, deleted)
    state = task_state.make_task_state(["焊接"], 2, "single", "TS")
    task_state.mark_keyword_done(
        state,
        make_keyword_result(
            "焊接",
            1,
            1,
            [["old", "", "", ""]],
            STATUS_FAILED,
            "第 1 页后翻页失败",
        ),
    )
    task_state.mark_keyword_progress(state, "焊接", 1, [["old", "", "", ""]])
    task_state.mark_keyword_done(
        state,
        make_keyword_result(
            "焊接",
            1,
            1,
            [["old", "", "", ""]],
            STATUS_FAILED,
            "第 2 页失败",
        ),
    )
    calls = []

    def scrape_keyword(*args, **kwargs):
        calls.append((kwargs["start_page"], kwargs["initial_records"]))
        kwargs["on_page_complete"](2, [["complete", "", "", ""]])
        return make_keyword_result(
            "焊接",
            1,
            1,
            [["complete", "", "", ""]],
            STATUS_SUCCESS,
        )

    monkeypatch.setattr(scrape_workflow, "scrape_keyword", scrape_keyword)

    scrape_workflow.scrape_cnki(["ignored"], 1, "single", resume_state=state)

    assert calls == [(2, [["old", "", "", ""]])]
    assert saved_results[-1] == {"焊接": [["complete", "", "", ""]]}
    assert state["completed"]["焊接"]["status"] == STATUS_SUCCESS
    assert state["completed"]["焊接"]["completed_page"] == 2
    assert deleted == [True]


def test_resume_preserves_partial_records_when_retry_fails(monkeypatch, caplog):
    saved_results = []
    deleted = []
    _patch_workflow(monkeypatch, saved_results, deleted)
    state = task_state.make_task_state(["焊接"], 2, "single", "TS")
    task_state.mark_keyword_done(
        state,
        make_keyword_result(
            "焊接",
            1,
            1,
            [["old", "", "", ""]],
            STATUS_FAILED,
            "旧失败",
        ),
    )
    monkeypatch.setattr(
        scrape_workflow,
        "scrape_keyword",
        lambda *args, **kwargs: make_keyword_result(
            "焊接",
            1,
            1,
            [["new", "", "", ""]],
            STATUS_FAILED,
            "再次失败",
        ),
    )

    scrape_workflow.scrape_cnki(["ignored"], 1, "single", resume_state=state)

    expected = {"焊接": [["old", "", "", ""], ["new", "", "", ""]]}
    assert saved_results[-1] == expected
    assert state["completed"]["焊接"]["records"] == expected["焊接"]
    assert deleted == []
    assert "已合并保留部分结果" in caplog.text
