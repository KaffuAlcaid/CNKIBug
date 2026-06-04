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

from .ui import _console
from .errors import _popup_error
from .exporter import _save_single, _save_multi_split, _save_multi_merge

# v0.1.6: 标志位——_scrape_keyword 在 Progress 循环内截获 Ctrl+C 后置 True，
# 通知 scrape_cnki 当前关键词已有半成品数据入账，应停止并保存
_stop_requested = False


def _check_captcha(page, progress: Progress | None = None) -> bool:
    captcha_selectors = [
        "div.captcha",
        "div#captcha",
        "iframe[src*='captcha']",
        "div.slide-verify",
        "div.passcode-area",
    ]
    # 用 progress.console.print 输出，不调用 stop()/start()，避免计时器归零
    out = progress.console.print if progress else _console.print
    for sel in captcha_selectors:
        if page.query_selector(sel):
            out("\n" + "!" * 50)
            out("  [bold yellow][!] 检测到人机验证！请切换到浏览器窗口处理：[/bold yellow]")
            out("  · 如果是【滑块验证】：按住滑块向右拖动到底")
            out("  · 如果是【图片验证码】：按提示点击或输入字符")
            out("  · 完成验证后，回到此窗口按 [回车键] 继续...")
            out("!" * 50)
            input()
            return True
    return False


def _scrape_keyword(page, keyword: str, max_pages: int) -> list:
    global _stop_requested
    results = []
    _console.print(f"\n[bold][*][/bold] 目标关键词：[bold cyan]{keyword}[/bold cyan]")

    # v0.1.5: 导航步骤拆分为独立 status 块，移除 KeyboardInterrupt 捕获使其向上传递
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
    _check_captcha(page)

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
    _check_captcha(page)

    # 输入关键词并发起检索，不捕获 KeyboardInterrupt
    with _console.status(
        "[bold magenta]少女祈祷中...[/bold magenta]",
        spinner="bouncingBar",
    ):
        page.fill("input.search-input", keyword)
        time.sleep(random.uniform(0.5, 1.5))
        page.click("input.search-btn")
        time.sleep(random.uniform(1, 2))
    _check_captcha(page)

    # v0.1.5: Progress 改为 bouncingBar + magenta 配色
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
            # 整个循环体统一捕获，任意位置异常都能安全退出并保留已有数据
            # v0.1.5: 移除对 KeyboardInterrupt 的捕获，使其向上传递至 scrape_cnki
            try:
                progress.update(task, description=f"第 [bold]{current_page}[/bold] / {max_pages} 页")
                try:
                    page.wait_for_selector(
                        "table.result-table-list tbody tr", timeout=15000
                    )
                except PlaywrightTimeoutError:
                    had_captcha = _check_captcha(page, progress)
                    if had_captcha:
                        page.wait_for_selector(
                            "table.result-table-list tbody tr", timeout=15000
                        )
                    else:
                        progress.console.print(
                            f"[red][x] 第 {current_page} 页等待超时且未检测到验证码，跳过本页。[/red]"
                        )
                        progress.advance(task)
                        continue

                time.sleep(random.uniform(2, 5))
                _check_captcha(page, progress)

                rows = page.query_selector_all("table.result-table-list tbody tr")
                for row in rows:
                    title_el = row.query_selector("td.name a")
                    if title_el:
                        title = title_el.inner_text().strip()
                        results.append([title])
                        progress.console.print(f"  [green]→[/green] {title}")

                progress.advance(task)

                if current_page < max_pages:
                    next_btn = page.query_selector("a#PageNext")
                    if next_btn:
                        next_btn.click()
                        time.sleep(random.uniform(4, 8))
                        _check_captcha(page, progress)
                    else:
                        progress.console.print(
                            "[yellow][!] 没找到下一页按钮，可能已到最后一页。[/yellow]"
                        )
                        break

            except PlaywrightError:
                # 不调用 progress.stop()，让 with 块的 __exit__ 统一处理
                # 避免手动 stop + __exit__ 二次 stop 引发内部异常
                progress.console.print(
                    "\n[yellow][!] 检测到浏览器被手动关闭或强制中断，"
                    "正在为您安全中止并保存已抓取的数据...[/yellow]"
                )
                break

            # v0.1.6: 在循环内截获 Ctrl+C，results 中已有的数据完好保留
            # 同样不调用 progress.stop()——让 __exit__ 统一处理，消息移到 with 块外
            except KeyboardInterrupt:
                _stop_requested = True
                break

    # with Progress() 已正常退出，此处打印不会被进度条覆盖
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
    all_results: dict[str, list] = {}

    # v0.1.6: 每次调用前重置标志位，防止同进程多轮运行时状态残留
    global _stop_requested
    _stop_requested = False

    with sync_playwright() as p:
        browser = None

        try:
            # v0.1.5: 浏览器启动包入 status 动画
            with _console.status(
                "[bold magenta]少女祈祷中...[/bold magenta]",
                spinner="bouncingBar",
            ):
                browser = p.chromium.launch(channel="msedge", headless=False)
            _console.print("[dim][*] 已启动 Microsoft Edge[/dim]")
        except Exception as _e1:
            _console.print(f"[yellow][!] Edge 启动失败 ({_e1})，尝试备用 Chromium...[/yellow]")
            try:
                with _console.status(
                    "[bold magenta]少女祈祷中...[/bold magenta]",
                    spinner="bouncingBar",
                ):
                    browser = p.chromium.launch(headless=False)
                _console.print("[dim][*] 已启动备用 Chromium 浏览器[/dim]")
            except Exception as _e2:
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

        try:
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            # 预热搜索：消耗首次 302 重定向
            # v0.1.5: 将 _check_captcha 调用移到 status 块外，避免 input() 在 Live 上下文中运行
            dummy_keyword = "焊接"
            try:
                with _console.status(
                    "[bold magenta]少女祈祷中...[/bold magenta]",
                    spinner="bouncingBar",
                ):
                    page.goto("https://www.cnki.net/", timeout=30000)
                    page.wait_for_load_state("domcontentloaded", timeout=20000)
                _check_captcha(page)
                with _console.status(
                    "[bold magenta]少女祈祷中...[/bold magenta]",
                    spinner="bouncingBar",
                ):
                    page.goto("https://kns.cnki.net/kns8s/", timeout=30000)
                    page.wait_for_load_state("load", timeout=20000)
                    page.fill("input.search-input", dummy_keyword)
                    time.sleep(random.uniform(0.5, 1.5))
                    page.click("input.search-btn")
                    page.wait_for_selector("table.result-table-list tbody tr", timeout=15000)
                _check_captcha(page)
                _console.print("[dim][*] 预热完成，开始正式抓取。[/dim]")
            except (PlaywrightTimeoutError, PlaywrightError) as warmup_err:
                _console.print(f"[yellow][!] 预热搜索未完全成功 ({warmup_err})，继续正式抓取。[/yellow]")
            time.sleep(random.uniform(2, 4))

            for idx, keyword in enumerate(keywords):
                if idx > 0:
                    wait_sec = random.uniform(5, 8)
                    # v0.1.5: 冷却等待包入 status 动画
                    with _console.status(
                        f"[dim]少女祈祷中... 等待 {wait_sec:.1f} 秒[/dim]",
                        spinner="dots",
                    ):
                        time.sleep(wait_sec)

                # v0.1.6: results 在 try 外初始化，无论何种异常都能安全写入 all_results
                results = []
                try:
                    results = _scrape_keyword(page, keyword, max_pages)
                except PlaywrightTimeoutError as e:
                    _console.print(f"[red][x] 关键词「{keyword}」页面等待超时，跳过: {e}[/red]")
                except PlaywrightError as e:
                    _console.print(f"[yellow][!] 浏览器连接已断开，停止后续关键词抓取: {e}[/yellow]")
                    _stop_requested = True
                except KeyboardInterrupt:
                    # 导航阶段的 Ctrl+C（Progress 循环内的已在底层截获）
                    # results 此时为空，_stop_requested 将在此处置 True
                    _stop_requested = True

                # 无论正常返回、超时、浏览器断开、还是导航阶段中断，都记录当前结果
                all_results[keyword] = results

                if _stop_requested:
                    break

        # v0.1.5: 捕获 Ctrl+C，打印提示后不重新抛出
        # finally 负责关闭浏览器，执行流随后自然落入函数末尾的保存逻辑
        except KeyboardInterrupt:
            _console.print(
                "\n[bold yellow][!] 用户中断，正在保存已抓取的数据...[/bold yellow]"
            )
        except RuntimeError as e:
            _console.print(f"[red][x] 运行时错误: {e}[/red]")
        finally:
            if browser:
                # v0.1.6: 用 BaseException 兜底——Ctrl+C 是 BaseException 子类，
                # except Exception 无法捕获，会从 finally 逃出并跳过保存逻辑
                try:
                    browser.close()
                except BaseException:
                    pass

    if save_mode == "single":
        _save_single(keywords[0], all_results.get(keywords[0], []))
    elif save_mode == "multi_split":
        _save_multi_split(all_results)
    elif save_mode == "multi_merge":
        _save_multi_merge(all_results)
