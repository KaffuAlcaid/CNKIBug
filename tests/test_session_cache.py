import json
import os

from cnkibug.app import runtime
from cnkibug.browser import cache as session_cache


def test_prepare_cookie_state_uses_fresh_cache(tmp_path):
    paths = runtime.init_runtime(program_dir=tmp_path, configure_logging=False).paths
    path = tmp_path / "CNKIBug-data" / "cache" / "cookies"
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    os.utime(path, (1000, 1000))

    assert session_cache.prepare_cookie_state(True, ttl_hours=12, paths=paths, now=1000) == path
    assert path.exists()


def test_prepare_cookie_state_deletes_expired_cache(tmp_path):
    paths = runtime.init_runtime(program_dir=tmp_path, configure_logging=False).paths
    path = tmp_path / "CNKIBug-data" / "cache" / "cookies"
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
    path = tmp_path / "CNKIBug-data" / "cache" / "cookies"
    path.write_text("not-json", encoding="utf-8")

    assert session_cache.prepare_cookie_state(True, ttl_hours=12, paths=paths) is None
    assert not path.exists()


def test_prepare_cookie_state_disabled(tmp_path):
    paths = runtime.init_runtime(program_dir=tmp_path, configure_logging=False).paths

    assert session_cache.prepare_cookie_state(False, ttl_hours=12, paths=paths) is None
