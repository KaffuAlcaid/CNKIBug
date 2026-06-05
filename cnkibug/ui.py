"""共享的 rich Console 单例 —— 全程序唯一一个，跨模块统一 import 使用。

注意：绝不要在其它模块里各自 new 一个 Console()。rich 的 status / Progress
等 Live 上下文若分属不同 Console 实例会互相打架，必须共用此处这一个。
"""

from rich.console import Console
from rich.panel import Panel

_console = Console(highlight=False)


def print_browser_banner():
    """浏览器弹出时的高亮提醒横幅（置顶第一层 + 建议3 的引导文案合并于此）。

    第一层置顶 = 把窗口最大化 + 在控制台醒目提示用户切过去；真正的 z-order
    置顶（第二层 ctypes）后续单独在 window.py 实现，此处不涉及。
    """
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
