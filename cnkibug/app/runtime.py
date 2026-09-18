from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.runtime import RuntimePaths
from ..core.settings import UPDATE_SOURCES


APP_DATA_DIR_NAME = "CNKIBug-data"
LEGACY_APP_DATA_DIR_NAME = "CNKIBug"
LEGACY_RUNTIME_ENTRIES = ("config.json", "cache", "log", "status")
CONFIG_VERSION = 2

DEFAULT_CONFIG: dict[str, Any] = {
    "version": CONFIG_VERSION,
    "timeout_goto_ms": 30000,
    "timeout_load_ms": 20000,
    "timeout_selector_ms": 15000,
    "verify_wait_timeout_sec": 180,
    "verify_notice_interval_sec": 15,
    "max_advance_fail": 2,
    "session_cache_enabled": True,
    "session_cache_ttl_hours": 12,
    "download_auth_wait_sec": 60,
    "log_level": "INFO",
    "log_save_path": True,
    "log_keywords": False,
    "log_scraped_records": False,
    "detail_txt_export": False,
    "gui_theme": "litera",
    "update_source": "auto",
    "linux_setup_completed": False,
    "output_dir": "",
}


@dataclass(frozen=True)
class RuntimeState:
    paths: RuntimePaths
    config: dict[str, Any]
    log_path: Path
    events: list[tuple[str, str]]


@dataclass(frozen=True)
class RuntimeCleanupResult:
    deleted: int
    failed: int
    preserved: int
    freed_bytes: int


def get_runtime_paths(program_dir: str | Path) -> RuntimePaths:
    resolved_program_dir = Path(program_dir).resolve()
    data_dir = resolved_program_dir / APP_DATA_DIR_NAME
    return RuntimePaths(
        program_dir=resolved_program_dir,
        data_dir=data_dir,
        config_path=data_dir / "config.json",
        cache_dir=data_dir / "cache",
        log_dir=data_dir / "log",
        status_dir=data_dir / "status",
    )


def build_log_path(paths: RuntimePaths, now: datetime | None = None) -> Path:
    current = now or datetime.now()
    return paths.log_dir / f"cnkibug_{current:%Y%m%d}.log"


def init_runtime(
    program_dir: str | Path,
    app_version: str | None = None,
    configure_logging: bool = True,
) -> RuntimeState:
    paths = get_runtime_paths(program_dir)
    migration_events = _migrate_legacy_runtime_data(paths)
    config, config_events = load_or_create_config(paths)
    events = [*migration_events, *config_events]
    log_path = build_log_path(paths)

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

    return RuntimeState(paths=paths, config=config.copy(), log_path=log_path, events=list(events))


def _migrate_legacy_runtime_data(paths: RuntimePaths) -> list[tuple[str, str]]:
    legacy_dir = paths.program_dir / LEGACY_APP_DATA_DIR_NAME
    if not legacy_dir.is_dir():
        return []

    events: list[tuple[str, str]] = []
    for name in LEGACY_RUNTIME_ENTRIES:
        source = legacy_dir / name
        if not source.exists():
            continue

        destination = paths.data_dir / name
        if destination.exists():
            events.append((
                "WARNING",
                f"旧运行数据未迁移，目标已存在: {source} -> {destination}",
            ))
            continue

        try:
            paths.data_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        except OSError as error:
            events.append((
                "WARNING",
                f"旧运行数据迁移失败: {source} -> {destination} ({error})",
            ))
        else:
            events.append(("INFO", f"已迁移旧运行数据: {source} -> {destination}"))
    return events


def cleanup_runtime_history(
    state: RuntimeState,
    now: datetime | None = None,
) -> RuntimeCleanupResult:
    current = now or datetime.now()
    day = current.strftime("%Y%m%d")
    active_log = state.log_path.resolve()
    candidates = [
        *state.paths.log_dir.glob("cnkibug_*.log"),
        *state.paths.status_dir.glob("cnki_task_report_*.json"),
    ]
    deleted = 0
    failed = 0
    preserved = 0
    freed_bytes = 0

    for path in candidates:
        is_today = (
            path.name == f"cnkibug_{day}.log"
            or path.name.startswith(f"cnki_task_report_{day}_")
        )
        if path.resolve() == active_log or is_today:
            preserved += 1
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as error:
            failed += 1
            logging.getLogger("cnkibug.runtime").warning(
                "历史运行文件删除失败: path=%s error=%s",
                path,
                error,
            )
        else:
            deleted += 1
            freed_bytes += size

    logging.getLogger("cnkibug.runtime").info(
        "历史运行文件清理完成: deleted=%d failed=%d preserved=%d freed_bytes=%d",
        deleted,
        failed,
        preserved,
        freed_bytes,
    )
    return RuntimeCleanupResult(deleted, failed, preserved, freed_bytes)


def load_or_create_config(paths: RuntimePaths) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    paths.status_dir.mkdir(parents=True, exist_ok=True)

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


def validate_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("配置文件根结构必须是 JSON 对象。")
    for key, default in DEFAULT_CONFIG.items():
        if key in raw and type(raw[key]) is not type(default):
            raise ValueError(f"配置项类型无效：{key}")
    config, _, _ = _normalize_config(raw)
    invalid = [
        key for key in raw.keys() & DEFAULT_CONFIG.keys()
        if raw[key] != config[key] and not (key == "version" and raw[key] == 1)
    ]
    if invalid:
        raise ValueError(f"配置项取值无效：{', '.join(sorted(invalid))}")
    return config


def read_config(path: Path) -> dict[str, Any]:
    return validate_config(json.loads(path.read_text(encoding="utf-8")))


def save_config(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    validated = validate_config(config)
    _write_config(path, validated)
    return validated


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
        newline="\n",
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

    raw_version = raw.get("version")
    for key, default in DEFAULT_CONFIG.items():
        if key not in raw:
            changed = True
            level = "INFO" if key in ("gui_theme", "update_source", "linux_setup_completed", "output_dir") or (raw_version == 1 and key == "detail_txt_export") else "WARNING"
            events.append((level, f"配置项缺失，已使用默认值: {key}={default!r}"))
            continue
        config[key] = raw[key]

    if config.get("version") == 1:
        config["version"] = CONFIG_VERSION
        changed = True
        events.append(("INFO", f"配置文件已升级到版本 {CONFIG_VERSION}"))

    int_keys = (
        "version",
        "timeout_goto_ms",
        "timeout_load_ms",
        "timeout_selector_ms",
        "verify_wait_timeout_sec",
        "verify_notice_interval_sec",
        "max_advance_fail",
        "session_cache_ttl_hours",
        "download_auth_wait_sec",
    )
    for key in int_keys:
        minimum = 0 if key == "download_auth_wait_sec" else 1
        if not isinstance(config.get(key), int) or isinstance(config.get(key), bool) or config[key] < minimum:
            events.append(("WARNING", f"配置项无效，已恢复默认值: {key}={DEFAULT_CONFIG[key]!r}"))
            config[key] = DEFAULT_CONFIG[key]
            changed = True

    if config.get("log_level") not in {"INFO", "WARNING", "ERROR"}:
        events.append(("WARNING", "配置项无效，已恢复默认值: log_level='INFO'"))
        config["log_level"] = DEFAULT_CONFIG["log_level"]
        changed = True

    if config.get("gui_theme") not in ("litera", "darkly"):
        events.append(("WARNING", "配置项无效，已恢复默认值: gui_theme='litera'"))
        config["gui_theme"] = DEFAULT_CONFIG["gui_theme"]
        changed = True

    if config.get("update_source") not in UPDATE_SOURCES:
        events.append(("WARNING", "配置项无效，已恢复默认值: update_source='auto'"))
        config["update_source"] = DEFAULT_CONFIG["update_source"]
        changed = True

    bool_keys = (
        "linux_setup_completed",
        "session_cache_enabled",
        "log_save_path",
        "log_keywords",
        "log_scraped_records",
        "detail_txt_export",
    )
    for key in bool_keys:
        if not isinstance(config.get(key), bool):
            events.append(("WARNING", f"配置项无效，已恢复默认值: {key}={DEFAULT_CONFIG[key]!r}"))
            config[key] = DEFAULT_CONFIG[key]
            changed = True

    if not isinstance(config.get("output_dir"), str) or "\x00" in config["output_dir"]:
        config["output_dir"] = DEFAULT_CONFIG["output_dir"]
        changed = True
        events.append(("WARNING", "保存目录配置无效，已恢复默认值"))

    unknown_keys = sorted(set(raw) - set(DEFAULT_CONFIG))
    if unknown_keys:
        changed = True
        events.append(("WARNING", f"配置文件存在未使用项，已移除: {unknown_keys}"))

    return config, changed, events
