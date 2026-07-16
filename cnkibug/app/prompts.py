from __future__ import annotations

import logging
from dataclasses import dataclass

from ..fileio.keyword_input import (
    KeywordImportError,
    KeywordImportResult,
    dedupe_keywords,
    load_keywords_txt,
)
from .console import safe_input
from ..core.estimate import estimate_seconds, format_eta
from .ui import _console


_logger = logging.getLogger("cnkibug.app.prompts")
_LONG_TASK_WARNING_SECONDS = 10 * 60


@dataclass(frozen=True)
class TaskRequest:
    keywords: list[str]
    max_pages: int
    save_mode: str
    include_citation: bool


def collect_task_request() -> TaskRequest | None:
    while True:
        mode = _ask_choice(
            "\n请选择抓取模式：",
            ["  1 -> 单关键词模式", "  2 -> 多关键词模式"],
            "请输入选项（1 或 2）: ",
            {"1", "2"},
        )
        if mode == "1":
            keywords, save_mode = _collect_single_keyword()
            keyword_source = "手动输入"
            import_result = None
        else:
            collected = _collect_multiple_keywords()
            if collected is None:
                return None
            keywords, save_mode, keyword_source, import_result = collected

        max_pages = _ask_page_count(keywords)
        include_citation = _ask_citation()
        eta_low, eta_high = estimate_seconds(
            max_pages,
            len(keywords),
            include_citation=include_citation,
        )
        preview_action = _preview_task(
            keywords,
            max_pages,
            save_mode,
            include_citation,
            keyword_source,
            import_result,
            eta_low,
            eta_high,
        )
        if preview_action == "start":
            return TaskRequest(keywords, max_pages, save_mode, include_citation)
        if preview_action == "exit":
            _console.print("\n[bold green]任务已取消，程序退出。[/bold green]")
            return None
        _logger.info("用户从任务预览返回重新设置")


def _collect_single_keyword() -> tuple[list[str], str]:
    while True:
        keyword = safe_input("\n请输入你要搜索的关键词: ").strip()
        if keyword:
            break
        print("[!] 关键词不能为空，请重新输入。")
    save_choice = _ask_choice(
        "\n请选择保存格式：",
        ["  1 -> Excel", "  2 -> CSV（包含 keyword 列）"],
        "请输入选项（1 或 2）: ",
        {"1", "2"},
    )
    return [keyword], "single" if save_choice == "1" else "single_csv"


def _collect_multiple_keywords(
) -> tuple[list[str], str, str, KeywordImportResult] | None:
    print("\n[多关键词模式] 每个关键词将【独立检索、分别出结果】。")
    print("若想【交叉检索】（多个词作为一个整体一起搜），请改用单关键词模式，")
    print("在一个关键词框里用空格分隔多个词，例如：增材制造 316L 残余应力")
    source = _ask_choice(
        "\n请选择关键词输入方式：",
        ["  1 -> 逐个手动输入", "  2 -> 从 TXT 文件导入（一行一个关键词）"],
        "请输入选项（1 或 2）: ",
        {"1", "2"},
    )
    if source == "1":
        result = _read_manual_keywords()
        source_text = "手动输入"
    else:
        result, source_text = _read_keyword_file()

    if not result.keywords:
        print("[!] 未输入任何关键词，程序退出。")
        return None
    if result.duplicates:
        sample = result.duplicates[:10]
        suffix = "……" if result.duplicate_count > 10 else ""
        print(f"[!] 已忽略重复关键词：{sample}{suffix}")
    print(f"\n[*] 去重后共 {len(result.keywords)} 个关键词。")

    save_choice = _ask_choice(
        "\n请选择保存方式：",
        [
            "  1 -> 分文件保存（每个关键词独立生成一个 Excel）",
            "  2 -> 单文件多 Sheet 保存（所有关键词汇总到一个 Excel）",
            "  3 -> 单文件 CSV 保存（包含 keyword 列）",
        ],
        "请输入选项（1、2 或 3）: ",
        {"1", "2", "3"},
    )
    save_mode = {
        "1": "multi_split",
        "2": "multi_merge",
        "3": "multi_csv",
    }[save_choice]
    return result.keywords, save_mode, source_text, result


def _read_manual_keywords() -> KeywordImportResult:
    while True:
        raw_keywords = []
        print("\n请依次输入关键词，每输入一个按回车确认；直接按回车结束输入：")
        while True:
            keyword = safe_input("  关键词: ").strip()
            if not keyword:
                break
            raw_keywords.append(keyword)
        try:
            return dedupe_keywords(raw_keywords)
        except KeywordImportError as error:
            _console.print(f"[red][x] 输入失败：{error}[/red]")


def _read_keyword_file() -> tuple[KeywordImportResult, str]:
    while True:
        path = safe_input("\n请输入或拖入 TXT 文件路径: ")
        try:
            return load_keywords_txt(path), f"TXT 文件（{path.strip()}）"
        except KeywordImportError as error:
            _console.print(f"[red][x] 导入失败：{error}[/red]")


def _ask_page_count(keywords: list[str]) -> int:
    _console.print(
        "\n[dim]知网每页通常约 20 条结果，例如抓取约 100 条可填写 5 页；"
        "实际数量以知网页面为准。[/dim]"
    )
    while True:
        prompt = (
            "请输入每个关键词想抓取的页数（纯数字）: "
            if len(keywords) > 1
            else "请输入想抓取的页数（纯数字）: "
        )
        try:
            pages = int(safe_input(prompt).strip())
        except ValueError:
            print("  [!] 错误：页数请输入【纯数字】，例如 3 或 10，请重新输入。")
            continue
        if pages <= 0:
            print("  [!] 页数必须大于 0，请重新输入。")
            continue
        return pages


def _ask_citation() -> bool:
    while True:
        choice = safe_input(
            "\n是否抓取 GB/T 引用格式？这会显著增加耗时 [y/N]: "
        ).strip().lower()
        if choice in {"", "n"}:
            return False
        if choice == "y":
            return True
        print("[!] 无效选项，请输入 y 或 n。")


def _preview_task(
    keywords: list[str],
    max_pages: int,
    save_mode: str,
    include_citation: bool,
    keyword_source: str,
    import_result: KeywordImportResult | None,
    eta_low: int,
    eta_high: int,
) -> str:
    save_mode_text = {
        "single": "单文件 Excel",
        "single_csv": "单文件 CSV",
        "multi_split": "分文件 Excel",
        "multi_merge": "单文件多 Sheet Excel",
        "multi_csv": "单文件 CSV",
    }[save_mode]
    is_single = save_mode in {"single", "single_csv"}
    total_pages = max_pages * len(keywords)
    preview_keywords = keywords[:20]
    _console.print("\n" + "=" * 50)
    _console.print("[bold cyan]任务预览[/bold cyan]")
    _console.print(f"  输入来源：{keyword_source}")
    if import_result is not None:
        _console.print(f"  读取行数：{import_result.total_lines}")
        _console.print(f"  空行：{import_result.blank_lines}")
        _console.print(f"  重复：{import_result.duplicate_count}")
    if is_single:
        _console.print(f"  关键词：{keywords[0]}")
        _console.print(f"  抓取页数：{max_pages} 页")
    else:
        _console.print(f"  最终关键词：{len(keywords)}")
        _console.print(f"  每词抓取：{max_pages} 页")
        _console.print(f"  理论最多：{total_pages} 页")
    _console.print(f"  预计耗时：{format_eta(eta_low, eta_high)}")
    _console.print(f"  GB/T 引用格式：{'开启' if include_citation else '关闭'}")
    _console.print(f"  保存方式：{save_mode_text}")
    if not is_single:
        _console.print(f"  关键词预览：{preview_keywords}")
    if len(keywords) > len(preview_keywords):
        _console.print(
            f"  [dim]另有 {len(keywords) - len(preview_keywords)} 个关键词未展开显示[/dim]"
        )
    if eta_high > _LONG_TASK_WARNING_SECONDS:
        _console.print(
            "  [bold yellow]风险提示：预计耗时上限已超过 10 分钟，"
            "任务较大且更容易触发知网反爬验证。[/bold yellow]"
        )
    _console.print("=" * 50)
    choice = _ask_choice(
        "",
        ["  1 -> 开始执行", "  2 -> 返回重新设置", "  0 -> 取消并退出程序"],
        "请输入选项（1、2 或 0）: ",
        {"0", "1", "2"},
    )
    return {"1": "start", "2": "reset", "0": "exit"}[choice]


def _ask_choice(
    title: str,
    options: list[str],
    prompt: str,
    valid: set[str],
) -> str:
    if title:
        print(title)
    for option in options:
        print(option)
    while True:
        choice = safe_input(prompt).strip()
        if choice in valid:
            return choice
        print("[!] 无效选项，请重新输入。")
