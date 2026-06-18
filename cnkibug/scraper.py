"""核心抓取逻辑 —— 验证码检测、单关键词抓取、多关键词编排。

==== 不可轻动的部分 ====
中断处理（KeyboardInterrupt / PlaywrightError / BaseException）经 v0.1.5、
v0.1.6 多版本迭代调校：_scrape_keyword 与 scrape_cnki 通过模块级
_stop_requested 协作，finally 用 BaseException 兜底以免 Ctrl+C 逃出导致
跳过保存。搬入时保持原相对位置与读写关系，未作任何逻辑修改。
"""

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
from .ui import _console, print_browser_banner, print_verify_alert
from .errors import _popup_error
from .exporter import save_all

_stop_requested = False


_VERIFY_WAIT_TIMEOUT = 180
_VERIFY_NOTICE_INTERVAL = 15


def _handle_verify(page) -> bool:
    """若当前处于知网安全验证页(/verify)，置顶浏览器并等待用户完成。

    返回 True 表示曾检测到验证并已处理（含等待超时）；False 表示无验证。
    返回值用于循环内 timeout 分支：True 则验证刚过、值得重等结果；False 是真超时。
    """
    if "/verify" not in page.url:
        return False

    window.bring_to_front()
    print_verify_alert()

    waited = 0.0
    interval = 1.0
    next_notice = float(_VERIFY_NOTICE_INTERVAL)
    while "/verify" in page.url:
        if waited >= _VERIFY_WAIT_TIMEOUT:
            _console.print("[yellow][!] 等待安全验证超时，将保存已抓取的数据。[/yellow]")
            return True
        if waited >= next_notice:
            remaining = int(_VERIFY_WAIT_TIMEOUT - waited)
            _console.print(
                f"[dim][*] 仍在等待手动完成安全验证…（剩余约 {remaining} 秒，完成后自动继续）[/dim]"
            )
            next_notice += _VERIFY_NOTICE_INTERVAL
        time.sleep(interval)
        waited += interval
    _console.print("[green][*] 验证已通过，继续抓取。[/green]")
    return True


def _wait_first_row_changed(page, old_href: str, timeout: int = 15000) -> bool:
    """等待结果列表首行详情链接变为与 old_href 不同的值，用于确认 AJAX 翻页
    真正完成。超时（首行未变 / 无首行）返回 False，不抛异常。"""
    try:
        page.wait_for_function(
            "(oldHref) => {"
            " const a = document.querySelector("
            "'table.result-table-list tbody tr td.name a');"
            " return a && a.getAttribute('href')"
            " && a.getAttribute('href') !== oldHref; }",
            arg=old_href,
            timeout=timeout,
        )
        return True
    except PlaywrightTimeoutError:
        return False


def _scrape_keyword(page, keyword: str, max_pages: int) -> list:
    global _stop_requested
    results = []
    # 本关键词内跨页去重用：记录已收录文献的去重 key（详情 href 优先）。
    seen: set = set()
    _console.print(f"\n[bold][*][/bold] 目标关键词：[bold cyan]{keyword}[/bold cyan]")

    try:
        with _console.status(
            "[bold magenta]少女祈祷中...[/bold magenta]",
            spinner="bouncingBar",
        ):
            page.goto("https://www.cnki.net/", timeout=30000)
            page.wait_for_load_state("domcontentloaded", timeout=20000)
    except PlaywrightTimeoutError:
        _console.print("[yellow][!] 预热请求超时，跳过该关键词。[/yellow]")
        return results
    except PlaywrightError as e:
        _console.print(f"[yellow][!] 预热请求失败: {e}，跳过该关键词。[/yellow]")
        return results
    _handle_verify(page)

    try:
        with _console.status(
            "[bold magenta]少女祈祷中...[/bold magenta]",
            spinner="bouncingBar",
        ):
            page.goto("https://kns.cnki.net/kns8s/", timeout=30000)
            page.wait_for_load_state("load", timeout=20000)
    except PlaywrightTimeoutError:
        _console.print("[yellow][!] 检索页加载超时，跳过该关键词。[/yellow]")
        return results
    except PlaywrightError as e:
        _console.print(f"[yellow][!] 检索页加载失败: {e}，跳过该关键词。[/yellow]")
        return results
    _handle_verify(page)

    with _console.status(
        "[bold magenta]少女祈祷中...[/bold magenta]",
        spinner="bouncingBar",
    ):
        page.fill("input.search-input", keyword, timeout=15000)
        time.sleep(random.uniform(0.5, 1.5))
        page.click("input.search-btn", timeout=15000)
        time.sleep(random.uniform(1, 2))
    _handle_verify(page)

    try:
        outcome = page.wait_for_function(
            """() => {
                if (document.querySelector('table.result-table-list tbody tr')) return 'has_results';
                if (document.querySelector('#briefBox p.no-content')) return 'no_content';
                return false;
            }""",
            timeout=15000,
        ).json_value()
    except PlaywrightTimeoutError:
        _console.print(f"[yellow][!] 关键词「{keyword}」结果加载超时，跳过。[/yellow]")
        return results

    if outcome == "no_content":
        _console.print(f"[yellow][!] 知网无「{keyword}」的检索结果，跳过。[/yellow]")
        return results

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

        for current_page in range(1, max_pages + 1):
            try:
                progress.update(task, description=f"第 [bold]{current_page}[/bold] / {max_pages} 页")
                try:
                    page.wait_for_selector(
                        "table.result-table-list tbody tr", timeout=15000
                    )
                except PlaywrightTimeoutError:
                    handled = _handle_verify(page)
                    if handled:
                        page.wait_for_selector(
                            "table.result-table-list tbody tr", timeout=15000
                        )
                    else:
                        progress.console.print(
                            f"[red][x] 第 {current_page} 页等待超时（非验证），跳过本页。[/red]"
                        )
                        progress.advance(task)
                        continue

                time.sleep(random.uniform(2, 5))
                _handle_verify(page)

                rows = page.query_selector_all("table.result-table-list tbody tr")
                for row in rows:
                    try:
                        title_el = row.query_selector("td.name a")
                        if not title_el:
                            continue
                        title = title_el.inner_text().strip()

                        # 去重 key：优先用详情链接 href（知网每篇文献唯一），
                        # 取不到再回退 (标题, 来源, 日期) 三元组。
                        href = title_el.get_attribute("href")

                        author_parts = []
                        for a in row.query_selector_all("td.author a.KnowledgeNetLink"):
                            name = a.text_content().strip()
                            if name:
                                author_parts.append(name)
                        authors = "; ".join(author_parts)

                        source_el = row.query_selector("td.source")
                        source = (
                            " ".join(source_el.text_content().split())
                            if source_el else ""
                        )

                        date_el = row.query_selector("td.date")
                        date = date_el.text_content().strip() if date_el else ""

                        dedup_key = href if href else (title, source, date)
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)

                        results.append([title, authors, source, date])
                        progress.console.print(f"  [green]→[/green] {title}")
                    except PlaywrightError:
                        # 单行解析失败（元素 stale / 被回收等）只跳过这一行，
                        # 不再让整页/整个关键词中断。
                        continue

                progress.advance(task)

                if current_page < max_pages:
                    next_btn = page.query_selector("a#PageNext")
                    if next_btn:
                        next_btn.click(timeout=15000)
                        time.sleep(random.uniform(4, 8))
                        _handle_verify(page)
                    else:
                        progress.console.print(
                            "[yellow][!] 没找到下一页按钮，可能已到最后一页。[/yellow]"
                        )
                        break

            except PlaywrightError:
                # 只有浏览器/页面真正关闭才中止整个关键词；其它操作异常
                # （翻页点击失败、临时 stale 等）只跳过本页继续。
                if page.is_closed():
                    progress.console.print(
                        "\n[yellow][!] 检测到浏览器被手动关闭，"
                        "正在为您安全中止并保存已抓取的数据...[/yellow]"
                    )
                    break
                progress.console.print(
                    f"[yellow][!] 第 {current_page} 页处理异常，跳过本页继续。[/yellow]"
                )
                progress.advance(task)
                continue

            except KeyboardInterrupt:
                _stop_requested = True
                break

    if _stop_requested:
        _console.print("[yellow][!] 用户中断，正在保存已抓取的数据...[/yellow]")

    return results


def scrape_cnki(keywords: list[str], max_pages: int, save_mode: str):
    """
    save_mode:
      'single'       -> 单关键词，保存为 cnki_titles_关键词.xlsx
      'multi_split'  -> 多关键词分文件保存
      'multi_merge'  -> 多关键词单文件多 Sheet 保存
    """
    if not keywords:
        _console.print("[yellow][!] 未提供任何关键词，已跳过抓取。[/yellow]")
        return

    all_results: dict[str, list] = {}
    # 所有增量落盘与最终保存共用同一时间戳，保证写的是同一批文件（覆盖而非堆积）。
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    global _stop_requested
    _stop_requested = False

    with sync_playwright() as p:
        browser = None

        try:
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
        except PlaywrightError as _e1:
            _console.print(f"[yellow][!] Edge 启动失败 ({_e1})，尝试备用 Chromium...[/yellow]")
            try:
                with _console.status(
                    "[bold magenta]少女祈祷中...[/bold magenta]",
                    spinner="bouncingBar",
                ):
                    browser = p.chromium.launch(
                        headless=False,
                        args=["--start-maximized"],
                    )
                _console.print("[dim][*] 已启动备用 Chromium 浏览器[/dim]")
            except PlaywrightError as _e2:
                if sys.platform == "win32":
                    _popup_error([
                        "==============================================",
                        " [错误] 浏览器启动失败！",
                        "----------------------------------------------",
                        " 程序找到了 Edge，但无法正常启动它。",
                        "",
                        " 可能原因：",
                        "   · Edge 浏览器文件损坏",
                        "   · 系统权限不足",
                        "   · 安全软件阻止了浏览器启动",
                        "",
                        " 建议：",
                        "   1. 重新安装 Microsoft Edge",
                        "      https://www.microsoft.com/zh-cn/edge/download",
                        "   2. 以管理员身份运行本程序",
                        "   3. 暂时关闭杀毒软件后重试",
                        "==============================================",
                    ])
                else:
                    _console.print(f"[red][FATAL] 浏览器启动失败: {_e2}[/red]")
                raise RuntimeError(f"浏览器启动彻底失败: {_e2}")
            except Exception:
                logging.exception("备用 Chromium 启动出现非预期异常")
                raise
        except Exception:
            logging.exception("Edge 启动出现非预期异常")
            raise

        try:
            context = browser.new_context(
                no_viewport=True,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            print_browser_banner()

            dummy_keyword = "焊接"
            try:
                with _console.status(
                    "[bold magenta]少女祈祷中...[/bold magenta]",
                    spinner="bouncingBar",
                ):
                    page.goto("https://www.cnki.net/", timeout=30000)
                    page.wait_for_load_state("domcontentloaded", timeout=20000)
                _handle_verify(page)
                with _console.status(
                    "[bold magenta]少女祈祷中...[/bold magenta]",
                    spinner="bouncingBar",
                ):
                    page.goto("https://kns.cnki.net/kns8s/", timeout=30000)
                    page.wait_for_load_state("load", timeout=20000)
                    page.fill("input.search-input", dummy_keyword, timeout=15000)
                    time.sleep(random.uniform(0.5, 1.5))
                    page.click("input.search-btn", timeout=15000)
                    page.wait_for_selector("table.result-table-list tbody tr", timeout=15000)
                _handle_verify(page)
                _console.print("[dim][*] 预热完成，开始正式抓取。[/dim]")
            except (PlaywrightTimeoutError, PlaywrightError) as warmup_err:
                _console.print(f"[yellow][!] 预热搜索未完全成功 ({warmup_err})，继续正式抓取。[/yellow]")
            time.sleep(random.uniform(2, 4))

            for idx, keyword in enumerate(keywords):
                if idx > 0:
                    wait_sec = random.uniform(5, 8)
                    with _console.status(
                        f"[dim]少女祈祷中... 等待 {wait_sec:.1f} 秒[/dim]",
                        spinner="dots",
                    ):
                        time.sleep(wait_sec)

                results = []
                try:
                    results = _scrape_keyword(page, keyword, max_pages)
                except PlaywrightTimeoutError as e:
                    _console.print(f"[red][x] 关键词「{keyword}」页面等待超时，跳过: {e}[/red]")
                except PlaywrightError as e:
                    _console.print(f"[yellow][!] 浏览器连接已断开，停止后续关键词抓取: {e}[/yellow]")
                    _stop_requested = True
                except KeyboardInterrupt:
                    _stop_requested = True

                all_results[keyword] = results

                # 增量落盘：每抓完一个关键词立即写盘，避免后续关键词出错或
                # 浏览器被关导致已抓数据丢失。静默写（announce=False），失败
                # 仅记日志、不打断抓取——最终保存时会再写一次并给出完整反馈。
                try:
                    save_all(save_mode, keywords, all_results, ts, announce=False)
                    if len(keywords) > 1:
                        _console.print(
                            f"[dim][*] 已落盘阶段性结果"
                            f"（已完成 {idx + 1}/{len(keywords)} 个关键词）[/dim]"
                        )
                except BaseException: # noqa
                    logging.exception("增量保存失败")

                if _stop_requested:
                    break

        except KeyboardInterrupt:
            _console.print(
                "\n[bold yellow][!] 用户中断，正在保存已抓取的数据...[/bold yellow]"
            )
        except RuntimeError as e:
            _console.print(f"[red][x] 运行时错误: {e}[/red]")
        finally:
            if browser:
                try:
                    browser.close()
                except BaseException: # noqa
                    pass

            # 最终保存 + 打印汇总。放在 finally 内、以 BaseException 兜底，
            # 确保正常完成、关键词中途出错、以及保存阶段的二次 Ctrl+C 都不丢数据。
            # 与增量落盘共用同一 ts，写的是同一批文件（幂等覆盖）。
            try:
                save_all(save_mode, keywords, all_results, ts, announce=True)
            except BaseException: # noqa
                logging.exception("最终保存失败")
