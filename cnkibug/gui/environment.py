from __future__ import annotations

import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from queue import Empty, Queue
from tempfile import TemporaryFile
from threading import Event, Thread
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

import ttkbootstrap as ttk

from ..app.runtime import save_config
from ..browser.environment import (
    EnvironmentCancelled, EnvironmentCheck, browser_cache_directory, check_environment, install_chromium,
)


class EnvironmentPanel(ttk.Frame):
    def __init__(self, parent, data_dir: Path, get_output_dir: Callable[[], Path], *,
                 can_run: Callable[[], bool] = lambda: True, on_change: Callable[[], None] = lambda: None,
                 show_actions: bool = True, show_directories: bool = True):
        super().__init__(parent)
        self.data_dir, self.get_output_dir = data_dir, get_output_dir
        self.can_run, self.on_change = can_run, on_change
        self.results: dict[str, EnvironmentCheck] = {}
        self.busy = False
        self.ready = False
        self.installable = False
        self._cancelled = Event()
        self._queue: Queue = Queue()
        self._poll_id = None
        self._worker = None
        self.columnconfigure(0, weight=1)
        rows = [("runtime", "程序运行环境"), ("system", "系统组件"), ("browser", "浏览器")]
        if show_directories:
            rows += [("data", "用户数据目录"), ("output", "论文保存目录")]
        self._labels = {}
        for index, (key, title) in enumerate(rows):
            row = ttk.Frame(self, padding=(0, 6))
            row.grid(row=index, column=0, sticky=tk.EW)
            ttk.Label(row, text=title).pack(side=tk.LEFT)
            label = ttk.Label(row, text="未检查", bootstyle="secondary")
            label.pack(side=tk.RIGHT)
            self._labels[key] = label
        self._details = ScrolledText(self, height=5, wrap=tk.WORD, font="TkDefaultFont", state=tk.DISABLED)
        colors = ttk.Style().colors
        self._details.configure(background=colors.inputbg, foreground=colors.inputfg)
        self._details.grid(row=len(rows), column=0, sticky=tk.NSEW, pady=(8, 8))
        self.rowconfigure(len(rows), weight=1)
        self._status = tk.StringVar(self, "等待检查")
        ttk.Label(self, textvariable=self._status, wraplength=540).grid(row=len(rows) + 1, column=0, sticky=tk.W)
        self._progress = ttk.Progressbar(self, mode="indeterminate")
        self._progress.grid(row=len(rows) + 2, column=0, sticky=tk.EW, pady=8)
        self._progress.grid_remove()
        actions = ttk.Frame(self)
        actions.grid(row=len(rows) + 3, column=0, sticky=tk.EW)
        self._check_button = ttk.Button(actions, text="开始检查", command=self.check, bootstyle="secondary-outline")
        self._check_button.pack(side=tk.LEFT)
        self._install_button = ttk.Button(actions, text="安装 Chromium", command=self.install, state=tk.DISABLED)
        if sys.platform == "linux":
            self._install_button.pack(side=tk.LEFT, padx=8)
        self._cancel_button = ttk.Button(actions, text="取消", command=self.cancel, bootstyle="secondary-outline", state=tk.DISABLED)
        self._cancel_button.pack(side=tk.RIGHT)
        if not show_actions:
            actions.grid_remove()
        self.bind("<Destroy>", self._destroyed, add="+")

    def _append(self, text: str) -> None:
        self._details.configure(state=tk.NORMAL)
        self._details.insert(tk.END, text + "\n")
        if int(self._details.index("end-1c").split(".")[0]) > 150:
            self._details.delete("1.0", "30.0")
        self._details.see(tk.END)
        self._details.configure(state=tk.DISABLED)

    def _start(self, installing: bool) -> None:
        if self.busy:
            return
        if not self.can_run():
            messagebox.showinfo("任务正在运行", "请在抓取和下载任务结束后检查运行环境。", parent=self.winfo_toplevel())
            return
        try:
            output_dir = self.get_output_dir().expanduser()
        except (ValueError, OSError) as error:
            self._append(str(error))
            return
        if not installing:
            self.results.clear()
            self.installable = False
            self._details.configure(state=tk.NORMAL)
            self._details.delete("1.0", tk.END)
            self._details.configure(state=tk.DISABLED)
        for label in self._labels.values():
            label.configure(text="检查中", bootstyle="secondary")
        self.busy, self.ready = True, False
        self._cancelled.clear()
        self._status.set("正在安装 Chromium" if installing else "正在检查运行环境")
        self._progress.grid()
        self._progress.start()
        self._check_button.configure(state=tk.DISABLED)
        self._install_button.configure(state=tk.DISABLED)
        self._cancel_button.configure(state=tk.NORMAL)
        self.on_change()

        def work() -> None:
            try:
                if installing:
                    install_chromium(self._cancelled, lambda line: self._queue.put(("output", line)))
                results = check_environment(self.data_dir, output_dir, self._cancelled,
                                            lambda item: self._queue.put(("item", item)))
                self._queue.put(("done", results))
            except EnvironmentCancelled:
                self._queue.put(("cancelled", None))
            except Exception as error:
                self._queue.put(("error", str(error)))

        self._worker = Thread(target=work, name="cnkibug-environment", daemon=True)
        self._worker.start()
        self._poll_id = self.after(100, self._poll)

    def check(self) -> None:
        self._start(False)

    def install(self) -> None:
        if sys.platform == "linux" and self.installable:
            self._start(True)

    def cancel(self) -> None:
        self._cancelled.set()
        if self.busy:
            self._status.set("正在取消")

    def _poll(self) -> None:
        self._poll_id = None
        try:
            while True:
                kind, value = self._queue.get_nowait()
                if kind == "output":
                    self._append(value)
                elif kind == "item":
                    self.results[value.key] = value
                    label = self._labels.get(value.key)
                    if label is not None:
                        text, style = {"ready": ("已就绪", "success"), "missing": ("需要安装", "warning"),
                                       "pending": ("待检查", "secondary"), "error": ("需要处理", "danger")}[value.status]
                        label.configure(text=text, bootstyle=style)
                    self._append(f"{value.label}：{value.detail}\n")
                else:
                    self.busy = False
                    for label in self._labels.values():
                        if label.cget("text") == "检查中":
                            label.configure(text="待检查", bootstyle="secondary")
                    self._progress.stop()
                    self._progress.grid_remove()
                    self._cancel_button.configure(state=tk.DISABLED)
                    self._check_button.configure(state=tk.NORMAL)
                    self.ready = kind == "done" and bool(value) and all(item.status == "ready" for item in value)
                    browser = self.results.get("browser")
                    self.installable = bool(browser and browser.status == "missing")
                    self._install_button.configure(state=tk.NORMAL if self.installable else tk.DISABLED)
                    if kind == "error":
                        self._append(value)
                    self._status.set("运行环境已就绪" if self.ready else "检查已取消" if kind == "cancelled" else "请查看需要处理的项目")
                    self.on_change()
        except Empty:
            pass
        if self.busy:
            self._poll_id = self.after(100, self._poll)

    def _destroyed(self, event) -> None:
        if event.widget is self:
            self._cancelled.set()
            if self._poll_id is not None:
                self.after_cancel(self._poll_id)


class InitializationDialog:
    def __init__(self, parent, config: dict, config_path: Path, on_apply: Callable[[dict], None]):
        self.config, self.config_path, self.on_apply = config, config_path, on_apply
        self._closing = False
        self.window = ttk.Toplevel(master=parent, title="CNKIBug - 初始化设置")
        self.window.withdraw()
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.bind("<Escape>", lambda _: self._close())
        body = ttk.Frame(self.window, padding=18)
        body.pack(fill=tk.BOTH, expand=True)
        footer = ttk.Frame(body)
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(body, text="初始化设置", font=("TkDefaultFont", 16, "bold")).pack(anchor=tk.W, pady=(0, 14))
        paths = ttk.Labelframe(body, text="保存位置", padding=12)
        paths.pack(fill=tk.X)
        paths.columnconfigure(1, weight=1)
        from ..fileio.paths import get_real_desktop_path

        self.output = tk.StringVar(self.window, config.get("output_dir") or get_real_desktop_path())
        for row, text in enumerate(("用户数据", "论文保存目录", "浏览器缓存")):
            ttk.Label(paths, text=text).grid(row=row, column=0, sticky=tk.W, padx=(0, 12), pady=6)
        ttk.Label(paths, text=str(config_path.parent), wraplength=430).grid(row=0, column=1, columnspan=2, sticky=tk.W)
        self._output_entry = ttk.Entry(paths, textvariable=self.output)
        self._output_entry.grid(row=1, column=1, sticky=tk.EW)
        self._browse = ttk.Button(paths, text="浏览", command=self._choose_output, bootstyle="secondary")
        self._browse.grid(row=1, column=2, padx=(8, 0))
        ttk.Label(paths, text=str(browser_cache_directory()), wraplength=430).grid(row=2, column=1, columnspan=2, sticky=tk.W)
        environment = ttk.Labelframe(body, text="运行环境", padding=12)
        environment.pack(fill=tk.BOTH, expand=True, pady=14)
        self.panel = EnvironmentPanel(environment, config_path.parent, lambda: Path(self.output.get()),
                                      on_change=self._sync, show_actions=False, show_directories=False)
        self.panel.pack(fill=tk.BOTH, expand=True)
        self._later = ttk.Button(footer, text="稍后设置", command=self._close, bootstyle="secondary-outline")
        self._later.pack(side=tk.LEFT)
        self._primary = ttk.Button(footer, text="开始检查", command=self._primary_action, width=16)
        self._primary.pack(side=tk.RIGHT)
        self.window.update_idletasks()
        width = min(680, parent.winfo_screenwidth() - 60)
        height = min(max(610, self.window.winfo_reqheight()), parent.winfo_screenheight() - 60)
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(min(540, width), min(560, height))
        self.window.position_center()
        self.window.deiconify()
        self.window.grab_set()
        self.window.after(100, self.panel.check)

    def show(self) -> None:
        self.window.wait_window()

    def _choose_output(self) -> None:
        value = filedialog.askdirectory(parent=self.window, initialdir=self.output.get(), title="选择论文保存目录")
        if value:
            self.output.set(value)
            self.panel.check()

    def _sync(self) -> None:
        if self._closing and not self.panel.busy:
            self.window.destroy()
            return
        self._later.configure(state=tk.DISABLED if self.panel.busy else tk.NORMAL)
        self._output_entry.configure(state=tk.DISABLED if self.panel.busy else tk.NORMAL)
        self._browse.configure(state=tk.DISABLED if self.panel.busy else tk.NORMAL)
        self._primary.configure(text="取消" if self.panel.busy else "开始使用" if self.panel.ready else "安装 Chromium" if self.panel.installable else "开始检查")

    def _primary_action(self) -> None:
        if self.panel.busy:
            self.panel.cancel()
        elif self.panel.ready:
            try:
                value = self.output.get().strip()
                if not value:
                    raise ValueError("请选择论文保存目录。")
                directory = Path(value).expanduser().resolve()
                directory.mkdir(parents=True, exist_ok=True)
                with TemporaryFile(dir=directory):
                    pass
                config = save_config(self.config_path, {**self.config, "output_dir": str(directory), "linux_setup_completed": True})
            except (OSError, ValueError) as error:
                messagebox.showerror("无法保存设置", str(error), parent=self.window)
                return
            self.on_apply(config)
            self.window.destroy()
        elif self.panel.installable:
            self.panel.install()
        else:
            self.panel.check()

    def _close(self) -> None:
        if self.panel.busy:
            self._closing = True
            self.panel.cancel()
        else:
            self.window.destroy()
