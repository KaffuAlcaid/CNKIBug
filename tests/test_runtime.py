import json
from datetime import datetime

import pytest

from cnkibug.app import runtime


def test_init_runtime_creates_dirs_and_default_config(tmp_path):
    state = runtime.init_runtime(program_dir=tmp_path, configure_logging=False)

    assert state.paths.data_dir == tmp_path / "CNKIBug-data"
    assert state.paths.cache_dir.is_dir()
    assert state.paths.log_dir.is_dir()
    assert state.paths.status_dir.is_dir()
    assert state.paths.config_path.is_file()
    assert json.loads(state.paths.config_path.read_text(encoding="utf-8")) == runtime.DEFAULT_CONFIG


def test_init_runtime_migrates_known_legacy_data_without_moving_other_files(tmp_path):
    legacy_dir = tmp_path / runtime.LEGACY_APP_DATA_DIR_NAME
    legacy_config = runtime.DEFAULT_CONFIG.copy()
    legacy_config["log_level"] = "WARNING"
    files = {
        "config.json": json.dumps(legacy_config),
        "cache/cookies": "{}",
        "log/old.log": "old log",
        "status/old.json": "{}",
    }
    for relative_path, content in files.items():
        path = legacy_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    source_marker = legacy_dir / "__init__.py"
    source_marker.write_text("source package marker", encoding="utf-8")

    state = runtime.init_runtime(program_dir=tmp_path, configure_logging=False)

    assert state.config["log_level"] == "WARNING"
    for relative_path in files:
        assert not (legacy_dir / relative_path).exists()
        assert (state.paths.data_dir / relative_path).exists()
    assert source_marker.read_text(encoding="utf-8") == "source package marker"
    assert any("已迁移旧运行数据" in message for _, message in state.events)


def test_init_runtime_does_not_overwrite_existing_data_during_migration(tmp_path):
    legacy_dir = tmp_path / runtime.LEGACY_APP_DATA_DIR_NAME
    legacy_dir.mkdir()
    legacy_config = runtime.DEFAULT_CONFIG.copy()
    legacy_config["log_level"] = "WARNING"
    (legacy_dir / "config.json").write_text(json.dumps(legacy_config), encoding="utf-8")

    paths = runtime.get_runtime_paths(tmp_path)
    paths.data_dir.mkdir()
    paths.config_path.write_text(json.dumps(runtime.DEFAULT_CONFIG), encoding="utf-8")

    state = runtime.init_runtime(program_dir=tmp_path, configure_logging=False)

    assert state.config["log_level"] == "INFO"
    assert (legacy_dir / "config.json").exists()
    assert any("目标已存在" in message for _, message in state.events)


def test_init_runtime_does_not_fallback_when_program_dir_is_unwritable(monkeypatch, tmp_path):
    captured_paths = []

    def fail_load(paths):
        captured_paths.append(paths)
        raise PermissionError("program dir is not writable")

    monkeypatch.setattr(runtime, "load_or_create_config", fail_load)

    with pytest.raises(PermissionError, match="program dir is not writable"):
        runtime.init_runtime(program_dir=tmp_path, configure_logging=False)

    assert len(captured_paths) == 1
    assert captured_paths[0].data_dir == tmp_path / "CNKIBug-data"


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


def test_save_config_persists_theme_and_scraper_values(tmp_path):
    path = tmp_path / "config.json"
    config = {**runtime.DEFAULT_CONFIG, "gui_theme": "darkly", "timeout_selector_ms": 30500}

    saved = runtime.save_config(path, config)

    assert saved == config
    assert runtime.read_config(path) == config
    assert b"\r\n" not in path.read_bytes()


@pytest.mark.parametrize("key,value", [
    ("timeout_goto_ms", 0),
    ("timeout_load_ms", True),
    ("verify_wait_timeout_sec", 1.5),
    ("session_cache_enabled", "false"),
    ("gui_theme", "unknown"),
    ("log_level", ["INFO"]),
])
def test_save_config_rejects_invalid_values_before_writing(tmp_path, key, value):
    path = tmp_path / "config.json"
    runtime.save_config(path, runtime.DEFAULT_CONFIG)
    before = path.read_bytes()

    with pytest.raises(ValueError, match=key):
        runtime.save_config(path, {**runtime.DEFAULT_CONFIG, key: value})

    assert path.read_bytes() == before


def test_read_config_reports_invalid_json_without_repairing_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ broken", encoding="utf-8")

    with pytest.raises(ValueError):
        runtime.read_config(path)

    assert path.read_text(encoding="utf-8") == "{ broken"
    assert list(tmp_path.iterdir()) == [path]


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
