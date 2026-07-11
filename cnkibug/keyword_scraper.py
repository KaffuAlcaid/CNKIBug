# 单关键词抓取流程：搜索、翻页、解析。
from __future__ import annotations

import logging
import random
import time
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Error as PlaywrightError
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
    MofNCompleteColumn,
)

from .cnki_guard import VERIFY_PASSED, VERIFY_TIMEOUT, handle_verify, print_page_debug
from .cnki_page import (
    SELECTOR_NO_CONTENT,
    SELECTOR_RESULT_ROWS,
    SELECTOR_SEARCH_BUTTON,
    SELECTOR_SEARCH_INPUT,
    query_first,
)
from .cnki_results import get_first_result_href, parse_result_rows, wait_result_page_advanced
from .scrape_logging import keyword_log_ref, missing_field_text, new_scrape_stats
from .scrape_report import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_STOPPED,
    STATUS_SUCCESS,
    KeywordResult,
    make_keyword_result,
)
from .scrape_session import ScrapeSession
from .settings import ScraperSettings
from .ui import _console


CNKI_HOME_URL = "https://www.cnki.net/"
CNKI_SEARCH_URL = "https://kns.cnki.net/kns8s/"
WARMUP_KEYWORD = "焊接"

_logger = logging.getLogger("cnkibug.keyword_scraper")


def warmup(session: ScrapeSession, settings: ScraperSettings) -> bool:
    page = _require_page(session)
    _logger.info("预热开始")
    try:
        with _console.status(
            "[bold magenta]少女祈祷中...[/bold magenta]",
            spinner="bouncingBar",
        ):
            page.goto(CNKI_HOME_URL, timeout=settings.timeout_goto_ms)
            page.wait_for_load_state("domcontentloaded", timeout=settings.timeout_load_ms)
        _logger.info("预热首页加载完成")
        if handle_verify(page, settings) == VERIFY_TIMEOUT:
            session.request_stop("安全验证等待超时", verify_timeout=True)
            _logger.warning("预热因安全验证超时停止")
        if not session.stop_requested:
            with _console.status(
                "[bold magenta]少女祈祷中...[/bold magenta]",
                spinner="bouncingBar",
            ):
                page.goto(CNKI_SEARCH_URL, timeout=settings.timeout_goto_ms)
                page.wait_for_load_state("load", timeout=settings.timeout_load_ms)
                page.fill(SELECTOR_SEARCH_INPUT, WARMUP_KEYWORD, timeout=settings.timeout_selector_ms)
                time.sleep(random.uniform(0.5, 1.5))
                page.click(SELECTOR_SEARCH_BUTTON, timeout=settings.timeout_selector_ms)
                page.wait_for_selector(SELECTOR_RESULT_ROWS, timeout=settings.timeout_selector_ms)
            _logger.info("预热检索完成")
            if handle_verify(page, settings) == VERIFY_TIMEOUT:
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


def _open_home_page(page: Any, settings: ScraperSettings) -> None:
    with _console.status(
        "[bold magenta]少女祈祷中...[/bold magenta]",
        spinner="bouncingBar",
    ):
        page.goto(CNKI_HOME_URL, timeout=settings.timeout_goto_ms)
        page.wait_for_load_state("domcontentloaded", timeout=settings.timeout_load_ms)


def _open_search_page(page: Any, settings: ScraperSettings) -> None:
    with _console.status(
        "[bold magenta]少女祈祷中...[/bold magenta]",
        spinner="bouncingBar",
    ):
        page.goto(CNKI_SEARCH_URL, timeout=settings.timeout_goto_ms)
        page.wait_for_load_state("load", timeout=settings.timeout_load_ms)


def _submit_search(page: Any, keyword: str, settings: ScraperSettings) -> None:
    with _console.status(
        "[bold magenta]少女祈祷中...[/bold magenta]",
        spinner="bouncingBar",
    ):
        page.fill(SELECTOR_SEARCH_INPUT, keyword, timeout=settings.timeout_selector_ms)
        time.sleep(random.uniform(0.5, 1.5))
        page.click(SELECTOR_SEARCH_BUTTON, timeout=settings.timeout_selector_ms)
        time.sleep(random.uniform(1, 2))


def _wait_search_outcome(page: Any, settings: ScraperSettings) -> str:
    return page.wait_for_function(
        """(selectors) => {
            if (location.pathname.includes('/verify')) return 'verify';
            if (document.querySelector(selectors.resultRows)) return 'has_results';
            if (document.querySelector(selectors.noContent)) return 'no_content';
            return false;
        }""",
        arg={
            "resultRows": SELECTOR_RESULT_ROWS,
            "noContent": SELECTOR_NO_CONTENT,
        },
        timeout=settings.timeout_selector_ms,
    ).json_value()


def scrape_keyword(
    session: ScrapeSession,
    keyword: str,
    max_pages: int,
    settings: ScraperSettings,
    keyword_index: int | None = None,
    keyword_total: int | None = None,
) -> KeywordResult:
    page = _require_page(session)
    results: list[list[str]] = []
    keyword_ref = keyword_log_ref(
        keyword,
        keyword_index,
        keyword_total,
        include_keyword=settings.log_keywords,
    )
    stats = new_scrape_stats()
    seen: set[Any] = set()
    _console.print(f"\n[bold][*][/bold] 目标关键词：[bold cyan]{keyword}[/bold cyan]")
    _logger.info("关键词开始: %s max_pages=%d", keyword_ref, max_pages)

    try:
        _open_home_page(page, settings)
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
    if handle_verify(page, settings) == VERIFY_TIMEOUT:
        session.request_stop("安全验证等待超时", verify_timeout=True)
        _logger.warning("关键词因首页安全验证超时停止: %s", keyword_ref)
        return make_keyword_result(
            keyword, keyword_index or 0, keyword_total or 0, results, STATUS_STOPPED, "安全验证等待超时"
        )

    try:
        _open_search_page(page, settings)
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
    if handle_verify(page, settings) == VERIFY_TIMEOUT:
        session.request_stop("安全验证等待超时", verify_timeout=True)
        _logger.warning("关键词因检索页安全验证超时停止: %s", keyword_ref)
        return make_keyword_result(
            keyword, keyword_index or 0, keyword_total or 0, results, STATUS_STOPPED, "安全验证等待超时"
        )

    _submit_search(page, keyword, settings)
    _logger.info("关键词检索已提交: %s", keyword_ref)
    if handle_verify(page, settings) == VERIFY_TIMEOUT:
        session.request_stop("安全验证等待超时", verify_timeout=True)
        _logger.warning("关键词提交后因安全验证超时停止: %s", keyword_ref)
        return make_keyword_result(
            keyword, keyword_index or 0, keyword_total or 0, results, STATUS_STOPPED, "安全验证等待超时"
        )

    while True:
        try:
            outcome = _wait_search_outcome(page, settings)
        except PlaywrightTimeoutError:
            _logger.warning("关键词结果加载超时，跳过: %s", keyword_ref)
            print_page_debug(page, f"关键词「{keyword}」结果加载超时")
            _console.print(f"[yellow][!] 关键词「{keyword}」结果加载超时，跳过。[/yellow]")
            return make_keyword_result(
                keyword, keyword_index or 0, keyword_total or 0, results, STATUS_FAILED, "结果加载超时"
            )

        if outcome != "verify":
            break

        _logger.warning("等待检索结果期间检测到安全验证: %s", keyword_ref)
        if handle_verify(page, settings) == VERIFY_TIMEOUT:
            session.request_stop("安全验证等待超时", verify_timeout=True)
            return make_keyword_result(
                keyword,
                keyword_index or 0,
                keyword_total or 0,
                results,
                STATUS_STOPPED,
                "安全验证等待超时",
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

        consecutive_advance_fail = 0
        incomplete_reason: str | None = None

        for current_page in range(1, max_pages + 1):
            try:
                progress.update(task, description=f"第 [bold]{current_page}[/bold] / {max_pages} 页")
                try:
                    page.wait_for_selector(
                        SELECTOR_RESULT_ROWS, timeout=settings.timeout_selector_ms
                    )
                except PlaywrightTimeoutError:
                    verify_status = handle_verify(page, settings)
                    if verify_status == VERIFY_PASSED:
                        try:
                            page.wait_for_selector(
                                SELECTOR_RESULT_ROWS, timeout=settings.timeout_selector_ms
                            )
                        except PlaywrightTimeoutError:
                            incomplete_reason = f"第 {current_page} 页验证通过后仍加载超时"
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
                    elif verify_status == VERIFY_TIMEOUT:
                        session.request_stop("安全验证等待超时", verify_timeout=True)
                        _logger.warning("结果页等待时安全验证超时: %s page=%d", keyword_ref, current_page)
                        break
                    else:
                        incomplete_reason = f"第 {current_page} 页结果表格等待超时"
                        _logger.warning("结果页表格等待超时，提前结束关键词: %s page=%d", keyword_ref, current_page)
                        progress.console.print(
                            f"[yellow][!] 第 {current_page} 页等待超时（非验证），"
                            "已停止当前关键词，避免重复抓取旧页面。[/yellow]"
                        )
                        print_page_debug(page, f"第 {current_page} 页结果表格等待超时")
                        break

                time.sleep(random.uniform(2, 5))
                if handle_verify(page, settings) == VERIFY_TIMEOUT:
                    session.request_stop("安全验证等待超时", verify_timeout=True)
                    _logger.warning("结果页解析前安全验证超时: %s page=%d", keyword_ref, current_page)
                    break

                page_parse = parse_result_rows(page, seen, stats)
                results.extend(page_parse.records)
                for record in page_parse.records:
                    progress.console.print(f"  [green]→[/green] {record[0]}")

                if settings.log_scraped_records:
                    _logger.info(
                        "结果页完成: %s page=%d rows=%d added=%d duplicates=%d "
                        "skipped_no_title=%d parse_errors=%d total_records=%d missing_fields=(%s)",
                        keyword_ref,
                        current_page,
                        page_parse.rows_seen,
                        page_parse.records_added,
                        page_parse.duplicates,
                        page_parse.skipped_no_title,
                        page_parse.parse_errors,
                        len(results),
                        missing_field_text(stats),
                    )
                else:
                    _logger.info(
                        "结果页完成: %s page=%d rows=%d added=%d total_records=%d",
                        keyword_ref,
                        current_page,
                        page_parse.rows_seen,
                        page_parse.records_added,
                        len(results),
                    )

                progress.advance(task)

                if current_page < max_pages:
                    next_btn = query_first(page, "next_page")
                    if next_btn:
                        old_first_href = get_first_result_href(page)
                        old_next_page = next_btn.get_attribute("data-curpage") or ""
                        next_btn.click(timeout=settings.timeout_selector_ms)
                        if wait_result_page_advanced(
                            page,
                            old_href=old_first_href,
                            old_next_page=old_next_page,
                            timeout=settings.timeout_selector_ms,
                        ):
                            consecutive_advance_fail = 0
                        else:
                            consecutive_advance_fail += 1
                            _logger.warning(
                                "翻页后未确认到结果变化: %s page=%d consecutive_fail=%d max_fail=%d",
                                keyword_ref,
                                current_page,
                                consecutive_advance_fail,
                                settings.max_advance_fail,
                            )
                            progress.console.print(
                                f"[yellow][!] 翻页后未确认到结果变化"
                                f"（连续 {consecutive_advance_fail}/{settings.max_advance_fail} 次）。[/yellow]"
                            )
                            print_page_debug(page, f"第 {current_page} 页翻页确认超时")
                            if consecutive_advance_fail >= settings.max_advance_fail:
                                incomplete_reason = f"第 {current_page} 页后连续翻页失败"
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
                        if handle_verify(page, settings) == VERIFY_TIMEOUT:
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
                if page.is_closed():
                    session.request_stop("浏览器页面已关闭")
                    _logger.warning("浏览器页面已关闭，结束关键词: %s page=%d", keyword_ref, current_page)
                    progress.console.print(
                        "\n[yellow][!] 检测到浏览器被手动关闭，"
                        "正在为您安全中止并保存已抓取的数据...[/yellow]"
                    )
                    break
                _logger.warning("结果页处理异常，提前结束关键词: %s page=%d", keyword_ref, current_page, exc_info=True)
                incomplete_reason = f"第 {current_page} 页处理异常"
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

    if incomplete_reason:
        _logger.warning(
            "关键词部分完成，将在恢复时重试: %s records=%d reason=%s",
            keyword_ref,
            len(results),
            incomplete_reason,
        )
        _console.print(
            f"[yellow][!] 当前关键词仅完成部分抓取，已保留 {len(results)} 条记录；"
            "下次恢复时将从第一页重试。[/yellow]"
        )
        return make_keyword_result(
            keyword,
            keyword_index or 0,
            keyword_total or 0,
            results,
            STATUS_FAILED,
            incomplete_reason,
        )

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


def _require_page(session: ScrapeSession) -> Any:
    if session.page is None:
        raise RuntimeError("浏览器页面未初始化")
    return session.page
