#核心抓取逻辑 —— 验证码检测、单关键词抓取、多关键词编排
#这个文件屎山太多了

import sys
import time
import random
import logging
from datetime import datetime

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
)
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
    MofNCompleteColumn,
)

from . import window
from .cnki_page import (
    SELECTOR_NO_CONTENT,
    SELECTOR_RESULT_ROWS,
    SELECTOR_RESULT_TITLE,
    SELECTOR_SEARCH_BUTTON,
    SELECTOR_SEARCH_INPUT,
    query_all,
    query_first,
)
from .ui import _console, print_browser_banner, print_verify_alert
from .errors import _popup_error
from .exporter import save_all
from .scrape_logging import (
    count_missing_fields,
    keyword_log_ref,
    missing_field_text,
    new_scrape_stats,
)
from .scrape_report import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_STOPPED,
    STATUS_SUCCESS,
    KeywordResult,
    TaskReport,
    make_keyword_result,
    print_task_report,
)
from .session_cache import discard_cookie_state, prepare_cookie_state, save_cookie_state
from .settings import get_scraper_settings
from .task_state import (
    completed_results,
    delete_last_task,
    make_task_state,
    mark_keyword_done,
    save_last_task,
)


_logger = logging.getLogger("cnkibug.scraper")


class ScrapeSession:

    def __init__(self):
        self.page = None
        self.stop_requested = False
        self.verify_timeout = False
        self.stop_reason = ""

    def request_stop(self, reason: str = "", verify_timeout: bool = False) -> None:
        self.stop_requested = True
        if reason:
            self.stop_reason = reason
        if verify_timeout:
            self.verify_timeout = True


_SETTINGS = get_scraper_settings()

_VERIFY_WAIT_TIMEOUT = _SETTINGS.verify_wait_timeout_sec
_VERIFY_NOTICE_INTERVAL = _SETTINGS.verify_notice_interval_sec
_VERIFY_NONE = "none"
_VERIFY_PASSED = "passed"
_VERIFY_TIMEOUT = "timeout"

CNKI_HOME_URL = "https://www.cnki.net/"
CNKI_SEARCH_URL = "https://kns.cnki.net/kns8s/"
WARMUP_KEYWORD = "焊接"

TIMEOUT_GOTO = _SETTINGS.timeout_goto_ms
TIMEOUT_LOAD = _SETTINGS.timeout_load_ms
TIMEOUT_SELECTOR = _SETTINGS.timeout_selector_ms

# 连续翻页未确认到页面变化达到此次数，则判定无法继续翻页，提前结束当前
# 关键词——避免在「翻页失败但不报错」时空转剩余页数、却让进度条虚报满格。
_MAX_ADVANCE_FAIL = _SETTINGS.max_advance_fail

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _keyword_ref(
    keyword: str,
    keyword_index: int | None = None,
    keyword_total: int | None = None,
) -> str:
    return keyword_log_ref(
        keyword,
        keyword_index,
        keyword_total,
        include_keyword=_SETTINGS.log_keywords,
    )


def _handle_verify(page) -> str:
    """若当前处于知网安全验证页(/verify)，置顶浏览器并等待用户完成。

    返回值用于区分：无验证、验证通过、验证等待超时。超时需要调用方停止
    当前抓取并触发保存，避免提示“将保存”后仍继续执行。
    """
    if "/verify" not in page.url:
        return _VERIFY_NONE

    _logger.warning("检测到安全验证，等待用户手动完成")
    window.bring_to_front()
    print_verify_alert()

    waited = 0.0
    interval = 1.0
    next_notice = float(_VERIFY_NOTICE_INTERVAL)
    while "/verify" in page.url:
        if waited >= _VERIFY_WAIT_TIMEOUT:
            _logger.warning("安全验证等待超时: waited_sec=%d", int(waited))
            _console.print("[yellow][!] 等待安全验证超时，将保存已抓取的数据。[/yellow]")
            return _VERIFY_TIMEOUT
        if waited >= next_notice:
            remaining = int(_VERIFY_WAIT_TIMEOUT - waited)
            _logger.info("仍在等待安全验证: waited_sec=%d remaining_sec=%d", int(waited), remaining)
            _console.print(
                f"[dim][*] 仍在等待手动完成安全验证…（剩余约 {remaining} 秒，完成后自动继续）[/dim]"
            )
            next_notice += _VERIFY_NOTICE_INTERVAL
        time.sleep(interval)
        waited += interval
    _console.print("[green][*] 验证已通过，继续抓取。[/green]")
    _logger.info("安全验证已通过: waited_sec=%d", int(waited))
    return _VERIFY_PASSED


def _print_page_debug(page, context: str) -> None:
    """打印页面状态，辅助判断 CNKI 页面结构或验证策略是否变化。"""
    _console.print(f"[yellow][debug] {context}[/yellow]")
    try:
        _console.print(f"[dim]当前 URL: {page.url}[/dim]")
    except PlaywrightError:
        _console.print("[dim]当前 URL: <无法读取>[/dim]")
    try:
        _console.print(f"[dim]页面标题: {page.title()}[/dim]")
    except PlaywrightError:
        _console.print("[dim]页面标题: <无法读取>[/dim]")


def _get_first_result_href(page) -> str:
    try:
        rows = query_all(page, "result_rows")
        if not rows:
            return ""
        first_title = query_first(rows[0], "title")
        if not first_title:
            return ""
        return first_title.get_attribute("href") or ""
    except PlaywrightError:
        return ""


def _get_next_page_marker(page) -> str:
    try:
        next_btn = query_first(page, "next_page")
        if not next_btn:
            return ""
        return next_btn.get_attribute("data-curpage") or ""
    except PlaywrightError:
        return ""


def _wait_result_page_advanced(
    page,
    old_href: str,
    old_next_page: str,
    timeout: int = 15000,
) -> bool:
    """等待翻页完成。

    CNKI 的“下一页”按钮 data-curpage 表示点击后将前往的页码，例如当前第 2 页时
    data-curpage="3"。因此点击后不能等待它等于旧值，而应等待它变化；同时用
    首行详情 href 变化作为另一个信号，避免单一 DOM 标记失效导致误判。
    """
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        new_href = _get_first_result_href(page)
        if old_href and new_href and new_href != old_href:
            return True

        new_next_page = _get_next_page_marker(page)
        if old_next_page and new_next_page and new_next_page != old_next_page:
            return True

        time.sleep(0.25)
    return False


def _wait_first_row_changed(page, old_href: str, timeout: int = 15000) -> bool:
    """等待结果列表首行详情链接变为与 old_href 不同的值。

    保留为窄用途工具函数；翻页主流程使用 _wait_result_page_advanced 同时参考
    首行 href 与 PageNext data-curpage。
    """
    try:
        page.wait_for_function(
            "(oldHref) => {"
            " const a = document.querySelector("
            f"'{SELECTOR_RESULT_ROWS} {SELECTOR_RESULT_TITLE}');"
            " return a && a.getAttribute('href')"
            " && a.getAttribute('href') !== oldHref; }",
            arg=old_href,
            timeout=timeout,
        )
        return True
    except PlaywrightTimeoutError:
        return False


def _launch_browser(p):
    try:
        _logger.info("浏览器启动开始: channel=msedge")
        with _console.status(
            "[bold magenta]少女祈祷中...[/bold magenta]",
            spinner="bouncingBar",
        ):
            browser = p.chromium.launch(
                channel="msedge",
                headless=False,
                args=["--start-maximized"],
            )
        _console.print("[dim][*] 已启动 Microsoft Edge[/dim]")
        _logger.info("浏览器启动成功: channel=msedge")
        return browser
    except PlaywrightError as edge_err:
        _logger.warning("Edge 启动失败，尝试备用 Chromium: %s", edge_err)
        _console.print(f"[yellow][!] Edge 启动失败 ({edge_err})，尝试备用 Chromium...[/yellow]")
        try:
            _logger.info("浏览器启动开始: channel=chromium")
            with _console.status(
                "[bold magenta]少女祈祷中...[/bold magenta]",
                spinner="bouncingBar",
            ):
                browser = p.chromium.launch(
                    headless=False,
                    args=["--start-maximized"],
                )
            _console.print("[dim][*] 已启动备用 Chromium 浏览器[/dim]")
            _logger.info("浏览器启动成功: channel=chromium")
            return browser
        except PlaywrightError as chromium_err:
            _logger.error("备用 Chromium 启动失败: %s", chromium_err)
            if sys.platform == "win32":
                _popup_error([
                    "==============================================",
                    " [错误] 浏览器启动失败！",
                    "----------------------------------------------",
                    " 程序无法启动 Edge，也无法启动备用 Chromium。",
                    "",
                    " 可能原因：",
                    "   · Edge 未安装或文件损坏",
                    "   · Playwright Chromium 未安装",
                    "   · 系统权限不足",
                    "   · 安全软件阻止了浏览器启动",
                    "",
                    " 建议：",
                    "   1. 安装或重新安装 Microsoft Edge",
                    "      https://www.microsoft.com/zh-cn/edge/download",
                    "   2. 源码运行用户可执行 playwright install chromium",
                    "   3. 以管理员身份运行本程序",
                    "==============================================",
                ])
            else:
                _console.print(f"[red][FATAL] 浏览器启动失败: {chromium_err}[/red]")
                _console.print("[yellow]建议执行：playwright install chromium[/yellow]")
                _console.print(
                    "[dim]Linux 若提示缺少系统依赖，可再执行："
                    "playwright install-deps chromium[/dim]"
                )
            raise RuntimeError(f"浏览器启动彻底失败: {chromium_err}")
        except Exception:
            _logger.exception("备用 Chromium 启动出现非预期异常")
            raise
    except Exception:
        _logger.exception("Edge 启动出现非预期异常")
        raise


def _warmup(session: "ScrapeSession") -> bool:
    page = session.page
    _logger.info("预热开始")
    try:
        with _console.status(
            "[bold magenta]少女祈祷中...[/bold magenta]",
            spinner="bouncingBar",
        ):
            page.goto(CNKI_HOME_URL, timeout=TIMEOUT_GOTO)
            page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_LOAD)
        _logger.info("预热首页加载完成")
        if _handle_verify(page) == _VERIFY_TIMEOUT:
            session.request_stop("安全验证等待超时", verify_timeout=True)
            _logger.warning("预热因安全验证超时停止")
        if not session.stop_requested:
            with _console.status(
                "[bold magenta]少女祈祷中...[/bold magenta]",
                spinner="bouncingBar",
            ):
                page.goto(CNKI_SEARCH_URL, timeout=TIMEOUT_GOTO)
                page.wait_for_load_state("load", timeout=TIMEOUT_LOAD)
                page.fill(SELECTOR_SEARCH_INPUT, WARMUP_KEYWORD, timeout=TIMEOUT_SELECTOR)
                time.sleep(random.uniform(0.5, 1.5))
                page.click(SELECTOR_SEARCH_BUTTON, timeout=TIMEOUT_SELECTOR)
                page.wait_for_selector(SELECTOR_RESULT_ROWS, timeout=TIMEOUT_SELECTOR)
            _logger.info("预热检索完成")
            if _handle_verify(page) == _VERIFY_TIMEOUT:
                session.request_stop("安全验证等待超时", verify_timeout=True)
                _logger.warning("预热检索后因安全验证超时停止")
        if session.stop_requested:
            _logger.warning("预热停止")
            return False
        _console.print("[dim][*] 预热完成，开始正式抓取。[/dim]")
        _logger.info("预热成功")
        return True
    except (PlaywrightTimeoutError, PlaywrightError) as warmup_err:
        _logger.warning("预热未完全成功，继续正式抓取: %s", warmup_err)
        _console.print(f"[yellow][!] 预热搜索未完全成功 ({warmup_err})，继续正式抓取。[/yellow]")
        return False


def _scrape_keyword(
    session: "ScrapeSession",
    keyword: str,
    max_pages: int,
    keyword_index: int | None = None,
    keyword_total: int | None = None,
) -> KeywordResult:
    page = session.page
    results = []
    keyword_ref = _keyword_ref(keyword, keyword_index, keyword_total)
    stats = new_scrape_stats()
    # 本关键词内跨页去重用：记录已收录文献的去重 key（详情 href 优先）。
    seen: set = set()
    _console.print(f"\n[bold][*][/bold] 目标关键词：[bold cyan]{keyword}[/bold cyan]")
    _logger.info("关键词开始: %s max_pages=%d", keyword_ref, max_pages)

    try:
        with _console.status(
            "[bold magenta]少女祈祷中...[/bold magenta]",
            spinner="bouncingBar",
        ):
            page.goto(CNKI_HOME_URL, timeout=TIMEOUT_GOTO)
            page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_LOAD)
    except PlaywrightTimeoutError:
        _logger.warning("关键词首页预热超时，跳过: %s", keyword_ref)
        _console.print("[yellow][!] 预热请求超时，跳过该关键词。[/yellow]")
        return make_keyword_result(
            keyword, keyword_index or 0, keyword_total or 0, results, STATUS_FAILED, "首页预热超时"
        )
    except PlaywrightError as e:
        _logger.warning("关键词首页预热失败，跳过: %s error=%s", keyword_ref, e)
        _console.print(f"[yellow][!] 预热请求失败: {e}，跳过该关键词。[/yellow]")
        return make_keyword_result(
            keyword, keyword_index or 0, keyword_total or 0, results, STATUS_FAILED, "首页预热失败"
        )
    if _handle_verify(page) == _VERIFY_TIMEOUT:
        session.request_stop("安全验证等待超时", verify_timeout=True)
        _logger.warning("关键词因首页安全验证超时停止: %s", keyword_ref)
        return make_keyword_result(
            keyword, keyword_index or 0, keyword_total or 0, results, STATUS_STOPPED, "安全验证等待超时"
        )

    try:
        with _console.status(
            "[bold magenta]少女祈祷中...[/bold magenta]",
            spinner="bouncingBar",
        ):
            page.goto(CNKI_SEARCH_URL, timeout=TIMEOUT_GOTO)
            page.wait_for_load_state("load", timeout=TIMEOUT_LOAD)
    except PlaywrightTimeoutError:
        _logger.warning("检索页加载超时，跳过关键词: %s", keyword_ref)
        _console.print("[yellow][!] 检索页加载超时，跳过该关键词。[/yellow]")
        return make_keyword_result(
            keyword, keyword_index or 0, keyword_total or 0, results, STATUS_FAILED, "检索页加载超时"
        )
    except PlaywrightError as e:
        _logger.warning("检索页加载失败，跳过关键词: %s error=%s", keyword_ref, e)
        _console.print(f"[yellow][!] 检索页加载失败: {e}，跳过该关键词。[/yellow]")
        return make_keyword_result(
            keyword, keyword_index or 0, keyword_total or 0, results, STATUS_FAILED, "检索页加载失败"
        )
    if _handle_verify(page) == _VERIFY_TIMEOUT:
        session.request_stop("安全验证等待超时", verify_timeout=True)
        _logger.warning("关键词因检索页安全验证超时停止: %s", keyword_ref)
        return make_keyword_result(
            keyword, keyword_index or 0, keyword_total or 0, results, STATUS_STOPPED, "安全验证等待超时"
        )

    with _console.status(
        "[bold magenta]少女祈祷中...[/bold magenta]",
        spinner="bouncingBar",
    ):
        page.fill(SELECTOR_SEARCH_INPUT, keyword, timeout=TIMEOUT_SELECTOR)
        time.sleep(random.uniform(0.5, 1.5))
        page.click(SELECTOR_SEARCH_BUTTON, timeout=TIMEOUT_SELECTOR)
        time.sleep(random.uniform(1, 2))
    _logger.info("关键词检索已提交: %s", keyword_ref)
    if _handle_verify(page) == _VERIFY_TIMEOUT:
        session.request_stop("安全验证等待超时", verify_timeout=True)
        _logger.warning("关键词提交后因安全验证超时停止: %s", keyword_ref)
        return make_keyword_result(
            keyword, keyword_index or 0, keyword_total or 0, results, STATUS_STOPPED, "安全验证等待超时"
        )

    try:
        outcome = page.wait_for_function(
            """(selectors) => {
                if (document.querySelector(selectors.resultRows)) return 'has_results';
                if (document.querySelector(selectors.noContent)) return 'no_content';
                return false;
            }""",
            arg={
                "resultRows": SELECTOR_RESULT_ROWS,
                "noContent": SELECTOR_NO_CONTENT,
            },
            timeout=TIMEOUT_SELECTOR,
        ).json_value()
    except PlaywrightTimeoutError:
        _logger.warning("关键词结果加载超时，跳过: %s", keyword_ref)
        _print_page_debug(page, f"关键词「{keyword}」结果加载超时")
        _console.print(f"[yellow][!] 关键词「{keyword}」结果加载超时，跳过。[/yellow]")
        return make_keyword_result(
            keyword, keyword_index or 0, keyword_total or 0, results, STATUS_FAILED, "结果加载超时"
        )

    if outcome == "no_content":
        _logger.info("关键词无结果: %s", keyword_ref)
        _console.print(f"[yellow][!] 知网无「{keyword}」的检索结果，跳过。[/yellow]")
        return make_keyword_result(
            keyword, keyword_index or 0, keyword_total or 0, results, STATUS_EMPTY, "知网无结果"
        )

    with Progress(
        SpinnerColumn(spinner_name="bouncingBar", style="bold magenta"),
        TextColumn("[bold magenta]{task.description}[/bold magenta]"),
        BarColumn(bar_width=36, style="magenta", complete_style="bright_magenta"),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        console=_console,
        transient=False,
    ) as progress:
        task = progress.add_task(
            description=f"第 1 / {max_pages} 页",
            total=max_pages,
        )

        consecutive_advance_fail = 0  # 连续翻页未确认次数

        for current_page in range(1, max_pages + 1):
            try:
                page_records_start = len(results)
                page_rows_seen = 0 # noqa
                page_duplicates = 0
                page_skipped_no_title = 0
                page_parse_errors = 0
                progress.update(task, description=f"第 [bold]{current_page}[/bold] / {max_pages} 页")
                try:
                    page.wait_for_selector(
                        SELECTOR_RESULT_ROWS, timeout=TIMEOUT_SELECTOR
                    )
                except PlaywrightTimeoutError:
                    verify_status = _handle_verify(page)
                    if verify_status == _VERIFY_PASSED:
                        try:
                            page.wait_for_selector(
                                SELECTOR_RESULT_ROWS, timeout=TIMEOUT_SELECTOR
                            )
                        except PlaywrightTimeoutError:
                            _logger.warning(
                                "验证通过后结果页表格仍等待超时，提前结束关键词: %s page=%d",
                                keyword_ref,
                                current_page,
                            )
                            progress.console.print(
                                f"[yellow][!] 第 {current_page} 页验证通过后仍加载超时，"
                                "已停止当前关键词，避免重复抓取旧页面。[/yellow]"
                            )
                            break
                    elif verify_status == _VERIFY_TIMEOUT:
                        session.request_stop("安全验证等待超时", verify_timeout=True)
                        _logger.warning("结果页等待时安全验证超时: %s page=%d", keyword_ref, current_page)
                        break
                    else:
                        _logger.warning("结果页表格等待超时，提前结束关键词: %s page=%d", keyword_ref, current_page)
                        progress.console.print(
                            f"[yellow][!] 第 {current_page} 页等待超时（非验证），"
                            "已停止当前关键词，避免重复抓取旧页面。[/yellow]"
                        )
                        _print_page_debug(page, f"第 {current_page} 页结果表格等待超时")
                        break

                time.sleep(random.uniform(2, 5))
                if _handle_verify(page) == _VERIFY_TIMEOUT:
                    session.request_stop("安全验证等待超时", verify_timeout=True)
                    _logger.warning("结果页解析前安全验证超时: %s page=%d", keyword_ref, current_page)
                    break

                rows = query_all(page, "result_rows")
                page_rows_seen = len(rows)
                stats["rows_seen"] += page_rows_seen
                for row in rows:
                    try:
                        title_el = query_first(row, "title")
                        if not title_el:
                            page_skipped_no_title += 1
                            stats["skipped_no_title"] += 1
                            continue
                        title = title_el.inner_text().strip()

                        # 去重 key：优先用详情链接 href（知网每篇文献唯一），
                        # 取不到再回退 (标题, 来源, 日期) 三元组。
                        href = title_el.get_attribute("href")

                        author_parts = []
                        for a in query_all(row, "author"):
                            name = a.text_content().strip()
                            if name:
                                author_parts.append(name)
                        authors = "; ".join(author_parts)

                        source_el = query_first(row, "source")
                        source = (
                            " ".join(source_el.text_content().split())
                            if source_el else ""
                        )

                        date_el = query_first(row, "date")
                        date = date_el.text_content().strip() if date_el else ""

                        dedup_key = href if href else (title, source, date)
                        if dedup_key in seen:
                            page_duplicates += 1
                            stats["duplicates"] += 1
                            continue
                        seen.add(dedup_key)

                        record = [title, authors, source, date]
                        count_missing_fields(record, stats)
                        results.append(record)
                        stats["records_added"] += 1
                        progress.console.print(f"  [green]→[/green] {title}")
                    except PlaywrightError:
                        page_parse_errors += 1
                        stats["row_parse_errors"] += 1
                        # 单行解析失败（元素 stale / 被回收等）只跳过这一行，
                        # 不再让整页/整个关键词中断。
                        continue

                page_records_added = len(results) - page_records_start
                if _SETTINGS.log_scraped_records:
                    _logger.info(
                        "结果页完成: %s page=%d rows=%d added=%d duplicates=%d "
                        "skipped_no_title=%d parse_errors=%d total_records=%d missing_fields=(%s)",
                        keyword_ref,
                        current_page,
                        page_rows_seen,
                        page_records_added,
                        page_duplicates,
                        page_skipped_no_title,
                        page_parse_errors,
                        len(results),
                        missing_field_text(stats),
                    )
                else:
                    _logger.info(
                        "结果页完成: %s page=%d rows=%d added=%d total_records=%d",
                        keyword_ref,
                        current_page,
                        page_rows_seen,
                        page_records_added,
                        len(results),
                    )

                progress.advance(task)

                if current_page < max_pages:
                    next_btn = query_first(page, "next_page")
                    if next_btn:
                        old_first_href = _get_first_result_href(page)
                        old_next_page = next_btn.get_attribute("data-curpage") or ""
                        next_btn.click(timeout=TIMEOUT_SELECTOR)
                        if _wait_result_page_advanced(
                            page,
                            old_href=old_first_href,
                            old_next_page=old_next_page,
                            timeout=TIMEOUT_SELECTOR,
                        ):
                            consecutive_advance_fail = 0
                        else:
                            consecutive_advance_fail += 1
                            _logger.warning(
                                "翻页后未确认到结果变化: %s page=%d consecutive_fail=%d max_fail=%d",
                                keyword_ref,
                                current_page,
                                consecutive_advance_fail,
                                _MAX_ADVANCE_FAIL,
                            )
                            progress.console.print(
                                f"[yellow][!] 翻页后未确认到结果变化"
                                f"（连续 {consecutive_advance_fail}/{_MAX_ADVANCE_FAIL} 次）。[/yellow]"
                            )
                            _print_page_debug(page, f"第 {current_page} 页翻页确认超时")
                            # 连续多次翻页都没动静，多半已到尾页或被限流，再翻只会
                            # 重复抓同一页（被 seen 去重）、白白拉满进度条。提前收尾，
                            # 如实告知用户真实有效页数。
                            if consecutive_advance_fail >= _MAX_ADVANCE_FAIL:
                                _logger.warning(
                                    "连续翻页失败，提前结束关键词: %s effective_pages=%d requested_pages=%d",
                                    keyword_ref,
                                    current_page,
                                    max_pages,
                                )
                                progress.console.print(
                                    f"[red][x] 连续翻页失败，提前结束关键词「{keyword}」："
                                    f"实际有效页数约 {current_page} 页"
                                    f"（共请求 {max_pages} 页）。[/red]"
                                )
                                break
                        time.sleep(random.uniform(1, 2))
                        if _handle_verify(page) == _VERIFY_TIMEOUT:
                            session.request_stop("安全验证等待超时", verify_timeout=True)
                            _logger.warning("翻页后安全验证超时: %s page=%d", keyword_ref, current_page)
                            break
                    else:
                        _logger.info("未找到下一页按钮，结束关键词: %s page=%d", keyword_ref, current_page)
                        progress.console.print(
                            "[yellow][!] 没找到下一页按钮，可能已到最后一页。[/yellow]"
                        )
                        break

            except PlaywrightError:
                # 只有浏览器/页面真正关闭才中止整个关键词；其它操作异常
                # （翻页点击失败、临时 stale 等）只跳过本页继续。
                if page.is_closed():
                    session.request_stop("浏览器页面已关闭")
                    _logger.warning("浏览器页面已关闭，结束关键词: %s page=%d", keyword_ref, current_page)
                    progress.console.print(
                        "\n[yellow][!] 检测到浏览器被手动关闭，"
                        "正在为您安全中止并保存已抓取的数据...[/yellow]"
                    )
                    break
                _logger.warning("结果页处理异常，提前结束关键词: %s page=%d", keyword_ref, current_page, exc_info=True)
                progress.console.print(
                    f"[yellow][!] 第 {current_page} 页处理异常，已停止当前关键词，避免重复抓取旧页面。[/yellow]"
                )
                break

            except KeyboardInterrupt:
                session.request_stop("用户中断")
                _logger.warning("关键词抓取被用户中断: %s page=%d", keyword_ref, current_page)
                break

    if session.stop_requested:
        _logger.warning("关键词停止: %s total_records=%d", keyword_ref, len(results))
        _console.print("[yellow][!] 用户中断，正在保存已抓取的数据...[/yellow]")
        return make_keyword_result(
            keyword,
            keyword_index or 0,
            keyword_total or 0,
            results,
            STATUS_STOPPED,
            session.stop_reason or "已停止",
        )
    else:
        _logger.info(
            "关键词完成: %s total_records=%d rows_seen=%d duplicates=%d "
            "skipped_no_title=%d parse_errors=%d missing_fields=(%s)",
            keyword_ref,
            len(results),
            stats["rows_seen"],
            stats["duplicates"],
            stats["skipped_no_title"],
            stats["row_parse_errors"],
            missing_field_text(stats),
        )

    status = STATUS_SUCCESS if results else STATUS_FAILED
    reason = "" if results else "未解析到有效记录"
    return make_keyword_result(
        keyword,
        keyword_index or 0,
        keyword_total or 0,
        results,
        status,
        reason,
    )


def scrape_cnki(
    keywords: list[str],
    max_pages: int,
    save_mode: str,
    resume_state: dict | None = None,
):
    """
    save_mode:
      'single'       -> 单关键词，保存为 cnki_titles_关键词.xlsx
      'multi_split'  -> 多关键词分文件保存
      'multi_merge'  -> 多关键词单文件多 Sheet 保存
    """
    if not keywords:
        _console.print("[yellow][!] 未提供任何关键词，已跳过抓取。[/yellow]")
        return

    if resume_state is not None:
        keywords = list(resume_state["keywords"])
        max_pages = int(resume_state["max_pages"])
        save_mode = str(resume_state["save_mode"])
        ts = str(resume_state["ts"])
        task_state = resume_state
        all_results: dict[str, list] = completed_results(task_state)
        _console.print(
            f"[dim][*] 已载入上次未完成任务："
            f"共 {len(keywords)} 个关键词，已完成 {len(all_results)} 个。[/dim]"
        )
        _logger.info(
            "恢复未完成任务: keyword_count=%d completed=%d max_pages=%d save_mode=%s ts=%s",
            len(keywords),
            len(all_results),
            max_pages,
            save_mode,
            ts,
        )
    else:
        all_results = {}
        # 所有增量落盘与最终保存共用同一时间戳，保证写的是同一批文件（覆盖而非堆积）。
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        task_state = make_task_state(keywords, max_pages, save_mode, ts)
        save_last_task(task_state)

    report = TaskReport(total_keywords=len(keywords))
    completed_state = task_state.get("completed", {})
    if isinstance(completed_state, dict):
        for idx, keyword in enumerate(keywords):
            item = completed_state.get(keyword)
            if not isinstance(item, dict):
                continue
            report.add(make_keyword_result(
                keyword,
                idx + 1,
                len(keywords),
                item.get("records", []),
                str(item.get("status", STATUS_FAILED)),
                str(item.get("reason", "")),
            ))

    # 每次调用独立的会话状态，取代旧的模块级全局 _stop_requested。
    session = ScrapeSession()
    _logger.info(
        "抓取任务开始: keyword_count=%d max_pages=%d save_mode=%s",
        len(keywords),
        max_pages,
        save_mode,
    )

    with sync_playwright() as p:
        browser = None # noqa
        context = None

        browser = _launch_browser(p)

        try:
            cookie_state_path = prepare_cookie_state(
                _SETTINGS.session_cache_enabled,
                _SETTINGS.session_cache_ttl_hours,
            )
            context_options = {
                "no_viewport": True,
                "user_agent": USER_AGENT,
            }
            if cookie_state_path is not None:
                context_options["storage_state"] = str(cookie_state_path)
            try:
                context = browser.new_context(**context_options)
            except PlaywrightError:
                if cookie_state_path is None:
                    raise
                discard_cookie_state(cookie_state_path, "创建浏览器上下文失败")
                _logger.warning("cookies 会话缓存加载失败，已改用新会话", exc_info=True)
                context_options.pop("storage_state", None)
                context = browser.new_context(**context_options)
            _logger.info("浏览器上下文已创建: no_viewport=True")
            page = context.new_page()
            session.page = page

            print_browser_banner()

            warmup_ok = _warmup(session)
            _logger.info("预热结果: ok=%s stop_requested=%s", warmup_ok, session.stop_requested)
            # _warmup 返回 False 且未触发验证超时（未置 session.stop_requested），意味着
            # 预热阶段网络异常或知网不可达——此时继续抓取大概率只是每个关键词逐个
            # 超时空转。明确告知并让用户决定是否继续，非交互环境下默认中止。
            if not warmup_ok and not session.stop_requested:
                _console.print(
                    "[yellow][!] 预热未成功，可能网络异常或知网暂时不可达。[/yellow]"
                )
                try:
                    cont = input("是否仍尝试继续抓取？(y/n): ").strip().lower()
                except EOFError:
                    cont = "n"
                if cont != "y":
                    session.request_stop("预热失败后用户选择停止")
                    _logger.warning("用户选择在预热失败后停止抓取")
                else:
                    _logger.info("用户选择在预热失败后继续抓取")
            time.sleep(random.uniform(2, 4))

            for idx, keyword in enumerate(keywords):
                if session.stop_requested:
                    break
                if keyword in all_results:
                    _logger.info(
                        "关键词已在 last_task 中完成，跳过: %s",
                        _keyword_ref(keyword, idx + 1, len(keywords)),
                    )
                    continue
                if idx > 0:
                    wait_sec = random.uniform(5, 8)
                    _logger.info(
                        "关键词间隔等待: next_keyword_index=%d/%d wait_sec=%.1f",
                        idx + 1,
                        len(keywords),
                        wait_sec,
                    )
                    with _console.status(
                        f"[dim]少女祈祷中... 等待 {wait_sec:.1f} 秒[/dim]",
                        spinner="dots",
                    ):
                        time.sleep(wait_sec)

                keyword_result = make_keyword_result( # noqa
                    keyword,
                    idx + 1,
                    len(keywords),
                    [],
                    STATUS_FAILED,
                    "未开始抓取",
                )
                try:
                    keyword_result = _scrape_keyword(
                        session,
                        keyword,
                        max_pages,
                        idx + 1,
                        len(keywords),
                    )
                except PlaywrightTimeoutError as e:
                    _logger.warning(
                        "关键词页面等待超时，跳过: %s error=%s",
                        _keyword_ref(keyword, idx + 1, len(keywords)),
                        e,
                    )
                    _console.print(f"[red][x] 关键词「{keyword}」页面等待超时，跳过: {e}[/red]")
                    keyword_result = make_keyword_result(
                        keyword,
                        idx + 1,
                        len(keywords),
                        [],
                        STATUS_FAILED,
                        "关键词页面等待超时",
                    )
                except PlaywrightError as e:
                    _logger.warning(
                        "浏览器连接异常，停止后续关键词: %s error=%s",
                        _keyword_ref(keyword, idx + 1, len(keywords)),
                        e,
                    )
                    _console.print(f"[yellow][!] 浏览器连接已断开，停止后续关键词抓取: {e}[/yellow]")
                    session.request_stop("浏览器连接异常")
                    keyword_result = make_keyword_result(
                        keyword,
                        idx + 1,
                        len(keywords),
                        [],
                        STATUS_STOPPED,
                        "浏览器连接异常",
                    )
                except KeyboardInterrupt:
                    session.request_stop("用户中断")
                    _logger.warning(
                        "用户中断关键词循环: %s",
                        _keyword_ref(keyword, idx + 1, len(keywords)),
                    )
                    keyword_result = make_keyword_result(
                        keyword,
                        idx + 1,
                        len(keywords),
                        [],
                        STATUS_STOPPED,
                        "用户中断",
                    )

                all_results[keyword] = keyword_result.records
                report.add(keyword_result)
                mark_keyword_done(task_state, keyword_result)
                save_last_task(task_state)
                _logger.info(
                    "关键词结果已记录: %s status=%s records=%d stop_requested=%s",
                    _keyword_ref(keyword, idx + 1, len(keywords)),
                    keyword_result.status,
                    len(keyword_result.records),
                    session.stop_requested,
                )
                try:
                    save_result = save_all(save_mode, keywords, all_results, ts, announce=False)
                    _logger.info(
                        "增量保存完成: completed_keywords=%d/%d total_records=%d attempted=%d saved=%d failed=%d",
                        idx + 1,
                        len(keywords),
                        sum(len(items) for items in all_results.values()),
                        save_result.attempted,
                        len(save_result.saved_paths),
                        save_result.failed,
                    )
                    if save_result.failed:
                        _console.print(
                            f"[yellow][!] 阶段性保存有 {save_result.failed} 个文件未成功写入，"
                            "最终保存时会再次尝试。[/yellow]"
                        )
                    elif len(keywords) > 1 and save_result.saved_paths:
                        _console.print(
                            f"[dim][*] 已落盘阶段性结果"
                            f"（已完成 {idx + 1}/{len(keywords)} 个关键词）[/dim]"
                        )
                except BaseException: # noqa
                    _logger.exception("增量保存失败")

                if session.stop_requested:
                    break

        except KeyboardInterrupt:
            session.request_stop("用户中断")
            _logger.warning("抓取任务被用户中断")
            _console.print(
                "\n[bold yellow][!] 用户中断，正在保存已抓取的数据...[/bold yellow]"
            )
        except RuntimeError as e:
            session.request_stop("运行时错误")
            _logger.error("抓取任务运行时错误: %s", e)
            _console.print(f"[red][x] 运行时错误: {e}[/red]")
        finally:
            report.stopped = session.stop_requested
            report.verify_timeout = session.verify_timeout
            # 多轮抓取下每轮新建 context，显式关闭避免句柄/进程残留；再关 browser
            # 兜底（browser.close 会级联关闭其下 context，二者都 try 包裹不互相影响）。
            if context:
                try:
                    save_cookie_state(context, _SETTINGS.session_cache_enabled)
                    context.close() # noqa
                    _logger.info("浏览器上下文已关闭")
                except BaseException: # noqa
                    _logger.warning("浏览器上下文关闭失败", exc_info=True)
                    pass
            if browser:
                try:
                    browser.close()
                    _logger.info("浏览器已关闭")
                except BaseException: # noqa
                    _logger.warning("浏览器关闭失败", exc_info=True)
                    pass

            # 最终保存 + 打印汇总。放在 finally 内、以 BaseException 兜底，
            # 确保正常完成、关键词中途出错、以及保存阶段的二次 Ctrl+C 都不丢数据。
            # 与增量落盘共用同一 ts，写的是同一批文件（幂等覆盖）。
            final_save_failed = False
            try:
                _logger.info(
                    "最终保存开始: keyword_count=%d total_records=%d stop_requested=%s",
                    len(keywords),
                    sum(len(items) for items in all_results.values()),
                    session.stop_requested,
                )
                save_result = save_all(save_mode, keywords, all_results, ts, announce=True)
                if save_result.failed:
                    final_save_failed = True
                    _console.print(
                        f"[bold red][x] 本轮有 {save_result.failed} 个文件未能成功保存。[/bold red]"
                    )
                elif save_result.saved_paths:
                    _logger.info("最终保存成功: saved_files=%d", len(save_result.saved_paths))
                _logger.info(
                    "抓取任务结束: completed_keywords=%d/%d total_records=%d stop_requested=%s save_attempted=%d save_failed=%d",
                    len(all_results),
                    len(keywords),
                    sum(len(items) for items in all_results.values()),
                    session.stop_requested,
                    save_result.attempted,
                    save_result.failed,
                )
            except BaseException as save_err: # noqa
                final_save_failed = True
                _logger.exception("最终保存失败")
                _console.print("\n[bold red][x] 最终保存失败！[/bold red]")
                _console.print(f"[red]错误信息：{save_err}[/red]")
                _console.print("[yellow]请关闭已打开的同名 Excel 文件，并检查桌面或程序目录写入权限。[/yellow]")

            try:
                print_task_report(report, all_results)
            except BaseException: # noqa
                _logger.exception("任务摘要输出失败")

            if not session.stop_requested and report.completed_keywords >= len(keywords) and not final_save_failed:
                delete_last_task()
            else:
                save_last_task(task_state)
