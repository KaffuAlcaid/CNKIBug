from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from math import ceil
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import Error as PlaywrightError, async_playwright

from ..browser.cache import DOWNLOAD_COOKIE_STATE_FILENAME, get_cookie_state_path, prepare_cookie_state, write_cookie_state
from ..browser.environment import browser_candidates, browser_launch_options
from ..cnki.search import CNKI_HOME_URL
from ..downloads.models import DownloadProblem, DownloadStopped
from ..downloads.transport import DownloadTransport


_logger = logging.getLogger("cnkibug.downloads.browser")


def _check_webvpn_path(path: str) -> None:
    if re.match(r"/https?/[0-9a-fA-F]{32,}(?:/|$)", path):
        raise ValueError("暂不支持路径加密型 WebVPN")


def validate_webvpn_url(value: str) -> None:
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.port == 0:
            raise ValueError()
    except ValueError as error:
        raise ValueError("请填写学校提供的完整知网 WebVPN 网址。") from error
    _check_webvpn_path(parts.path)
    prefix, separator, school_domain = parts.hostname.partition(".")
    if not separator or not school_domain or prefix not in {"www-cnki-net-443", "kns-cnki-net-443"}:
        raise ValueError(
            "当前支持网址以 www-cnki-net-443. 或 kns-cnki-net-443. 开头的知网 WebVPN 入口，"
            "后面需包含完整学校域名。\n\n"
            "对于其他门户及地址中含 /https/编码/ 的入口，请在学校 WebVPN 浏览器页面中手动下载 PDF。"
        )


class ManagedDownloadBrowser:
    def __init__(self, paths, batch, settings, control, report):
        self.paths, self.batch, self.settings = paths, batch, settings
        self.control, self.report = control, report
        self.context = self.driver = self.home = None
        self._temporary_profile = None
        self._storage_path = None
        self.webvpn_url = batch["webvpn_url"]

    async def open(self):
        await self.control.check()
        self.driver = await async_playwright().start()
        failures = []
        profile_root = self.paths.data_dir / "browser_profiles" / "downloads" / self.batch["session_id"]
        if not self.settings.session_cache_enabled:
            self._temporary_profile = TemporaryDirectory(prefix="cnkibug-download-session-")
            profile_root = Path(self._temporary_profile.name)
        for candidate in browser_candidates(self.driver):
            await self.control.check()
            browser_id = hashlib.sha256(candidate.name.encode()).hexdigest()[:12]
            profile = profile_root / browser_id
            fresh = not (profile / "Default").exists()
            profile.mkdir(parents=True, exist_ok=True)
            preferences = profile / "Default" / "Preferences"
            if not preferences.exists():
                preferences.parent.mkdir(parents=True, exist_ok=True)
                preferences.write_text(json.dumps({"browser": {"show_hub_popup_on_download_start": False,
                    "show_hub_popup_on_downloads_completed": False}, "download_bubble": {"partial_view_enabled": False}}), encoding="utf-8")
            try:
                self.context = await self.driver.chromium.launch_persistent_context(
                    str(profile), accept_downloads=True, no_viewport=True, **browser_launch_options(candidate))
                self._storage_path = profile / "cnkibug-storage.json"
                self.report("session", session_id=self.batch["session_id"], entry=self.webvpn_url or CNKI_HOME_URL,
                            profile=str(profile), browser=candidate.name)
                break
            except PlaywrightError as error:
                failures.append(f"{candidate.name}：{error}")
        if self.context is None:
            raise DownloadProblem("下载浏览器无法启动；请检查是否有旧窗口仍在使用此会话。\n" + "\n".join(failures), "session_error")
        restored = False
        if self.settings.session_cache_enabled and self._storage_path.is_file():
            try:
                if time.time() - self._storage_path.stat().st_mtime <= self.settings.session_cache_ttl_hours * 3600:
                    state = json.loads(self._storage_path.read_text(encoding="utf-8"))
                    await self.context.set_storage_state(state)
                    restored = True
                else:
                    await self.context.set_storage_state({"cookies": [], "origins": []})
            except (OSError, ValueError, PlaywrightError):
                _logger.warning("保存的下载会话无法恢复，将重新检查登录", exc_info=True)
        if not restored and fresh and not self.webvpn_url and not (profile_root / ".no_legacy_import").exists():
            cached = prepare_cookie_state(self.settings.session_cache_enabled, self.settings.session_cache_ttl_hours,
                                          self.paths, filename=DOWNLOAD_COOKIE_STATE_FILENAME)
            if cached is None:
                cached = prepare_cookie_state(self.settings.session_cache_enabled, self.settings.session_cache_ttl_hours, self.paths)
            if cached:
                await self.context.set_storage_state(cached)
        self.home = self.context.pages[0] if self.context.pages else await self.context.new_page()
        for stale in self.context.pages[1:]:
            await stale.close()
        self.report("phase", state="preparing", message="正在打开机构 WebVPN" if self.webvpn_url else "正在打开知网首页")
        await self.home.bring_to_front()
        await self._await(self.home.goto(self.webvpn_url or CNKI_HOME_URL, wait_until="domcontentloaded", timeout=self.settings.timeout_goto_ms))
        self.control.continued.clear()
        if self.webvpn_url:
            _check_webvpn_path(urlsplit(self.home.url).path)
            self.report("phase", state="waiting", message="请完成机构 WebVPN 登录，点击“处理完成”继续")
            while not self.control.continued.is_set():
                await self.control.check()
                if self.home.is_closed():
                    raise DownloadProblem("机构 WebVPN 页面已关闭", "browser_closed")
                _check_webvpn_path(urlsplit(self.home.url).path)
                await asyncio.sleep(0.2)
            self.webvpn_url = self.home.url
            validate_webvpn_url(self.webvpn_url)
        else:
            remaining = self.settings.download_auth_wait_sec
            while remaining > 0 and not self.control.continued.is_set():
                self.report("phase", state="preparing", message=f"知网首页准备：{ceil(remaining)} 秒，可点击“处理完成”提前继续")
                await self.control.wait(min(1.0, remaining))
                remaining -= 1
        self.control.continued.clear()
        await self._manual(self.home)
        self.report("phase", state="preparing", message="会话准备完成")

    async def _await(self, awaitable, *, allow_pause=True):
        task = asyncio.create_task(awaitable)
        try:
            while not task.done():
                await self.control.check(allow_pause=allow_pause)
                await asyncio.wait({task}, timeout=0.1)
            return await task
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def _state(self, page):
        if page.is_closed():
            raise DownloadProblem("下载页面已关闭", "browser_closed")
        return await page.evaluate("""() => {
            const visible = e => !!(e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
            const text = document.body?.innerText || '';
            const challenge = /安全验证|拼图验证|拼图校验/.test(document.title) ||
                /\\/verify|\\/checkcode/i.test(location.pathname) ||
                [...document.querySelectorAll('iframe[src*=captcha],iframe[src*=verify],#tCaptchaDyMainWrap,.nc-container,.captcha-dialog')].some(visible);
            const login = visible(document.querySelector('input[type=password]')) ||
                [...document.querySelectorAll('iframe[src*=login],iframe[src*=passport],iframe[src*=sso]')].some(visible);
            const button = [...document.querySelectorAll('#pdfDown,.btn-dlpdf a')].find(visible);
            const detail = visible(document.querySelector('.brief h1,h1.title,#ChDivSummary'));
            return {challenge, login, button:!!button, detail,
                complete:document.readyState==='complete', pending:!!window.jQuery?.active,
                denied:/机构未订购|无权访问|没有.{0,8}权限/.test(text),
                rate:/访问频次较高|访问过于频繁|请求过于频繁|too many requests/i.test(text)};
        }""")

    async def _manual(self, page, transport=None):
        state = await self._state(page)
        if not (state["challenge"] or state["login"] or state["rate"]):
            return False
        self.control.continued.clear()
        self.report("throttle")
        self.report("phase", state="waiting", message="请在浏览器中处理登录或安全验证，完成后点击“处理完成”")
        await page.bring_to_front()
        remaining = self.settings.verify_wait_timeout_sec
        confirmed = False
        while remaining > 0:
            if transport and transport.started:
                return True
            await self.control.check()
            if self.control.continued.is_set():
                self.control.continued.clear()
                confirmed = True
            if confirmed:
                state = await self._state(page)
                if not (state["challenge"] or state["login"] or state["rate"]):
                    self.report("phase", state="preparing", message="人工处理完成，正在重新检查页面")
                    return True
            await self.control.wait(1)
            remaining -= 1
            if confirmed:
                await self.control.wait(4)
                remaining -= 4
        raise DownloadProblem("等待登录或安全验证超时，请处理后继续此任务", "verification_timeout")

    def _detail_url(self, paper):
        original = urlsplit(paper.detail_url)
        host = (original.hostname or "").lower()
        portal = urlsplit(self.webvpn_url) if self.webvpn_url else None
        allowed = host == "cnki.net" or host.endswith(".cnki.net")
        if portal:
            validate_webvpn_url(self.webvpn_url)
            _, school = portal.hostname.split(".", 1)
            detail_host = "kns-cnki-net-443." + school
            if host in {portal.hostname, detail_host}:
                return paper.detail_url
            if allowed:
                authority = detail_host + (f":{portal.port}" if portal.port else "")
                return urlunsplit((portal.scheme, authority, original.path, original.query, original.fragment))
        if allowed and original.scheme in {"https", "http"}:
            return paper.detail_url
        raise DownloadProblem("缺少有效的知网详情链接", "invalid_url")

    async def download(self, paper, incoming, before_click):
        transport = DownloadTransport(self.context, incoming, self.control, self.report)
        await transport.start()
        page = None
        clicked = False
        try:
            await self.control.check()
            if not self.context.browser.is_connected():
                raise DownloadProblem("下载浏览器已关闭", "browser_closed")
            await self.home.bring_to_front()
            async with self.home.expect_popup(timeout=self.settings.timeout_goto_ms) as opened:
                await self.home.evaluate("() => window.open('about:blank','_blank')")
            page = await opened.value
            await transport.own(page)
            await page.bring_to_front()
            self.report("phase", state="preparing", message="正在加载论文详情")
            for attempt in range(2):
                try:
                    response = await self._await(page.goto(self._detail_url(paper), referer=self.home.url,
                                                          wait_until="domcontentloaded", timeout=self.settings.timeout_goto_ms))
                    if response and response.status == 429:
                        self.report("throttle")
                        raise DownloadProblem("知网限制当前请求频率，请稍后继续", "rate_limited")
                    if response and response.status >= 500:
                        if not attempt:
                            await self.control.wait(2)
                            continue
                        raise DownloadProblem("知网暂时无法提供页面，请稍后重试", "page_not_ready", retryable=True)
                    break
                except PlaywrightError:
                    if attempt or transport.started:
                        raise
                    await self.control.wait(2)
            remaining = self.settings.timeout_load_ms / 1000
            stable = 0.0
            while not transport.started:
                await self.control.check()
                state = await self._state(page)
                if state["challenge"] or state["login"] or state["rate"]:
                    await self._manual(page, transport)
                    stable = 0
                    continue
                if state["denied"]:
                    raise DownloadProblem("当前机构或账号没有此文献的下载权限", "access_denied")
                if state["complete"] and not state["pending"] and state["button"]:
                    stable += 0.25
                    if stable >= 1.5:
                        break
                else:
                    stable = 0
                if remaining <= 0:
                    if state["detail"] and state["complete"] and not state["button"]:
                        raise DownloadProblem("论文页面没有提供 PDF 下载入口", "no_pdf")
                    raise DownloadProblem("论文页面未准备完成，请稍后重试", "page_not_ready", retryable=True)
                await self.control.wait(0.25)
                remaining -= 0.25
            if not transport.started:
                await self.control.check()
                before_click()
                clicked = True
                self.report("phase", state="requested", message="已点击下载，正在等待文件")
                try:
                    await page.locator("#pdfDown:visible,.btn-dlpdf a:visible").first.click(
                        delay=150, timeout=self.settings.timeout_selector_ms, no_wait_after=True)
                except PlaywrightError:
                    if not transport.started:
                        raise
            remaining = self.settings.verify_wait_timeout_sec
            while not transport.started:
                await self.control.check()
                if remaining <= 0:
                    raise DownloadProblem("已发起下载但未取得结果，请先核对", "download_uncertain")
                alive = [p for p in transport.pages if not p.is_closed()]
                if not alive:
                    raise DownloadProblem("下载页面已关闭，尚未取得文件", "download_uncertain")
                for candidate in alive:
                    if transport.started:
                        break
                    state = await self._state(candidate)
                    if state["denied"]:
                        raise DownloadProblem("当前机构或账号没有此文献的下载权限", "access_denied")
                    if state["challenge"] or state["login"] or state["rate"]:
                        await self._manual(candidate, transport)
                        remaining = self.settings.verify_wait_timeout_sec
                for _ in range(10):
                    if transport.started:
                        break
                    await self.control.wait(0.5)
                    remaining -= 0.5
            self.report("evidence", requested=True)
            self.report("phase", state="transferring", message="正在接收 PDF")
            return await transport.receive(max(120, self.settings.verify_wait_timeout_sec))
        except DownloadStopped as error:
            if await transport.cancel():
                if transport.guid and transport.state == "completed" and (incoming / transport.guid).is_file():
                    return incoming / transport.guid
                error.code = "cancelled" if transport.started else "stopped"
            raise
        except Exception:
            if transport.guid and transport.state == "completed" and (incoming / transport.guid).is_file():
                return incoming / transport.guid
            if clicked or transport.started:
                await transport.cancel()
            raise
        finally:
            await transport.close()
            for owned in reversed(transport.pages):
                try:
                    if not owned.is_closed():
                        await owned.close()
                except PlaywrightError:
                    pass
            if not self.control.stopped and not self.home.is_closed():
                try:
                    await self.home.bring_to_front()
                    search = self.home.locator("#txt_SearchText")
                    if await search.count():
                        await search.click(timeout=1500)
                except PlaywrightError:
                    pass

    async def close(self):
        try:
            if self.context:
                if self.settings.session_cache_enabled:
                    try:
                        state = await self.context.storage_state(indexed_db=True)
                        write_cookie_state(state, self._storage_path)
                        if not self.webvpn_url:
                            write_cookie_state(state, get_cookie_state_path(self.paths, DOWNLOAD_COOKIE_STATE_FILENAME))
                    except (OSError, PlaywrightError):
                        _logger.warning("下载会话缓存未保存", exc_info=True)
                await self.context.close()
        finally:
            self.context = None
            if self.driver:
                await self.driver.stop()
                self.driver = None
            if self._temporary_profile:
                self._temporary_profile.cleanup()
                self._temporary_profile = None
