from types import SimpleNamespace

from cnkibug.app.runtime import get_runtime_paths
from cnkibug.browser import runtime as browser_runtime


def test_create_browser_context_uses_browser_default_user_agent(monkeypatch, tmp_path):
    captured_options = []

    class Browser:
        def new_context(self, **options):
            captured_options.append(options)
            return object()

    monkeypatch.setattr(browser_runtime, "prepare_cookie_state", lambda *args: None)
    settings = SimpleNamespace(
        session_cache_enabled=False,
        session_cache_ttl_hours=12,
    )

    context = browser_runtime.create_browser_context(
        Browser(),
        settings,
        get_runtime_paths(tmp_path),
    )

    assert context is not None
    assert captured_options == [{"no_viewport": True}]
