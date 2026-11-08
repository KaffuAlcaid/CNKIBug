import json
from pathlib import Path

import pytest
from playwright.async_api import BrowserType

from cnkibug.app.runtime import DEFAULT_CONFIG, init_runtime
from cnkibug.browser.environment import browser_launch_options
from cnkibug.cnki import downloads
from cnkibug.cnki.models import Paper
from cnkibug.core.events import EventSink
from cnkibug.core.settings import get_scraper_settings


def test_verification_visibility_ignores_offscreen_container_and_preserves_download_below_fold():
    import asyncio
    from playwright.async_api import async_playwright
    from cnkibug.browser.environment import browser_candidates

    async def run():
        async with async_playwright() as playwright:
            candidate = browser_candidates(playwright)[0]
            browser = await playwright.chromium.launch(**{**browser_launch_options(candidate), "headless": True})
            try:
                page = await browser.new_page()
                await page.set_content('''
                    <title>中国知网</title>
                    <div id="tCaptchaDyMainWrap" style="position:fixed;top:-1000000px;width:218px;height:280px"></div>
                    <input type="password" style="display:none">
                    <div style="margin-top:2000px"><a id="pdfDown" href="#">PDF下载</a></div>
                ''')
                state = await downloads.ManagedDownloadBrowser._state(None, page)
                assert not state["challenge"] and not state["login"]
                assert state["button"]
                await page.locator("#tCaptchaDyMainWrap").evaluate("e=>e.style.top='20px'")
                assert (await downloads.ManagedDownloadBrowser._state(None, page))["challenge"]
                await page.locator("#tCaptchaDyMainWrap").evaluate("e=>e.style.opacity='0'")
                assert not (await downloads.ManagedDownloadBrowser._state(None, page))["challenge"]
            finally:
                await browser.close()

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel", "webvpn_redirect"])
def test_batch_saves_isolated_cookies_and_closes_browser(monkeypatch, tmp_path, outcome):
    import time
    from cnkibug.downloads.service import DownloadService
    from cnkibug.downloads.models import DownloadProblem, DownloadStopped

    paths = init_runtime(program_dir=tmp_path, configure_logging=False).paths
    scrape_cache = paths.cache_dir / "cookies"
    scrape_cache.write_text(json.dumps({"cookies": [{
        "name": "session", "value": "scrape", "domain": "fixture.invalid", "path": "/",
        "expires": -1, "httpOnly": False, "secure": True, "sameSite": "Lax",
    }], "origins": []}), encoding="utf-8")
    original_cache = scrape_cache.read_bytes()
    contexts, closed, seen_cookies, events = [], [], [], []
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

    async def download_local(self, paper, incoming, before_click):
        cookies = await self.context.cookies("https://fixture.invalid/")
        seen_cookies.append(next(cookie["value"] for cookie in cookies if cookie["name"] == "session"))
        await self.context.add_cookies([{"name": "session", "value": "download", "url": "https://fixture.invalid/"}])
        before_click()
        if outcome == "cancel":
            self.control.stopped = True
            raise DownloadStopped()
        if outcome == "failure":
            raise DownloadProblem("Local access denied", "access_denied")
        path = incoming / "local.pdf"
        path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        self.report("evidence", transport_completed=True)
        return path

    class Events(EventSink):
        def emit(self, name, **payload):
            events.append((name, payload))

    monkeypatch.setattr(BrowserType, "launch_persistent_context", launch_local)
    monkeypatch.setattr(downloads, "browser_launch_options", lambda candidate: {**browser_launch_options(candidate), "headless": True})
    monkeypatch.setattr(downloads, "CNKI_HOME_URL", "https://fixture.invalid/")
    monkeypatch.setattr(downloads.ManagedDownloadBrowser, "download", download_local)
    service = DownloadService(paths, Events())
    webvpn_url = "https://www-cnki-net-443.webvpn.example/" if outcome == "webvpn_redirect" else ""
    try:
        for _ in range(2 if outcome == "success" else 1):
            settings = get_scraper_settings({**DEFAULT_CONFIG, "download_auth_wait_sec": 0})
            batch = service.submit([Paper(title="Local paper", doi="10.1234/local")], tmp_path, settings, webvpn_url, force=True).result(3)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                current = next(b for b in service.snapshot()["batches"] if b["id"] == batch["id"])
                if current["state"] not in {"queued", "running"} and not service.busy:
                    break
                time.sleep(0.05)
            else:
                pytest.fail(str(service.snapshot()))
            assert len(closed) == len(contexts)
            assert all(not context.browser.is_connected() for context in contexts)
            assert scrape_cache.read_bytes() == original_cache
        if outcome == "webvpn_redirect":
            assert seen_cookies == []
            assert "暂不支持路径加密型 WebVPN" in current["items"][0]["error"]
        else:
            saved = json.loads((paths.cache_dir / "download_cookies").read_text(encoding="utf-8"))
            assert next(cookie["value"] for cookie in saved["cookies"] if cookie["name"] == "session") == "download"
            assert seen_cookies == (["scrape", "download"] if outcome == "success" else ["scrape"])
    finally:
        service.close()
        assert service.wait_closed()


def test_encrypted_webvpn_is_rejected_before_browser_launch(tmp_path):
    from cnkibug.downloads.service import DownloadService

    paths = init_runtime(program_dir=tmp_path, configure_logging=False).paths
    service = DownloadService(paths)
    try:
        with pytest.raises(ValueError, match="暂不支持路径加密型 WebVPN"):
            service.submit([Paper(title="Local", doi="10.1/local")], tmp_path, get_scraper_settings(DEFAULT_CONFIG),
                           "https://webvpn.example/https/" + "a" * 64 + "/").result(3)
        assert not service.busy
        assert not (paths.cache_dir / "download_cookies").exists()
    finally:
        service.close()
        assert service.wait_closed()


@pytest.mark.parametrize("new_tab", [True, False])
def test_native_download_tracks_owned_file_and_survives_context_close(tmp_path, new_tab):
    import asyncio
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from playwright.async_api import async_playwright
    from cnkibug.browser.environment import browser_candidates
    from cnkibug.downloads.service import DownloadControl
    from cnkibug.downloads.transport import DownloadTransport

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/paper":
                content = b"%PDF-1.4\n" + b" " * 16000 + b"\n%%EOF\n"
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", "attachment; filename=paper.pdf")
                self.send_header("Content-Length", str(len(content)))
            else:
                target = 'target="_blank"' if new_tab else ""
                content = f'<a id="pdf" href="/paper" {target}>PDF</a>'.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()

    async def run():
        incoming = tmp_path / "incoming"
        incoming.mkdir()
        evidence = []
        async with async_playwright() as playwright:
            candidate = browser_candidates(playwright)[0]
            context = await playwright.chromium.launch_persistent_context(
                str(tmp_path / "profile"), accept_downloads=True,
                **{**browser_launch_options(candidate), "headless": True})
            transfer = DownloadTransport(context, incoming, DownloadControl(), lambda kind, **data: evidence.append((kind, data)))
            try:
                await transfer.start()
                page = context.pages[0]
                await page.goto(f"http://127.0.0.1:{server.server_port}/")
                await transfer.own(page)
                await page.locator("#pdf").click()
                path = await transfer.receive(15)
                assert transfer.guid
                assert path.read_bytes().startswith(b"%PDF-")
                assert any(data.get("transport_completed") for _, data in evidence)
            finally:
                await transfer.close()
                await context.close()
            assert path.is_file()

    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("outcome", ["pdf", "html", "cancel"])
def test_managed_browser_to_persisted_task_and_pdf_index(monkeypatch, tmp_path, outcome):
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from cnkibug.downloads.service import DownloadService
    from cnkibug.fileio.pdf_links import PdfIndex

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/file":
                body = b"<html>login required</html>" if outcome == "html" else b"%PDF-1.4\n" + b" " * 1_000_000 + b"\n%%EOF\n"
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", "attachment; filename=local.pdf")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    for offset in range(0, len(body), 16384):
                        self.wfile.write(body[offset:offset+16384])
                        self.wfile.flush()
                        if outcome == "cancel":
                            time.sleep(0.05)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                return
            body = '<input id="txt_SearchText">'
            if self.path == "/detail":
                body += '<div class="brief"><h1>Local managed paper</h1></div><a id="pdfDown" href="/file" target="_blank">PDF</a>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(downloads, "CNKI_HOME_URL", base)
    monkeypatch.setattr(downloads, "browser_launch_options", lambda candidate: {**browser_launch_options(candidate), "headless": True})
    monkeypatch.setattr(downloads.ManagedDownloadBrowser, "_detail_url", lambda self, paper: paper.detail_url)
    paths = init_runtime(program_dir=tmp_path, configure_logging=False).paths
    service = DownloadService(paths)
    paper = Paper(title="Local managed paper", doi="10.1234/native-managed", detail_url=base + "/detail")
    try:
        service.submit([paper], tmp_path / "pdf", get_scraper_settings({**DEFAULT_CONFIG, "download_auth_wait_sec": 0})).result(3)
        deadline = time.monotonic() + 35
        stop_sent = False
        while time.monotonic() < deadline:
            snapshot = service.snapshot()
            if outcome == "cancel" and not stop_sent and snapshot.get("progress", {}).get("received", 0) > 0:
                service.request("stop").result(3)
                stop_sent = True
            if snapshot["batches"] and snapshot["batches"][0]["state"] in {"completed", "stopped", "blocked"} and not service.busy:
                break
            time.sleep(0.05)
        else:
            pytest.fail(str(service.snapshot()))
        item = snapshot["batches"][0]["items"][0]
        history = service.request("history", item_id=item["id"]).result(3)
        attempt = history["attempts"][0]
        assert attempt["requested"]
        assert attempt["metadata"]["guid"]
        if outcome == "pdf":
            assert item["state"] == "completed"
            assert Path(item["path"]).read_bytes().startswith(b"%PDF-")
            assert PdfIndex(tmp_path / "pdf").find(paper)["outcome"] == "downloaded"
            assert attempt["metadata"]["transport_completed"]
        elif outcome == "html":
            assert item["state"] == "failed" and item["error_code"] == "invalid_pdf"
            assert not list((tmp_path / "pdf").glob("*.pdf"))
        else:
            assert stop_sent
            assert item["state"] == "queued"
            assert not list((tmp_path / "pdf").glob("*.pdf"))
    finally:
        service.close()
        assert service.wait_closed()
        server.shutdown()
        server.server_close()
