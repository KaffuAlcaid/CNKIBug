from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import ttkbootstrap as ttk
from ttkbootstrap.utility import scale_size

from ..downloads.models import BATCH_LABELS, ITEM_LABELS
from ..fileio.paths import open_directory
from . import dialogs as messagebox
from .appearance import (
    Card,
    Divider,
    TextView,
    apply_zebra,
    configure_window_surface,
    place_toplevel,
    style_treeview,
)


def _size(value):
    size = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
        size /= 1024


class DownloadVerificationPrompt:
    def __init__(self, parent, service):
        self.parent, self.service = parent, service
        self.window = None
        self._attention_id = ""
        self._raise_timer = None

    def update(self, snapshot):
        progress = snapshot.get("progress", {})
        attention_id = progress.get("attention_id", "")
        if not snapshot.get("busy") or snapshot.get("closing") or progress.get("stage") != "waiting" or not attention_id:
            self.close()
            return
        if attention_id == self._attention_id:
            return
        self.close()
        self._attention_id = attention_id
        title = next(
            (item["paper"]["title"] for batch in snapshot.get("batches", []) for item in batch["items"]
             if item["id"] == snapshot.get("active_item")),
            "",
        )
        self.window = window = ttk.Toplevel(self.parent)
        window.title("CNKIBug - 下载需要人工处理")
        window._cnkibug_surface = "card"
        configure_window_surface(window, "card")
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self.close)
        body = ttk.Frame(window, style="Card.TFrame", padding=scale_size(window, 22))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="请处理登录或安全验证", style="CardHeading.TLabel").pack(
            anchor=tk.W, pady=(0, scale_size(window, 10)),
        )
        wrap = min(scale_size(window, 500), window.winfo_screenwidth() - 100)
        if title:
            ttk.Label(body, text=title, style="CardBold.TLabel", wraplength=wrap, justify=tk.LEFT).pack(
                anchor=tk.W, pady=(0, scale_size(window, 10)),
            )
        ttk.Label(
            body, text=progress.get("message", "请在浏览器中完成人工验证"),
            style="Card.TLabel", wraplength=wrap, justify=tk.LEFT,
        ).pack(anchor=tk.W)
        ttk.Label(
            body,
            text="验证完成后，请点击下方绿色“处理完成”按钮，下载才会继续\n若已关闭此提示，请在“下载任务”窗口点击绿色“处理完成”按钮",
            style="CardMuted.TLabel", wraplength=wrap, justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(scale_size(window, 8), scale_size(window, 18)))
        Divider(body).pack(fill=tk.X, pady=(0, scale_size(window, 14)))
        actions = ttk.Frame(body, style="Card.TFrame")
        actions.pack(fill=tk.X)
        self.continue_button = ttk.Button(
            actions, text="处理完成", style="Success.TButton",
            command=lambda: self._continue(attention_id),
        )
        self.continue_button.pack(side=tk.RIGHT)
        ttk.Button(actions, text="稍后处理", command=self.close, style="Soft.TButton").pack(
            side=tk.RIGHT, padx=(0, scale_size(window, 8)),
        )
        window.update_idletasks()
        window.position_center()
        window.attributes("-topmost", True)
        window.lift()
        window.bell()
        self._raise_timer = window.after(1500, self._release_topmost)

    def _release_topmost(self):
        self._raise_timer = None
        if self.window is not None:
            self.window.attributes("-topmost", False)

    def _continue(self, attention_id):
        if attention_id != self._attention_id:
            return
        snapshot = self.service.snapshot()
        progress = snapshot.get("progress", {})
        if snapshot.get("busy") and progress.get("stage") == "waiting" and progress.get("attention_id") == attention_id:
            try:
                self.service.request("continue_user", attention_id=attention_id)
            except RuntimeError as error:
                messagebox.showerror("下载任务", str(error), parent=self.window)
        self.close()

    def close(self):
        if self.window is not None:
            if self._raise_timer is not None:
                self.window.after_cancel(self._raise_timer)
                self._raise_timer = None
            self.window.destroy()
            self.window = None


class DownloadTasksWindow:
    def __init__(self, parent, service, *, selected_papers=lambda: []):
        self.service = service
        self.selected_papers = selected_papers
        self.window = ttk.Toplevel(parent)
        self.window.title("CNKIBug - 下载任务")
        self.window._cnkibug_surface = "canvas"
        configure_window_surface(self.window, "canvas")
        place_toplevel(self.window, parent, 1180, 820, min_width=860, min_height=580)
        self.window.protocol("WM_DELETE_WINDOW", self.window.withdraw)
        self._snapshot = {}
        self._batches = {}
        self._items = {}
        self._futures = []
        self._sort = ("position", False)
        self._last_details = None
        self._detail_visible = True
        self._build()
        self.refresh()
        self.window.after(200, self._poll)

    def _build(self):
        # Flush Header & Grouped Controls
        header_bar = ttk.Frame(
            self.window, style="Card.TFrame", padding=scale_size(self.window, (16, 12, 16, 12)),
        )
        header_bar.pack(fill=tk.X)

        left_head = ttk.Frame(header_bar, style="Card.TFrame")
        left_head.pack(side=tk.LEFT)
        ttk.Label(left_head, text="下载任务", style="CardHeading.TLabel").pack(side=tk.LEFT)
        self.status = tk.StringVar(self.window, "正在读取任务")
        self._status_badge = ttk.Label(left_head, textvariable=self.status, style="BadgeNeutral.TLabel")
        self._status_badge.pack(side=tk.LEFT, padx=(scale_size(self.window, 10), 0))

        actions = ttk.Frame(header_bar, style="Card.TFrame")
        actions.pack(side=tk.RIGHT)
        gap = scale_size(self.window, 6)
        self.resume_button = ttk.Button(
            actions, text="继续队列", command=lambda: self._batch_command("resume"), style="PrimaryCompact.TButton",
        )
        self.resume_button.pack(side=tk.LEFT)
        self.pause_button = ttk.Button(
            actions, text="暂停队列", command=lambda: self._send("pause"), style="SoftCompact.TButton",
        )
        self.pause_button.pack(side=tk.LEFT, padx=(gap, 0))
        self.stop_button = ttk.Button(
            actions, text="停止执行", command=lambda: self._batch_command("stop"), style="DangerCompact.TButton",
        )
        self.stop_button.pack(side=tk.LEFT, padx=(gap, 0))

        Divider(actions, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=scale_size(self.window, 10), pady=2)
        self.continue_button = ttk.Button(
            actions, text="处理完成", command=lambda: self._send("continue_user"), style="SuccessCompact.TButton",
        )
        self.continue_button.pack(side=tk.LEFT)

        Divider(actions, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=scale_size(self.window, 10), pady=2)
        self.batch_filter = tk.StringVar(self.window, "全部批次")
        box = ttk.Combobox(
            actions, textvariable=self.batch_filter, values=["全部批次", "未结束", "历史记录"],
            state="readonly", width=10,
        )
        box.pack(side=tk.LEFT)
        box.bind("<<ComboboxSelected>>", lambda _: self.refresh(force=True))
        ttk.Button(
            actions, text="追加结果中所选", command=self._append, style="GhostCompact.TButton",
        ).pack(side=tk.LEFT, padx=(gap, 0))
        menu_button = ttk.Menubutton(actions, text="批次操作", style="GhostCompact.TMenubutton")
        menu_button.pack(side=tk.LEFT, padx=(gap, 0))
        menu = tk.Menu(menu_button, tearoff=False)
        for label, command in (
            ("批次上移", lambda: self._batch_command("move", offset=-1)),
            ("批次下移", lambda: self._batch_command("move", offset=1)),
            ("移出本批待执行项", lambda: self._batch_command("remove_batch")),
            ("核对文件与记录", lambda: self._batch_command("recheck")),
            ("修改未完成项保存目录", self._destination),
            ("重试失败项", lambda: self._batch_command("retry")),
            ("导出批次结果 CSV", self._export),
            ("打开保存目录", self._open_folder),
            ("清除本批使用的登录会话", self._reset_session),
            ("清理已结束批次记录", self._clear),
        ):
            menu.add_command(label=label, command=command)
        menu_button.configure(menu=menu)

        Divider(self.window).pack(fill=tk.X)

        # Bottom Progress & Collapsible Detail Surface
        bottom_wrap = ttk.Frame(
            self.window, style="Canvas.TFrame", padding=scale_size(self.window, (16, 0, 16, 12)),
        )
        bottom_wrap.pack(side=tk.BOTTOM, fill=tk.X)
        progress_card = Card(bottom_wrap)
        progress_card.pack(fill=tk.X)
        pbody = progress_card.body

        prog_head = ttk.Frame(pbody, style="Card.TFrame", padding=scale_size(self.window, (14, 10, 14, 6)))
        prog_head.pack(fill=tk.X)
        self.progress_text = tk.StringVar(self.window)
        ttk.Label(prog_head, textvariable=self.progress_text, style="CardMuted.TLabel").pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._detail_toggle = ttk.Button(
            prog_head, text="▾ 详情", command=self._toggle_detail, style="GhostMuted.TButton",
        )
        self._detail_toggle.pack(side=tk.RIGHT)

        prog_bar_wrap = ttk.Frame(pbody, style="Card.TFrame", padding=scale_size(self.window, (14, 0, 14, 10)))
        prog_bar_wrap.pack(fill=tk.X)
        self.progress = ttk.Progressbar(prog_bar_wrap, maximum=100, bootstyle="primary")
        self.progress.pack(fill=tk.X)

        self._detail_divider = Divider(pbody)
        self._detail_divider.pack(fill=tk.X)
        self.detail = TextView(pbody, height=3, bordered=False)
        self.detail.pack(fill=tk.X)
        self.detail.text.configure(state=tk.DISABLED)

        # Paned Workspace: Top Batches Card + Bottom Items Card
        workspace = ttk.Frame(
            self.window, style="Canvas.TFrame", padding=scale_size(self.window, (16, 12, 16, 10)),
        )
        workspace.pack(fill=tk.BOTH, expand=True)
        panes = ttk.Panedwindow(workspace, orient=tk.VERTICAL)
        panes.pack(fill=tk.BOTH, expand=True)

        # Top Pane: Batches Card
        batch_wrap = ttk.Frame(panes, style="Canvas.TFrame", padding=(0, 0, 0, scale_size(self.window, 5)))
        panes.add(batch_wrap, weight=2)
        batch_card = Card(batch_wrap)
        batch_card.pack(fill=tk.BOTH, expand=True)
        batch_head = ttk.Frame(batch_card.body, style="CardSubtle.TFrame", padding=scale_size(self.window, (14, 8)))
        batch_head.pack(fill=tk.X)
        ttk.Label(batch_head, text="下载批次", style="CardSubtleBold.TLabel").pack(side=tk.LEFT)
        self.batch_note = tk.StringVar(self.window)
        ttk.Label(batch_head, textvariable=self.batch_note, style="CardSubtleSmall.TLabel").pack(side=tk.RIGHT)
        Divider(batch_card.body).pack(fill=tk.X)
        self.batches = self._table(
            batch_card.body,
            ("name", "state", "count", "directory"),
            (("批次 / 创建时间", 260), ("状态", 95), ("完成 / 总数", 105), ("保存目录", 480)),
            height=4,
            expand=True,
        )
        self.batches.bind("<<TreeviewSelect>>", lambda _: self._render_items())

        # Bottom Pane: Items Card
        items_wrap = ttk.Frame(panes, style="Canvas.TFrame", padding=(0, scale_size(self.window, 5), 0, 0))
        panes.add(items_wrap, weight=3)
        items_card = Card(items_wrap)
        items_card.pack(fill=tk.BOTH, expand=True)

        filters = ttk.Frame(items_card.body, style="CardSubtle.TFrame", padding=scale_size(self.window, (12, 8)))
        filters.pack(fill=tk.X)
        ttk.Label(filters, text="筛选论文", style="CardSubtleMuted.TLabel").pack(side=tk.LEFT)
        self.search = tk.StringVar(self.window)
        ttk.Entry(filters, textvariable=self.search, width=24).pack(
            side=tk.LEFT, padx=scale_size(self.window, 8),
        )
        self.search.trace_add("write", lambda *_: self._render_items())
        self.item_filter = tk.StringVar(self.window, "全部状态")
        item_box = ttk.Combobox(
            filters, textvariable=self.item_filter,
            values=["全部状态", "未完成", *ITEM_LABELS.values()], state="readonly", width=12,
        )
        item_box.pack(side=tk.LEFT)
        item_box.bind("<<ComboboxSelected>>", lambda _: self._render_items())

        ttk.Button(filters, text="重试所选", command=self._retry, style="SoftCompact.TButton").pack(side=tk.RIGHT)
        ttk.Button(
            filters, text="跳过所选", command=lambda: self._item_command("skip"), style="GhostSubtleCompact.TButton",
        ).pack(side=tk.RIGHT, padx=(0, scale_size(self.window, 6)))
        item_actions = ttk.Menubutton(filters, text="论文操作", style="GhostSubtleCompact.TMenubutton")
        item_actions.pack(side=tk.RIGHT, padx=(0, scale_size(self.window, 6)))
        item_menu = tk.Menu(item_actions, tearoff=False)
        for label, command in (
            ("关联已有 PDF", self._associate),
            ("未开始项上移", lambda: self._item_command("move", offset=-1)),
            ("未开始项下移", lambda: self._item_command("move", offset=1)),
            ("移除未开始项", lambda: self._item_command("remove")),
            ("查看尝试记录", self._history),
        ):
            item_menu.add_command(label=label, command=command)
        item_actions.configure(menu=item_menu)

        Divider(items_card.body).pack(fill=tk.X)
        self.items = self._table(
            items_card.body,
            ("title", "state", "attempts", "path", "reason"),
            (("论文标题", 330), ("状态", 95), ("尝试", 56), ("文件", 230), ("原因", 260)),
            height=8,
            expand=True,
            multi=True,
        )
        self.items.bind("<<TreeviewSelect>>", lambda _: self._details())
        for column in ("title", "state", "attempts"):
            self.items.heading(column, command=lambda key=column: self._sort_items(key))

    def apply_theme(self) -> None:
        configure_window_surface(self.window, "canvas")

    def _toggle_detail(self):
        self._detail_visible = not self._detail_visible
        if self._detail_visible:
            self._detail_toggle.configure(text="▾ 详情")
            self._detail_divider.pack(fill=tk.X)
            self.detail.pack(fill=tk.X)
        else:
            self._detail_toggle.configure(text="▸ 详情")
            self._detail_divider.pack_forget()
            self.detail.pack_forget()

    def _table(self, parent, columns, headings, *, height, expand=False, multi=False):
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill=tk.BOTH if expand else tk.X, expand=expand)
        table = ttk.Treeview(
            frame, columns=columns, show="headings", height=height,
            selectmode="extended" if multi else "browse",
        )
        style_treeview(table)
        for name, (label, width) in zip(columns, headings):
            anchor = tk.CENTER if name == "attempts" else tk.W
            table.heading(name, text=label, anchor=anchor)
            table.column(
                name,
                width=scale_size(self.window, width),
                minwidth=scale_size(self.window, min(width, 64)),
                stretch=name not in {"state", "count", "attempts"},
                anchor=anchor,
            )
        vertical = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=table.yview, bootstyle="secondary")
        horizontal = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=table.xview, bootstyle="secondary")

        def on_vscroll(first, last):
            vertical.set(first, last)
            try:
                needed = float(first) > 0.0 or float(last) < 1.0
            except ValueError:
                needed = True
            if needed and not vertical.winfo_ismapped():
                vertical.grid()
            elif not needed and vertical.winfo_ismapped():
                vertical.grid_remove()

        def on_hscroll(first, last):
            horizontal.set(first, last)
            try:
                needed = float(first) > 0.0 or float(last) < 1.0
            except ValueError:
                needed = False
            if needed and not horizontal.winfo_ismapped():
                horizontal.grid()
            elif not needed and horizontal.winfo_ismapped():
                horizontal.grid_remove()

        table.configure(yscrollcommand=on_vscroll, xscrollcommand=on_hscroll)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        table.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        return table

    def show(self):
        self.window.deiconify()
        self.window.lift()
        self.refresh(force=True)

    def _send(self, action, *, callback=None, **kwargs):
        try:
            future = self.service.request(action, **kwargs)
            self._futures.append((future, callback))
        except RuntimeError as error:
            messagebox.showerror("下载任务", str(error), parent=self.window)

    def _poll(self):
        try:
            if not self.window.winfo_exists():
                return
            remaining = []
            for future, callback in self._futures:
                if not future.done():
                    remaining.append((future, callback))
                    continue
                try:
                    value = future.result()
                    if callback:
                        callback(value)
                except Exception:
                    pass
            self._futures = remaining
            self.refresh()
            self.window.after(250, self._poll)
        except tk.TclError:
            return

    def refresh(self, *, force=False):
        snapshot = self.service.snapshot()
        if not force and snapshot == self._snapshot:
            return
        self._snapshot = snapshot
        self._batches = {batch["id"]: batch for batch in snapshot.get("batches", [])}
        selected = self.batches.selection()
        wanted = selected[0] if selected else snapshot.get("active_batch", "")
        visible = []
        for batch in self._batches.values():
            if self.batch_filter.get() == "未结束" and batch["state"] == "completed":
                continue
            if self.batch_filter.get() == "历史记录" and batch["state"] != "completed":
                continue
            visible.append(batch["id"])
            stamp = datetime.fromtimestamp(batch["created"]).strftime("%m-%d %H:%M")
            counts = batch["counts"]
            values = (
                f"{batch['title']} · {stamp}", BATCH_LABELS[batch["state"]],
                f"{counts['completed']} / {batch['total']}", batch["destination"],
            )
            if self.batches.exists(batch["id"]):
                self.batches.item(batch["id"], values=values)
            else:
                self.batches.insert("", tk.END, iid=batch["id"], values=values)
            self.batches.move(batch["id"], "", len(visible) - 1)
        for key in self.batches.get_children():
            if key not in visible:
                self.batches.delete(key)
        apply_zebra(self.batches)
        if wanted in visible:
            self.batches.selection_set(wanted)
        elif visible:
            self.batches.selection_set(visible[-1] if not snapshot.get("active_batch") else visible[0])

        if snapshot.get("recovering"):
            text, badge = "正在核对上次任务与文件", "BadgeInfo.TLabel"
        elif snapshot.get("external_busy"):
            text, badge = "抓取或论文操作正在运行，下载等待执行", "BadgeWarning.TLabel"
        elif snapshot.get("paused"):
            text, badge = ("队列已暂停", "BadgeWarning.TLabel") if snapshot.get("batches") else ("尚无下载任务", "BadgeNeutral.TLabel")
        elif snapshot.get("busy"):
            text, badge = "正在执行下载队列", "BadgeInfo.TLabel"
        else:
            text, badge = "队列空闲", "BadgeNeutral.TLabel"
        self.status.set(text)
        self._status_badge.configure(style=badge)

        disabled = snapshot.get("recovering") or snapshot.get("closing")
        for button in (self.resume_button, self.pause_button, self.stop_button):
            button.configure(state=tk.DISABLED if disabled else tk.NORMAL)
        progress = snapshot.get("progress", {})
        self.progress_text.set(progress.get("message", "") or "队列就绪")
        received, total = progress.get("received", 0), progress.get("total", 0)
        if received:
            self.progress_text.set(
                f"{progress.get('message', '正在接收文件')}  {_size(received)}"
                + (f" / {_size(total)}" if total else "")
            )
        self.progress.configure(value=min(100, received / total * 100) if total > 0 else 0)
        self.continue_button.configure(
            state=tk.NORMAL if snapshot.get("busy") and progress.get("stage") in {"waiting", "preparing"} else tk.DISABLED
        )
        self._render_items()

    def _batch(self):
        selected = self.batches.selection()
        return self._batches.get(selected[0]) if selected else None

    def _render_items(self):
        batch = self._batch()
        if batch is None:
            self._items = {}
            for item_id in self.items.get_children():
                self.items.delete(item_id)
            self.batch_note.set("")
            self._set_details("选择批次查看论文；关闭此窗口不会停止下载")
            return
        counts = batch["counts"]
        waiting = counts["waiting"] + counts["uncertain"] + counts["finalizing"]
        note = (
            f"成功 {counts['completed']} · 失败 {counts['failed']} · 跳过 {counts['skipped']} · 待处理 {waiting}"
            f" · {'机构 WebVPN' if batch['webvpn_url'] else '知网直接访问'}"
        )
        if batch["message"]:
            note += f" · {batch['message']}"
        self.batch_note.set(note)
        selected = set(self.items.selection())
        self._items = {item["id"]: item for item in batch["items"]}
        query, state = self.search.get().strip().casefold(), self.item_filter.get()
        values = list(self._items.values())
        key, reverse = self._sort
        values.sort(
            key=lambda item: item["paper"]["title"].casefold() if key == "title"
            else item["state"] if key == "state"
            else item.get("attempt_count", 0) if key == "attempts"
            else item["position"],
            reverse=reverse,
        )
        visible = []
        for item in values:
            if query and query not in f"{item['paper']['title']} {item['paper']['authors']} {item['paper']['doi']}".casefold():
                continue
            if state == "未完成" and item["state"] in {"completed", "skipped", "removed"}:
                continue
            if state not in {"全部状态", "未完成", ITEM_LABELS[item["state"]]}:
                continue
            visible.append(item["id"])
            row = (item["paper"]["title"], ITEM_LABELS[item["state"]], item.get("attempt_count", 0), item["path"], item["error"])
            if self.items.exists(item["id"]):
                self.items.item(item["id"], values=row)
            else:
                self.items.insert("", tk.END, iid=item["id"], values=row)
            self.items.move(item["id"], "", len(visible) - 1)
        for key in self.items.get_children():
            if key not in visible:
                self.items.delete(key)
        apply_zebra(self.items)
        retained = [key for key in visible if key in selected]
        if retained:
            self.items.selection_set(retained)
        self._details()

    def _sort_items(self, key):
        self._sort = (key, not self._sort[1] if self._sort[0] == key else False)
        self._render_items()

    def _set_details(self, value):
        if value == self._last_details:
            return
        self._last_details = value
        self.detail.text.configure(state=tk.NORMAL)
        self.detail.text.delete("1.0", tk.END)
        self.detail.text.insert("1.0", value)
        self.detail.text.configure(state=tk.DISABLED)

    def _details(self):
        selection = self.items.selection()
        if not selection or selection[0] not in self._items:
            self._set_details("选择论文查看文件位置、处理原因和尝试记录；关闭此窗口不会停止下载")
            return
        item = self._items[selection[0]]
        value = f"{item['paper']['title']}\n状态：{ITEM_LABELS[item['state']]}"
        if item["path"]:
            value += f"\n文件：{item['path']}"
        if item["error"]:
            value += f"\n原因：{item['error']}"
        self._set_details(value)

    def _batch_command(self, action, **kwargs):
        batch = self._batch()
        if batch:
            self._send(action, batch_id=batch["id"], **kwargs)
        elif action == "resume":
            self._send(action)

    def _item_command(self, action, **kwargs):
        for item_id in self.items.selection():
            self._send(action, item_id=item_id, **kwargs)

    def _retry(self):
        batch, selected = self._batch(), self.items.selection()
        if batch and selected and messagebox.askyesno(
            "重试所选论文", f"重新尝试所选 {len(selected)} 篇？已有 PDF 文件会保留", parent=self.window,
        ):
            self._send("retry", batch_id=batch["id"], item_ids=list(selected))

    def _append(self):
        batch = self._batch()
        papers = self.selected_papers()
        if not papers:
            messagebox.showinfo("追加论文", "请先在论文结果窗口勾选要追加的论文", parent=self.window)
        elif batch:
            self._send("append", batch_id=batch["id"], papers=papers)

    def _associate(self):
        selected = self.items.selection()
        if len(selected) != 1:
            messagebox.showinfo("关联 PDF", "请选择一篇论文", parent=self.window)
            return
        path = filedialog.askopenfilename(parent=self.window, title="关联已有 PDF", filetypes=[("PDF", "*.pdf")])
        if path:
            self._send("associate", item_id=selected[0], path=path)

    def _destination(self):
        batch = self._batch()
        if batch:
            directory = filedialog.askdirectory(
                parent=self.window, initialdir=batch["destination"], title="未完成项的保存目录",
            )
            if directory:
                self._send("destination", batch_id=batch["id"], destination=directory)

    def _export(self):
        batch = self._batch()
        if batch:
            path = filedialog.asksaveasfilename(
                parent=self.window, title="导出下载结果", defaultextension=".csv",
                initialfile="download-results.csv", filetypes=[("CSV", "*.csv")],
            )
            if path:
                self._send("export", batch_id=batch["id"], path=path)

    def _open_folder(self):
        batch = self._batch()
        if batch:
            try:
                open_directory(Path(batch["destination"]))
            except OSError as error:
                messagebox.showerror("保存目录", str(error), parent=self.window)

    def _clear(self):
        batch = self._batch()
        if batch and messagebox.askyesno(
            "清理任务记录", "清除此批次的任务记录？PDF 文件和文件关联会保留", parent=self.window,
        ):
            self._send("clear", batch_id=batch["id"])

    def _reset_session(self):
        batch = self._batch()
        if batch and messagebox.askyesno(
            "清除登录会话", "清除此访问环境保存的浏览器会话？下次下载需要重新准备登录", parent=self.window,
        ):
            self._send("reset_session", session_id=batch["session_id"])

    def _history(self):
        selected = self.items.selection()
        if len(selected) != 1:
            return

        def show(history):
            window = ttk.Toplevel(self.window)
            window.title("下载尝试记录")
            window._cnkibug_surface = "card"
            configure_window_surface(window, "card")
            place_toplevel(window, self.window, 760, 440, min_width=520, min_height=320)
            view = TextView(window, height=18)
            view.pack(fill=tk.BOTH, expand=True, padx=scale_size(window, 14), pady=scale_size(window, 14))
            lines = []
            for index, attempt in enumerate(history["attempts"], 1):
                stamp = datetime.fromtimestamp(attempt["created"]).strftime("%Y-%m-%d %H:%M:%S")
                lines.append(
                    f"尝试 {index}  {stamp}  {ITEM_LABELS.get(attempt['state'], attempt['state'])}\n{attempt['error']}"
                )
            for event in history["events"]:
                stamp = datetime.fromtimestamp(event["created"]).strftime("%H:%M:%S")
                label = {
                    "attempt_started": "开始尝试", "late_event": "上次尝试的补充结果",
                }.get(event["kind"], ITEM_LABELS.get(event["kind"], "状态记录"))
                lines.append(f"{stamp}  {label}  {event['message']}")
            view.text.insert("1.0", "\n\n".join(lines) or "暂无尝试记录")
            view.text.configure(state=tk.DISABLED)

        self._send("history", item_id=selected[0], callback=show)
