from __future__ import annotations

import logging
import sys
from pathlib import Path

from ..core.events import EventSink
from ..core.runtime import RuntimePaths
from ..core.settings import ScraperSettings, get_scraper_settings
from ..workflow.runner import scrape_cnki
from ..workflow.state import (
    delete_last_task,
    describe_task,
    get_last_task_path,
    load_last_task,
)
from .console import clear_screen, safe_input
from .environment import check_env
from .errors import _popup_error
from .events import ConsoleEventSink
from .prompts import collect_task_request
from .runtime import RuntimeState, init_runtime
from .ui import _console
from ..core.version import APP_VERSION


logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

_logger = logging.getLogger("cnkibug.app")


def main(program_dir: Path) -> None:
    runtime_state = _initialize_runtime(program_dir)
    settings = get_scraper_settings(runtime_state.config)
    events = ConsoleEventSink()
    try:
        clear_screen()
        _print_banner(runtime_state)
        check_env()

        while True:
            try:
                resume_action = _handle_pending_task(runtime_state.paths, settings, events)
                if resume_action == "exit":
                    break
                if resume_action == "again":
                    clear_screen()
                    continue

                request = collect_task_request(
                    detail_txt_export=settings.detail_txt_export,
                    config_path=runtime_state.paths.config_path,
                )
                if request is None:
                    break
                _logger.info(
                    "用户选择: save_mode=%s keyword_count=%d pages=%d "
                    "include_citation=%s include_details=%s detail_txt_export=%s",
                    request.save_mode,
                    len(request.keywords),
                    request.max_pages,
                    request.include_citation,
                    request.include_details,
                    request.detail_txt_export,
                )
                scrape_cnki(
                    request.keywords,
                    max_pages=request.max_pages,
                    save_mode=request.save_mode,
                    include_citation=request.include_citation,
                    include_details=request.include_details,
                    detail_txt_export=request.detail_txt_export,
                    settings=settings,
                    paths=runtime_state.paths,
                    events=events,
                )
                _logger.info("本轮抓取完成")
                if _ask_run_again("\n[*] 本轮抓取已完成！是否清屏并开始新一轮抓取？(y/n): "):
                    clear_screen()
                    continue
                _console.print("\n[bold green]感谢使用 CNKIBug，再见！[/bold green]")
                break
            except RuntimeError as error:
                _logger.warning("运行时错误: %s", error)
                print(f"\n[!] {error}")
                if safe_input("是否返回主菜单重试？(y/n): ").strip().lower() == "y":
                    _logger.info("用户选择返回主菜单重试")
                    continue
                break
            except Exception as error:
                _logger.exception("程序遇到未知错误")
                print("\n" + "!" * 40)
                print(f"  程序遇到未知错误: {error}")
                print("!" * 40)
                break
    except KeyboardInterrupt:
        _logger.warning("用户中断，程序退出")
        print("\n[*] 用户中断，程序退出。")
    finally:
        _logger.info("程序退出")
        try:
            input("\n按 [回车键 Enter] 退出程序...")
        except (EOFError, KeyboardInterrupt):
            pass


def _initialize_runtime(program_dir: Path) -> RuntimeState:
    try:
        return init_runtime(program_dir=program_dir, app_version=APP_VERSION)
    except OSError as error:
        if sys.platform == "win32":
            _popup_error([
                "==============================================",
                " [致命错误] 无法创建运行数据目录！",
                "----------------------------------------------",
                f" 错误信息: {error}",
                "",
                " 请检查 run.py 或 CNKIBug.exe 所在目录的写入权限。",
                "==============================================",
            ])
        else:
            print(f"[FATAL] 无法创建运行数据目录: {error}")
        raise SystemExit(1) from error


def _print_banner(runtime_state: RuntimeState) -> None:
    _console.print("=" * 50)
    _console.print("  CNKI_Bug_dev  |  copyright by Kaffu_Alcaid")
    _console.print(f"  Version {APP_VERSION}")
    _console.print("=" * 50)
    _console.print("  本软件用于抓取中国知网的论文标题\n")
    _console.print("按 Ctrl+C 可随时中断并保存已抓取数据")
    _console.print("それは，幾千の夜を舞う、さくらと少女たちの物語ーーー")
    _console.print("祈祷着今后的你的人生，永远都有幸福的“魔法”相伴")
    if any(level in {"WARNING", "ERROR"} for level, _ in runtime_state.events):
        _console.print(
            f"[yellow][配置提示] 配置文件或运行目录已自动调整，"
            f"详情见日志：{runtime_state.log_path}[/yellow]"
        )


def _handle_pending_task(
    paths: RuntimePaths,
    settings: ScraperSettings,
    events: EventSink,
) -> str:
    resume_state = load_last_task(paths)
    last_task_path = get_last_task_path(paths)
    if resume_state is None:
        if last_task_path and last_task_path.exists():
            _console.print("[yellow][!] 检测到损坏的未完成任务缓存，已删除。[/yellow]")
            delete_last_task(paths)
        return "new"

    _console.print("\n[yellow][!] 检测到上次未完成的抓取任务。[/yellow]")
    _console.print(f"    {describe_task(resume_state)}")
    print("  1 -> 继续上次任务")
    print("  0 -> 删除缓存并开始新任务")
    while True:
        choice = safe_input("请输入选项（1 或 0）: ").strip()
        if choice in {"0", "1"}:
            break
        print("[!] 无效选项，请重新输入。")
    if choice == "0":
        delete_last_task(paths)
        _logger.info("用户选择删除未完成任务缓存")
        return "new"

    _logger.info("用户选择继续未完成任务")
    scrape_cnki(
        list(resume_state["keywords"]),
        int(resume_state["max_pages"]),
        str(resume_state["save_mode"]),
        resume_state=resume_state,
        settings=settings,
        paths=paths,
        events=events,
    )
    _logger.info("恢复任务结束")
    if _ask_run_again("\n[*] 本轮抓取已结束！是否清屏并开始新一轮抓取？(y/n): "):
        _logger.info("用户选择开始新一轮抓取")
        return "again"
    _console.print("\n[bold green]感谢使用 CNKIBug，再见！[/bold green]")
    return "exit"


def _ask_run_again(prompt: str) -> bool:
    return safe_input(prompt).strip().lower() == "y"
