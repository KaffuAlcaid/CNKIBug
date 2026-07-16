from pathlib import Path

from cnkibug.app.runtime import DEFAULT_CONFIG, get_runtime_paths
from cnkibug.browser.runtime import BrowserLaunchResult
from cnkibug.cnki.models import STATUS_FAILED, STATUS_SUCCESS, make_keyword_result
from cnkibug.core.events import EventSink
from cnkibug.core.settings import get_scraper_settings
from cnkibug.fileio.exporter import SaveResult
from cnkibug.workflow import finalize, keyword_run
from cnkibug.workflow import runner as scrape_workflow
from cnkibug.workflow import state as task_state


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


class RecordingEvents(EventSink):
    def __init__(self, events):
        self.events = events

    def emit(self, name, **payload):
        self.events.append((name, payload))


def _patch_workflow(monkeypatch, tmp_path, saved_results, deleted, recorded=None):
    recorded = recorded if recorded is not None else []
    events = RecordingEvents(recorded)

    monkeypatch.setattr(scrape_workflow, "sync_playwright", lambda: _PlaywrightContext())
    monkeypatch.setattr(
        scrape_workflow,
        "launch_browser",
        lambda playwright, events: BrowserLaunchResult(_Browser(), "chromium"),
    )
    monkeypatch.setattr(
        scrape_workflow,
        "create_browser_context",
        lambda browser, settings, paths: _BrowserContext(),
    )
    monkeypatch.setattr(scrape_workflow, "warmup", lambda session, settings: True)
    monkeypatch.setattr(finalize, "save_cookie_state", lambda context, enabled, paths: None)
    monkeypatch.setattr(
        finalize,
        "save_task_report",
        lambda payload, ts, paths: "/tmp/task_report.json",
    )
    monkeypatch.setattr(scrape_workflow.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        task_state,
        "save_last_task",
        lambda state, paths: Path("/tmp/last_task.json"),
    )
    monkeypatch.setattr(
        finalize,
        "delete_last_task",
        lambda paths: deleted.append(True),
    )

    def save_all(
        save_mode,
        keywords,
        all_results,
        ts,
        include_citation=False,
        **kwargs,
    ):
        saved_results.append({key: list(value) for key, value in all_results.items()})
        return SaveResult()

    monkeypatch.setattr(keyword_run, "save_all", save_all)
    monkeypatch.setattr(finalize, "save_all", save_all)
    return {
        "settings": get_scraper_settings(DEFAULT_CONFIG),
        "paths": get_runtime_paths(tmp_path),
        "events": events,
    }


def test_resume_retries_failed_keyword_and_deletes_finished_task(monkeypatch, tmp_path):
    saved_results = []
    deleted = []
    run_context = _patch_workflow(monkeypatch, tmp_path, saved_results, deleted)
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

    monkeypatch.setattr(keyword_run, "scrape_keyword", scrape_keyword)

    scrape_workflow.scrape_cnki(
        ["ignored"],
        1,
        "single",
        resume_state=state,
        **run_context,
    )

    assert calls == [(2, [["old", "", "", ""]])]
    assert saved_results[-1] == {"焊接": [["complete", "", "", ""]]}
    assert state["completed"]["焊接"]["status"] == STATUS_SUCCESS
    assert state["completed"]["焊接"]["completed_page"] == 2
    assert deleted == [True]


def test_resume_preserves_partial_records_when_retry_fails(monkeypatch, tmp_path, caplog):
    saved_results = []
    deleted = []
    progress_events = []
    run_context = _patch_workflow(
        monkeypatch,
        tmp_path,
        saved_results,
        deleted,
        progress_events,
    )
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
        keyword_run,
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

    scrape_workflow.scrape_cnki(
        ["ignored"],
        1,
        "single",
        resume_state=state,
        **run_context,
    )

    expected = {"焊接": [["old", "", "", ""], ["new", "", "", ""]]}
    assert saved_results[-1] == expected
    assert state["completed"]["焊接"]["records"] == expected["焊接"]
    assert deleted == []
    assert (
        "progress_stopped",
        {"message": "任务未完整完成，已保留断点"},
    ) in progress_events
    assert not any(name == "progress_completed" for name, _ in progress_events)
    assert "已合并保留部分结果" in caplog.text


def test_browser_launch_failure_still_writes_not_started_report(monkeypatch, tmp_path):
    saved_results = []
    deleted = []
    captured_reports = []
    run_context = _patch_workflow(monkeypatch, tmp_path, saved_results, deleted)

    def fail_launch(playwright, events):
        raise RuntimeError("browser unavailable")

    monkeypatch.setattr(scrape_workflow, "launch_browser", fail_launch)
    monkeypatch.setattr(
        finalize,
        "save_task_report",
        lambda payload, ts, paths: captured_reports.append(payload) or "/tmp/report.json",
    )

    scrape_workflow.scrape_cnki(
        ["焊接", "增材"],
        2,
        "multi_csv",
        **run_context,
    )

    assert saved_results[-1] == {}
    assert captured_reports[0]["execution"]["stopped"] is True
    assert [item["status"] for item in captured_reports[0]["keywords"]] == [
        "not_started",
        "not_started",
    ]
    assert deleted == []


def test_new_task_propagates_citation_setting(monkeypatch, tmp_path):
    saved_results = []
    deleted = []
    captured_reports = []
    citation_flags = []
    run_context = _patch_workflow(monkeypatch, tmp_path, saved_results, deleted)

    def scrape_keyword(*args, **kwargs):
        assert kwargs["include_citation"] is True
        kwargs["on_page_complete"](
            1,
            [["标题", "作者", "来源", "日期", "https://example.test/1", "[1] 引文"]],
        )
        return make_keyword_result(
            "焊接",
            1,
            1,
            [["标题", "作者", "来源", "日期", "https://example.test/1", "[1] 引文"]],
            STATUS_SUCCESS,
        )

    def save_all(*args, include_citation=False, **kwargs):
        citation_flags.append(include_citation)
        return SaveResult()

    monkeypatch.setattr(keyword_run, "scrape_keyword", scrape_keyword)
    monkeypatch.setattr(keyword_run, "save_all", save_all)
    monkeypatch.setattr(finalize, "save_all", save_all)
    monkeypatch.setattr(
        finalize,
        "save_task_report",
        lambda payload, ts, paths: captured_reports.append(payload) or "/tmp/report.json",
    )

    scrape_workflow.scrape_cnki(
        ["焊接"],
        1,
        "single",
        include_citation=True,
        **run_context,
    )

    assert citation_flags == [True, True]
    assert captured_reports[0]["request"]["include_citation"] is True
    assert captured_reports[0]["execution"]["citation"] == {
        "success": 1,
        "failed": 0,
        "empty": 0,
    }
    assert deleted == [True]


def test_progress_display_receives_page_verify_save_and_complete_events(monkeypatch, tmp_path):
    saved_results = []
    deleted = []
    progress_events = []
    run_context = _patch_workflow(
        monkeypatch,
        tmp_path,
        saved_results,
        deleted,
        progress_events,
    )

    records = [["标题", "作者", "来源", "日期", "https://example.test/1"]]

    def scrape_keyword(*args, **kwargs):
        session = args[0]
        session.events.emit("progress_updated", page=1)
        session.events.emit("progress_paused")
        session.events.emit("progress_resumed")
        kwargs["on_page_complete"](1, records)
        return make_keyword_result("焊接", 1, 1, records, STATUS_SUCCESS)

    monkeypatch.setattr(keyword_run, "scrape_keyword", scrape_keyword)

    scrape_workflow.scrape_cnki(["焊接"], 1, "single", **run_context)

    assert (
        "progress_started",
        {"low_seconds": 8, "high_seconds": 12},
    ) in progress_events
    assert ("progress_paused", {}) in progress_events
    assert ("progress_resumed", {}) in progress_events
    assert ("progress_saving", {}) in progress_events
    assert ("progress_completed", {}) in progress_events
    assert progress_events[-1][0] == "task_report"
    assert ("progress_closed", {}) in progress_events
    assert any(
        event[0] == "progress_updated" and event[1].get("records") == 1
        for event in progress_events
    )
