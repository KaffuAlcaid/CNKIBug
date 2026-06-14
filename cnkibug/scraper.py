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
from .exporter import _save_single, _save_multi_split, _save_multi_merge

# v0.1.6: 标志位——_scrape_keyword 在 Progress 循环内截获 Ctrl+C 后置 True，
# 通知 scrape_cnki 当前关键词已有半成品数据入账，应停止并保存
_stop_requested = False


# v0.1.7: 验证监视 —— 替换旧的 _check_captcha（DOM 选择器检测 + 阻塞 input 等回车）。
# 判据改为「URL 是否进入知网安全验证页 /verify」，比一堆脆弱的 div 选择器可靠。
# 命中即把浏览器置顶(window.bring_to_front)并红框提醒，轮询等用户手动过验证
# （URL 离开 /verify 即恢复），上限 _VERIFY_WAIT_TIMEOUT 秒；超时则放弃等待，
# 交由上层保存已抓数据，绝不无限卡死。
_VERIFY_WAIT_TIMEOUT = 180  # 等待用户完成安全验证的上限（秒）
_VERIFY_NOTICE_INTERVAL = 15  # 等待期间每隔多少秒打印一次剩余时间（避免“看起来卡死”）


def _handle_verify(page) -> bool:
    """若当前处于知网安全验证页(/verify)，置顶浏览器并等待用户完成。

    返回 True 表示曾检测到验证并已处理（含等待超时）；False 表示无验证。
    返回值用于循环内 timeout 分支：True 则验证刚过、值得重等结果；False 是真超时。
    """
    if "/verify" not in page.url:
        return False

    window.bring_to_front()
    print_verify_alert()

    # v0.1.8: 轮询期间每隔 _VERIFY_NOTICE_INTERVAL 秒打印一次剩余时间。
    # 原实现是纯 time.sleep 静默忙等，又不在任何 spinner 内，最长 300s 没有任何
    # 反馈——用户看到的就是“卡死”。这里用 _console.print 打心跳（在 Progress 活动
    # 期间也安全，rich 会渲染在进度条上方），并把上限从 300s 降到 180s。
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

    # 输入关键词并发起检索，不捕获 KeyboardInterrupt
    with _console.status(
        "[bold magenta]少女祈祷中...[/bold magenta]",
        spinner="bouncingBar",
    ):
        page.fill("input.search-input", keyword, timeout=15000)
        time.sleep(random.uniform(0.5, 1.5))
        page.click("input.search-btn", timeout=15000)
        time.sleep(random.uniform(1, 2))
    _handle_verify(page)

    # v0.1.7 Bug1: 翻页循环之前，一次性判定整词是否有结果。
    # 原逻辑无结果时每页 wait_for_selector 各超时 15s，max_pages 页累计假死。
    # 改为正向探测：同时 race 结果表与“暂无数据”空提示，谁先出现谁说话。
    # 说明：no_content 只可能是“该词零命中”这一全局状态（要么第一页就有、
    # 要么永远没有），故只在进入循环前判一次；“翻到尾页”是另一回事，由循环内
    # 现有的 #PageNext 是否存在来决定，两套信号互不交叉。
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
                    # 超时先看是不是弹了安全验证；是则过验证后重等，否则当本页真超时跳过
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
                    title_el = row.query_selector("td.name a")
                    if title_el:
                        title = title_el.inner_text().strip()
                        results.append([title])
                        progress.console.print(f"  [green]→[/green] {title}")

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
    # M3：防御空关键词列表——避免 single 模式下 keywords[0] 抛 IndexError
    if not keywords:
        _console.print("[yellow][!] 未提供任何关键词，已跳过抓取。[/yellow]")
        return

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
                browser = p.chromium.launch(
                    channel="msedge",
                    headless=False,
                    args=["--start-maximized"],  # v0.1.7 置顶第一层：窗口最大化
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
                        args=["--start-maximized"],  # v0.1.7 置顶第一层：窗口最大化
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
                # M4(方案B)：备用 Chromium 启动的非预期异常——记录后重抛，不伪装成启动失败
                logging.exception("备用 Chromium 启动出现非预期异常")
                raise
        except Exception:
            # M4(方案B)：Edge 启动的非预期异常——记录后重抛，不伪装成启动失败
            logging.exception("Edge 启动出现非预期异常")
            raise

        try:
            context = browser.new_context(
                no_viewport=True,  # v0.1.7 置顶第一层：不设固定视口，让 --start-maximized 生效
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            # v0.1.7 置顶第一层 + 建议3：浏览器已弹出，打印高亮横幅引导用户切过去
            print_browser_banner()

            # 预热搜索：消耗首次 302 重定向
            # v0.1.7: 验证检测改为 _handle_verify（URL /verify 判据 + 置顶），放 status 块外
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
