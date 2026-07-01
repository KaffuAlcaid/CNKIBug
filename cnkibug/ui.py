"""共享的 rich Console 全程序唯一，跨模块统一 import 使用。

"""

from rich.console import Console
from rich.panel import Panel

_console = Console(highlight=False)


def print_browser_banner():

    _console.print(
        Panel.fit(
            "[bold yellow]浏览器已在新窗口打开[/bold yellow]\n"
            "· 全程请[bold]勿关闭[/bold]该浏览器窗口\n"
            "· 若出现滑块 / 验证码属正常现象，请手动完成后回到本窗口\n"
            "· 抓取过程中页面会自动翻页，请勿手动操作",
            title="[bold]⚠ 请切换到浏览器窗口[/bold]",
            border_style="yellow",
        )
    )


def print_verify_alert():
    """检测到知网安全验证(/verify)时的高亮提醒（配合 window.bring_to_front 置顶）。"""
    _console.print(
        Panel.fit(
            "[bold]检测到知网安全验证（滑块）[/bold]\n"
            "· 浏览器已尝试置顶，请切换过去完成滑块验证\n"
            "· 完成后[bold]无需操作本窗口[/bold]，程序会自动继续抓取",
            title="[bold red]需要手动验证[/bold red]",
            border_style="red",
        )
    )
