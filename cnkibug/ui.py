"""共享的 rich Console 单例 —— 全程序唯一一个，跨模块统一 import 使用。

注意：绝不要在其它模块里各自 new 一个 Console()。
rich 的 status / Progress等 Live 上下文若分属不同 Console 实例
会互相打架，必须共用此处这一个。
"""

from rich.console import Console

_console = Console(highlight=False)
