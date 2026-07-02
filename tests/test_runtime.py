import json
from datetime import datetime

from cnkibug import runtime


def test_init_runtime_creates_dirs_and_default_config(tmp_path):
    state = runtime.init_runtime(base_dir=tmp_path, configure_logging=False)

    assert state.paths.data_dir == tmp_path / "CNKIBug"
    assert state.paths.cache_dir.is_dir()
    assert state.paths.log_dir.is_dir()
    assert state.paths.config_path.is_file()
    assert json.loads(state.paths.config_path.read_text(encoding="utf-8")) == runtime.DEFAULT_CONFIG


def test_init_runtime_falls_back_to_user_data_dir(monkeypatch, tmp_path):
    real_load_or_create_config = runtime.load_or_create_config
    fallback_base = tmp_path / "user-data"
    calls = []

    def fake_load_or_create_config(paths):
        calls.append(paths)
        if len(calls) == 1:
            raise PermissionError("program dir is not writable")
        return real_load_or_create_config(paths)

    monkeypatch.setattr(runtime, "load_or_create_config", fake_load_or_create_config)
    monkeypatch.setattr(runtime, "get_user_data_base_dir", lambda: fallback_base)

    state = runtime.init_runtime(configure_logging=False)

    assert state.paths.base_dir == fallback_base
    assert state.paths.data_dir == fallback_base / "CNKIBug"
    assert state.paths.config_path.is_file()
    assert any("已改用用户数据目录" in message for level, message in state.events if level == "WARNING")


def test_init_runtime_exposes_config_repair_events(tmp_path):
    paths = runtime.get_runtime_paths(tmp_path)
    paths.data_dir.mkdir()
    paths.config_path.write_text("{ broken", encoding="utf-8")

    state = runtime.init_runtime(base_dir=tmp_path, configure_logging=False)

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


def test_build_log_path_uses_log_dir_and_current_day(tmp_path):
    paths = runtime.get_runtime_paths(tmp_path)
    log_path = runtime.build_log_path(paths, datetime(2026, 6, 30, 12, 0, 0))

    assert log_path == tmp_path / "CNKIBug" / "log" / "cnkibug_20260630.log"
