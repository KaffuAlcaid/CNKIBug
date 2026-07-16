import json
import os
from pathlib import Path

import pytest

from cnkibug.app import runtime
from cnkibug.browser import cache as session_cache


def test_prepare_cookie_state_uses_fresh_cache(tmp_path):
    paths = runtime.init_runtime(program_dir=tmp_path, configure_logging=False).paths
    path = tmp_path / "CNKIBug" / "cache" / "cookies"
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    os.utime(path, (1000, 1000))

    assert session_cache.prepare_cookie_state(True, ttl_hours=12, paths=paths, now=1000) == path
    assert path.exists()


def test_prepare_cookie_state_deletes_expired_cache(tmp_path):
    paths = runtime.init_runtime(program_dir=tmp_path, configure_logging=False).paths
    path = tmp_path / "CNKIBug" / "cache" / "cookies"
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    os.utime(path, (1000, 1000))

    assert session_cache.prepare_cookie_state(
        True,
        ttl_hours=12,
        paths=paths,
        now=1000 + 13 * 3600,
    ) is None
    assert not path.exists()


def test_prepare_cookie_state_deletes_invalid_cache(tmp_path):
    paths = runtime.init_runtime(program_dir=tmp_path, configure_logging=False).paths
    path = tmp_path / "CNKIBug" / "cache" / "cookies"
    path.write_text("not-json", encoding="utf-8")

    assert session_cache.prepare_cookie_state(True, ttl_hours=12, paths=paths) is None
    assert not path.exists()


def test_prepare_cookie_state_disabled(tmp_path):
    paths = runtime.init_runtime(program_dir=tmp_path, configure_logging=False).paths

    assert session_cache.prepare_cookie_state(False, ttl_hours=12, paths=paths) is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission behavior")
def test_prepare_cookie_state_tightens_permissions(tmp_path, caplog):
    paths = runtime.init_runtime(program_dir=tmp_path, configure_logging=False).paths
    path = tmp_path / "CNKIBug" / "cache" / "cookies"
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    path.parent.chmod(0o755)
    path.chmod(0o644)

    assert session_cache.prepare_cookie_state(True, ttl_hours=12, paths=paths) == path
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    assert "cookies 会话缓存权限已收紧" in caplog.text


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission behavior")
def test_save_cookie_state_creates_private_file(tmp_path):
    paths = runtime.init_runtime(program_dir=tmp_path, configure_logging=False).paths

    class Context:
        def storage_state(self, path):
            Path(path).write_text(
                json.dumps({"cookies": [], "origins": []}),
                encoding="utf-8",
            )

    path = session_cache.save_cookie_state(Context(), enabled=True, paths=paths)

    assert path is not None
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
