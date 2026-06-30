import json
import os

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
