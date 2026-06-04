"""结果导出 —— 文件名清洗、输出路径解析、写盘、三种保存模式。"""

import os
import re

import openpyxl

from .ui import _console
from .environment import get_real_desktop_path


def _sanitize_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\[\]]', '_', text)


def _get_output_path(filename: str) -> str:
    try:
        real_desktop = get_real_desktop_path()
        os.makedirs(real_desktop, exist_ok=True)
        return os.path.join(real_desktop, filename)
    except OSError:
        return os.path.join(os.getcwd(), filename)


def _try_save_workbook(wb, filepath: str) -> bool:
    try:
        # v0.1.5: 写盘操作包入 status 动画，提供视觉反馈
        with _console.status(
            "[bold magenta]少女祈祷中...[/bold magenta]",
            spinner="bouncingBar",
        ):
            wb.save(filepath)
        return True
    except PermissionError:
        _console.print(f"\n[red][x] 文件保存失败：没有写入权限！[/red]")
        _console.print(f"    目标路径：{filepath}")
        _console.print(f"    请确认桌面文件夹未被锁定，或关闭已打开的同名 Excel 文件。")
        return False
    except OSError as save_err:
        _console.print(f"\n[red][x] 文件保存失败：{save_err}[/red]")
        fallback = os.path.join(os.getcwd(), os.path.basename(filepath))
        _console.print(f"    尝试保存到程序目录：{fallback}")
        try:
            wb.save(fallback)
            _console.print(f"    已保存至备用路径：{fallback}")
            return True
        except OSError as fb_err:
            _console.print(f"[red][x] 备用路径也保存失败：{fb_err}[/red]")
            return False


def _save_single(keyword: str, results: list):
    if not results:
        _console.print("[yellow][!] 未抓取到任何数据，不生成文件。[/yellow]")
        return

    clean_keyword = _sanitize_name(keyword)
    filepath = _get_output_path(f"cnki_titles_{clean_keyword}.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "论文标题"
    ws.append(["论文标题"])
    for row in results:
        ws.append(row)

    if _try_save_workbook(wb, filepath):
        _console.print("\n" + "═" * 50)
        _console.print(f"[bold green][*] 共抓取 {len(results)} 条数据。[/bold green]")
        _console.print(f"[*] 文件已保存至：")
        _console.print(f"    [bold]>>> {os.path.abspath(filepath)} <<<[/bold]")
        _console.print("═" * 50 + "\n")


def _save_multi_split(all_results: dict[str, list]):
    total = 0
    saved_files = []
    for keyword, results in all_results.items():
        if not results:
            _console.print(f"[yellow][!] 关键词「{keyword}」无数据，跳过生成文件。[/yellow]")
            continue
        clean_keyword = _sanitize_name(keyword)
        filepath = _get_output_path(f"cnki_titles_{clean_keyword}.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "论文标题"
        ws.append(["论文标题"])
        for row in results:
            ws.append(row)
        if _try_save_workbook(wb, filepath):
            saved_files.append((keyword, len(results), os.path.abspath(filepath)))
            total += len(results)

    _console.print("\n" + "═" * 50)
    _console.print(
        f"[bold green][*] 全部抓取完毕，共 {total} 条数据，生成 {len(saved_files)} 个文件：[/bold green]"
    )
    for kw, cnt, path in saved_files:
        _console.print(f"  · [cyan][{kw}][/cyan] {cnt} 条  ->  {path}")
    _console.print("═" * 50 + "\n")


def _save_multi_merge(all_results: dict[str, list]):
    if not any(len(v) > 0 for v in all_results.values()):
        _console.print("[yellow][!] 所有关键词均未抓取到数据，不生成文件。[/yellow]")
        return

    filepath = _get_output_path("cnki_titles_多词汇总.xlsx")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    total = 0
    # 记录已使用的 sheet 名，截断后若重复则追加 _1/_2 ... 保证唯一
    used_sheet_names: set[str] = set()
    for keyword, results in all_results.items():
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
        ws.append(["论文标题"])
        for row in results:
            ws.append(row)
        total += len(results)

    if _try_save_workbook(wb, filepath):
        _console.print("\n" + "═" * 50)
        _console.print(f"[bold green][*] 全部抓取完毕，共 {total} 条数据。[/bold green]")
        _console.print(f"[*] 已合并保存至：")
        _console.print(f"    [bold]>>> {os.path.abspath(filepath)} <<<[/bold]")
        for kw, results in all_results.items():
            _console.print(f"  · Sheet [cyan][{kw}][/cyan]：{len(results)} 条")
        _console.print("═" * 50 + "\n")
