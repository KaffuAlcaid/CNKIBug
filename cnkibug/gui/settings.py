from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tkinter import messagebox
from typing import Any

import ttkbootstrap as ttk

from ..app.runtime import DEFAULT_CONFIG, read_config, save_config
from ..core.version import APP_VERSION
from .update_dialog import UpdateDialog
from .updater import SOURCE_LABELS
from .environment import EnvironmentPanel
from ..fileio.paths import get_real_desktop_path


_NUMERIC_FIELDS = (
    ("timeout_goto_ms", "页面导航超时（秒）", 1000),
    ("timeout_load_ms", "页面加载超时（秒）", 1000),
    ("timeout_selector_ms", "元素与结果等待超时（秒）", 1000),
    ("verify_wait_timeout_sec", "安全验证等待时间（秒）", 1),
    ("verify_notice_interval_sec", "安全验证提醒间隔（秒）", 1),
    ("max_advance_fail", "连续翻页失败上限（次）", 1),
    ("session_cache_ttl_hours", "会话有效期（小时）", 1),
    ("download_auth_wait_sec", "下载前首页等待（秒）", 1),
)


class SettingsDialog:
    def __init__(
        self,
        parent: tk.Misc,
        config: dict[str, Any],
        config_path: Path,
        on_apply: Callable[[dict[str, Any]], None],
        *,
        on_restart: Callable[[], None],
        get_output_dir: Callable[[], Path] | None = None,
        can_check_environment: Callable[[], bool] = lambda: True,
        selected_tab: str | None = None,
    ) -> None:
        self._config = config.copy()
        self._config_path = config_path
        self._on_apply = on_apply
        self._on_restart = on_restart
        self._closing = False
        self.window = ttk.Toplevel(title="设置", transient=parent, master=parent)
        self.window.withdraw()
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.bind("<Escape>", lambda _event: self._close())

        outer = ttk.Frame(self.window, padding=18)
        outer.pack(fill=tk.BOTH, expand=True)
        footer = ttk.Frame(outer)
        footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(16, 0))
        ttk.Button(footer, text="恢复默认", command=self._reset, bootstyle="secondary-link").pack(side=tk.LEFT)
        ttk.Button(footer, text="重新读取配置", command=self._reload, bootstyle="secondary-outline").pack(
            side=tk.LEFT, padx=8,
        )
        ttk.Button(footer, text="保存", command=self._save, width=8, bootstyle="primary").pack(side=tk.RIGHT)
        ttk.Button(footer, text="取消", command=self._close, width=8, bootstyle="secondary-outline").pack(
            side=tk.RIGHT, padx=8,
        )

        body = ttk.Frame(outer)
        body.pack(fill=tk.BOTH, expand=True)
        sidebar = ttk.Frame(body)
        sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 18))
        ttk.Label(sidebar, text="设置", font=("TkDefaultFont", 14, "bold")).pack(anchor=tk.W, pady=(0, 16))
        self.window.style.configure("Settings.Treeview", rowheight=36)
        self._navigation = ttk.Treeview(sidebar, show="tree", selectmode="browse", height=6, style="Settings.Treeview")
        self._navigation.column("#0", width=116, minwidth=116, stretch=False)
        self._navigation.pack(fill=tk.Y, expand=True)
        ttk.Separator(body, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=(0, 20))
        content = ttk.Frame(body)
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._page_title = tk.StringVar(master=self.window)
        ttk.Label(content, textvariable=self._page_title, font=("TkDefaultFont", 12, "bold")).pack(anchor=tk.W, pady=(4, 18))
        pages = ttk.Frame(content)
        pages.pack(fill=tk.BOTH, expand=True)
        pages.columnconfigure(0, weight=1)
        pages.rowconfigure(0, weight=1)
        tabs = self._tabs = {}
        for name in ("外观", "抓取", "会话", "日志", "运行环境", "更新"):
            tab = ttk.Frame(pages)
            tab.columnconfigure(0, weight=1)
            tab.grid(row=0, column=0, sticky=tk.NSEW)
            self._navigation.insert("", tk.END, iid=name, text=name)
            tabs[name] = tab
        self._navigation.bind("<<TreeviewSelect>>", self._select_page)

        self.environment = EnvironmentPanel(
            tabs["运行环境"], config_path.parent,
            get_output_dir or (lambda: Path(config.get("output_dir") or get_real_desktop_path())),
            can_run=can_check_environment,
            on_change=self._environment_changed,
        )
        self.environment.grid(row=0, column=0, columnspan=2, sticky=tk.NSEW)
        tabs["运行环境"].rowconfigure(0, weight=1)
        page_name = selected_tab if selected_tab in tabs else "外观"
        self._navigation.selection_set(page_name)
        self._show_page(page_name)

        ttk.Label(tabs["更新"], text=f"当前版本：{APP_VERSION}").grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 16),
        )
        self._update_source = tk.StringVar(master=self.window)
        ttk.Label(tabs["更新"], text="下载线路").grid(row=1, column=0, sticky=tk.W, padx=(0, 16))
        ttk.Combobox(tabs["更新"], textvariable=self._update_source,
                     values=list(SOURCE_LABELS.values()), state="readonly", width=24).grid(
            row=1, column=1, sticky=tk.EW,
        )
        ttk.Button(tabs["更新"], text="检查更新", command=self._check_updates).grid(
            row=2, column=0, sticky=tk.W, pady=16,
        )
        ttk.Button(tabs["更新"], text="测试连接", command=self._test_connections,
                   bootstyle="secondary-outline").grid(
            row=2, column=1, sticky=tk.W, pady=16,
        )

        self._theme = tk.StringVar(master=self.window)
        ttk.Label(tabs["外观"], text="主题").grid(row=0, column=0, sticky=tk.W, padx=(0, 24))
        theme_row = ttk.Frame(tabs["外观"])
        theme_row.grid(row=0, column=1, sticky=tk.W)
        for text, value in (("浅色", "litera"), ("暗色", "darkly")):
            ttk.Radiobutton(theme_row, text=text, value=value, variable=self._theme,
                            width=10, bootstyle="secondary-toolbutton").pack(side=tk.LEFT, padx=(0, 4))

        self._numbers: dict[str, tk.StringVar] = {}
        for row, (key, label, divisor) in enumerate(_NUMERIC_FIELDS):
            session_rows = {"session_cache_ttl_hours": 1, "download_auth_wait_sec": 2}
            tab = tabs["会话"] if key in session_rows else tabs["抓取"]
            row = session_rows.get(key, row)
            variable = tk.StringVar(master=self.window)
            self._numbers[key] = variable
            ttk.Label(tab, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 24), pady=8)
            spinbox = ttk.Spinbox(tab, textvariable=variable, from_=0 if key == "download_auth_wait_sec" else 1 / divisor, to=2147483647 / divisor,
                                 increment=1, width=12)
            spinbox.grid(row=row, column=1, sticky=tk.E, pady=8)
            if key == "session_cache_ttl_hours":
                self._cache_ttl = spinbox

        self._flags = {
            key: tk.BooleanVar(master=self.window)
            for key in ("session_cache_enabled", "log_save_path", "log_keywords", "log_scraped_records")
        }
        ttk.Checkbutton(tabs["会话"], text="复用浏览器会话", variable=self._flags["session_cache_enabled"],
                        command=self._sync_cache, bootstyle="round-toggle").grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 12),
        )
        self._log_level = tk.StringVar(master=self.window)
        ttk.Label(tabs["日志"], text="日志级别").grid(row=0, column=0, sticky=tk.W, padx=(0, 24), pady=8)
        ttk.Combobox(tabs["日志"], textvariable=self._log_level, values=("INFO", "WARNING", "ERROR"),
                     state="readonly", width=12).grid(row=0, column=1, sticky=tk.EW, pady=8)
        for row, (key, text) in enumerate((
            ("log_save_path", "记录导出文件路径"),
            ("log_keywords", "记录检索词"),
            ("log_scraped_records", "记录详细抓取统计"),
        ), start=1):
            ttk.Checkbutton(tabs["日志"], text=text, variable=self._flags[key]).grid(
                row=row, column=0, columnspan=2, sticky=tk.W, pady=10,
            )

        self._populate(config)
        self.window.update_idletasks()
        width = min(max(800, self.window.winfo_reqwidth()), parent.winfo_screenwidth() - 80)
        height = min(max(540, self.window.winfo_reqheight()), parent.winfo_screenheight() - 80)
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(min(720, width), min(460, height))
        self.window.position_center()
        self.window.deiconify()
        self.window.grab_set()

    def show(self) -> None:
        self.window.wait_window()

    def _select_page(self, _event=None) -> None:
        selection = self._navigation.selection()
        if selection:
            self._show_page(selection[0])

    def _show_page(self, name: str) -> None:
        self._tabs[name].tkraise()
        self._page_title.set(name)

    def _close(self) -> None:
        if self.environment.busy:
            self._closing = True
            self.environment.cancel()
        else:
            self.window.destroy()

    def _environment_changed(self) -> None:
        if self._closing and not self.environment.busy:
            self.window.destroy()

    def _populate(self, config: dict[str, Any]) -> None:
        self._theme.set(config["gui_theme"])
        self._log_level.set(config["log_level"])
        self._update_source.set(SOURCE_LABELS[config["update_source"]])
        for key, _label, divisor in _NUMERIC_FIELDS:
            self._numbers[key].set(format(Decimal(config[key]) / divisor, "f"))
        for key, variable in self._flags.items():
            variable.set(config[key])
        self._sync_cache()

    def _sync_cache(self) -> None:
        self._cache_ttl.configure(state="normal" if self._flags["session_cache_enabled"].get() else "disabled")

    def _collect(self) -> dict[str, Any]:
        config = self._config.copy()
        config["gui_theme"] = self._theme.get()
        config["log_level"] = self._log_level.get()
        config["update_source"] = self._selected_update_source()
        for key, label, divisor in _NUMERIC_FIELDS:
            try:
                number = Decimal(self._numbers[key].get().strip()) * divisor
            except InvalidOperation as error:
                raise ValueError(f"{label}必须是有效数值。") from error
            minimum = 0 if key == "download_auth_wait_sec" else 1
            if not number.is_finite() or number < minimum or number != number.to_integral_value():
                requirement = "非负整数" if minimum == 0 else "正数，精确到毫秒" if divisor == 1000 else "正整数"
                raise ValueError(f"{label}必须为{requirement}。")
            config[key] = int(number)
        for key, variable in self._flags.items():
            config[key] = variable.get()
        return config

    def _save(self) -> None:
        try:
            config = save_config(self._config_path, self._collect())
        except (OSError, ValueError) as error:
            messagebox.showerror("无法保存设置", str(error), parent=self.window)
            return
        self._on_apply(config)
        self._close()

    def _reload(self) -> None:
        try:
            config = read_config(self._config_path)
        except (OSError, ValueError) as error:
            messagebox.showerror("无法读取配置", str(error), parent=self.window)
            return
        self._config = config
        self._populate(config)
        self._on_apply(config)

    def _reset(self) -> None:
        self._populate(DEFAULT_CONFIG)

    def _check_updates(self) -> None:
        UpdateDialog(
            self.window, self._config_path.parent, self._prepare_update, self._on_restart,
            source=self._selected_update_source(),
        ).show()

    def _test_connections(self) -> None:
        UpdateDialog(
            self.window, self._config_path.parent, self._prepare_update, self._on_restart,
            source=self._selected_update_source(), probe=True,
        ).show()

    def _selected_update_source(self) -> str:
        return next(key for key, label in SOURCE_LABELS.items() if label == self._update_source.get())

    def _prepare_update(self, parent: tk.Misc) -> str | None:
        if self.environment.busy:
            messagebox.showinfo("运行环境正在处理", "请在环境检查或浏览器安装完成后更新程序。", parent=parent)
            return None
        try:
            config = self._collect()
        except ValueError as error:
            messagebox.showerror("设置无效", str(error), parent=parent)
            return None
        if config == self._config:
            return config["update_source"]
        choice = messagebox.askyesnocancel(
            "保存设置",
            "设置尚未保存。是否保存后继续更新？\n选择否将使用已保存的设置。",
            parent=parent,
        )
        if choice is None:
            return None
        if choice:
            try:
                config = save_config(self._config_path, config)
            except (OSError, ValueError) as error:
                messagebox.showerror("无法保存设置", str(error), parent=parent)
                return None
            self._config = config
            self._on_apply(config)
        self._populate(self._config)
        return self._config["update_source"]
