import builtins
from queue import Queue
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import run_gui

from cnkibug.app.runtime import DEFAULT_CONFIG, RuntimeState, get_runtime_paths
from cnkibug.cnki.models import Paper
from cnkibug.gui.app import (
    CNKIBugApp,
    GuiTaskRequest,
    _merge_task_keywords,
    _prepare_output_directory,
    _resolve_save_mode,
)
from cnkibug.gui.events import GuiEvent, GuiEventSink
from cnkibug.gui.settings import SettingsDialog, _NUMERIC_FIELDS
from cnkibug.workflow.state import make_task_state


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


def test_finished_task_updates_existing_results_even_when_display_is_declined(monkeypatch):
    app = CNKIBugApp.__new__(CNKIBugApp)
    app.root = Mock()
    app._actual_seconds = 1.0
    app._set_running = Mock()
    app._new_task_button = Mock()
    app._close_when_done = False
    app._result_prompt_pending = True
    app._progress_mode = "completed"
    app._current_results = [Paper(title="Current task")]
    viewer = app._results_window = Mock(busy=False)

    def decline(*args, **kwargs):
        viewer.set_papers.assert_called_once_with(app._current_results)
        return False

    monkeypatch.setattr("cnkibug.gui.app.messagebox.askyesno", decline)
    app._handle_event(GuiEvent("worker_done", {}))

    viewer.set_papers.assert_called_once_with(app._current_results)
    viewer.window.deiconify.assert_not_called()
    app._show_results()
    viewer.window.deiconify.assert_called_once()
    viewer.set_papers.assert_called_once()


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
    dialog._closing = False
    dialog.environment = Mock(busy=False)
    dialog.window = Mock()
    dialog._on_apply = Mock()
    dialog._theme = Mock(get=lambda: "darkly")
    dialog._log_level = Mock(get=lambda: "INFO")
    dialog._update_source = Mock(get=lambda: "自动")
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


def test_update_uses_saved_route_when_unsaved_settings_are_discarded(monkeypatch):
    dialog = _settings_dialog({**DEFAULT_CONFIG, "update_source": "direct"})
    dialog._update_source.get.return_value = "ghfast.top"
    dialog._populate = Mock()
    monkeypatch.setattr("cnkibug.gui.settings.messagebox.askyesnocancel", lambda *args, **kwargs: False)

    assert dialog._prepare_update(dialog.window) == "direct"
    dialog._on_apply.assert_not_called()
    dialog._populate.assert_called_once_with(dialog._config)
