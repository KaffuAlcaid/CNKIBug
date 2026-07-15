import sys
import os
import logging

from cnkibug.errors import _popup_error
from cnkibug.version import APP_VERSION


def _handle_import_error(_err: ImportError) -> None:
    if sys.platform == "win32":
        _popup_error([
            "==============================================",
            " [致命错误] 程序核心组件加载失败！",
            "----------------------------------------------",
            f" 缺失模块: {_err}",
            "",
            " 可能原因：您运行的不是完整的 exe 文件，",
            " 或 exe 文件已损坏。",
            "",
            " 解决方法：",
            "   请重新下载完整的 CNKIBug.exe 文件，",
            "   不要解压、不要移动内部文件，直接双击运行。",
            "==============================================",
        ])
    else:
        print(f"[FATAL] 缺少依赖: {_err}")
        print("请运行: pip install playwright openpyxl rich && playwright install chromium")
    sys.exit(1)


try:
    from cnkibug.ui import _console
    from cnkibug.environment import check_env
    from cnkibug.estimate import estimate_seconds, format_eta
    from cnkibug.keyword_import import (
        KeywordImportError,
        dedupe_keywords,
        load_keywords_txt,
    )
    from cnkibug.runtime import init_runtime
except ImportError as _err:
    _handle_import_error(_err)



logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _clear_screen() -> None:
    """清屏（含滚动回溯缓冲）。

    用 sys.stdout.isatty() 判断是否真终端——比 rich 的 is_terminal 在 PyInstaller
    打包 exe 下更可靠（后者会误判为 False，导致清屏被整个跳过）。
    Windows：cls 清整个控制台缓冲（传统 conhost 含 scrollback），再补 ANSI ESC[3J
    清 Windows Terminal 的 scrollback。非 Windows：ESC c 终端重置。
    IDE 运行面板 / 输出重定向（非 tty）直接跳过，避免乱码。

    """
    try:
        if not sys.stdout.isatty():
            return
    except Exception: # noqa
        return
    if sys.platform == "win32":
        os.system("cls") # noqa
        sys.stdout.write("\033[3J")
        sys.stdout.flush()
    else:
        sys.stdout.write("\033c")
        sys.stdout.flush()


def safe_input(prompt: str = "") -> str:
    """统一输入入口：stdin 被关闭/重定向（EOF）时视为用户请求退出。

    避免裸 input() 在管道、重定向、CI 等无交互输入场景下抛出未捕获的
    EOFError 导致程序以红字堆栈崩溃。
    """
    try:
        return input(prompt)
    except EOFError:
        print("\n[*] 检测到输入流结束（EOF），程序退出。")
        sys.exit(0)


def _run_self_check() -> int:
    try:
        import cnkibug.exporter
        import cnkibug.scraper
        import cnkibug.task_state
    except ImportError as err:
        print(f"CNKIBug self-check failed: {err}")
        return 1
    print(f"CNKIBug self-check OK: {APP_VERSION}")
    return 0


def main():
    app_logger = logging.getLogger("cnkibug.app")
    try:
        runtime_state = init_runtime(app_version=APP_VERSION)
    except OSError as err:
        if sys.platform == "win32":
            _popup_error([
                "==============================================",
                " [致命错误] 无法创建运行数据目录！",
                "----------------------------------------------",
                f" 错误信息: {err}",
                "",
                " 请检查程序所在目录或用户数据目录的写入权限。",
                "==============================================",
            ])
        else:
            print(f"[FATAL] 无法创建运行数据目录: {err}")
        sys.exit(1)
    try:
        from cnkibug.scraper import scrape_cnki
        from cnkibug.task_state import (
            delete_last_task,
            describe_task,
            get_last_task_path,
            load_last_task,
        )
    except ImportError as _err:
        logging.getLogger("cnkibug.app").exception("程序核心组件加载失败")
        _handle_import_error(_err)

    try:
        _clear_screen()

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
                f"[yellow][配置提示] 配置文件或运行目录已自动调整，详情见日志：{runtime_state.log_path}[/yellow]"
            )

        check_env()

        while True:
            try:
                resume_state = load_last_task()
                last_task_path = get_last_task_path()
                if resume_state is None and last_task_path and last_task_path.exists():
                    _console.print("[yellow][!] 检测到损坏的未完成任务缓存，已删除。[/yellow]")
                    delete_last_task()
                elif resume_state is not None:
                    _console.print("\n[yellow][!] 检测到上次未完成的抓取任务。[/yellow]")
                    _console.print(f"    {describe_task(resume_state)}")
                    print("  1 -> 继续上次任务")
                    print("  0 -> 删除缓存并开始新任务")
                    while True:
                        resume_input = safe_input("请输入选项（1 或 0）: ").strip()
                        if resume_input in ("1", "0"):
                            break
                        print("[!] 无效选项，请重新输入。")
                    if resume_input == "1":
                        app_logger.info("用户选择继续未完成任务")
                        scrape_cnki(
                            list(resume_state["keywords"]),
                            int(resume_state["max_pages"]),
                            str(resume_state["save_mode"]),
                            resume_state=resume_state,
                        )
                        app_logger.info("恢复任务结束")

                        again = safe_input("\n[*] 本轮抓取已结束！是否清屏并开始新一轮抓取？(y/n): ").strip().lower()
                        if again == "y":
                            app_logger.info("用户选择开始新一轮抓取")
                            _clear_screen()
                            continue
                        _console.print("\n[bold green]感谢使用 CNKIBug，再见！[/bold green]")
                        break

                    delete_last_task()
                    app_logger.info("用户选择删除未完成任务缓存")

                print("\n请选择抓取模式：")
                print("  1 -> 单关键词模式")
                print("  2 -> 多关键词模式")
                while True:
                    mode_input = safe_input("请输入选项（1 或 2）: ").strip()
                    if mode_input in ("1", "2"):
                        break
                    print("[!] 无效选项，请重新输入。")

                keywords = []
                keyword_input_result = None
                keyword_source = "手动输入"
                if mode_input == "1":
                    while True:
                        word = safe_input("\n请输入你要搜索的关键词: ").strip()
                        if word:
                            break
                        print("[!] 关键词不能为空，请重新输入。")
                    keywords = [word]
                    print("\n请选择保存格式：")
                    print("  1 -> Excel")
                    print("  2 -> CSV（包含 keyword 列）")
                    while True:
                        save_input = safe_input("请输入选项（1 或 2）: ").strip()
                        if save_input == "1":
                            save_mode = "single"
                            break
                        if save_input == "2":
                            save_mode = "single_csv"
                            break
                        print("[!] 无效选项，请重新输入。")
                else:
                    print("\n[多关键词模式] 每个关键词将【独立检索、分别出结果】。")
                    print("若想【交叉检索】（多个词作为一个整体一起搜），请改用单关键词模式，")
                    print("在一个关键词框里用空格分隔多个词，例如：增材制造 316L 残余应力")

                    print("\n请选择关键词输入方式：")
                    print("  1 -> 逐个手动输入")
                    print("  2 -> 从 TXT 文件导入（一行一个关键词）")
                    while True:
                        source_input = safe_input("请输入选项（1 或 2）: ").strip()
                        if source_input in ("1", "2"):
                            break
                        print("[!] 无效选项，请重新输入。")

                    if source_input == "1":
                        raw_keywords = []
                        print("\n请依次输入关键词，每输入一个按回车确认；直接按回车结束输入：")
                        while True:
                            word = safe_input("  关键词: ").strip()
                            if not word:
                                break
                            raw_keywords.append(word)
                        try:
                            keyword_input_result = dedupe_keywords(raw_keywords)
                        except KeywordImportError as exc:
                            _console.print(f"[red][x] 输入失败：{exc}[/red]")
                            continue
                    else:
                        while True:
                            import_path = safe_input("\n请输入或拖入 TXT 文件路径: ")
                            try:
                                keyword_input_result = load_keywords_txt(import_path)
                                keyword_source = f"TXT 文件（{import_path.strip()}）"
                                break
                            except KeywordImportError as exc:
                                _console.print(f"[red][x] 导入失败：{exc}[/red]")

                    keywords = keyword_input_result.keywords
                    if not keywords:
                        print("[!] 未输入任何关键词，程序退出。")
                        sys.exit(0)
                    if keyword_input_result.duplicates:
                        duplicate_sample = keyword_input_result.duplicates[:10]
                        suffix = "……" if keyword_input_result.duplicate_count > 10 else ""
                        print(f"[!] 已忽略重复关键词：{duplicate_sample}{suffix}")
                    print(f"\n[*] 去重后共 {len(keywords)} 个关键词。")

                    print("\n请选择保存方式：")
                    print("  1 -> 分文件保存（每个关键词独立生成一个 Excel）")
                    print("  2 -> 单文件多 Sheet 保存（所有关键词汇总到一个 Excel）")
                    print("  3 -> 单文件 CSV 保存（包含 keyword 列）")
                    while True:
                        save_input = safe_input("请输入选项（1、2 或 3）: ").strip()
                        if save_input == "1":
                            save_mode = "multi_split"
                            break
                        if save_input == "2":
                            save_mode = "multi_merge"
                            break
                        if save_input == "3":
                            save_mode = "multi_csv"
                            break
                        print("[!] 无效选项，请重新输入。")
                target_pages = 0
                while True:
                    try:
                        if len(keywords) > 1:
                            pages_prompt = "\n请输入每个关键词想抓取的页数（纯数字，值不要太大）: "
                        else:
                            pages_prompt = "\n请输入想抓取的页数（纯数字，值不要太大）: "
                        user_input_pages = safe_input(pages_prompt).strip()
                        target_pages = int(user_input_pages)
                        if target_pages <= 0:
                            print("  [!] 页数必须大于 0，请重新输入。")
                            continue
                    except ValueError:
                        print("  [!] 错误：页数请输入【纯数字】，例如 3 或 10，请重新输入。")
                        continue

                    total_requested_pages = target_pages * len(keywords)
                    if mode_input == "1" and target_pages > 20:
                        warning_text = (
                            f"您输入的页数较大（{target_pages}页），"
                            "预计将耗时较长，且容易触发知网反爬验证。"
                        )
                        _console.print(
                            f"\n[yellow][!] {warning_text}[/yellow]"
                        )
                        confirm = safe_input("确定要继续吗？(y/n): ").strip().lower()
                        if confirm == "y":
                            break
                        else:
                            continue
                    else:
                        break

                while True:
                    citation_input = safe_input(
                        "\n是否抓取 GB/T 引用格式？这会显著增加耗时 [y/N]: "
                    ).strip().lower()
                    if citation_input in ("", "n"):
                        include_citation = False
                        break
                    if citation_input == "y":
                        include_citation = True
                        break
                    print("[!] 无效选项，请输入 y 或 n。")

                eta_low, eta_high = estimate_seconds(
                    target_pages,
                    len(keywords),
                    include_citation=include_citation,
                )
                if mode_input == "1":
                    _console.print(
                        f"\n[dim][*] 预计耗时 {format_eta(eta_low, eta_high)}"
                        f"（实际受网络与知网反爬等待波动，仅供参考）[/dim]"
                    )
                else:
                    save_mode_text = {
                        "multi_split": "分文件 Excel",
                        "multi_merge": "单文件多 Sheet Excel",
                        "multi_csv": "单文件 CSV",
                    }[save_mode]
                    _console.print("\n" + "=" * 50)
                    _console.print("[bold cyan]批量任务预览[/bold cyan]")
                    _console.print(f"  输入来源：{keyword_source}")
                    _console.print(f"  读取行数：{keyword_input_result.total_lines}")
                    _console.print(f"  空行：{keyword_input_result.blank_lines}")
                    _console.print(f"  重复：{keyword_input_result.duplicate_count}")
                    _console.print(f"  最终关键词：{len(keywords)}")
                    _console.print(f"  每词抓取：{target_pages} 页")
                    _console.print(f"  理论最多：{total_requested_pages} 页")
                    _console.print(f"  预计耗时：{format_eta(eta_low, eta_high)}")
                    _console.print(
                        f"  GB/T 引用格式：{'开启' if include_citation else '关闭'}"
                    )
                    _console.print(f"  保存方式：{save_mode_text}")
                    preview_keywords = keywords[:20]
                    _console.print(f"  关键词预览：{preview_keywords}")
                    if len(keywords) > len(preview_keywords):
                        _console.print(f"  [dim]另有 {len(keywords) - len(preview_keywords)} 个关键词未展开显示[/dim]")
                    if target_pages > 20 or total_requested_pages > 100:
                        _console.print(
                            "  [yellow]风险提示：任务较大，预计耗时较长，"
                            "且容易触发知网反爬验证。[/yellow]"
                        )
                    _console.print("=" * 50)
                    print("  1 -> 开始执行")
                    print("  2 -> 返回重新设置")
                    print("  0 -> 取消并退出程序")
                    while True:
                        preview_input = safe_input("请输入选项（1、2 或 0）: ").strip()
                        if preview_input in ("1", "2", "0"):
                            break
                        print("[!] 无效选项，请重新输入。")
                    if preview_input == "2":
                        app_logger.info("用户从批量任务预览返回重新设置")
                        continue
                    if preview_input == "0":
                        app_logger.info("用户从批量任务预览取消任务")
                        _console.print("\n[bold green]任务已取消，程序退出。[/bold green]")
                        break
                app_logger.info(
                    "用户选择: save_mode=%s keyword_count=%d pages=%d include_citation=%s",
                    save_mode,
                    len(keywords),
                    target_pages,
                    include_citation,
                )

                scrape_cnki(
                    keywords,
                    max_pages=target_pages,
                    save_mode=save_mode,
                    include_citation=include_citation,
                )
                app_logger.info("本轮抓取完成")

                again = safe_input("\n[*] 本轮抓取已完成！是否清屏并开始新一轮抓取？(y/n): ").strip().lower()
                if again == "y":
                    app_logger.info("用户选择开始新一轮抓取")
                    _clear_screen()
                    continue
                else:
                    _console.print("\n[bold green]感谢使用 CNKIBug，再见！[/bold green]")
                    break

            except RuntimeError as e:
                app_logger.warning("运行时错误: %s", e)
                print(f"\n[!] {e}")
                retry = safe_input("是否返回主菜单重试？(y/n): ").strip().lower()
                if retry == "y":
                    app_logger.info("用户选择返回主菜单重试")
                    continue
                else:
                    break

            except Exception as ex:
                app_logger.exception("程序遇到未知错误")
                print("\n" + "!" * 40)
                print(f"  程序遇到未知错误: {ex}")
                print("!" * 40)
                break

    except KeyboardInterrupt:
        app_logger.warning("用户中断，程序退出")
        print("\n[*] 用户中断，程序退出。")
    finally:
        app_logger.info("程序退出")
        try:
            input("\n按 [回车键 Enter] 退出程序...")
        except (EOFError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-check"]:
        sys.exit(_run_self_check())
    main()
