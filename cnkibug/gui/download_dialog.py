from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog

import ttkbootstrap as ttk

from ..cnki.downloads import validate_webvpn_url

class DownloadDialog:
    def __init__(self, parent, wait_seconds: int, webvpn_url: str = "") -> None:
        self.accepted = False
        self.webvpn_url = ""
        self._last_url = webvpn_url
        self.window = ttk.Toplevel(parent)
        self.window.withdraw()
        self.window.title("论文下载（实验性）")
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.bind("<Escape>", lambda _: self.window.destroy())
        body = ttk.Frame(self.window, padding=20)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="下载需要有效的机构或个人全文访问权限。机构用户请先连接学校或机构提供的访问网络。",
                  wraplength=570).pack(fill=tk.X, pady=(0, 16))
        ttk.Label(body, text="PDF 下载为实验性功能，可能出现安全验证或下载中断。请留意浏览器窗口，及时完成验证后继续。",
                  wraplength=570, bootstyle="warning").pack(fill=tk.X, pady=(0, 16))
        ttk.Label(body, text=f"知网首页准备时间：{wait_seconds} 秒。").pack(anchor=tk.W)
        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(24, 0))
        ttk.Button(buttons, text="取消", command=self.window.destroy, bootstyle="secondary-outline", width=8).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="使用机构 WebVPN 登录", command=self._use_webvpn, bootstyle="secondary").pack(side=tk.RIGHT, padx=8)
        ttk.Button(buttons, text="确定", command=self._direct, width=8).pack(side=tk.RIGHT)
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
            value = simpledialog.askstring("机构 WebVPN 登录", "学校指定的知网 WebVPN 网址：", initialvalue=self._last_url, parent=self.window)
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
