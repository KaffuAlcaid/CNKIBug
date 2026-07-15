#是的孩子们，超长屎山scraper.py终于被我肢解了
#作用：编排多关键词抓取、断点续抓、保存和收尾流程
from __future__ import annotations

import logging
import random
import time
from datetime import datetime

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Error as PlaywrightError

from .browser_runtime import create_browser_context, launch_browser
from .exporter import SaveResult, save_all
from .keyword_scraper import scrape_keyword, warmup
from .scrape_logging import keyword_log_ref
from .scrape_report import (
    STATUS_FAILED,
    STATUS_STOPPED,
    TaskReport,
    build_task_report,
    make_keyword_result,
    print_task_report,
    save_task_report,
)
from .scrape_session import ScrapeSession
from .session_cache import save_cookie_state
from .settings import get_scraper_settings
from .task_state import (
    completed_results,
    delete_last_task,
    keyword_checkpoint,
    make_task_state,
    mark_keyword_done,
    mark_keyword_progress,
    save_last_task,
    stored_results,
    task_is_finished,
)
from .ui import _console, print_browser_banner


_logger = logging.getLogger("cnkibug.scrape_workflow")


def _save_task_state(task_state: dict, context: str) -> bool:
    if save_last_task(task_state) is not None:
        return True
    _logger.error("断点状态未写入，继续尝试保存抓取结果: context=%s", context)
    _console.print(
        "[bold yellow][!] 无法更新断点文件；若程序现在退出，本轮进度可能无法恢复。"
        "程序将继续尝试保存结果文件。[/bold yellow]"
    )
    return False


def scrape_cnki(
    keywords: list[str],
    max_pages: int,
    save_mode: str,
    resume_state: dict | None = None,
    include_citation: bool = False,
) -> None:
    """
    save_mode:
      'single'       -> 单关键词，保存为 cnki_titles_关键词.xlsx
      'single_csv'   -> 单关键词，保存为 cnki_titles_关键词.csv
      'multi_split'  -> 多关键词分文件保存
      'multi_merge'  -> 多关键词单文件多 Sheet 保存
      'multi_csv'    -> 多关键词单文件 CSV 保存
    """
    settings = get_scraper_settings()

    if not keywords:
        _console.print("[yellow][!] 未提供任何关键词，已跳过抓取。[/yellow]")
        return

    if resume_state is not None:
        keywords = list(resume_state["keywords"])
        max_pages = int(resume_state["max_pages"])
        save_mode = str(resume_state["save_mode"])
        include_citation = bool(resume_state.get("include_citation", False))
        ts = str(resume_state["ts"])
        task_state = resume_state
        all_results: dict[str, list] = stored_results(task_state)
        terminal_results = completed_results(task_state)
        _console.print(
            f"[dim][*] 已载入上次未完成任务："
            f"共 {len(keywords)} 个关键词，已完成 {len(terminal_results)} 个。[/dim]"
        )
        _logger.info(
            "恢复未完成任务: keyword_count=%d completed=%d stored_results=%d "
            "max_pages=%d save_mode=%s include_citation=%s ts=%s",
            len(keywords),
            len(terminal_results),
            len(all_results),
            max_pages,
            save_mode,
            include_citation,
            ts,
        )
    else:
        all_results = {}
        terminal_results = {}
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        task_state = make_task_state(
            keywords,
            max_pages,
            save_mode,
            ts,
            include_citation=include_citation,
        )
        _save_task_state(task_state, "创建新任务")

    report = TaskReport(
        total_keywords=len(keywords),
        include_citation=include_citation,
    )
    completed_state = task_state.get("completed", {})
    if isinstance(completed_state, dict):
        for idx, keyword in enumerate(keywords):
            item = completed_state.get(keyword)
            if not isinstance(item, dict) or keyword not in terminal_results:
                continue
            report.add(make_keyword_result(
                keyword,
                idx + 1,
                len(keywords),
                item.get("records", []),
                str(item.get("status", STATUS_FAILED)),
                str(item.get("reason", "")),
            ))

    session = ScrapeSession()
    _logger.info(
        "抓取任务开始: keyword_count=%d max_pages=%d save_mode=%s include_citation=%s",
        len(keywords),
        max_pages,
        save_mode,
        include_citation,
    )

    with sync_playwright() as p:
        context = None
        browser = None

        try:
            browser = launch_browser(p)
            context = create_browser_context(browser, settings)
            page = context.new_page()
            session.page = page

            print_browser_banner()

            warmup_ok = warmup(session, settings)
            _logger.info("预热结果: ok=%s stop_requested=%s", warmup_ok, session.stop_requested)
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
                keyword_ref = keyword_log_ref(
                    keyword,
                    idx + 1,
                    len(keywords),
                    include_keyword=settings.log_keywords,
                )
                if keyword in terminal_results:
                    _logger.info(
                        "关键词已在 last_task 中完成，跳过: %s",
                        keyword_ref,
                    )
                    continue
                completed_page, checkpoint_records = keyword_checkpoint(task_state, keyword)
                if completed_page > max_pages:
                    _logger.warning(
                        "关键词页级断点超过请求页数，已从第一页重抓: %s completed_page=%d max_pages=%d",
                        keyword_ref,
                        completed_page,
                        max_pages,
                    )
                    completed_page = 0
                    checkpoint_records = []
                if keyword in all_results:
                    if completed_page:
                        _logger.info(
                            "关键词从页级断点恢复: %s completed_page=%d resume_page=%d records=%d",
                            keyword_ref,
                            completed_page,
                            completed_page + 1,
                            len(checkpoint_records),
                        )
                        _console.print(
                            f"[dim][*] 关键词「{keyword}」将从第 {completed_page + 1} 页继续，"
                            f"已保留 {len(checkpoint_records)} 条记录。[/dim]"
                        )
                    else:
                        _logger.warning(
                            "关键词存在无页码的失败或中止结果，将从第一页重试: %s records=%d",
                            keyword_ref,
                            len(all_results[keyword]),
                        )
                        _console.print(
                            f"[dim][*] 关键词「{keyword}」上次未完整完成，将从第一页重新抓取。[/dim]"
                        )
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

                historical_records = list(all_results.get(keyword, []))

                def save_page_checkpoint(completed: int, records: list[list[str]]) -> None:
                    all_results[keyword] = list(records)
                    mark_keyword_progress(task_state, keyword, completed, records)
                    _save_task_state(task_state, f"关键词第 {completed} 页检查点")
                    if completed:
                        _logger.info(
                            "页级断点已保存: %s completed_page=%d records=%d",
                            keyword_ref,
                            completed,
                            len(records),
                        )
                    else:
                        _logger.warning(
                            "页级恢复已回退到第一页: %s preserved_records=%d",
                            keyword_ref,
                            len(records),
                        )

                try:
                    keyword_result = scrape_keyword(
                        session,
                        keyword,
                        max_pages,
                        settings,
                        idx + 1,
                        len(keywords),
                        start_page=completed_page + 1,
                        initial_records=checkpoint_records if completed_page else [],
                        on_page_complete=save_page_checkpoint,
                        include_citation=include_citation,
                    )
                except PlaywrightTimeoutError as e:
                    _logger.warning(
                        "关键词页面等待超时，跳过: %s error=%s",
                        keyword_ref,
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
                        keyword_ref,
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
                        keyword_ref,
                    )
                    keyword_result = make_keyword_result(
                        keyword,
                        idx + 1,
                        len(keywords),
                        [],
                        STATUS_STOPPED,
                        "用户中断",
                    )

                previous_records = historical_records
                if keyword_result.status in {STATUS_FAILED, STATUS_STOPPED} and previous_records:
                    current_count = len(keyword_result.records)
                    merged_records = list(previous_records)
                    seen_records = {tuple(record) for record in merged_records}
                    for record in keyword_result.records:
                        record_key = tuple(record)
                        if record_key not in seen_records:
                            seen_records.add(record_key)
                            merged_records.append(record)
                    keyword_result.records = merged_records
                    _logger.warning(
                        "关键词重试仍未完整完成，已合并保留部分结果: %s previous=%d current=%d merged=%d",
                        keyword_ref,
                        len(previous_records),
                        current_count,
                        len(merged_records),
                    )
                all_results[keyword] = keyword_result.records
                report.add(keyword_result)
                mark_keyword_done(task_state, keyword_result)
                _save_task_state(task_state, "关键词结果更新")
                _logger.info(
                    "关键词结果已记录: %s status=%s records=%d stop_requested=%s",
                    keyword_ref,
                    keyword_result.status,
                    len(keyword_result.records),
                    session.stop_requested,
                )
                try:
                    save_result = save_all(
                        save_mode,
                        keywords,
                        all_results,
                        ts,
                        announce=False,
                        include_citation=include_citation,
                    )
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
                except KeyboardInterrupt:
                    session.request_stop("用户在增量保存期间中断")
                    _logger.warning("用户在增量保存期间中断")
                    raise
                except Exception:
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
        except PlaywrightError as e:
            session.request_stop("浏览器运行错误")
            _logger.error("浏览器运行错误: %s", e)
            _console.print(f"[red][x] 浏览器运行错误: {e}[/red]")
        finally:
            report.stopped = session.stop_requested
            report.verify_timeout = session.verify_timeout
            if context:
                try:
                    save_cookie_state(context, settings.session_cache_enabled)
                    context.close()
                    _logger.info("浏览器上下文已关闭")
                except Exception:
                    _logger.warning("浏览器上下文关闭失败", exc_info=True)
            if browser:
                try:
                    browser.close()
                    _logger.info("浏览器已关闭")
                except Exception:
                    _logger.warning("浏览器关闭失败", exc_info=True)

            final_save_failed = False
            save_result = SaveResult()
            try:
                _logger.info(
                    "最终保存开始: keyword_count=%d total_records=%d stop_requested=%s",
                    len(keywords),
                    sum(len(items) for items in all_results.values()),
                    session.stop_requested,
                )
                save_result = save_all(
                    save_mode,
                    keywords,
                    all_results,
                    ts,
                    announce=True,
                    include_citation=include_citation,
                )
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
            except KeyboardInterrupt:
                final_save_failed = True
                session.request_stop("用户在最终保存期间中断")
                _logger.warning("用户在最终保存期间中断，保留断点状态")
                _console.print("\n[bold yellow][!] 最终保存被中断，已保留断点状态。[/bold yellow]")
            except Exception as save_err:
                final_save_failed = True
                _logger.exception("最终保存失败")
                _console.print("\n[bold red][x] 最终保存失败！[/bold red]")
                _console.print(f"[red]错误信息：{save_err}[/red]")
                _console.print("[yellow]请关闭已打开的同名结果文件，并检查桌面或程序目录写入权限。[/yellow]")

            result_export_failed = final_save_failed
            try:
                report_payload = build_task_report(
                    report,
                    all_results,
                    task_state,
                    keywords,
                    max_pages,
                    save_mode,
                    ts,
                    save_result.saved_paths,
                    result_export_failed,
                    include_citation=include_citation,
                )
                report_path = save_task_report(report_payload, ts)
                if report_path:
                    _console.print(f"[dim][*] JSON 任务报告已保存至：{report_path}[/dim]")
                else:
                    final_save_failed = True
                    _console.print("[red][x] JSON 任务报告保存失败。[/red]")
            except KeyboardInterrupt:
                final_save_failed = True
                session.request_stop("用户在 JSON 任务报告保存期间中断")
                _logger.warning("JSON 任务报告保存被用户中断")
            except Exception:
                final_save_failed = True
                _logger.exception("JSON 任务报告生成失败")
                _console.print("[red][x] JSON 任务报告生成失败，详情见日志。[/red]")

            try:
                print_task_report(report, all_results)
            except KeyboardInterrupt:
                session.request_stop("用户在任务摘要输出期间中断")
                _logger.warning("用户在任务摘要输出期间中断，继续保存断点状态")
            except Exception:
                _logger.exception("任务摘要输出失败")

            if task_is_finished(task_state) and not final_save_failed:
                delete_last_task()
            else:
                _save_task_state(task_state, "任务收尾")
