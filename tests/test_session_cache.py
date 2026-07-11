import json
import os
from pathlib import Path

import pytest

from cnkibug import runtime, session_cache


def test_prepare_cookie_state_uses_fresh_cache(tmp_path):
    runtime.init_runtime(base_dir=tmp_path, configure_logging=False)
    path = tmp_path / "CNKIBug" / "cache" / "cookies"
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    os.utime(path, (1000, 1000))

    assert session_cache.prepare_cookie_state(True, ttl_hours=12, now=1000) == path
    assert path.exists()


def test_prepare_cookie_state_deletes_expired_cache(tmp_path):
    runtime.init_runtime(base_dir=tmp_path, configure_logging=False)
    path = tmp_path / "CNKIBug" / "cache" / "cookies"
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    os.utime(path, (1000, 1000))

    assert session_cache.prepare_cookie_state(True, ttl_hours=12, now=1000 + 13 * 3600) is None
    assert not path.exists()


def test_prepare_cookie_state_deletes_invalid_cache(tmp_path):
    runtime.init_runtime(base_dir=tmp_path, configure_logging=False)
    path = tmp_path / "CNKIBug" / "cache" / "cookies"
    path.write_text("not-json", encoding="utf-8")

    assert session_cache.prepare_cookie_state(True, ttl_hours=12) is None
    assert not path.exists()


def test_prepare_cookie_state_disabled(tmp_path):
    runtime.init_runtime(base_dir=tmp_path, configure_logging=False)

    assert session_cache.prepare_cookie_state(False, ttl_hours=12) is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission behavior")
def test_prepare_cookie_state_tightens_permissions(tmp_path, caplog):
    runtime.init_runtime(base_dir=tmp_path, configure_logging=False)
    path = tmp_path / "CNKIBug" / "cache" / "cookies"
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    path.parent.chmod(0o755)
    path.chmod(0o644)

    assert session_cache.prepare_cookie_state(True, ttl_hours=12) == path
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    assert "cookies 会话缓存权限已收紧" in caplog.text


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission behavior")
def test_save_cookie_state_creates_private_file(tmp_path):
    runtime.init_runtime(base_dir=tmp_path, configure_logging=False)

    class Context:
        def storage_state(self, path):
            Path(path).write_text(
                json.dumps({"cookies": [], "origins": []}),
                encoding="utf-8",
            )

    path = session_cache.save_cookie_state(Context(), enabled=True)

    assert path is not None
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
