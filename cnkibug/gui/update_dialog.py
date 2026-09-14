from __future__ import annotations

import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from tkinter.scrolledtext import ScrolledText

import ttkbootstrap as ttk

from ..core.version import APP_VERSION
from . import updater


class UpdateDialog:
    def __init__(
        self,
        parent: tk.Misc,
        data_dir: Path,
        before_install: Callable[[tk.Misc], bool],
        on_restart: Callable[[], None],
    ) -> None:
        self._parent = parent
        self._data_dir = data_dir
        self._before_install = before_install
        self._on_restart = on_restart
        self._queue: Queue = Queue()
        self._cancelled = Event()
        self._stage = "checking"
        self._release: updater.ReleaseInfo | None = None
        self.window = ttk.Toplevel(title="检查更新", master=parent, transient=parent)
        self.window.withdraw()
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.bind("<Escape>", lambda _event: self._close())
        width = min(600, max(280, parent.winfo_screenwidth() - 80))
        height = min(460, max(240, parent.winfo_screenheight() - 80))
        outer = ttk.Frame(self.window, padding=20)
        outer.pack(fill=tk.BOTH, expand=True)
        footer = ttk.Frame(outer)
        footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))
        self._primary = ttk.Button(footer, text="取消", command=self._close, width=10)
        self._primary.pack(side=tk.RIGHT)
        self._secondary = ttk.Button(footer, text="忽略", command=self._close,
                                     width=10, bootstyle="secondary-outline")
        ttk.Label(outer, text=f"当前版本：{APP_VERSION}").pack(anchor=tk.W)
        self._status = tk.StringVar(master=self.window, value="正在检查 GitHub 正式发布...")
        ttk.Label(outer, textvariable=self._status, wraplength=width - 40,
                  justify=tk.LEFT).pack(fill=tk.X, pady=(12, 8))
        self._progress = ttk.Progressbar(outer, mode="indeterminate")
        self._progress.pack(fill=tk.X, pady=8)
        self._progress.start()
        self._notes = ScrolledText(outer, wrap=tk.WORD, height=8, font="TkDefaultFont",
                                   borderwidth=1, relief=tk.SOLID, state=tk.DISABLED)
        self._notes.configure(background=ttk.Style().colors.inputbg,
                              foreground=ttk.Style().colors.inputfg)
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(min(400, width), min(280, height))
        self.window.position_center()
        self.window.deiconify()
        self.window.grab_set()
        self._work(updater.check_release, "checked")
        self._poll_id = self.window.after(100, self._poll)

    def show(self) -> None:
        self.window.wait_window()

    def _work(self, action: Callable, event: str) -> None:
        def worker() -> None:
            try:
                result = action()
            except updater.UpdateCancelled:
                self._queue.put(("cancelled", None))
            except Exception as error:
                self._queue.put(("error", str(error)))
            else:
                self._queue.put((event, result))

        Thread(target=worker, name="cnkibug-gui-update", daemon=True).start()

    def _poll(self) -> None:
        self._poll_id = None
        try:
            while True:
                event, value = self._queue.get_nowait()
                if event == "progress":
                    if not self._cancelled.is_set():
                        received, total = value
                        self._progress.configure(value=received / total * 100)
                        self._status.set(f"正在下载：{received / 1048576:.1f} / {total / 1048576:.1f} MB")
                elif event == "checked":
                    self._show_release(value)
                elif event == "downloaded":
                    if self._cancelled.is_set():
                        try:
                            value.unlink(missing_ok=True)
                            value.parent.rmdir()
                        except OSError:
                            self._show_message(f"下载已取消。临时文件未能删除：\n{value}")
                        else:
                            self._show_message("下载已取消。")
                    else:
                        self._stage = "installing"
                        self._status.set("正在准备重启...")
                        self._primary.configure(text="更新中", state=tk.DISABLED)
                        self._work(
                            lambda candidate=value: updater.start_installer(candidate, self._release),
                            "ready",
                        )
                elif event == "ready":
                    self._on_restart()
                    return
                elif event == "cancelled":
                    self._show_message("下载已取消。")
                elif event == "error":
                    self._show_message(value)
        except Empty:
            pass
        if self.window.winfo_exists():
            self._poll_id = self.window.after(100, self._poll)

    def _show_message(self, text: str) -> None:
        self._stage = "result"
        self._status.set(text)
        self._progress.stop()
        self._progress.pack_forget()
        self._notes.pack_forget()
        self._secondary.pack_forget()
        self._primary.configure(text="确定", state=tk.NORMAL, command=self._close)

    def _show_release(self, release: updater.ReleaseInfo) -> None:
        self._release = release
        if not release.newer:
            self._show_message(f"最新正式发布：{release.version}\n当前版本没有可用更新。")
            return
        self._stage = "result"
        text = f"可用版本：{release.version}\n发布时间：{release.published_at[:10]}"
        if updater.can_install_update():
            text += "\n更新完成后将重启程序，尚未开始的检索项不会保留。"
            if not release.ready:
                text += "\n该版本的 GUI 文件或校验信息尚未就绪。"
        else:
            text += "\n当前运行方式需手动更新，将打开发布页面。"
        self._status.set(text)
        self._progress.stop()
        self._progress.pack_forget()
        self._notes.configure(state=tk.NORMAL)
        self._notes.insert(tk.END, release.notes)
        self._notes.configure(state=tk.DISABLED)
        self._notes.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self._secondary.pack(side=tk.RIGHT, padx=(0, 8))
        available = release.ready or not updater.can_install_update()
        self._primary.configure(text="更新", command=self._update,
                                state=tk.NORMAL if available else tk.DISABLED)

    def _update(self) -> None:
        release = self._release
        if release is None:
            return
        if not updater.can_install_update():
            if webbrowser.open(release.page_url):
                self._close()
            else:
                self._show_message(f"无法打开浏览器，请访问：\n{release.page_url}")
            return
        if not self._before_install(self.window):
            return
        self._stage = "downloading"
        self._cancelled.clear()
        self._notes.pack_forget()
        self._secondary.pack_forget()
        self._progress.configure(mode="determinate", maximum=100, value=0)
        self._progress.pack(fill=tk.X, pady=8)
        self._status.set("正在连接下载服务器...")
        self._primary.configure(text="取消", command=self._close)
        self._work(
            lambda: updater.download_release(
                release, self._data_dir, self._cancelled,
                lambda received, total: self._queue.put(("progress", (received, total))),
            ),
            "downloaded",
        )

    def _close(self) -> None:
        if self._stage == "installing":
            return
        self._cancelled.set()
        if self._stage == "downloading":
            self._status.set("正在取消下载...")
            self._primary.configure(state=tk.DISABLED)
            return
        if self._poll_id is not None:
            self.window.after_cancel(self._poll_id)
        self.window.destroy()
        if self._parent.winfo_exists():
            self._parent.grab_set()
