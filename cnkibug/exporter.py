import os
import re

import openpyxl

from .ui import _console
from .environment import get_real_desktop_path

# 三种保存模式共用的表头，与 scraper 抓取的列顺序一一对应：
# [论文标题, 作者, 来源, 发表日期]
_HEADERS = ["论文标题", "作者", "来源", "发表日期"]


def _sanitize_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\[\]]', '_', text)


def _get_output_path(filename: str) -> str:
    try:
        real_desktop = get_real_desktop_path()
        os.makedirs(real_desktop, exist_ok=True)
        return os.path.join(real_desktop, filename)
    except OSError:
        return os.path.join(os.getcwd(), filename)


def _try_save_workbook(wb, filepath: str, announce: bool = True) -> bool:
    """写盘。announce=False 时静默（不显示 spinner、不打印失败提示），
    供每抓完一个关键词的增量落盘使用；失败由调用方记日志、最终保存时再提示。
    """
    try:
        if announce:
            with _console.status(
                "[bold magenta]少女祈祷中...[/bold magenta]",
                spinner="bouncingBar",
            ):
                wb.save(filepath)
        else:
            wb.save(filepath)
        return True
    except PermissionError:
        if announce:
            _console.print(f"\n[red][x] 文件保存失败：没有写入权限！[/red]")
            _console.print(f"    目标路径：{filepath}")
            _console.print(f"    请确认桌面文件夹未被锁定，或关闭已打开的同名 Excel 文件。")
        return False
    except OSError as save_err:
        fallback = os.path.join(os.getcwd(), os.path.basename(filepath))
        if announce:
            _console.print(f"\n[red][x] 文件保存失败：{save_err}[/red]")
            _console.print(f"    尝试保存到程序目录：{fallback}")
        try:
            wb.save(fallback)
            if announce:
                _console.print(f"    已保存至备用路径：{fallback}")
            return True
        except OSError as fb_err:
            if announce:
                _console.print(f"[red][x] 备用路径也保存失败：{fb_err}[/red]")
            return False


def _build_single_sheet_workbook(results: list):
    """构建单 Sheet 工作簿（single / multi_split 共用）。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "论文标题"
    ws.append(_HEADERS)
    for row in results:
        ws.append(row)
    return wb


def _save_single(keyword: str, results: list, ts: str, announce: bool):
    if not results:
        if announce:
            _console.print("[yellow][!] 未抓取到任何数据，不生成文件。[/yellow]")
        return

    clean_keyword = _sanitize_name(keyword)
    filepath = _get_output_path(f"cnki_titles_{clean_keyword}_{ts}.xlsx")
    wb = _build_single_sheet_workbook(results)

    if _try_save_workbook(wb, filepath, announce) and announce:
        _console.print("\n" + "═" * 50)
        _console.print(f"[bold green][*] 共抓取 {len(results)} 条数据。[/bold green]")
        _console.print(f"[*] 文件已保存至：")
        _console.print(f"    [bold]>>> {os.path.abspath(filepath)} <<<[/bold]")
        _console.print("═" * 50 + "\n")


def _save_multi_split(all_results: dict[str, list], ts: str, announce: bool):
    total = 0
    saved_files = []
    for keyword, results in all_results.items():
        if not results:
            if announce:
                _console.print(f"[yellow][!] 关键词「{keyword}」无数据，跳过生成文件。[/yellow]")
            continue

        clean_keyword = _sanitize_name(keyword)
        filepath = _get_output_path(f"cnki_titles_{clean_keyword}_{ts}.xlsx")
        wb = _build_single_sheet_workbook(results)
        if _try_save_workbook(wb, filepath, announce):
            saved_files.append((keyword, len(results), os.path.abspath(filepath)))
            total += len(results)

    if announce:
        _console.print("\n" + "═" * 50)
        _console.print(
            f"[bold green][*] 全部抓取完毕，共 {total} 条数据，生成 {len(saved_files)} 个文件：[/bold green]"
        )
        for kw, cnt, path in saved_files:
            _console.print(f"  · [cyan][{kw}][/cyan] {cnt} 条  ->  {path}")
        _console.print("═" * 50 + "\n")


def _save_multi_merge(all_results: dict[str, list], ts: str, announce: bool):
    if not any(len(v) > 0 for v in all_results.values()):
        if announce:
            _console.print("[yellow][!] 所有关键词均未抓取到数据，不生成文件。[/yellow]")
        return

    filepath = _get_output_path(f"cnki_titles_多词汇总_{ts}.xlsx")
    wb = openpyxl.Workbook()
    wb.remove(wb.active) # noqa

    total = 0

    used_sheet_names: set[str] = set()
    for keyword, results in all_results.items():
        if not results:
            if announce:
                _console.print(f"[yellow][!] 关键词「{keyword}」无数据，跳过该 Sheet。[/yellow]")
            continue
        clean_keyword = _sanitize_name(keyword)
        base_name = clean_keyword[:31]
        sheet_name = base_name
        counter = 1
        while sheet_name in used_sheet_names:
            suffix = f"_{counter}"
            sheet_name = base_name[:31 - len(suffix)] + suffix
            counter += 1
        used_sheet_names.add(sheet_name)

        ws = wb.create_sheet(title=sheet_name)
        ws.append(_HEADERS)
        for row in results:
            ws.append(row)
        total += len(results)

    if _try_save_workbook(wb, filepath, announce) and announce:
        _console.print("\n" + "═" * 50)
        _console.print(f"[bold green][*] 全部抓取完毕，共 {total} 条数据。[/bold green]")
        _console.print(f"[*] 已合并保存至：")
        _console.print(f"    [bold]>>> {os.path.abspath(filepath)} <<<[/bold]")
        for kw, results in all_results.items():
            if not results:
                continue
            _console.print(f"  · Sheet [cyan][{kw}][/cyan]：{len(results)} 条")
        _console.print("═" * 50 + "\n")


def save_all(
    save_mode: str,
    keywords: list[str],
    all_results: dict[str, list],
    ts: str,
    announce: bool,
):
    """统一保存入口（幂等）。

    - 增量调用（announce=False）：静默写盘，用于每抓完一个关键词的阶段性落盘。
    - 最终调用（announce=True）：写盘并打印完整汇总，用于流程结束（含中断）时。

    文件名以传入的 ts 固定，增量与最终写同一文件、覆盖而非堆积，从而保证
    中途任何异常（含保存阶段的二次 Ctrl+C）都不会丢失已抓取的数据。
    """
    if save_mode == "single":
        if keywords:
            _save_single(keywords[0], all_results.get(keywords[0], []), ts, announce)
    elif save_mode == "multi_split":
        _save_multi_split(all_results, ts, announce)
    elif save_mode == "multi_merge":
        _save_multi_merge(all_results, ts, announce)
