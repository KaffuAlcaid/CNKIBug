from __future__ import annotations

import logging
import random
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from ..browser.runtime import BrowserLaunchError, create_browser_context, launch_browser
from ..cnki.details import ArticleDetailFetcher
from ..cnki.search import warmup
from ..core.events import EventSink, NULL_EVENTS
from ..core.runtime import RuntimePaths
from ..core.settings import ScraperSettings
from .finalize import finalize_task
from .keyword_run import run_keywords, start_progress
from .task import TaskContext, initialize_task


_logger = logging.getLogger("cnkibug.workflow.runner")


def scrape_cnki(
    keywords: list[str],
    max_pages: int,
    save_mode: str,
    resume_state: dict | None = None,
    include_citation: bool = False,
    include_details: bool = False,
    detail_txt_export: bool = False,
    *,
    settings: ScraperSettings,
    paths: RuntimePaths,
    events: EventSink = NULL_EVENTS,
) -> None:
    if not keywords:
        events.emit("message", text="[!] 未提供任何关键词，已跳过抓取。", level="warning")
        return

    task = initialize_task(
        keywords,
        max_pages,
        save_mode,
        resume_state,
        include_citation,
        include_details,
        detail_txt_export,
        settings,
        paths,
        events,
    )
    _logger.info(
        "抓取任务开始: keyword_count=%d max_pages=%d save_mode=%s "
        "include_citation=%s include_details=%s detail_txt_export=%s",
        len(task.keywords),
        task.max_pages,
        task.save_mode,
        task.include_citation,
        task.include_details,
        task.detail_txt_export,
    )

    with sync_playwright() as playwright:
        try:
            _open_browser(task, playwright)
            _warm_up(task)
            start_progress(task)
            run_keywords(task)
        except KeyboardInterrupt:
            task.session.request_stop("用户中断")
            _logger.warning("抓取任务被用户中断")
            task.events.emit(
                "message",
                text="[!] 用户中断，正在保存已抓取的数据...",
                level="warning",
            )
        except BrowserLaunchError as error:
            task.session.request_stop("浏览器启动失败")
            _logger.error("浏览器启动失败: %s", error)
            task.events.emit("browser_launch_failed", error=str(error))
        except RuntimeError as error:
            task.session.request_stop("运行时错误")
            _logger.error("抓取任务运行时错误: %s", error)
            task.events.emit("message", text=f"[x] 运行时错误: {error}", level="error")
        except PlaywrightError as error:
            task.session.request_stop("浏览器运行错误")
            _logger.error("浏览器运行错误: %s", error)
            task.events.emit("message", text=f"[x] 浏览器运行错误: {error}", level="error")
        finally:
            finalize_task(task)


def _open_browser(task: TaskContext, playwright) -> None:
    launch = launch_browser(playwright, task.events)
    task.browser = launch.browser
    task.browser_context = create_browser_context(
        task.browser,
        task.settings,
        task.paths,
    )
    task.session.page = task.browser_context.new_page()
    if task.include_details:
        task.detail_fetcher = ArticleDetailFetcher(
            task.browser_context,
            task.settings,
            task.events,
        )
    task.events.emit("browser_ready")


def _warm_up(task: TaskContext) -> None:
    warmup_ok = warmup(task.session, task.settings)
    _logger.info(
        "预热结果: ok=%s stop_requested=%s",
        warmup_ok,
        task.session.stop_requested,
    )
    if not warmup_ok and not task.session.stop_requested:
        task.events.emit(
            "message",
            text="[!] 预热未成功，可能网络异常或知网暂时不可达。",
            level="warning",
        )
        if not task.events.confirm("是否仍尝试继续抓取？(y/n): "):
            task.session.request_stop("预热失败后用户选择停止")
            _logger.warning("用户选择在预热失败后停止抓取")
        else:
            _logger.info("用户选择在预热失败后继续抓取")
    time.sleep(random.uniform(2, 4))
