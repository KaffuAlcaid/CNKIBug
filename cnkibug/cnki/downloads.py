from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from math import ceil
from pathlib import Path
from queue import Empty, Queue
from tempfile import TemporaryDirectory
from threading import Event, Thread
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import Error as PlaywrightError, async_playwright

from ..browser.cache import (
    DOWNLOAD_COOKIE_STATE_FILENAME, get_cookie_state_path, prepare_cookie_state, write_cookie_state,
)
from ..browser.environment import browser_channels, browser_launch_options
from ..cnki.models import Paper
from ..core.events import EventSink
from ..core.runtime import RuntimePaths
from ..core.settings import ScraperSettings
from .search import CNKI_HOME_URL


_logger = logging.getLogger("cnkibug.cnki.downloads")
DOWNLOAD_PAGE_CHECK_INTERVAL_SEC = 5.0


def validate_webvpn_url(value: str) -> None:
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.port == 0:
            raise ValueError()
    except ValueError as error:
        raise ValueError("请填写学校提供的完整知网 WebVPN 网址。") from error
    prefix, separator, school_domain = parts.hostname.partition(".")
    if not separator or not school_domain or prefix not in {"www-cnki-net-443", "kns-cnki-net-443"}:
        raise ValueError(
            "当前支持网址以 www-cnki-net-443. 或 kns-cnki-net-443. 开头的知网 WebVPN 入口，"
            "后面需包含完整学校域名。\n\n"
            "对于其他门户及地址中含 /https/编码/ 的入口，请在学校 WebVPN 浏览器页面中手动下载 PDF。"
        )


class DownloadSession:
    """Keep one download browser and context on their owning worker thread."""

    def __init__(self, paths: RuntimePaths, events: EventSink, cancel: Event, continue_event: Event):
        self.paths, self.events = paths, events
        self.cancel, self.continue_event = cancel, continue_event
        self._jobs: Queue = Queue()
        self._closed = Event()
        self._thread: Thread | None = None

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def submit(self, items: list[tuple[int, Paper]], destination: Path, settings: ScraperSettings,
               webvpn_url: str = "") -> None:
        self._closed.clear()
        self.cancel.clear()
        self.continue_event.clear()
        self._jobs.put((items, destination, settings, webvpn_url))
        if not self.alive:
            self._thread = Thread(target=self._run, name="cnkibug-download", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self.cancel.set()
        self._closed.set()

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception as error:
            _logger.exception("下载浏览器运行失败")
            self.events.emit("download_error", error=str(error))
        finally:
            while True:
                try:
                    self._jobs.get_nowait()
                except Empty:
                    break
            self._thread = None
            self.events.emit("download_session_closed")

    async def _serve(self) -> None:
        browser = context = home_page = None
        profile = None
        async with async_playwright() as playwright:
            try:
                while not self._closed.is_set():
                    try:
                        items, destination, settings, webvpn_url = self._jobs.get_nowait()
                    except Empty:
                        await asyncio.sleep(0.1)
                        continue
                    try:
                        destination.mkdir(parents=True, exist_ok=True)
                        if browser is None or not browser.is_connected():
                            if profile is not None:
                                profile.cleanup()
                            profile = TemporaryDirectory(prefix="cnkibug-download-")
                            preferences = Path(profile.name) / "Default" / "Preferences"
                            preferences.parent.mkdir(parents=True)
                            preferences.write_text(json.dumps({
                                "browser": {
                                    "show_hub_popup_on_download_start": False,
                                    "show_hub_popup_on_downloads_completed": False,
                                },
                                "download_bubble": {"partial_view_enabled": False},
                            }), encoding="utf-8")
                            options = {"accept_downloads": True, "no_viewport": True}
                            for channel in browser_channels():
                                try:
                                    context = await playwright.chromium.launch_persistent_context(
                                        profile.name, **options, **browser_launch_options(channel),
                                    )
                                    break
                                except PlaywrightError:
                                    if channel != "msedge":
                                        raise
                            browser = context.browser
                            state_path = prepare_cookie_state(
                                settings.session_cache_enabled, settings.session_cache_ttl_hours, self.paths,
                                filename=DOWNLOAD_COOKIE_STATE_FILENAME,
                            )
                            if state_path is None and settings.session_cache_enabled:
                                state_path = prepare_cookie_state(True, settings.session_cache_ttl_hours, self.paths)
                            if state_path:
                                await context.set_storage_state(state_path)
                            home_page = context.pages[0] if context.pages else None
                        if home_page is None or home_page.is_closed():
                            home_page = await context.new_page()
                        self.events.emit("download_preparing", remaining=None, webvpn=bool(webvpn_url))
                        home_url = webvpn_url or CNKI_HOME_URL
                        await home_page.bring_to_front()
                        await _await_or_cancel(home_page.goto(home_url, wait_until="domcontentloaded", timeout=settings.timeout_goto_ms), self.cancel)
                        if webvpn_url:
                            self.events.emit("download_webvpn_login")
                            while not self.continue_event.is_set():
                                if self.cancel.is_set() or self._closed.is_set():
                                    raise RuntimeError("已停止")
                                if home_page.is_closed():
                                    raise RuntimeError("机构 WebVPN 页面已关闭")
                                await asyncio.sleep(0.2)
                            webvpn_url = home_page.url
                            validate_webvpn_url(webvpn_url)
                        else:
                            deadline = asyncio.get_running_loop().time() + settings.download_auth_wait_sec
                            last_remaining = None
                            while not self.continue_event.is_set():
                                if self.cancel.is_set() or self._closed.is_set():
                                    raise RuntimeError("已停止")
                                if home_page.is_closed():
                                    raise RuntimeError("知网首页已关闭")
                                remaining = max(0, ceil(deadline - asyncio.get_running_loop().time()))
                                if remaining != last_remaining:
                                    self.events.emit("download_preparing", remaining=remaining)
                                    last_remaining = remaining
                                if remaining == 0:
                                    break
                                await asyncio.sleep(0.2)
                        await _wait_for_manual_access(
                            home_page, self.cancel, self.events, self.continue_event,
                            asyncio.get_running_loop().time() + settings.verify_wait_timeout_sec,
                        )
                        self.events.emit("download_prepared")
                        for position, (index, paper) in enumerate(items, start=1):
                            if self.cancel.is_set() or self._closed.is_set():
                                break
                            if not browser.is_connected():
                                raise RuntimeError("下载浏览器已关闭")
                            _logger.info("PDF 下载开始: paper=%d/%d", position, len(items))
                            self.events.emit("paper_download", index=index, status="下载中", path="")
                            try:
                                path = await _download_one(home_page, paper, destination, settings, self.cancel, self.events, self.continue_event, webvpn_url)
                                _logger.info("PDF 下载完成: paper=%d/%d", position, len(items))
                                self.events.emit("paper_download", index=index, status="已下载", path=str(path))
                            except Exception as error:
                                _logger.warning("PDF 下载结束: paper=%d/%d error=%s", position, len(items), error)
                                self.events.emit("paper_download", index=index, status=str(error), path="")
                    except Exception as error:
                        if not self.cancel.is_set():
                            _logger.exception("PDF 下载任务失败")
                            self.events.emit("download_error", error=str(error))
                    finally:
                        if context is not None and settings.session_cache_enabled:
                            try:
                                write_cookie_state(
                                    await context.storage_state(),
                                    get_cookie_state_path(self.paths, DOWNLOAD_COOKIE_STATE_FILENAME),
                                )
                            except (PlaywrightError, OSError) as error:
                                _logger.warning("下载会话缓存保存失败: %s", error)
                        self.events.emit("download_finished", stopped=self.cancel.is_set())
            finally:
                try:
                    if context is not None:
                        await context.close()
                finally:
                    try:
                        if browser is not None and browser.is_connected():
                            await browser.close()
                    finally:
                        if profile is not None:
                            profile.cleanup()


async def _requires_manual_access(page) -> bool:
    address = urlsplit(page.url)
    return address.hostname == "login.cnki.net" or "/verify" in address.path.lower() or "安全验证" in await page.title()


async def _wait_for_manual_access(page, cancel: Event, events: EventSink, continue_event: Event,
                                  deadline: float, download_ready: asyncio.Event | None = None) -> None:
    if download_ready is not None and download_ready.is_set():
        return
    if not await _requires_manual_access(page):
        return
    continue_event.clear()
    events.emit("download_manual_access")
    confirmed = False
    next_check = 0.0
    try:
        await _await_or_cancel(page.bring_to_front(), cancel)
        while not cancel.is_set():
            if download_ready is not None and download_ready.is_set():
                return
            if page.is_closed():
                cancel.set()
                raise RuntimeError("验证页面已关闭，本批下载已停止")
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                cancel.set()
                raise RuntimeError("等待登录或安全验证超时，本批下载已停止")
            if continue_event.is_set():
                continue_event.clear()
                confirmed = True
                next_check = 0.0
            if confirmed and now >= next_check:
                if not await _requires_manual_access(page):
                    return
                next_check = now + DOWNLOAD_PAGE_CHECK_INTERVAL_SEC
            await asyncio.sleep(0.2)
        raise RuntimeError("已停止")
    finally:
        continue_event.clear()
        events.emit("download_prepared")


async def _await_or_cancel(awaitable, cancel: Event):
    task = asyncio.create_task(awaitable)
    while not task.done():
        if cancel.is_set():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise RuntimeError("已停止")
        await asyncio.wait({task}, timeout=0.2)
    return await task


async def _download_one(home_page, paper, destination, settings, cancel, events, continue_event: Event,
                        webvpn_url: str = "") -> Path:
    context = home_page.context
    original = urlsplit(paper.detail_url)
    host = (original.hostname or "").lower()
    portal = urlsplit(webvpn_url) if webvpn_url else None
    detail_authority = portal.netloc if portal else ""
    detail_host = portal.hostname if portal else ""
    if portal:
        validate_webvpn_url(webvpn_url)
        prefix, separator, school_domain = (portal.hostname or "").partition(".")
        if separator and prefix in {"www-cnki-net-443", "kns-cnki-net-443"}:
            detail_host = f"kns-cnki-net-443.{school_domain}"
            if host == "kns.cnki.net":
                detail_authority = detail_host
                if portal.port is not None:
                    detail_authority += f":{portal.port}"
    is_cnki = host == "cnki.net" or host.endswith(".cnki.net")
    if not is_cnki and not (portal and host in {portal.hostname, detail_host}):
        raise ValueError("缺少有效的知网详情链接")
    detail_url = urlunsplit((portal.scheme, detail_authority, original.path, original.query, original.fragment)) if portal and is_cnki else paper.detail_url
    pages = []
    captured = {"download": None, "response": None}
    download_ready = asyncio.Event()

    def on_download(download):
        _logger.info("PDF 下载事件已收到")
        captured["download"] = download
        download_ready.set()

    def on_response(response):
        if "application/pdf" in response.headers.get("content-type", "").lower():
            captured["response"] = response
            download_ready.set()

    def watch(new_page):
        if new_page in pages:
            return
        pages.append(new_page)
        new_page.on("download", on_download)
        new_page.on("response", on_response)

    context.on("page", watch)
    target = None
    keep_pages_open = False
    try:
        _logger.info("PDF 下载步骤: 激活知网首页")
        await _await_or_cancel(home_page.bring_to_front(), cancel)
        _logger.info("PDF 下载步骤: 打开论文详情页")
        async with home_page.expect_popup(timeout=settings.timeout_goto_ms) as opened:
            await home_page.evaluate("() => window.open('about:blank', '_blank')")
        page = await opened.value
        watch(page)
        _logger.info("PDF 下载步骤: 激活论文标签页")
        await page.bring_to_front()
        _logger.info("PDF 下载步骤: 加载论文详情")
        await _await_or_cancel(page.goto(detail_url, referer=home_page.url, wait_until="load", timeout=settings.timeout_goto_ms), cancel)
        deadline = asyncio.get_running_loop().time() + settings.verify_wait_timeout_sec
        while not download_ready.is_set():
            if cancel.is_set():
                raise RuntimeError("已停止")
            if asyncio.get_running_loop().time() >= deadline:
                keep_pages_open = True
                cancel.set()
                raise RuntimeError("等待论文页面就绪超时，本批下载已停止")
            wait_seconds = random.uniform(1, 2)
            _logger.info("PDF 下载步骤: 详情页鉴权等待 %.1f 秒", wait_seconds)
            try:
                await _await_or_cancel(asyncio.wait_for(download_ready.wait(), timeout=wait_seconds), cancel)
            except asyncio.TimeoutError:
                pass
            if download_ready.is_set():
                break
            buttons = page.locator("#pdfDown:visible")
            if await buttons.count():
                break
            if not await _requires_manual_access(page):
                if download_ready.is_set():
                    break
                raise RuntimeError("页面未提供 PDF 下载")
            keep_pages_open = True
            await _wait_for_manual_access(page, cancel, events, continue_event, deadline, download_ready)
            keep_pages_open = False
        # The PDF link opens a separate page; its download is watched below.
        if not download_ready.is_set():
            _logger.info("PDF 下载步骤: 点击 PDF 按钮")
            await buttons.first.click(delay=150, timeout=settings.timeout_selector_ms, no_wait_after=True)
            _logger.info("PDF 下载步骤: 等待下载开始")
        active_page = page
        deadline = asyncio.get_running_loop().time() + settings.verify_wait_timeout_sec
        while not download_ready.is_set():
            if cancel.is_set():
                raise RuntimeError("已停止")
            if asyncio.get_running_loop().time() >= deadline:
                keep_pages_open = True
                cancel.set()
                raise RuntimeError("等待下载开始超时，本批下载已停止")
            latest_page = next((candidate for candidate in reversed(pages) if not candidate.is_closed()), None)
            if latest_page is None:
                raise RuntimeError("下载页面已关闭")
            if latest_page is not active_page:
                await _await_or_cancel(latest_page.bring_to_front(), cancel)
                active_page = latest_page
            for candidate in pages:
                if download_ready.is_set():
                    break
                if candidate.is_closed():
                    continue
                if await _requires_manual_access(candidate):
                    keep_pages_open = True
                    await _wait_for_manual_access(candidate, cancel, events, continue_event, deadline, download_ready)
                    keep_pages_open = False
                if download_ready.is_set():
                    break
                no_right = candidate.locator(".organizationTip:visible")
                if await no_right.count():
                    message = (await no_right.first.inner_text()).strip()
                    if "未订购" in message or "无权限" in message:
                        raise RuntimeError(message.rstrip("，,。"))
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining > 0 and not download_ready.is_set():
                try:
                    await _await_or_cancel(asyncio.wait_for(
                        download_ready.wait(), timeout=min(DOWNLOAD_PAGE_CHECK_INTERVAL_SEC, remaining),
                    ), cancel)
                except asyncio.TimeoutError:
                    pass
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", paper.title).strip(" .")[:100] or "论文"
        if name.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            name = "_" + name
        target = destination / f"{name}.pdf"
        number = 2
        while target.exists():
            target = destination / f"{name}_{number}.pdf"
            number += 1
        if captured["download"] is not None:
            _logger.info("PDF 下载步骤: 保存浏览器下载文件")
            await _await_or_cancel(captured["download"].save_as(str(target)), cancel)
        else:
            _logger.info("PDF 下载步骤: 读取 PDF 响应内容")
            content = await _await_or_cancel(captured["response"].body(), cancel)
            target.write_bytes(content)
        with target.open("rb") as stream:
            if b"%PDF-" not in stream.read(1024):
                raise RuntimeError("知网返回的文件不是 PDF")
        _logger.info("PDF 文件保存完成")
        return target
    except BaseException:
        if captured["download"] is not None:
            await captured["download"].cancel()
        if target is not None:
            target.unlink(missing_ok=True)
        raise
    finally:
        context.remove_listener("page", watch)
        if not keep_pages_open:
            if pages and not home_page.is_closed() and not cancel.is_set():
                try:
                    _logger.info("PDF 下载步骤: 返回知网主界面并点击搜索框")
                    await _await_or_cancel(home_page.bring_to_front(), cancel)
                    await _await_or_cancel(asyncio.sleep(1), cancel)
                    await _await_or_cancel(
                        home_page.locator("#txt_SearchText").click(delay=150, timeout=settings.timeout_selector_ms),
                        cancel,
                    )
                    _logger.info("PDF 下载步骤: 主界面搜索框已点击")
                except (PlaywrightError, RuntimeError) as error:
                    _logger.warning("点击知网主界面搜索框未完成: %s", error)
            for number, candidate in enumerate(pages, start=1):
                if not candidate.is_closed():
                    _logger.info("PDF 下载步骤: 关闭论文页面 %d/%d", number, len(pages))
                    await candidate.close()
            _logger.info("PDF 论文页面关闭完成")
