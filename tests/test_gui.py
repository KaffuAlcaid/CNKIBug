import builtins
import importlib.util
from pathlib import Path
from queue import Queue
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import run_gui

from cnkibug.app.runtime import DEFAULT_CONFIG, RuntimeState, get_runtime_paths
from cnkibug.cnki.models import STATUS_FAILED, STATUS_SUCCESS, make_keyword_result
from cnkibug.core.version import APP_VERSION
from cnkibug.gui.app import (
    CNKIBugApp,
    GuiTaskRequest,
    _EVENTS_PER_DRAIN,
    _MAX_LOG_LINES,
    _fit_window_geometry,
    _merge_task_keywords,
    _prepare_output_directory,
    _resolve_save_mode,
)
from cnkibug.gui.events import GuiEvent, GuiEventSink
from cnkibug.gui.settings import SettingsDialog, _NUMERIC_FIELDS
from cnkibug.workflow.report import TaskReport
from cnkibug.workflow.state import (
    make_task_state,
    mark_keyword_done,
    mark_keyword_progress,
)


def test_gui_self_check_reports_app_version(capsys):
    assert run_gui._run_self_check() == 0
    assert capsys.readouterr().out.strip() == f"CNKIBug GUI self-check OK: {APP_VERSION}"


def test_gui_launcher_module_load_does_not_import_project(monkeypatch):
    original_import = builtins.__import__

    def fail_project_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "cnkibug" or name.startswith("cnkibug."):
            raise ImportError("project import blocked")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_project_import)
    spec = importlib.util.spec_from_file_location("isolated_run_gui", run_gui.__file__)
    assert spec is not None
    assert spec.loader is not None
    isolated_launcher = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(isolated_launcher)

    assert callable(isolated_launcher._run)
    assert callable(isolated_launcher._run_self_check)


def test_gui_startup_import_failure_reports_console_dialog_and_log_path(
    monkeypatch,
    capsys,
    tmp_path,
):
    original_import = builtins.__import__
    import_error = ImportError("No module named 'ttkbootstrap'")
    log_path = tmp_path / "gui_startup_error.log"
    shown = []

    def fail_gui_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "cnkibug.gui.app":
            raise import_error
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_gui_import)
    monkeypatch.setattr(run_gui, "_write_startup_error_log", lambda error: log_path)
    monkeypatch.setattr(run_gui, "_show_startup_error_dialog", shown.append)

    try:
        run_gui._run()
    except SystemExit as error:
        assert error.code == 1
        assert error.__cause__ is import_error
    else:
        raise AssertionError("GUI launcher did not exit after an import failure")

    assert capsys.readouterr().out.splitlines() == [
        "CNKIBug GUI 启动失败：No module named 'ttkbootstrap'",
        '请运行：pip install -e ".[gui]"',
        f"日志路径：{log_path}",
    ]
    assert shown == [
        "CNKIBug GUI 启动失败：No module named 'ttkbootstrap'"
        f"\n\n日志路径：{log_path}"
    ]


def test_gui_startup_import_failure_writes_traceback_log(monkeypatch, tmp_path):
    monkeypatch.setattr(run_gui, "_entry_directory", lambda: tmp_path)
    try:
        raise ImportError("broken GUI dependency")
    except ImportError as error:
        log_path = run_gui._write_startup_error_log(error)

    assert log_path == tmp_path / "CNKIBug-data" / "log" / "gui_startup_error.log"
    log_text = log_path.read_text(encoding="utf-8")
    assert "GUI startup import failure" in log_text
    assert "ImportError: broken GUI dependency" in log_text


def test_gui_source_and_frozen_entry_directories(monkeypatch, tmp_path):
    assert run_gui._entry_directory() == Path(run_gui.__file__).resolve().parent
    assert run_gui._resource_path("icon.ico") == Path(run_gui.__file__).resolve().parent / "icon.ico"

    executable = tmp_path / "CNKIBug-GUI.exe"
    bundle_dir = tmp_path / "bundle"
    monkeypatch.setattr(run_gui.sys, "frozen", True, raising=False)
    monkeypatch.setattr(run_gui.sys, "executable", str(executable))
    monkeypatch.setattr(run_gui.sys, "_MEIPASS", str(bundle_dir), raising=False)
    assert run_gui._entry_directory() == tmp_path
    assert run_gui._resource_path("icon.ico") == bundle_dir / "icon.ico"


def test_gui_window_uses_preferred_size_and_fits_smaller_screens():
    assert _fit_window_geometry(3840, 2160) == (900, 1219, 1470, 470)
    assert _fit_window_geometry(1920, 1080) == (900, 1000, 510, 40)


def test_gui_save_mode_is_derived_without_single_multi_mode_selector():
    assert _resolve_save_mode(1, "excel", False) == "single"
    assert _resolve_save_mode(1, "csv", True) == "single_csv"
    assert _resolve_save_mode(2, "excel", False) == "multi_merge"
    assert _resolve_save_mode(2, "excel", True) == "multi_split"
    assert _resolve_save_mode(2, "csv", True) == "multi_csv"


def test_gui_keyword_list_append_replace_and_dedupe():
    appended = _merge_task_keywords(["人工智能", "数字人文"], ["数字人文", "大语言模型"])
    assert appended.keywords == ["人工智能", "数字人文", "大语言模型"]
    assert appended.duplicates == ["数字人文"]

    replaced = _merge_task_keywords(
        ["人工智能"],
        ["大语言模型", "大语言模型"],
        replace=True,
    )
    assert replaced.keywords == ["大语言模型"]
    assert replaced.duplicates == ["大语言模型"]


def test_gui_info_dialog_uses_project_version_and_disclaimer(monkeypatch):
    shown = {}

    def showinfo(title, message, *, parent):
        shown.update(title=title, message=message, parent=parent)

    monkeypatch.setattr("cnkibug.gui.app.messagebox.showinfo", showinfo)
    app = CNKIBugApp.__new__(CNKIBugApp)
    app.root = object()

    app._show_info()

    assert shown["title"] == "关于 CNKIBug"
    assert f"版本：v{APP_VERSION}" in shown["message"]
    assert "与中国知网（CNKI）及其关联方不存在隶属、授权、合作或背书关系" in shown["message"]
    assert "相关使用风险由使用者依法承担" in shown["message"]
    assert "即视为您已阅读、理解并同意本免责声明及 MIT License" in shown["message"]
    assert shown["parent"] is app.root


def test_gui_cleanup_requires_confirmation_and_uses_current_day_copy(monkeypatch):
    prompts = []
    completed = []
    app = CNKIBugApp.__new__(CNKIBugApp)
    app.root = object()
    app.runtime = object()
    app._running = False

    monkeypatch.setattr(
        "cnkibug.gui.app.messagebox.askyesno",
        lambda title, message, **kwargs: prompts.append((title, message, kwargs)) or True,
    )
    monkeypatch.setattr(
        "cnkibug.gui.app.cleanup_runtime_history",
        lambda state: SimpleNamespace(deleted=2, failed=0),
    )
    monkeypatch.setattr(
        "cnkibug.gui.app.messagebox.showinfo",
        lambda title, message, **kwargs: completed.append((title, message, kwargs)),
    )

    app._cleanup_logs_and_reports()

    assert prompts[0][0] == "清理日志与报告"
    assert "本日运行文件将保留" in prompts[0][1]
    assert "此操作不可撤销" in prompts[0][1]
    assert completed[0][1] == "已删除 2 个历史文件。"


def test_gui_cleanup_cancel_does_not_delete(monkeypatch):
    cleanup = Mock()
    app = CNKIBugApp.__new__(CNKIBugApp)
    app.root = object()
    app.runtime = object()
    app._running = False

    monkeypatch.setattr("cnkibug.gui.app.messagebox.askyesno", lambda *args, **kwargs: False)
    monkeypatch.setattr("cnkibug.gui.app.cleanup_runtime_history", cleanup)

    app._cleanup_logs_and_reports()

    cleanup.assert_not_called()


def test_gui_maintenance_actions_are_hidden_while_running():
    app = CNKIBugApp.__new__(CNKIBugApp)
    app._form_controls = [Mock()]
    app._stop_button = Mock()
    app._maintenance_actions = Mock()
    app._sync_keyword_action_states = Mock()
    app._sync_option_states = Mock()

    app._set_running(True)

    app._maintenance_actions.pack_forget.assert_called_once_with()

    app._set_running(False)

    app._maintenance_actions.pack.assert_called_once_with(fill="x", pady=(6, 0))


def test_gui_clears_keywords_only_after_completed_task():
    app = CNKIBugApp.__new__(CNKIBugApp)
    app._freeze_active = Mock()
    app._current_percentage = Mock(return_value=42)
    app._progress_var = Mock()
    app._progress_percent_var = Mock()
    app._status_var = Mock()
    app._set_keywords = Mock()
    app._keyword_var = Mock()

    app._handle_event(GuiEvent("progress_completed", {}))

    app._set_keywords.assert_called_once_with([])
    app._keyword_var.set.assert_called_once_with("")

    app._set_keywords.reset_mock()
    app._keyword_var.set.reset_mock()
    app._handle_event(GuiEvent("progress_stopped", {"message": "任务已停止"}))

    app._set_keywords.assert_not_called()
    app._keyword_var.set.assert_not_called()


def test_gui_event_sink_marshals_confirmation_and_cancellation():
    event_queue = Queue()
    cancel_event = Event()
    sink = GuiEventSink(event_queue, cancel_event)
    result = []

    thread = Thread(target=lambda: result.append(sink.confirm("继续？")))
    thread.start()
    event = event_queue.get(timeout=1)
    assert event.name == "confirm_requested"
    event.payload["response_queue"].put(True)
    thread.join(timeout=1)

    assert result == [True]
    assert sink.cancel_requested() is False
    cancel_event.set()
    assert sink.cancel_requested() is True


def test_gui_progress_percent_label_tracks_reset_running_save_stop_and_completion(monkeypatch):
    app = CNKIBugApp.__new__(CNKIBugApp)
    app._progress_var = Mock()
    app._progress_percent_var = Mock()
    app._time_var = Mock()
    app._total_eta_var = Mock()
    app._status_var = Mock()
    app._detail_var = Mock()
    app._progress_state = {"keyword": "", "records": 0}
    app._task_started_at = None
    app._actual_seconds = None
    app._active_elapsed = 0.0
    app._active_started_at = None
    app._eta_low = 40
    app._eta_high = 72
    app._total_eta_low = 0
    app._total_eta_high = 0
    app._progress_mode = "idle"
    app._stopped_progress = 0

    app._reset_progress()
    app._progress_percent_var.set.assert_any_call("0%")

    app._progress_mode = "running"
    app._active_started_at = 0.0
    app.root = Mock()
    app.root.winfo_exists.return_value = False
    app._update_memory_status = Mock()
    monkeypatch.setattr("cnkibug.gui.app.time.monotonic", lambda: 20.0)
    app._tick()
    app._progress_percent_var.set.assert_any_call("45%")

    app._freeze_active = Mock()
    app._current_percentage = Mock(return_value=45)
    app._set_keywords = Mock()
    app._keyword_var = Mock()
    app._handle_event(GuiEvent("progress_saving", {}))
    app._progress_percent_var.set.assert_called_with("99%")

    app._progress_mode = "running"
    app._handle_event(GuiEvent("progress_stopped", {"message": "任务已停止"}))
    app._progress_percent_var.set.assert_called_with("45%")

    app._handle_event(GuiEvent("progress_completed", {}))
    app._progress_percent_var.set.assert_called_with("100%")


def test_gui_event_drain_limits_each_callback_batch():
    app = CNKIBugApp.__new__(CNKIBugApp)
    app._event_queue = Queue()
    app._handle_event = Mock()
    app.root = Mock()
    app.root.winfo_exists.return_value = False
    for index in range(_EVENTS_PER_DRAIN + 1):
        app._event_queue.put(GuiEvent("test", {"index": index}))

    app._drain_events()

    assert app._handle_event.call_count == _EVENTS_PER_DRAIN
    assert app._event_queue.qsize() == 1


def test_gui_log_discards_oldest_lines_at_limit():
    app = CNKIBugApp.__new__(CNKIBugApp)
    app._log = Mock()
    app._log_line_count = _MAX_LOG_LINES

    app._append_log("新日志")

    app._log.delete.assert_called_once_with("1.0", "2.0")
    assert app._log_line_count == _MAX_LOG_LINES


def test_gui_handles_page_debug_and_task_report_events():
    app = CNKIBugApp.__new__(CNKIBugApp)
    app._append_log = Mock()
    report = TaskReport(total_keywords=2)
    report.add(
        make_keyword_result(
            "完成项",
            1,
            2,
            [["标题", "作者", "来源", "日期"]],
            STATUS_SUCCESS,
        )
    )
    report.add(
        make_keyword_result("失败项", 2, 2, [], STATUS_FAILED, "结果页超时")
    )

    app._handle_event(
        GuiEvent(
            "page_debug",
            {"context": "结果加载超时", "url": "https://example.test", "title": "错误页"},
        )
    )
    app._handle_event(
        GuiEvent(
            "task_report",
            {"report": report, "all_results": {"完成项": [["标题"]], "失败项": []}},
        )
    )

    logged = [call.args[0] for call in app._append_log.call_args_list]
    assert "页面异常：结果加载超时" in logged
    assert "当前 URL：https://example.test" in logged
    assert "页面标题：错误页" in logged
    assert "本轮摘要：成功 1，无结果 0，失败 1，中止 0，共 1 条。" in logged
    assert "第 2/2 个检索项「失败项」：结果页超时" in logged


def test_gui_total_eta_is_calculated_independently_for_new_and_resumed_tasks():
    app = CNKIBugApp.__new__(CNKIBugApp)
    app._total_eta_var = Mock()
    new_request = GuiTaskRequest(
        keywords=["焊接"],
        max_pages=3,
        save_mode="single",
        include_citation=False,
        include_details=False,
        detail_txt_export=False,
        output_dir=None,
    )

    app._set_total_eta(new_request)

    assert (app._total_eta_low, app._total_eta_high) == (42, 61)
    app._total_eta_var.set.assert_called_with("预计总耗时：00:42～01:01")

    resumed_request = GuiTaskRequest(
        keywords=["焊接", "铸造"],
        max_pages=3,
        save_mode="multi_merge",
        include_citation=False,
        include_details=False,
        detail_txt_export=False,
        output_dir=None,
    )
    resume_state = make_task_state(["焊接", "铸造"], 3, "multi_merge", "TS")
    records = [["标题", "作者", "来源", "日期", "https://example.test/1"]]
    mark_keyword_done(
        resume_state,
        make_keyword_result("焊接", 1, 2, records, STATUS_SUCCESS),
    )
    mark_keyword_progress(resume_state, "铸造", 2, records)
    app._set_total_eta(resumed_request, resume_state)

    assert (app._total_eta_low, app._total_eta_high) == (26, 37)
    app._total_eta_var.set.assert_called_with("预计总耗时：00:26～00:37")


def test_gui_task_review_includes_long_task_risk_warning(monkeypatch):
    app = CNKIBugApp.__new__(CNKIBugApp)
    app.root = object()
    app._start_task = Mock()
    request = GuiTaskRequest(
        keywords=["焊接"],
        max_pages=50,
        save_mode="single",
        include_citation=False,
        include_details=False,
        detail_txt_export=False,
        output_dir=Path("results"),
    )
    app._collect_request = Mock(return_value=request)
    prompts = []
    monkeypatch.setattr(
        "cnkibug.gui.app.messagebox.askokcancel",
        lambda title, message, **kwargs: prompts.append((title, message, kwargs)) or False,
    )

    app._review_task()

    assert "预计耗时上限已超过 10 分钟" in prompts[0][1]
    assert "更容易触发知网反爬验证" in prompts[0][1]
    app._start_task.assert_not_called()


def test_gui_output_directory_is_created_and_write_checked(tmp_path):
    output_dir = tmp_path / "nested" / "results"

    assert _prepare_output_directory(output_dir) == output_dir.resolve()
    assert output_dir.is_dir()
    assert list(output_dir.iterdir()) == []


def test_gui_does_not_start_when_output_directory_is_unavailable(monkeypatch, tmp_path):
    app = CNKIBugApp.__new__(CNKIBugApp)
    app._running = False
    app.root = object()
    app._set_running = Mock()
    errors = []
    request = GuiTaskRequest(
        keywords=["焊接"],
        max_pages=1,
        save_mode="single",
        include_citation=False,
        include_details=False,
        detail_txt_export=False,
        output_dir=tmp_path / "unavailable",
    )
    monkeypatch.setattr(
        "cnkibug.gui.app._prepare_output_directory",
        lambda _path: (_ for _ in ()).throw(OSError("拒绝访问")),
    )
    monkeypatch.setattr(
        "cnkibug.gui.app.messagebox.showerror",
        lambda title, message, **kwargs: errors.append((title, message, kwargs)),
    )

    app._start_task(request=request)

    assert errors[0][0] == "保存位置不可用"
    assert "拒绝访问" in errors[0][1]
    app._set_running.assert_not_called()


def test_gui_memory_status_is_updated_outside_task_status_frame():
    app = CNKIBugApp.__new__(CNKIBugApp)
    app._memory_var = Mock()
    app._memory_sampler = Mock()
    app._memory_sampler.sample.return_value = None

    app._update_memory_status()

    app._memory_var.set.assert_called_once_with("内存：暂不可用")


def _resume_app(paths):
    app = CNKIBugApp.__new__(CNKIBugApp)
    app.root = Mock()
    app.runtime = SimpleNamespace(paths=paths)
    app._append_log = Mock()
    app._populate_resume_form = Mock()
    app._start_task = Mock()
    return app


def test_gui_ignore_checkpoint_deletes_file(monkeypatch, tmp_path):
    paths = get_runtime_paths(tmp_path)
    checkpoint = paths.cache_dir / "last_task.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("{}\n", encoding="utf-8")
    app = _resume_app(paths)
    state = make_task_state(["焊接"], 2, "single", "TS")
    monkeypatch.setattr("cnkibug.gui.app.load_last_task", lambda _paths: state)
    monkeypatch.setattr(
        "cnkibug.gui.app.messagebox.askyesnocancel",
        lambda *args, **kwargs: False,
    )

    app._offer_resume()

    assert not checkpoint.exists()
    app._append_log.assert_called_once_with("已忽略并删除上次任务断点。", "warning")
    app._start_task.assert_not_called()
    app.root.destroy.assert_not_called()


def test_gui_cancel_resume_keeps_checkpoint_and_exits(monkeypatch, tmp_path):
    paths = get_runtime_paths(tmp_path)
    checkpoint = paths.cache_dir / "last_task.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("{}\n", encoding="utf-8")
    app = _resume_app(paths)
    state = make_task_state(["焊接"], 2, "single", "TS")
    monkeypatch.setattr("cnkibug.gui.app.load_last_task", lambda _paths: state)
    monkeypatch.setattr(
        "cnkibug.gui.app.messagebox.askyesnocancel",
        lambda *args, **kwargs: None,
    )

    app._offer_resume()

    assert checkpoint.exists()
    app.root.destroy.assert_called_once_with()
    app._start_task.assert_not_called()


def test_gui_failed_checkpoint_delete_reprompts(monkeypatch, tmp_path):
    paths = get_runtime_paths(tmp_path)
    checkpoint = paths.cache_dir / "last_task.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("{}\n", encoding="utf-8")
    app = _resume_app(paths)
    choices = iter([False, None])
    errors = []
    state = make_task_state(["焊接"], 2, "single", "TS")
    monkeypatch.setattr("cnkibug.gui.app.load_last_task", lambda _paths: state)
    monkeypatch.setattr("cnkibug.gui.app.delete_last_task", lambda _paths: False)
    monkeypatch.setattr(
        "cnkibug.gui.app.messagebox.askyesnocancel",
        lambda *args, **kwargs: next(choices),
    )
    monkeypatch.setattr(
        "cnkibug.gui.app.messagebox.showerror",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )

    app._offer_resume()

    assert checkpoint.exists()
    assert len(errors) == 1
    app.root.destroy.assert_called_once_with()
    app._append_log.assert_not_called()
    app._start_task.assert_not_called()


def test_gui_damaged_checkpoint_delete_failure_exits(monkeypatch, tmp_path):
    paths = get_runtime_paths(tmp_path)
    checkpoint = paths.cache_dir / "last_task.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("broken\n", encoding="utf-8")
    app = _resume_app(paths)
    errors = []
    monkeypatch.setattr("cnkibug.gui.app.load_last_task", lambda _paths: None)
    monkeypatch.setattr("cnkibug.gui.app.delete_last_task", lambda _paths: False)
    monkeypatch.setattr(
        "cnkibug.gui.app.messagebox.showerror",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )

    app._offer_resume()

    assert checkpoint.exists()
    assert len(errors) == 1
    app.root.destroy.assert_called_once_with()
    app._start_task.assert_not_called()


def test_gui_show_form_restores_scroll_view_before_footer():
    app = CNKIBugApp.__new__(CNKIBugApp)
    app._running = False
    app._progress_frame = Mock()
    app._form_view = Mock()
    app._form_canvas = Mock()
    app._footer = object()
    app._settings_button = Mock()

    app._show_form()

    app._progress_frame.pack_forget.assert_called_once_with()
    app._form_view.pack.assert_called_once_with(
        fill="both",
        expand=True,
        before=app._footer,
    )
    app._form_canvas.yview_moveto.assert_called_once_with(0)
    app._settings_button.pack.assert_called_once_with(side="right", padx=(0, 8))


def test_gui_scroll_ignores_tcl_only_popdown():
    app = CNKIBugApp.__new__(CNKIBugApp)
    app.root = Mock()
    app.root.winfo_pointerxy.return_value = (100, 200)
    app.root.winfo_containing.side_effect = KeyError("popdown")
    app._form_view = Mock()
    app._form_view.winfo_ismapped.return_value = True
    app._form_canvas = Mock()

    assert app._scroll_form(SimpleNamespace(delta=-120)) is None

    app._form_canvas.yview_scroll.assert_not_called()


def test_gui_scroll_still_scrolls_main_form_children():
    app = CNKIBugApp.__new__(CNKIBugApp)
    app.root = Mock()
    app.root.winfo_pointerxy.return_value = (100, 200)
    app._form = Mock()
    app._form_view = Mock()
    app._form_view.winfo_ismapped.return_value = True
    app._form_canvas = Mock()
    app._keyword_list = Mock()
    app.root.winfo_containing.return_value = Mock(master=app._form)

    app._scroll_form(SimpleNamespace(delta=-120))
    app._form_canvas.yview_scroll.assert_called_with(3, "units")
    app._scroll_form(SimpleNamespace(delta=120))
    app._form_canvas.yview_scroll.assert_called_with(-3, "units")


@pytest.mark.parametrize("running,form_visible", [(True, True), (False, False)])
def test_gui_settings_cannot_open_in_task_progress_view(monkeypatch, running, form_visible):
    app = CNKIBugApp.__new__(CNKIBugApp)
    app._running = running
    app._form_view = Mock()
    app._form_view.winfo_ismapped.return_value = form_visible
    dialog = Mock()
    monkeypatch.setattr("cnkibug.gui.app.SettingsDialog", dialog)

    app._open_settings()

    dialog.assert_not_called()


def test_gui_applies_config_to_runtime_settings_logging_and_theme(monkeypatch, tmp_path):
    app = CNKIBugApp.__new__(CNKIBugApp)
    paths = get_runtime_paths(tmp_path)
    app.runtime = RuntimeState(paths, DEFAULT_CONFIG.copy(), paths.log_dir / "run.log", [])
    app.root = Mock()
    app.root.style.theme_use.return_value = "litera"
    app.root.style.theme.type = "dark"
    app._form_canvas = Mock()
    app._log = Mock()
    logger = Mock()
    monkeypatch.setattr("cnkibug.gui.app.logging.getLogger", lambda: logger)
    config = {**DEFAULT_CONFIG, "gui_theme": "darkly", "log_level": "WARNING", "timeout_goto_ms": 45000}

    app._apply_config(config)

    assert app.runtime.config == config
    assert app.runtime.config is not config
    assert app.settings.timeout_goto_ms == 45000
    logger.setLevel.assert_called_once_with("WARNING")
    app.root.style.theme_use.assert_called_with("darkly")
    app._form_canvas.configure.assert_called_once_with(background=app.root.style.colors.bg)
    app._log.tag_configure.assert_any_call("error", foreground=app.root.style.colors.danger)


def _settings_dialog(config=None):
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._config = (config or DEFAULT_CONFIG).copy()
    dialog.window = Mock()
    dialog._on_apply = Mock()
    dialog._theme = Mock(get=lambda: "darkly")
    dialog._log_level = Mock(get=lambda: "INFO")
    dialog._numbers = {key: Mock() for key, _label, _divisor in _NUMERIC_FIELDS}
    for key, _label, divisor in _NUMERIC_FIELDS:
        dialog._numbers[key].get.return_value = str(DEFAULT_CONFIG[key] / divisor)
    dialog._flags = {
        key: Mock() for key in ("session_cache_enabled", "log_save_path", "log_keywords", "log_scraped_records")
    }
    for key, variable in dialog._flags.items():
        variable.get.return_value = DEFAULT_CONFIG[key]
    return dialog


def test_gui_settings_converts_seconds_without_changing_task_output_option():
    dialog = _settings_dialog({**DEFAULT_CONFIG, "detail_txt_export": True})
    dialog._numbers["timeout_selector_ms"].get.return_value = "30.001"

    config = dialog._collect()

    assert config["timeout_selector_ms"] == 30001
    assert config["gui_theme"] == "darkly"
    assert config["detail_txt_export"] is True


@pytest.mark.parametrize("value", ["", "abc", "0", "-1", "NaN", "Infinity", "1.0001"])
def test_gui_settings_rejects_invalid_durations(value):
    dialog = _settings_dialog()
    dialog._numbers["timeout_selector_ms"].get.return_value = value

    with pytest.raises(ValueError):
        dialog._collect()


def test_gui_settings_save_failure_keeps_dialog_and_current_settings(monkeypatch, tmp_path):
    dialog = _settings_dialog()
    dialog._config_path = tmp_path / "config.json"
    monkeypatch.setattr("cnkibug.gui.settings.save_config", Mock(side_effect=OSError("write failed")))
    show_error = Mock()
    monkeypatch.setattr("cnkibug.gui.settings.messagebox.showerror", show_error)

    dialog._save()

    dialog._on_apply.assert_not_called()
    dialog.window.destroy.assert_not_called()
    show_error.assert_called_once()


def test_gui_settings_reload_applies_file_without_writing_it(tmp_path):
    import json

    dialog = _settings_dialog()
    dialog._config_path = tmp_path / "config.json"
    config = {**DEFAULT_CONFIG, "gui_theme": "darkly", "verify_wait_timeout_sec": 240}
    dialog._config_path.write_text(json.dumps(config), encoding="utf-8")
    before = dialog._config_path.read_bytes()
    dialog._populate = Mock()

    dialog._reload()

    dialog._on_apply.assert_called_once_with(config)
    dialog._populate.assert_called_once_with(config)
    assert dialog._config_path.read_bytes() == before
    dialog.window.destroy.assert_not_called()
