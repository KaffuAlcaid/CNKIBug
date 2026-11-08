from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import unicodedata
from math import ceil
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urljoin, urlsplit, urlunsplit

from playwright.async_api import Error as PlaywrightError, async_playwright

from ..browser.cache import DOWNLOAD_COOKIE_STATE_FILENAME, get_cookie_state_path, prepare_cookie_state, write_cookie_state
from ..browser.environment import browser_candidates, browser_launch_options
from .search import CNKI_HOME_URL, CNKI_SEARCH_URL
from .selectors import SELECTOR_SEARCH_INPUT, SELECTOR_SEARCH_BUTTON, SELECTOR_RESULT_ROWS, SELECTOR_NO_CONTENT
from .models import split_authors
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
        self.search_page = None
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
            const rendered = (e, inViewport=false) => {
                if (!e) return false;
                const r=e.getBoundingClientRect();
                if (r.width<=0 || r.height<=0) return false;
                for (let node=e; node; node=node.parentElement) {
                    const style=getComputedStyle(node);
                    if (style.display==='none' || style.visibility==='hidden' ||
                        style.visibility==='collapse' || Number(style.opacity)===0) return false;
                }
                return !inViewport || (r.bottom>0 && r.right>0 && r.top<innerHeight && r.left<innerWidth);
            };
            const visible = e => rendered(e, true);
            const text = document.body?.innerText || '';
            const challenge = /安全验证|拼图验证|拼图校验/.test(document.title) ||
                /\\/verify|\\/checkcode/i.test(location.pathname) ||
                [...document.querySelectorAll('iframe[src*=captcha],iframe[src*=verify],#tCaptchaDyMainWrap,.nc-container,.captcha-dialog')].some(visible);
            const login = [...document.querySelectorAll('input[type=password]')].some(visible) ||
                [...document.querySelectorAll('iframe[src*=login],iframe[src*=passport],iframe[src*=sso]')].some(visible);
            const button = [...document.querySelectorAll('#pdfDown,.btn-dlpdf a')].find(e=>rendered(e));
            const detail = rendered(document.querySelector('.brief h1,h1.title,#ChDivSummary'));
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

    def _search_entry_url(self):
        if not self.webvpn_url:
            return CNKI_SEARCH_URL
        validate_webvpn_url(self.webvpn_url)
        portal = urlsplit(self.webvpn_url)
        _, school = portal.hostname.split(".", 1)
        authority = "kns-cnki-net-443." + school + (f":{portal.port}" if portal.port else "")
        return urlunsplit((portal.scheme, authority, urlsplit(CNKI_SEARCH_URL).path, "", ""))

    @staticmethod
    def _identity_text(value):
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()

    def _matches_result(self, paper, result):
        normalize = self._identity_text
        if normalize(result["title"]) != normalize(paper.title):
            return False
        if paper.source and result["source"] and normalize(paper.source) != normalize(result["source"]):
            return False
        expected_year = re.search(r"\d{4}", paper.publication_date)
        found_year = re.search(r"\d{4}", result["date"])
        if expected_year and found_year and expected_year.group() != found_year.group():
            return False
        if paper.authors and result["authors"]:
            authors = {normalize(author) for author in split_authors(paper.authors)}
            if not authors.intersection(normalize(author) for author in result["authors"]):
                return False
        return True

    @staticmethod
    def _title_query(title):
        # CNKI treats unquoted -, + and * as search operators.
        for quote in ("'", '"'):
            if quote not in title:
                return quote + title.strip() + quote
        parts = [part.strip() for part in re.split(r"['\"]", title) if part.strip()]
        return " * ".join("'" + part + "'" for part in parts)

    async def _select_title_search(self, page):
        field = page.locator("#selectfield")
        if await field.input_value(timeout=self.settings.timeout_selector_ms) == "TI":
            return
        menu = page.locator(".search-main .sort-default")
        option = page.locator('.search-main li[data-val="TI"] a')
        await self._await(menu.hover(timeout=self.settings.timeout_selector_ms))
        if not await option.is_visible():
            await self._await(menu.click(timeout=self.settings.timeout_selector_ms))
        await self._await(option.click(timeout=self.settings.timeout_selector_ms))
        if await field.input_value() != "TI":
            raise DownloadProblem("无法切换到篇名检索，请稍后重试", "lookup_not_ready", retryable=True)

    async def _wait_search_ready(self, page, *, results=False):
        remaining = self.settings.timeout_load_ms / 1000
        stable = 0.0
        previous = ""
        while remaining > 0:
            await self.control.check()
            state = await self._state(page)
            if state["challenge"] or state["login"] or state["rate"]:
                await self._manual(page)
                remaining = self.settings.timeout_load_ms / 1000
                stable = 0
                previous = ""
                continue
            if results:
                links = page.locator(SELECTOR_RESULT_ROWS).locator("td.name a")
                marker = await links.evaluate_all("links=>links.map(e=>(e.textContent||'').trim()+'|'+e.getAttribute('href')).join('\\n')")
                ready = bool(marker)
                if not ready and await page.locator(SELECTOR_NO_CONTENT).count():
                    raise DownloadProblem("当前会话未检索到该论文，请核对题名", "lookup_not_found")
            else:
                field = page.locator(SELECTOR_SEARCH_INPUT)
                button = page.locator(SELECTOR_SEARCH_BUTTON)
                ready = await field.is_visible() and await button.is_visible()
                if ready:
                    ready = await field.is_enabled() and await button.is_enabled()
                marker = "form" if ready else ""
            if ready:
                stable = stable + 0.25 if marker == previous else 0.25
                previous = marker
                if stable >= 0.75:
                    return
            else:
                stable = 0
                previous = ""
            await self.control.wait(0.25)
            remaining -= 0.25
        raise DownloadProblem("检索页面未准备完成，请稍后重试", "lookup_not_ready", retryable=True)

    async def _open_detail_from_search(self, paper, transport):
        if not paper.title.strip():
            raise DownloadProblem("缺少论文题名，无法在当前会话定位文献", "lookup_metadata_missing")
        if self.search_page is None or self.search_page.is_closed():
            self.search_page = await self.context.new_page()
        search = self.search_page
        await search.bring_to_front()
        self.report("phase", state="preparing", message="正在当前会话检索论文题名")
        # Start from the search form so a previous result table cannot be mistaken
        # for the response to this title. Saved signed detail URLs are never replayed.
        response = await self._await(search.goto(self._search_entry_url(), referer=self.home.url,
                                                wait_until="domcontentloaded", timeout=self.settings.timeout_goto_ms))
        if response and response.status == 429:
            self.report("throttle")
            raise DownloadProblem("知网限制当前检索频率，请稍后继续", "rate_limited")
        await self._wait_search_ready(search)
        await self._select_title_search(search)
        query = self._title_query(paper.title)
        self.report("evidence", lookup={"field": "TI", "query": query})
        await self._await(search.locator(SELECTOR_SEARCH_INPUT).fill(query, timeout=self.settings.timeout_selector_ms))
        await self.control.wait(0.5)
        await self._await(search.locator(SELECTOR_SEARCH_BUTTON).click(timeout=self.settings.timeout_selector_ms))
        await self._wait_search_ready(search, results=True)
        rows = await search.locator(SELECTOR_RESULT_ROWS).evaluate_all("""rows => rows.map((row,index) => {
            const link=row.querySelector('td.name a');
            return {index,title:link?.textContent?.trim()||'',href:link?.getAttribute('href')||'',
                source:row.querySelector('td.source')?.textContent?.trim()||'',
                date:row.querySelector('td.date')?.textContent?.trim()||'',
                authors:[...row.querySelectorAll('td.author a')].map(e=>e.textContent.trim()).filter(Boolean)};
        })""")
        matches = [row for row in rows if self._matches_result(paper, row)]
        self.report("evidence", lookup={"field": "TI", "query": query, "result_count": len(rows),
                    "match_count": len(matches), "candidates": [
                        {key: row[key] for key in ("title", "authors", "source", "date")} for row in rows[:5]]})
        _logger.info("下载篇名检索: title=%s results=%s matches=%s", paper.title, len(rows), len(matches))
        if len(matches) != 1:
            reason = "未找到与题名和已知书目信息一致的论文" if not matches else "同名检索结果不唯一，无法确定要下载的论文"
            raise DownloadProblem(reason + "；请核对文献信息", "lookup_not_found" if not matches else "lookup_ambiguous")
        selected = matches[0]
        address = urlsplit(urljoin(search.url, selected["href"]))
        expected_host = urlsplit(self._search_entry_url()).hostname
        allowed = address.hostname == expected_host or (not self.webvpn_url and (
            address.hostname == "cnki.net" or (address.hostname or "").endswith(".cnki.net")))
        if address.scheme not in {"http", "https"} or not allowed:
            raise DownloadProblem("检索结果没有提供可确认的知网论文入口", "lookup_invalid_link")
        await transport.own(search)
        link = search.locator(SELECTOR_RESULT_ROWS).nth(selected["index"]).locator("td.name a").first
        await self.control.check()
        self.report("phase", state="preparing", message="正在点击当前检索结果进入论文详情")
        if await link.get_attribute("target") == "_blank":
            async with search.expect_popup(timeout=self.settings.timeout_selector_ms) as opened:
                await self._await(link.click(timeout=self.settings.timeout_selector_ms))
            page = await self._await(opened.value)
        else:
            await self._await(link.click(timeout=self.settings.timeout_selector_ms))
            page = search
        await transport.own(page)
        await page.bring_to_front()
        await self._await(page.wait_for_load_state("domcontentloaded", timeout=self.settings.timeout_goto_ms))
        self.report("evidence", located_from_search=True)
        return page

    async def _confirm_detail_title(self, page, paper):
        title = await page.locator(".brief h1,h1.title").first.inner_text(timeout=self.settings.timeout_selector_ms)
        title = re.sub(r"(?:\s*(?:网络首发|附视频))+$", "", title).strip()
        if self._identity_text(title) != self._identity_text(paper.title):
            raise DownloadProblem("打开的详情页题名与目标论文不一致，未点击下载", "identity_mismatch")

    async def download(self, paper, incoming, before_click):
        transport = DownloadTransport(self.context, incoming, self.control, self.report)
        await transport.start()
        page = None
        clicked = False
        try:
            await self.control.check()
            if not self.context.browser.is_connected():
                raise DownloadProblem("下载浏览器已关闭", "browser_closed")
            page = await self._open_detail_from_search(paper, transport)
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
                await self._confirm_detail_title(page, paper)
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
                    if owned is not self.search_page and not owned.is_closed():
                        await owned.close()
                except PlaywrightError:
                    pass
            if not self.control.stopped and self.search_page is not None and not self.search_page.is_closed():
                try:
                    await self.search_page.bring_to_front()
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
