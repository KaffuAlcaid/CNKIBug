import sys
import os
import logging

from cnkibug.errors import _popup_error

APP_VERSION = "0.2.1"


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
                if mode_input == "1":
                    while True:
                        word = safe_input("\n请输入你要搜索的关键词: ").strip()
                        if word:
                            break
                        print("[!] 关键词不能为空，请重新输入。")
                    keywords = [word]
                    save_mode = "single"
                else:

                    print("\n[多关键词模式] 每个关键词将【独立检索、分别出结果】。")
                    print("若想【交叉检索】（多个词作为一个整体一起搜），请改用单关键词模式，")
                    print("在一个关键词框里用空格分隔多个词，例如：增材制造 316L 残余应力")
                    print("\n请依次输入关键词，每输入一个按回车确认；直接按回车结束输入：")
                    while True:
                        word = safe_input("  关键词: ").strip()
                        if not word:
                            break
                        keywords.append(word)
                    if not keywords:
                        print("[!] 未输入任何关键词，程序退出。")
                        sys.exit(0)
                    seen_keywords = set()
                    deduped_keywords = []
                    duplicate_keywords = []
                    for word in keywords:
                        if word in seen_keywords:
                            duplicate_keywords.append(word)
                            continue
                        seen_keywords.add(word)
                        deduped_keywords.append(word)
                    keywords = deduped_keywords
                    if duplicate_keywords:
                        print(f"[!] 已忽略重复关键词：{duplicate_keywords}")
                    print(f"\n[*] 共确认 {len(keywords)} 个关键词：{keywords}")

                    print("\n请选择保存方式：")
                    print("  1 -> 分文件保存（每个关键词独立生成一个 Excel）")
                    print("  2 -> 单文件多 Sheet 保存（所有关键词汇总到一个 Excel）")
                    while True:
                        save_input = safe_input("请输入选项（1 或 2）: ").strip()
                        if save_input == "1":
                            save_mode = "multi_split"
                            break
                        if save_input == "2":
                            save_mode = "multi_merge"
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
                    if len(keywords) > 1:
                        _console.print(
                            f"\n[dim][*] 本次共 {len(keywords)} 个关键词，"
                            f"每个关键词 {target_pages} 页，"
                            f"理论最多抓取 {total_requested_pages} 页。[/dim]"
                        )

                    if target_pages > 20 or total_requested_pages > 100:
                        if len(keywords) > 1 and total_requested_pages > 100:
                            warning_text = (
                                f"本次理论抓取总页数较大（{total_requested_pages}页），"
                                "预计将耗时较长，且容易触发知网反爬验证。"
                            )
                        else:
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

                eta_low, eta_high = estimate_seconds(target_pages, len(keywords))
                _console.print(
                    f"\n[dim][*] 预计耗时 {format_eta(eta_low, eta_high)}"
                    f"（实际受网络与知网反爬等待波动，仅供参考）[/dim]"
                )
                app_logger.info(
                    "用户选择: save_mode=%s keyword_count=%d pages=%d",
                    save_mode,
                    len(keywords),
                    target_pages,
                )

                scrape_cnki(keywords, max_pages=target_pages, save_mode=save_mode)
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
    main()
