from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from ..core.runtime import RuntimePaths


COOKIE_STATE_FILENAME = "cookies"

_logger = logging.getLogger("cnkibug.session_cache")


def get_cookie_state_path(paths: RuntimePaths) -> Path:
    return paths.cache_dir / COOKIE_STATE_FILENAME


def prepare_cookie_state(
    enabled: bool,
    ttl_hours: int,
    paths: RuntimePaths,
    now: float | None = None,
) -> Path | None:
    if not enabled:
        _logger.info("cookies 会话缓存未启用")
        return None

    path = get_cookie_state_path(paths)

    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _logger.info("未发现 cookies 会话缓存，将使用新会话: path=%s", path)
        return None

    _secure_cookie_permissions(path)

    current = time.time() if now is None else now
    age_seconds = current - path.stat().st_mtime
    ttl_seconds = ttl_hours * 3600
    if age_seconds > ttl_seconds:
        _delete_cookie_state(path, "已过期")
        return None

    if not _looks_like_storage_state(path):
        _delete_cookie_state(path, "格式无效")
        return None

    _logger.info(
        "已加载 cookies 会话缓存: path=%s age_hours=%.2f ttl_hours=%d",
        path,
        age_seconds / 3600,
        ttl_hours,
    )
    return path


def discard_cookie_state(path: Path, reason: str) -> None:
    _delete_cookie_state(path, reason)


def save_cookie_state(
    context: Any,
    enabled: bool,
    paths: RuntimePaths,
) -> Path | None:
    if not enabled:
        return None

    path = get_cookie_state_path(paths)

    path.parent.mkdir(parents=True, exist_ok=True)
    _secure_cookie_permissions(path)
    try:
        context.storage_state(path=str(path))
    except Exception as exc:  # noqa: BLE001
        _logger.warning("cookies 会话缓存保存失败: path=%s error=%s", path, exc)
        return None

    _secure_cookie_permissions(path)
    _logger.info("cookies 会话缓存已保存: path=%s", path)
    return path


def _secure_cookie_permissions(path: Path) -> None:
    if os.name != "posix":
        return
    try:
        directory_mode = path.parent.stat().st_mode & 0o777
        file_mode = path.stat().st_mode & 0o777 if path.exists() else None
        path.parent.chmod(0o700)
        if path.exists():
            path.chmod(0o600)
        if directory_mode != 0o700 or file_mode not in {None, 0o600}:
            _logger.warning(
                "cookies 会话缓存权限已收紧: directory_mode=%03o file_mode=%s",
                directory_mode,
                "missing" if file_mode is None else f"{file_mode:03o}",
            )
    except OSError as exc:
        _logger.warning("cookies 会话缓存权限调整失败: path=%s error=%s", path, exc)


def _looks_like_storage_state(path: Path) -> bool:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("cookies 会话缓存读取失败: path=%s error=%s", path, exc)
        return False
    return isinstance(raw, dict) and "cookies" in raw and "origins" in raw


def _delete_cookie_state(path: Path, reason: str) -> None:
    try:
        path.unlink(missing_ok=True)
        _logger.info("cookies 会话缓存已删除: path=%s reason=%s", path, reason)
    except OSError as exc:
        _logger.warning("cookies 会话缓存删除失败: path=%s reason=%s error=%s", path, reason, exc)
