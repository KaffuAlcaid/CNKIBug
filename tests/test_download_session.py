import json
from threading import Event

import pytest
from playwright.async_api import BrowserType

from cnkibug.app.runtime import DEFAULT_CONFIG, init_runtime
from cnkibug.browser.environment import browser_launch_options
from cnkibug.cnki import downloads
from cnkibug.cnki.models import Paper
from cnkibug.core.events import EventSink
from cnkibug.core.settings import get_scraper_settings


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel", "webvpn_redirect"])
def test_batch_saves_isolated_cookies_and_closes_browser(monkeypatch, tmp_path, outcome):
    paths = init_runtime(program_dir=tmp_path, configure_logging=False).paths
    scrape_cache = paths.cache_dir / "cookies"
    scrape_cache.write_text(json.dumps({"cookies": [{
        "name": "session", "value": "scrape", "domain": "fixture.invalid", "path": "/",
        "expires": -1, "httpOnly": False, "secure": True, "sameSite": "Lax",
    }], "origins": []}), encoding="utf-8")
    original_cache = scrape_cache.read_bytes()
    contexts, closed, seen_cookies, events, finished = [], [], [], [], []
    done = Event()
    launch = BrowserType.launch_persistent_context

    async def launch_local(self, *args, **kwargs):
        context = await launch(self, *args, **kwargs)
        contexts.append(context)
        context.on("close", lambda _: closed.append(context))
        content = "<title>Local browser check</title>"
        if outcome == "webvpn_redirect":
            content += f'<script>history.replaceState(null, "", "/https/{"a" * 64}/cas/login")</script>'
        await context.route("**/*", lambda route: route.fulfill(body=content, content_type="text/html"))
        return context

    async def download_local(home_page, *args):
        cookies = await home_page.context.cookies("https://fixture.invalid/")
        seen_cookies.append(next(cookie["value"] for cookie in cookies if cookie["name"] == "session"))
        await home_page.context.add_cookies([{"name": "session", "value": "download", "url": "https://fixture.invalid/"}])
        if outcome == "cancel":
            session.close()
        if outcome in {"failure", "cancel"}:
            raise RuntimeError("Local batch stopped")
        return tmp_path / "local.pdf"

    class Events(EventSink):
        def emit(self, name, **payload):
            events.append((name, payload))
            if name == "download_finished":
                finished.append(len(closed) == len(contexts) and all(not context.browser.is_connected() for context in contexts))
                done.set()

    monkeypatch.setattr(BrowserType, "launch_persistent_context", launch_local)
    monkeypatch.setattr(downloads, "browser_launch_options", lambda candidate: {**browser_launch_options(candidate), "headless": True})
    monkeypatch.setattr(downloads, "CNKI_HOME_URL", "https://fixture.invalid/")
    monkeypatch.setattr(downloads, "_download_one", download_local)
    session = downloads.DownloadSession(paths, Events(), Event(), Event())
    webvpn_url = "https://www-cnki-net-443.webvpn.example/" if outcome == "webvpn_redirect" else ""

    for _ in range(2 if outcome == "success" else 1):
        done.clear()
        settings = get_scraper_settings({**DEFAULT_CONFIG, "download_auth_wait_sec": 0})
        session.submit([(0, Paper(title="Local paper"))], tmp_path, settings, webvpn_url)
        worker = session._thread
        assert done.wait(30), events
        worker.join(timeout=5)
        assert not worker.is_alive() and not session.alive
        assert scrape_cache.read_bytes() == original_cache
        assert finished[-1]

    if outcome == "webvpn_redirect":
        assert seen_cookies == []
        assert any(name == "download_error" and payload["error"] == "暂不支持路径加密型 WebVPN" for name, payload in events)
    else:
        saved = json.loads((paths.cache_dir / "download_cookies").read_text(encoding="utf-8"))
        assert next(cookie["value"] for cookie in saved["cookies"] if cookie["name"] == "session") == "download"
        assert seen_cookies == (["scrape", "download"] if outcome == "success" else ["scrape"])


def test_encrypted_webvpn_is_rejected_before_starting_worker(tmp_path):
    paths = init_runtime(program_dir=tmp_path, configure_logging=False).paths
    session = downloads.DownloadSession(paths, EventSink(), Event(), Event())
    with pytest.raises(ValueError, match="暂不支持路径加密型 WebVPN"):
        session.submit([], tmp_path, get_scraper_settings(DEFAULT_CONFIG), "https://webvpn.example/https/" + "a" * 64 + "/")
    assert session._thread is None
    assert not (paths.cache_dir / "download_cookies").exists()
