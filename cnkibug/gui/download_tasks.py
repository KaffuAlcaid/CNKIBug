from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import ttkbootstrap as ttk

from ..downloads.models import BATCH_LABELS, ITEM_LABELS
from ..fileio.paths import open_directory
from . import dialogs as messagebox
from .appearance import TextView


def _size(value):
    size = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
        size /= 1024


class DownloadTasksWindow:
    def __init__(self, parent, service, *, selected_papers=lambda: []):
        self.service = service
        self.selected_papers = selected_papers
        self.window = ttk.Toplevel(parent)
        self.window.title("CNKIBug - 下载任务")
        width = min(1220, parent.winfo_screenwidth() - 70)
        height = min(850, parent.winfo_screenheight() - 90)
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(min(900, width), min(620, height))
        self.window.protocol("WM_DELETE_WINDOW", self.window.withdraw)
        self._snapshot = {}
        self._batches = {}
        self._items = {}
        self._futures = []
        self._sort = ("position", False)
        self._last_details = None
        self._build()
        self.refresh()
        self.window.after(200, self._poll)

    def _build(self):
        body = ttk.Frame(self.window, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        header = ttk.Frame(body)
        header.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(header, text="下载任务", style="Heading.TLabel").pack(side=tk.LEFT)
        self.status = tk.StringVar(self.window, "正在读取任务")
        ttk.Label(header, textvariable=self.status, bootstyle="secondary").pack(side=tk.LEFT, padx=16)
        self.batch_filter = tk.StringVar(self.window, "全部批次")
        box = ttk.Combobox(header, textvariable=self.batch_filter, values=["全部批次", "未结束", "历史记录"], state="readonly", width=12)
        box.pack(side=tk.RIGHT)
        box.bind("<<ComboboxSelected>>", lambda _: self.refresh(force=True))

        actions = ttk.Frame(body)
        actions.pack(fill=tk.X, pady=(0, 10))
        self.resume_button = ttk.Button(actions, text="继续队列", command=lambda: self._batch_command("resume"))
        self.resume_button.pack(side=tk.LEFT)
        self.pause_button = ttk.Button(actions, text="暂停队列", command=lambda: self._send("pause"), bootstyle="secondary-outline")
        self.pause_button.pack(side=tk.LEFT, padx=6)
        self.stop_button = ttk.Button(actions, text="停止执行", command=lambda: self._batch_command("stop"), bootstyle="danger-outline")
        self.stop_button.pack(side=tk.LEFT)
        self.continue_button = ttk.Button(actions, text="处理完成", command=lambda: self._send("continue_user"), bootstyle="success")
        self.continue_button.pack(side=tk.LEFT, padx=6)
        ttk.Button(actions, text="追加结果中所选论文", command=self._append, bootstyle="secondary-outline").pack(side=tk.LEFT, padx=(8, 0))
        menu_button = ttk.Menubutton(actions, text="批次操作", bootstyle="secondary-outline")
        menu_button.pack(side=tk.RIGHT)
        menu = tk.Menu(menu_button, tearoff=False)
        for label, command in (
            ("批次上移", lambda: self._batch_command("move", offset=-1)),
            ("批次下移", lambda: self._batch_command("move", offset=1)),
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

        self.batches = self._table(body, ("name", "state", "count", "directory"),
                                   (("批次 / 创建时间", 260), ("状态", 95), ("完成 / 总数", 110), ("保存目录", 500)), height=5)
        self.batches.bind("<<TreeviewSelect>>", lambda _: self._render_items())
        self.batch_note = tk.StringVar(self.window)
        ttk.Label(body, textvariable=self.batch_note, wraplength=1100, bootstyle="secondary").pack(fill=tk.X, pady=(6, 10))

        filters = ttk.Frame(body)
        filters.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(filters, text="筛选论文").pack(side=tk.LEFT)
        self.search = tk.StringVar(self.window)
        ttk.Entry(filters, textvariable=self.search, width=26).pack(side=tk.LEFT, padx=8)
        self.search.trace_add("write", lambda *_: self._render_items())
        self.item_filter = tk.StringVar(self.window, "全部状态")
        item_box = ttk.Combobox(filters, textvariable=self.item_filter,
                               values=["全部状态", "未完成", *ITEM_LABELS.values()], state="readonly", width=13)
        item_box.pack(side=tk.LEFT)
        item_box.bind("<<ComboboxSelected>>", lambda _: self._render_items())
        ttk.Button(filters, text="重试所选", command=self._retry, bootstyle="secondary-outline").pack(side=tk.RIGHT)
        ttk.Button(filters, text="跳过所选", command=lambda: self._item_command("skip"), bootstyle="secondary-outline").pack(side=tk.RIGHT, padx=6)
        item_actions = ttk.Menubutton(filters, text="论文操作", bootstyle="secondary-outline")
        item_actions.pack(side=tk.RIGHT)
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

        self.items = self._table(body, ("title", "state", "attempts", "path", "reason"),
                                 (("论文标题", 340), ("状态", 100), ("尝试", 55), ("文件", 250), ("原因", 300)), height=9, expand=True)
        self.items.bind("<<TreeviewSelect>>", lambda _: self._details())
        for column in ("title", "state", "attempts"):
            self.items.heading(column, command=lambda key=column: self._sort_items(key))
        self.progress_text = tk.StringVar(self.window)
        ttk.Label(body, textvariable=self.progress_text, wraplength=1100).pack(fill=tk.X, pady=(8, 4))
        self.progress = ttk.Progressbar(body, maximum=100)
        self.progress.pack(fill=tk.X)
        self.detail = TextView(body, height=5)
        self.detail.pack(fill=tk.X, pady=(10, 0))
        self.detail.text.configure(state=tk.DISABLED)

    def _table(self, parent, columns, headings, *, height, expand=False):
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH if expand else tk.X, expand=expand)
        table = ttk.Treeview(frame, columns=columns, show="headings", height=height, selectmode="extended" if expand else "browse")
        for name, (label, width) in zip(columns, headings):
            table.heading(name, text=label)
            table.column(name, width=width, minwidth=min(width, 70), stretch=name not in {"state", "count", "attempts"})
        vertical = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=table.yview)
        horizontal = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=table.xview)
        table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
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
                    # The application displays the service's command-error event.
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
            values = (f"{batch['title']} · {stamp}", BATCH_LABELS[batch["state"]],
                      f"{counts['completed']} / {batch['total']}", batch["destination"])
            if self.batches.exists(batch["id"]):
                self.batches.item(batch["id"], values=values)
            else:
                self.batches.insert("", tk.END, iid=batch["id"], values=values)
            self.batches.move(batch["id"], "", len(visible)-1)
        for key in self.batches.get_children():
            if key not in visible:
                self.batches.delete(key)
        if wanted in visible:
            self.batches.selection_set(wanted)
        elif visible:
            self.batches.selection_set(visible[-1] if not snapshot.get("active_batch") else visible[0])
        if snapshot.get("recovering"):
            text = "正在核对上次任务与文件"
        elif snapshot.get("external_busy"):
            text = "抓取或论文操作正在运行，下载任务等待执行"
        elif snapshot.get("paused"):
            text = "队列已暂停" if snapshot.get("batches") else "尚无下载任务"
        elif snapshot.get("busy"):
            text = "正在执行下载队列"
        else:
            text = "队列空闲"
        self.status.set(text)
        disabled = snapshot.get("recovering") or snapshot.get("closing")
        for button in (self.resume_button, self.pause_button, self.stop_button):
            button.configure(state=tk.DISABLED if disabled else tk.NORMAL)
        progress = snapshot.get("progress", {})
        self.progress_text.set(progress.get("message", ""))
        received, total = progress.get("received", 0), progress.get("total", 0)
        if received:
            self.progress_text.set(f"{progress.get('message', '正在接收文件')}　{_size(received)}" + (f" / {_size(total)}" if total else ""))
        self.progress.configure(value=min(100, received / total * 100) if total > 0 else 0)
        self.continue_button.configure(state=tk.NORMAL if snapshot.get("busy") and progress.get("stage") in {"waiting", "preparing"} else tk.DISABLED)
        self._render_items()

    def _batch(self):
        selected = self.batches.selection()
        return self._batches.get(selected[0]) if selected else None

    def _render_items(self):
        batch = self._batch()
        if batch is None:
            return
        counts = batch["counts"]
        waiting = counts["waiting"] + counts["uncertain"] + counts["finalizing"]
        self.batch_note.set(f"成功 {counts['completed']}　失败 {counts['failed']}　跳过 {counts['skipped']}　待处理 {waiting}"
                            f"　访问：{'机构 WebVPN' if batch['webvpn_url'] else '知网直接访问'}　{batch['message']}")
        selected = set(self.items.selection())
        self._items = {item["id"]: item for item in batch["items"]}
        query, state = self.search.get().strip().casefold(), self.item_filter.get()
        values = list(self._items.values())
        key, reverse = self._sort
        values.sort(key=lambda item: item["paper"]["title"].casefold() if key == "title" else
                    item["state"] if key == "state" else item.get("attempt_count", 0) if key == "attempts" else item["position"], reverse=reverse)
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
            self.items.move(item["id"], "", len(visible)-1)
        for key in self.items.get_children():
            if key not in visible:
                self.items.delete(key)
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
            self._set_details("选择论文查看文件位置、处理原因和尝试记录。关闭此窗口不会停止下载。")
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
        if batch and selected and messagebox.askyesno("重试所选论文", f"重新尝试所选 {len(selected)} 篇？已有 PDF 文件会保留。", parent=self.window):
            self._send("retry", batch_id=batch["id"], item_ids=list(selected))

    def _append(self):
        batch = self._batch()
        papers = self.selected_papers()
        if not papers:
            messagebox.showinfo("追加论文", "请先在论文结果窗口勾选要追加的论文。", parent=self.window)
        elif batch:
            self._send("append", batch_id=batch["id"], papers=papers)

    def _associate(self):
        selected = self.items.selection()
        if len(selected) != 1:
            messagebox.showinfo("关联 PDF", "请选择一篇论文。", parent=self.window)
            return
        path = filedialog.askopenfilename(parent=self.window, title="关联已有 PDF", filetypes=[("PDF", "*.pdf")])
        if path:
            self._send("associate", item_id=selected[0], path=path)

    def _destination(self):
        batch = self._batch()
        if batch:
            directory = filedialog.askdirectory(parent=self.window, initialdir=batch["destination"], title="未完成项的保存目录")
            if directory:
                self._send("destination", batch_id=batch["id"], destination=directory)

    def _export(self):
        batch = self._batch()
        if batch:
            path = filedialog.asksaveasfilename(parent=self.window, title="导出下载结果", defaultextension=".csv", initialfile="download-results.csv", filetypes=[("CSV", "*.csv")])
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
        if batch and messagebox.askyesno("清理任务记录", "清除此批次的任务记录？PDF 文件和文件关联会保留。", parent=self.window):
            self._send("clear", batch_id=batch["id"])

    def _reset_session(self):
        batch = self._batch()
        if batch and messagebox.askyesno("清除登录会话", "清除此访问环境保存的浏览器会话？下次下载需要重新准备登录。", parent=self.window):
            self._send("reset_session", session_id=batch["session_id"])

    def _history(self):
        selected = self.items.selection()
        if len(selected) != 1:
            return
        def show(history):
            window = ttk.Toplevel(self.window)
            window.title("下载尝试记录")
            window.geometry("760x440")
            view = TextView(window, height=18)
            view.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
            lines = []
            for index, attempt in enumerate(history["attempts"], 1):
                stamp = datetime.fromtimestamp(attempt["created"]).strftime("%Y-%m-%d %H:%M:%S")
                lines.append(f"尝试 {index}　{stamp}　{ITEM_LABELS.get(attempt['state'], attempt['state'])}\n{attempt['error']}")
            for event in history["events"]:
                stamp = datetime.fromtimestamp(event["created"]).strftime("%H:%M:%S")
                lines.append(f"{stamp}　{ITEM_LABELS.get(event['kind'], event['kind'])}　{event['message']}")
            view.text.insert("1.0", "\n\n".join(lines) or "暂无尝试记录")
            view.text.configure(state=tk.DISABLED)
        self._send("history", item_id=selected[0], callback=show)
