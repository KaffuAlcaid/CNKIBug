from pathlib import Path
from queue import Queue
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock

import run_gui

from cnkibug.core.version import APP_VERSION
from cnkibug.gui.app import (
    CNKIBugApp,
    _fit_window_geometry,
    _merge_task_keywords,
    _resolve_save_mode,
)
from cnkibug.gui.events import GuiEvent, GuiEventSink


def test_gui_self_check_reports_app_version(capsys):
    assert run_gui._run_self_check() == 0
    assert capsys.readouterr().out.strip() == f"CNKIBug GUI self-check OK: {APP_VERSION}"


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
