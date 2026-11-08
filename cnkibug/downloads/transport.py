from __future__ import annotations

import asyncio
import re
from pathlib import Path

from .models import DownloadProblem, DownloadStopped


class DownloadTransport:
    """Observe one owned page tree; only an authorized download can claim a GUID."""

    def __init__(self, context, incoming: Path, control, report):
        self.context, self.incoming = context, incoming
        self.control, self.report = control, report
        self.client = None
        self.guid = ""
        self.url = ""
        self.state = ""
        self.download = None
        self.response = None
        self.response_at = 0.0
        self.pages = []
        self.frames = set()
        self.authorized_urls = set()
        self.candidates = {}
        self.progress = {}
        self._tasks = set()
        self._closed = False

    async def start(self):
        try:
            self.client = await self.context.browser.new_browser_cdp_session()
            await self.client.send("Browser.setDownloadBehavior", {
                "behavior": "allowAndName", "downloadPath": str(self.incoming.resolve()), "eventsEnabled": True,
            })
            self.client.on("Browser.downloadWillBegin", self._begin)
            self.client.on("Browser.downloadProgress", self._progress)
        except Exception:
            if self.client:
                await self.client.detach()
            self.client = None
        self.context.on("page", self._new_page)

    async def own(self, page):
        if self._closed or page in self.pages:
            return
        self.pages.append(page)
        page.on("download", self._download)
        page.on("response", self._response)
        try:
            client = await self.context.new_cdp_session(page)
            tree = await client.send("Page.getFrameTree")
            self._frames(tree["frameTree"])
            await client.detach()
        except Exception:
            pass
        self._claim()

    def _frames(self, tree):
        self.frames.add(tree["frame"]["id"])
        for child in tree.get("childFrames", []):
            self._frames(child)

    def _new_page(self, page):
        task = asyncio.create_task(self._check_page(page))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _check_page(self, page):
        try:
            opener = await page.opener()
            for _ in range(5):
                if opener in self.pages:
                    await self.own(page)
                    return
                if opener is None:
                    return
                opener = await opener.opener()
        except Exception:
            return

    def _download(self, download):
        if download.page not in self.pages or self._closed:
            return
        self.download = download
        self.authorized_urls.add(download.url)
        self.report("evidence", state="transferring", requested=True)
        self._claim()

    def _response(self, response):
        if ("application/pdf" in response.headers.get("content-type", "").lower()
                and "attachment" not in response.headers.get("content-disposition", "").lower()):
            self.response = response
            self.response_at = asyncio.get_running_loop().time()

    def _begin(self, event):
        if not re.fullmatch(r"[A-Za-z0-9-]+", event.get("guid", "")):
            return
        self.candidates[event["guid"]] = event
        self._claim()

    def _claim(self):
        if self.guid or self._closed:
            return
        for guid, event in self.candidates.items():
            if event.get("frameId") in self.frames or event.get("url") in self.authorized_urls:
                self.guid, self.url = guid, event["url"]
                self.report("evidence", state="transferring", requested=True, guid=guid)
                if guid in self.progress:
                    self._progress(self.progress[guid])
                return

    def _progress(self, event):
        self.progress[event["guid"]] = event
        if event["guid"] != self.guid or self._closed:
            return
        self.state = event["state"]
        self.report("progress", received=int(event.get("receivedBytes", 0)), total=int(event.get("totalBytes", 0)))
        if self.state == "completed":
            self.report("evidence", transport_completed=True, guid=self.guid)

    @property
    def started(self):
        return bool(self.guid or self.download or self.response)

    async def receive(self, timeout):
        deadline = asyncio.get_running_loop().time() + timeout
        fallback = None
        try:
            while True:
                if self.guid and self.state == "completed":
                    path = self.incoming / self.guid
                    if path.is_file():
                        return path
                if self.guid and self.state == "canceled":
                    raise DownloadProblem("浏览器已取消下载", "cancelled")
                try:
                    await self.control.check(allow_pause=False)
                except DownloadStopped as stopped:
                    confirmed = await self.cancel()
                    if self.guid and self.state == "completed" and (self.incoming / self.guid).is_file():
                        return self.incoming / self.guid
                    if confirmed:
                        stopped.code = "cancelled"
                    raise
                if not self.client and self.download and fallback is None:
                    path = self.incoming / "received.pdf"
                    fallback = asyncio.create_task(self.download.save_as(str(path)))
                elif (self.response and not self.download and not self.guid and fallback is None
                      and asyncio.get_running_loop().time() - self.response_at >= 0.5):
                    fallback = asyncio.create_task(self._save_response())
                if fallback and fallback.done():
                    try:
                        await fallback
                    except Exception:
                        if not self.client:
                            raise
                        self.response = fallback = None
                        continue
                    if self.guid:
                        fallback = None
                        continue
                    self.report("evidence", transport_completed=True)
                    return self.incoming / "received.pdf"
                if asyncio.get_running_loop().time() >= deadline:
                    await self.cancel()
                    raise DownloadProblem("等待文件传输超时，需核对下载结果", "transfer_timeout")
                await asyncio.sleep(0.1)
        finally:
            if fallback and not fallback.done():
                fallback.cancel()
                await asyncio.gather(fallback, return_exceptions=True)

    async def _save_response(self):
        content = await self.response.body()
        path = self.incoming / "received.pdf"
        await asyncio.to_thread(path.write_bytes, content)

    async def cancel(self) -> bool:
        if self.guid and self.client:
            try:
                await self.client.send("Browser.cancelDownload", {"guid": self.guid})
                deadline = asyncio.get_running_loop().time() + 3
                while self.state not in {"completed", "canceled"} and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.1)
                return self.state in {"completed", "canceled"}
            except Exception:
                return False
        if self.download:
            try:
                await self.download.cancel()
                return True
            except Exception:
                return False
        return not self.started

    async def close(self):
        self._closed = True
        self.context.remove_listener("page", self._new_page)
        for page in self.pages:
            page.remove_listener("download", self._download)
            page.remove_listener("response", self._response)
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.client:
            try:
                await self.client.detach()
            except Exception:
                pass
