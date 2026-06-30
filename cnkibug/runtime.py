from __future__ import annotations

import json
import logging
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APP_DATA_DIR_NAME = "CNKIBug"

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "timeout_goto_ms": 30000,
    "timeout_load_ms": 20000,
    "timeout_selector_ms": 15000,
    "verify_wait_timeout_sec": 180,
    "verify_notice_interval_sec": 15,
    "max_advance_fail": 2,
    "session_cache_enabled": True,
    "session_cache_ttl_hours": 12,
    "log_level": "INFO",
    "log_save_path": True,
    "log_keywords": False,
    "log_scraped_records": False,
}


@dataclass(frozen=True)
class RuntimePaths:
    base_dir: Path
    data_dir: Path
    config_path: Path
    cache_dir: Path
    log_dir: Path


@dataclass(frozen=True)
class RuntimeState:
    paths: RuntimePaths
    config: dict[str, Any]
    log_path: Path


_CONFIG: dict[str, Any] = DEFAULT_CONFIG.copy()
_PATHS: RuntimePaths | None = None


def get_program_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def get_runtime_paths(base_dir: str | Path | None = None) -> RuntimePaths:
    resolved_base = Path(base_dir).resolve() if base_dir is not None else get_program_dir()
    data_dir = resolved_base / APP_DATA_DIR_NAME
    return RuntimePaths(
        base_dir=resolved_base,
        data_dir=data_dir,
        config_path=data_dir / "config.json",
        cache_dir=data_dir / "cache",
        log_dir=data_dir / "log",
    )


def get_config() -> dict[str, Any]:
    return _CONFIG.copy()


def get_paths() -> RuntimePaths | None:
    return _PATHS


def build_log_path(paths: RuntimePaths, now: datetime | None = None) -> Path:
    current = now or datetime.now()
    return paths.log_dir / f"cnkibug_{current:%Y%m%d}.log"


def init_runtime(
    base_dir: str | Path | None = None,
    app_version: str | None = None,
    configure_logging: bool = True,
) -> RuntimeState:
    paths = get_runtime_paths(base_dir)
    config, events = load_or_create_config(paths)
    log_path = build_log_path(paths)

    global _CONFIG, _PATHS
    _CONFIG = config.copy()
    _PATHS = paths

    if configure_logging:
        setup_file_logging(log_path, config)
        logger = logging.getLogger("cnkibug.runtime")
        for level, message in events:
            if level == "WARNING":
                logger.warning(message)
            elif level == "ERROR":
                logger.error(message)
            else:
                logger.info(message)
        version_part = f" version={app_version}" if app_version else ""
        logger.info("程序启动%s", version_part)
        logger.info("运行数据目录: %s", paths.data_dir)

    return RuntimeState(paths=paths, config=config.copy(), log_path=log_path)


def load_or_create_config(paths: RuntimePaths) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    paths.log_dir.mkdir(parents=True, exist_ok=True)

    events: list[tuple[str, str]] = []
    if not paths.config_path.exists():
        config = DEFAULT_CONFIG.copy()
        _write_config(paths.config_path, config)
        events.append(("INFO", f"已创建默认配置文件: {paths.config_path}"))
        return config, events

    try:
        raw = json.loads(paths.config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        backup_path = _backup_broken_config(paths.config_path)
        config = DEFAULT_CONFIG.copy()
        _write_config(paths.config_path, config)
        events.append(("WARNING", f"配置文件 JSON 格式错误，已备份到: {backup_path} ({exc})"))
        events.append(("INFO", f"已重新创建默认配置文件: {paths.config_path}"))
        return config, events

    if not isinstance(raw, dict):
        backup_path = _backup_broken_config(paths.config_path)
        config = DEFAULT_CONFIG.copy()
        _write_config(paths.config_path, config)
        events.append(("WARNING", f"配置文件根结构不是对象，已备份到: {backup_path}"))
        events.append(("INFO", f"已重新创建默认配置文件: {paths.config_path}"))
        return config, events

    config, changed, repair_events = _normalize_config(raw)
    events.extend(repair_events)
    if changed:
        _write_config(paths.config_path, config)
        events.append(("INFO", f"已修复配置文件: {paths.config_path}"))
    else:
        events.append(("INFO", f"已加载配置文件: {paths.config_path}"))
    return config, events


def setup_file_logging(log_path: Path, config: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, str(config.get("log_level", "INFO")), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )


def _write_config(path: Path, config: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _backup_broken_config(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_suffix(f".broken_{stamp}.json")
    counter = 1
    while backup_path.exists():
        backup_path = path.with_suffix(f".broken_{stamp}_{counter}.json")
        counter += 1
    shutil.copy2(path, backup_path)
    return backup_path


def _normalize_config(raw: dict[str, Any]) -> tuple[dict[str, Any], bool, list[tuple[str, str]]]:
    config = DEFAULT_CONFIG.copy()
    events: list[tuple[str, str]] = []
    changed = False

    for key, default in DEFAULT_CONFIG.items():
        if key not in raw:
            changed = True
            events.append(("WARNING", f"配置项缺失，已使用默认值: {key}={default!r}"))
            continue
        config[key] = raw[key]

    int_keys = (
        "version",
        "timeout_goto_ms",
        "timeout_load_ms",
        "timeout_selector_ms",
        "verify_wait_timeout_sec",
        "verify_notice_interval_sec",
        "max_advance_fail",
        "session_cache_ttl_hours",
    )
    for key in int_keys:
        if not isinstance(config.get(key), int) or isinstance(config.get(key), bool) or config[key] <= 0:
            events.append(("WARNING", f"配置项无效，已恢复默认值: {key}={DEFAULT_CONFIG[key]!r}"))
            config[key] = DEFAULT_CONFIG[key]
            changed = True

    if config.get("log_level") not in {"INFO", "WARNING", "ERROR"}:
        events.append(("WARNING", "配置项无效，已恢复默认值: log_level='INFO'"))
        config["log_level"] = DEFAULT_CONFIG["log_level"]
        changed = True

    bool_keys = (
        "session_cache_enabled",
        "log_save_path",
        "log_keywords",
        "log_scraped_records",
    )
    for key in bool_keys:
        if not isinstance(config.get(key), bool):
            events.append(("WARNING", f"配置项无效，已恢复默认值: {key}={DEFAULT_CONFIG[key]!r}"))
            config[key] = DEFAULT_CONFIG[key]
            changed = True

    unknown_keys = sorted(set(raw) - set(DEFAULT_CONFIG))
    if unknown_keys:
        changed = True
        events.append(("WARNING", f"配置文件存在未使用项，已移除: {unknown_keys}"))

    return config, changed, events
