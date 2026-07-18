import json
from datetime import datetime

import pytest

from cnkibug.app import runtime
from cnkibug.fileio import paths as file_paths


def test_init_runtime_creates_dirs_and_default_config(tmp_path):
    state = runtime.init_runtime(program_dir=tmp_path, configure_logging=False)

    assert state.paths.data_dir == tmp_path / "CNKIBug"
    assert state.paths.cache_dir.is_dir()
    assert state.paths.log_dir.is_dir()
    assert state.paths.status_dir.is_dir()
    assert state.paths.config_path.is_file()
    assert json.loads(state.paths.config_path.read_text(encoding="utf-8")) == runtime.DEFAULT_CONFIG


def test_init_runtime_does_not_fallback_when_program_dir_is_unwritable(monkeypatch, tmp_path):
    captured_paths = []

    def fail_load(paths):
        captured_paths.append(paths)
        raise PermissionError("program dir is not writable")

    monkeypatch.setattr(runtime, "load_or_create_config", fail_load)

    with pytest.raises(PermissionError, match="program dir is not writable"):
        runtime.init_runtime(program_dir=tmp_path, configure_logging=False)

    assert len(captured_paths) == 1
    assert captured_paths[0].data_dir == tmp_path / "CNKIBug"


def test_init_runtime_exposes_config_repair_events(tmp_path):
    paths = runtime.get_runtime_paths(tmp_path)
    paths.data_dir.mkdir()
    paths.config_path.write_text("{ broken", encoding="utf-8")

    state = runtime.init_runtime(program_dir=tmp_path, configure_logging=False)

    assert any(level == "WARNING" for level, _ in state.events)


def test_load_or_create_config_repairs_missing_and_invalid_values(tmp_path):
    paths = runtime.get_runtime_paths(tmp_path)
    paths.data_dir.mkdir()
    paths.config_path.write_text(
        json.dumps({
            "version": 1,
            "timeout_goto_ms": -1,
            "timeout_load_ms": 20000,
            "timeout_selector_ms": 15000,
            "verify_wait_timeout_sec": 180,
            "verify_notice_interval_sec": 15,
            "max_advance_fail": True,
            "log_level": "DEBUG",
            "log_save_path": "yes",
            "unused": "kept in user file only until repair",
        }),
        encoding="utf-8",
    )

    config, events = runtime.load_or_create_config(paths)

    assert config["timeout_goto_ms"] == runtime.DEFAULT_CONFIG["timeout_goto_ms"]
    assert config["max_advance_fail"] == runtime.DEFAULT_CONFIG["max_advance_fail"]
    assert config["log_level"] == "INFO"
    assert config["session_cache_enabled"] is True
    assert config["session_cache_ttl_hours"] == 12
    assert config["log_save_path"] is True
    assert config["log_keywords"] is False
    assert config["log_scraped_records"] is False
    assert config["detail_txt_export"] is False
    assert config["version"] == runtime.CONFIG_VERSION
    assert any(level == "WARNING" for level, _ in events)

    written = json.loads(paths.config_path.read_text(encoding="utf-8"))
    assert "unused" not in written
    assert written == config


def test_load_or_create_config_migrates_version_one_without_warning(tmp_path):
    paths = runtime.get_runtime_paths(tmp_path)
    paths.data_dir.mkdir()
    old_config = runtime.DEFAULT_CONFIG.copy()
    old_config["version"] = 1
    old_config.pop("detail_txt_export")
    paths.config_path.write_text(json.dumps(old_config), encoding="utf-8")

    config, events = runtime.load_or_create_config(paths)

    assert config["version"] == runtime.CONFIG_VERSION
    assert config["detail_txt_export"] is False
    assert not any(level == "WARNING" for level, _ in events)
    assert any("已升级到版本 2" in message for _, message in events)


def test_load_or_create_config_backs_up_broken_json(tmp_path):
    paths = runtime.get_runtime_paths(tmp_path)
    paths.data_dir.mkdir()
    paths.config_path.write_text("{ broken", encoding="utf-8")

    config, events = runtime.load_or_create_config(paths)

    backups = list(paths.data_dir.glob("config.broken_*.json"))
    assert config == runtime.DEFAULT_CONFIG
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{ broken"
    assert json.loads(paths.config_path.read_text(encoding="utf-8")) == runtime.DEFAULT_CONFIG
    assert any(level == "WARNING" for level, _ in events)


def test_build_log_path_uses_log_dir_and_current_day(tmp_path):
    paths = runtime.get_runtime_paths(tmp_path)
    log_path = runtime.build_log_path(paths, datetime(2026, 6, 30, 12, 0, 0))

    assert log_path == tmp_path / "CNKIBug" / "log" / "cnkibug_20260630.log"


def test_cleanup_runtime_history_deletes_only_known_historical_files(tmp_path):
    state = runtime.init_runtime(program_dir=tmp_path, configure_logging=False)
    active_log = state.paths.log_dir / "cnkibug_20260716.log"
    state = runtime.RuntimeState(state.paths, state.config, active_log, state.events)
    files = {
        "active_log": active_log,
        "old_log": state.paths.log_dir / "cnkibug_20260715.log",
        "today_log": state.paths.log_dir / "cnkibug_20260717.log",
        "old_report": state.paths.status_dir / "cnki_task_report_20260716_120000.json",
        "today_report": state.paths.status_dir / "cnki_task_report_20260717_120000.json",
        "unrelated": state.paths.status_dir / "notes.json",
    }
    for path in files.values():
        path.write_text("data", encoding="utf-8")

    result = runtime.cleanup_runtime_history(
        state,
        now=datetime(2026, 7, 17, 12, 0, 0),
    )

    assert result.deleted == 2
    assert result.failed == 0
    assert result.preserved == 3
    assert result.freed_bytes == 8
    assert not files["old_log"].exists()
    assert not files["old_report"].exists()
    assert files["active_log"].exists()
    assert files["today_log"].exists()
    assert files["today_report"].exists()
    assert files["unrelated"].exists()


def test_open_directory_uses_platform_file_manager(monkeypatch, tmp_path):
    launched = []
    monkeypatch.setattr(file_paths.sys, "platform", "linux")
    monkeypatch.setattr(file_paths.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        file_paths.subprocess,
        "Popen",
        lambda args, **kwargs: launched.append((args, kwargs)),
    )

    file_paths.open_directory(tmp_path)

    assert launched[0][0] == ["/usr/bin/xdg-open", str(tmp_path)]
    assert launched[0][1] == {
        "stdout": file_paths.subprocess.DEVNULL,
        "stderr": file_paths.subprocess.DEVNULL,
    }


def test_open_directory_uses_startfile_on_windows(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(file_paths.sys, "platform", "win32")
    monkeypatch.setattr(file_paths.os, "startfile", opened.append, raising=False)

    file_paths.open_directory(tmp_path)

    assert opened == [str(tmp_path)]


def test_open_directory_rejects_missing_path(tmp_path):
    with pytest.raises(FileNotFoundError, match="目录不存在"):
        file_paths.open_directory(tmp_path / "missing")
