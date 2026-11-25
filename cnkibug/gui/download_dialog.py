from __future__ import annotations

import tkinter as tk

import ttkbootstrap as ttk
from ttkbootstrap.utility import scale_size

from ..cnki.downloads import validate_webvpn_url
from . import dialogs as messagebox
from .appearance import Card, Divider, configure_window_surface


class DownloadDialog:
    def __init__(self, parent, wait_seconds: int, webvpn_url: str = "") -> None:
        self.accepted = False
        self.webvpn_url = ""
        self._last_url = webvpn_url
        self.window = ttk.Toplevel(parent)
        self.window.withdraw()
        self.window.title("论文下载（实验性）")
        self.window._cnkibug_surface = "card"
        configure_window_surface(self.window, "card")
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.bind("<Escape>", lambda _: self.window.destroy())

        body = ttk.Frame(self.window, style="Card.TFrame", padding=scale_size(self.window, 22))
        body.pack(fill=tk.BOTH, expand=True)

        head = ttk.Frame(body, style="Card.TFrame")
        head.pack(fill=tk.X, pady=(0, scale_size(self.window, 12)))
        ttk.Label(head, text="确认下载方式", style="CardHeading.TLabel").pack(side=tk.LEFT)
        ttk.Label(head, text="实验性", style="BadgeWarning.TLabel").pack(
            side=tk.LEFT, padx=(scale_size(self.window, 8), 0),
        )

        wrap = scale_size(self.window, 520)
        ttk.Label(
            body,
            text="下载需要有效的机构或个人全文访问权限；机构用户请先连接学校或机构提供的访问网络",
            style="Card.TLabel",
            wraplength=wrap,
            justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(0, scale_size(self.window, 12)))

        notice = Card(body, subtle=True, padding=scale_size(self.window, (12, 10)))
        notice.pack(fill=tk.X, pady=(0, scale_size(self.window, 12)))
        ttk.Label(
            notice.body,
            text="当前 PDF 下载为实验性功能，可能出现安全验证或下载中断；请留意浏览器窗口，完成验证后回到程序点击绿色“处理完成”按钮继续",
            style="CardSubtleMuted.TLabel",
            wraplength=wrap - scale_size(self.window, 24),
            justify=tk.LEFT,
        ).pack(fill=tk.X)

        ttk.Label(
            body, text=f"知网首页准备时间：{wait_seconds} 秒", style="CardMuted.TLabel",
        ).pack(anchor=tk.W)

        Divider(body).pack(fill=tk.X, pady=(scale_size(self.window, 16), scale_size(self.window, 14)))
        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.pack(fill=tk.X)
        # Consistent right-aligned primary CTA order: [取消] [使用机构 WebVPN 登录] [确定]
        ttk.Button(buttons, text="确定", command=self._direct, style="Primary.TButton", width=9).pack(side=tk.RIGHT)
        ttk.Button(
            buttons, text="使用机构 WebVPN 登录", command=self._use_webvpn, style="Soft.TButton",
        ).pack(side=tk.RIGHT, padx=(0, scale_size(self.window, 8)))
        ttk.Button(buttons, text="取消", command=self.window.destroy, style="Ghost.TButton", width=8).pack(
            side=tk.RIGHT, padx=(0, scale_size(self.window, 8)),
        )

        self.window.update_idletasks()
        self.window.position_center()
        self.window.deiconify()
        self.window.grab_set()

    def show(self) -> bool:
        self.window.wait_window()
        return self.accepted

    def _direct(self) -> None:
        self.accepted = True
        self.window.destroy()

    def _use_webvpn(self) -> None:
        while True:
            value = messagebox.askstring(
                "机构 WebVPN 登录", "学校指定的知网 WebVPN 网址：",
                initialvalue=self._last_url, parent=self.window,
            )
            if value is None:
                self.window.grab_set()
                return
            self._last_url = value
            try:
                validate_webvpn_url(value.strip())
            except ValueError as error:
                messagebox.showerror("WebVPN 网址", str(error), parent=self.window)
                continue
            self.webvpn_url = value.strip()
            self.accepted = True
            self.window.destroy()
            return
