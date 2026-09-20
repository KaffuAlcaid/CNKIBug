from __future__ import annotations

import copy
import logging
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

import ttkbootstrap as ttk

from ..cnki.downloads import DownloadSession
from ..cnki.details import fetch_selected_details
from ..cnki.models import Paper, deduplicate_papers
from ..fileio.papers import associate_pdf, read_papers, save_papers, split_values
from ..fileio.zotero import send_papers_to_zotero
from ..fileio.paths import get_real_desktop_path
from .events import GuiEvent, GuiEventSink
from .download_dialog import DownloadDialog


class ResultsWindow:
    def __init__(self, parent, papers: list[Paper], *, settings, paths, output_dir: Path | None = None,
                 get_output_dir=None, initial_format="xlsx", can_run=lambda: True,
                 prepare_browser=lambda: True):
        self.window = ttk.Toplevel(parent)
        self.window.title("CNKIBug - 论文结果")
        width = min(1200, parent.winfo_screenwidth() - 70)
        height = min(880, parent.winfo_screenheight() - 90)
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(min(780, width), min(560, height))
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.settings, self.paths = settings, paths
        self.output_dir = output_dir
        self.get_output_dir = get_output_dir or (lambda: self.output_dir or Path(get_real_desktop_path()))
        self._initial_format = initial_format
        self.can_run = can_run
        self.prepare_browser = prepare_browser
        self.busy = False
        self._closing = False
        self._hide_when_done = False
        self._worker: Thread | None = None
        self._queue: Queue[GuiEvent] = Queue()
        self.cancel = Event()
        self._continue = Event()
        self._events = GuiEventSink(self._queue, self.cancel)
        self._download_session = DownloadSession(paths, self._events, self.cancel, self._continue)
        self._webvpn_url = ""
        self._checked: set[int] = set()
        self._statuses: dict[int, str] = {}
        self._detail_statuses: dict[int, str] = {}
        self._zotero_statuses: dict[int, str] = {}
        self._row_statuses: dict[int, str] = {}
        self._sort_key = "publication_date"
        self._descending = True
        self._papers: list[Paper] = []
        self._visible: list[int] = []
        self._build()
        self.set_papers(papers)
        self.window.bind("<FocusIn>", lambda _: self._show_output_dir())
        self.window.after(100, self._drain)
        self.window.after(150, lambda: self._panes.sashpos(0, int(height * .55)))

    def _build(self):
        body = ttk.Frame(self.window, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        footer = ttk.Frame(body)
        footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        toolbar = ttk.Frame(body)
        toolbar.pack(fill=tk.X, pady=(0, 10))
        toolbar.columnconfigure(2, weight=1)
        self._open_button = ttk.Button(toolbar, text="打开文件", command=self.open_file, bootstyle="secondary-outline")
        self._open_button.grid(row=0, column=0, padx=(0, 12))
        ttk.Label(toolbar, text="标题 / 作者").grid(row=0, column=1, padx=(0, 6))
        self.search = tk.StringVar(self.window)
        search = ttk.Entry(toolbar, textvariable=self.search, width=18)
        search.grid(row=0, column=2, sticky="ew")
        self.search.trace_add("write", lambda *_: self._populate())
        self.query = tk.StringVar(self.window, value="全部检索项")
        self._query_box = ttk.Combobox(toolbar, textvariable=self.query, state="readonly", width=18)
        self._query_box.grid(row=0, column=3, padx=(8, 0))
        self._query_box.bind("<<ComboboxSelected>>", lambda _: self._populate())
        self.kind = tk.StringVar(self.window, value="全部类型")
        self._kind_box = ttk.Combobox(toolbar, textvariable=self.kind, state="readonly", width=10)
        self._kind_box.grid(row=0, column=4, padx=(8, 0))
        self._kind_box.bind("<<ComboboxSelected>>", lambda _: self._populate())

        self._action_bar = ttk.Frame(body)
        self._action_bar.pack(fill=tk.X, pady=(0, 10))
        self._selection_button = ttk.Menubutton(self._action_bar, text="选择", bootstyle="secondary-outline")
        self._selection_button.pack(side=tk.LEFT)
        selection_menu = tk.Menu(self._selection_button, tearoff=False)
        selection_menu.add_command(label="全选筛选结果", command=self._select_visible)
        selection_menu.add_command(label="清空勾选", command=self._clear_checked)
        self._selection_button.configure(menu=selection_menu)
        self._download_button = ttk.Button(self._action_bar, text="下载 PDF", command=self._download, bootstyle="secondary-outline")
        self._download_button.pack(side=tk.LEFT, padx=(8, 0))
        self._paper_actions = ttk.Menubutton(self._action_bar, text="论文操作", bootstyle="secondary-outline")
        self._paper_actions.pack(side=tk.LEFT, padx=(8, 0))
        self._paper_menu = tk.Menu(self._paper_actions, tearoff=False)
        self._paper_menu.add_command(label="补抓所选详情", command=self._fetch_details)
        self._paper_menu.add_separator()
        self._paper_menu.add_command(label="关联本地 PDF", command=self._associate_pdf)
        self._paper_menu.add_command(label="解除 PDF 关联", command=self._unlink_pdf)
        self._paper_menu.add_separator()
        self._paper_menu.add_command(label="发送到 Zotero", command=self._send_zotero)
        self._paper_actions.configure(menu=self._paper_menu)
        self._export_button = ttk.Button(self._action_bar, text="导出所选", command=self._export, bootstyle="primary")
        self._export_button.pack(side=tk.RIGHT)
        self._include_pdf = tk.BooleanVar(self.window, value=False)
        self._pdf_export_check = ttk.Checkbutton(self._action_bar, text="附带 PDF", variable=self._include_pdf)
        self.export_format = tk.StringVar(self.window, value=self._initial_format)
        formats = ttk.Frame(self._action_bar)
        formats.pack(side=tk.RIGHT, padx=10)
        for label, value in (("Excel", "xlsx"), ("CSV", "csv"), ("Zotero", "ris")):
            ttk.Radiobutton(
                formats, text=label, value=value, variable=self.export_format,
                command=self._sync_export_type, bootstyle="secondary-toolbutton",
            ).pack(side=tk.LEFT, padx=(0, 3))
        self._sync_export_type()

        self._operation_bar = ttk.Frame(footer)
        self._operation_bar.pack(fill=tk.X)
        self._continue_button = ttk.Button(self._operation_bar, text="立即继续", command=self._continue.set, bootstyle="secondary-outline")
        self._stop_button = ttk.Button(self._operation_bar, text="停止下载", command=self.cancel.set, state=tk.DISABLED, bootstyle="danger-outline")
        self._show_detail = tk.BooleanVar(self.window, value=True)

        self._panes = ttk.Panedwindow(body, orient=tk.VERTICAL)
        self._panes.pack(fill=tk.BOTH, expand=True)
        listing = ttk.Frame(self._panes)
        self._panes.add(listing, weight=3)
        listing.rowconfigure(0, weight=1)
        listing.columnconfigure(0, weight=1)
        columns = ("checked", "title", "authors", "source", "publication_date", "status")
        self.table = ttk.Treeview(listing, columns=columns, show="headings", selectmode="browse", height=10)
        self.window.style.configure("Results.Treeview", rowheight=30)
        self.table.configure(style="Results.Treeview")
        for key, label, size in (("checked", "勾选", 46), ("title", "论文标题", 470), ("authors", "作者", 130), ("source", "来源", 170), ("publication_date", "发表日期", 110), ("status", "状态", 120)):
            anchor = tk.CENTER if key == "checked" else tk.W
            self.table.heading(key, text=label, anchor=anchor, command=(lambda name=key: self._sort(name)))
            self.table.column(key, width=size, minwidth=40 if key == "checked" else 90, stretch=key == "title", anchor=anchor)
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(listing, orient=tk.VERTICAL, command=self.table.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(listing, orient=tk.HORIZONTAL, command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.bind("<Button-1>", self._click)
        self.table.bind("<space>", self._toggle_current)
        self.table.bind("<<TreeviewSelect>>", lambda _: self._show_paper())

        self._details = ttk.Frame(self._panes, padding=(0, 8, 0, 0))
        self._panes.add(self._details, weight=2)
        self._title = ttk.Label(self._details, text="", font=("TkDefaultFont", 11, "bold"), wraplength=900)
        self._title.pack(fill=tk.X)
        self._details.bind("<Configure>", lambda event: self._title.configure(wraplength=max(200, event.width - 16)))
        self._meta = ttk.Label(self._details, text="", wraplength=900)
        self._meta.pack(fill=tk.X, pady=(4, 6))
        self._notebook = ttk.Notebook(self._details)
        self._notebook.pack(fill=tk.BOTH, expand=True)
        self._texts = {}
        for name, label in (("abstract", "摘要与关键词"), ("info", "详细信息"), ("citation", "引用格式")):
            frame = ttk.Frame(self._notebook)
            self._notebook.add(frame, text=label)
            text = ScrolledText(frame, wrap=tk.WORD, height=7, font=("TkDefaultFont", 10), relief=tk.FLAT, padx=10, pady=8, spacing1=3, spacing3=3)
            text.configure(background=self.window.style.colors.inputbg, foreground=self.window.style.colors.inputfg, state=tk.DISABLED)
            text.pack(fill=tk.BOTH, expand=True)
            self._texts[name] = text
        bottom = ttk.Frame(footer)
        bottom.pack(fill=tk.X)
        self._summary = tk.StringVar(self.window)
        ttk.Label(bottom, textvariable=self._summary).pack(side=tk.LEFT)
        ttk.Checkbutton(bottom, text="显示详情", variable=self._show_detail, command=self._toggle_details).pack(side=tk.LEFT, padx=12)
        ttk.Button(bottom, text="查看知网页面", command=self._open_url, bootstyle="secondary").pack(side=tk.RIGHT)
        ttk.Button(bottom, text="打开 DOI", command=self._open_doi, bootstyle="secondary").pack(side=tk.RIGHT, padx=8)
        self._operation_status = tk.StringVar(self.window)
        ttk.Label(footer, textvariable=self._operation_status, wraplength=1100).pack(fill=tk.X, pady=(5, 0))
        self._output_text = tk.StringVar(self.window)
        self._output_label = ttk.Label(footer, textvariable=self._output_text, wraplength=1100)
        self._output_label.pack(fill=tk.X, pady=(4, 0))
        body.bind("<Configure>", lambda event: self._output_label.configure(wraplength=max(200, event.width - 8)))
        self._show_output_dir()

    def _sync_export_type(self):
        if self.export_format.get() == "ris":
            self._pdf_export_check.pack(side=tk.RIGHT, padx=(0, 8), before=self._export_button)
        else:
            self._pdf_export_check.pack_forget()

    def _show_output_dir(self):
        self._output_text.set(f"保存位置：{self.get_output_dir()}")

    def _destination(self) -> Path:
        directory = Path(self.get_output_dir()).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self._output_text.set(f"保存位置：{directory}")
        return directory

    def set_papers(self, papers: list[Paper]) -> None:
        if self.busy:
            return
        self.window.title("CNKIBug - 论文结果")
        self._papers = deduplicate_papers(copy.deepcopy(papers))
        self._checked.clear()
        self._statuses.clear()
        self._detail_statuses.clear()
        self._zotero_statuses.clear()
        self._row_statuses.clear()
        self.query.set("全部检索项")
        self.kind.set("全部类型")
        self.search.set("")
        self._query_box.configure(values=["全部检索项", *dict.fromkeys(query for paper in self._papers for query in paper.queries)])
        self._kind_box.configure(values=["全部类型", *dict.fromkeys(paper.document_type for paper in self._papers if paper.document_type)])
        self._populate()

    def _values(self, index: int):
        paper = self._papers[index]
        authors = split_values(paper.authors)
        display_authors = "、".join(authors[:2]) + (" 等" if len(authors) > 2 else "")
        status = self._row_statuses.get(index, "已下载" if paper.pdf_path else "")
        return ("☑" if index in self._checked else "☐", paper.title, display_authors, paper.source, paper.publication_date[:10], status)

    def _populate(self):
        selected = self.table.selection()
        search = self.search.get().strip().casefold()
        self._visible = [i for i, paper in enumerate(self._papers)
                         if (not search or search in (paper.title + " " + paper.authors).casefold())
                         and (self.query.get() == "全部检索项" or self.query.get() in paper.queries)
                         and (self.kind.get() == "全部类型" or self.kind.get() == paper.document_type)]
        self._visible.sort(key=lambda i: (str(getattr(self._papers[i], self._sort_key)).casefold(), i), reverse=self._descending)
        self.table.delete(*self.table.get_children())
        for index in self._visible:
            self.table.insert("", tk.END, iid=str(index), values=self._values(index))
        if selected and int(selected[0]) in self._visible:
            self.table.selection_set(selected[0])
        elif self._visible:
            self.table.selection_set(str(self._visible[0]))
        self._update_summary()
        self._show_paper()

    def _set_row_status(self, index: int, status: str) -> None:
        self._row_statuses[index] = status
        if self.table.exists(str(index)):
            self.table.item(str(index), values=self._values(index))
        if self.table.selection() == (str(index),):
            self._show_paper()

    def _sort(self, name):
        if name in {"checked", "status"}:
            return
        self._descending = not self._descending if name == self._sort_key else False
        self._sort_key = name
        self._populate()

    def _click(self, event):
        row = self.table.identify_row(event.y)
        if row and self.table.identify_column(event.x) == "#1":
            self._toggle(int(row))
            return "break"

    def _toggle_current(self, _event):
        selected = self.table.selection()
        if selected:
            self._toggle(int(selected[0]))
        return "break"

    def _toggle(self, index):
        if index in self._checked:
            self._checked.remove(index)
        else:
            self._checked.add(index)
        self.table.item(str(index), values=self._values(index))
        self._update_summary()

    def _select_visible(self):
        self._checked.update(self._visible)
        self._refresh_checks()

    def _clear_checked(self):
        self._checked.clear()
        self._refresh_checks()

    def _refresh_checks(self):
        for index in self._visible:
            self.table.item(str(index), values=self._values(index))
        self._update_summary()

    def _update_summary(self):
        self._summary.set(f"显示 {len(self._visible)} / {len(self._papers)} 篇，已勾选 {len(self._checked)} 篇")
        self._export_button.configure(state=tk.NORMAL if self._checked and not self.busy else tk.DISABLED)
        self._download_button.configure(state=tk.NORMAL if self._checked and not self.busy else tk.DISABLED)
        self._paper_actions.configure(state=tk.NORMAL if self._checked and not self.busy else tk.DISABLED)
        single = len(self._checked) == 1 and not self.busy
        self._paper_menu.entryconfigure("关联本地 PDF", state=tk.NORMAL if single else tk.DISABLED)
        linked = single and bool(self._papers[next(iter(self._checked))].pdf_path)
        self._paper_menu.entryconfigure("解除 PDF 关联", state=tk.NORMAL if linked else tk.DISABLED)

    def _current(self) -> Paper | None:
        selected = self.table.selection()
        return self._papers[int(selected[0])] if selected else None

    def _show_paper(self):
        paper = self._current()
        self._title.configure(text=paper.title if paper else "")
        self._meta.configure(text=f"{paper.authors.replace(';', '；')}  |  {paper.source}  |  {paper.publication_date}" if paper else "", wraplength=max(200, self._details.winfo_width() - 16))
        content = {"abstract": "", "info": "", "citation": ""}
        if paper:
            content["abstract"] = f"关键词：{'；'.join(split_values(paper.paper_keywords)) or '未采集'}\n\n{paper.abstract or '摘要未采集'}"
            content["citation"] = paper.citation or "引用格式未采集"
            content["info"] = "\n\n".join(f"{label}：{value or '未采集'}" for label, value in (
                ("文献类型", paper.document_type), ("DOI", paper.doi), ("作者单位", paper.institutions),
                ("基金", paper.funds), ("分类号", paper.classification), ("卷", paper.volume), ("期", paper.issue),
                ("页码", paper.pages), ("被引次数", paper.citation_count), ("下载次数", paper.download_count),
                ("命中检索项", "；".join(paper.queries)), ("详情链接", paper.detail_url),
                ("本地 PDF", paper.pdf_path),
            ))
            selected = self.table.selection()
            status = self._statuses.get(int(selected[0]), "") if selected else ""
            if status:
                content["info"] += f"\n\n下载状态：{status}"
            detail_status = self._detail_statuses.get(int(selected[0]), "") if selected else ""
            if detail_status:
                content["info"] += f"\n\n详情状态：{detail_status}"
            zotero_status = self._zotero_statuses.get(int(selected[0]), "") if selected else ""
            if zotero_status:
                content["info"] += f"\n\nZotero：{zotero_status}"
        for name, text in self._texts.items():
            text.configure(state=tk.NORMAL)
            text.delete("1.0", tk.END)
            text.insert("1.0", content[name])
            text.configure(state=tk.DISABLED)

    def _toggle_details(self):
        if self._show_detail.get():
            self._panes.add(self._details, weight=2)
            self._panes.sashpos(0, int(self._panes.winfo_height() * .55))
        else:
            self._panes.forget(self._details)

    def open_file(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self.window, title="打开论文结果", filetypes=[("CNKIBug 结果", "*.xlsx *.csv"), ("Excel", "*.xlsx"), ("CSV", "*.csv")])
        if path:
            try:
                self.set_papers(read_papers(path))
                self.window.title(f"CNKIBug - 论文结果 - {Path(path).name}")
            except Exception as error:
                messagebox.showerror("无法打开结果文件", str(error), parent=self.window)

    def _export(self):
        if not self._checked or self.busy:
            return
        papers = [self._papers[i] for i in sorted(self._checked)]
        try:
            directory = self._destination()
            name = datetime.now().strftime("cnki_selected_%Y%m%d_%H%M%S")
            extension = self.export_format.get()
            path = directory / f"{name}.{extension}"
            sequence = 2
            while path.exists():
                path = directory / f"{name}_{sequence}.{extension}"
                sequence += 1
            save_papers(path, papers, extension == "ris" and self._include_pdf.get())
            self._operation_status.set(f"已导出 {len(papers)} 篇：{path.name}")
        except Exception as error:
            messagebox.showerror("导出失败", str(error), parent=self.window)

    def _attachment_target(self):
        if self.busy:
            return None
        if len(self._checked) != 1:
            messagebox.showinfo("选择论文", "请只勾选一篇论文。", parent=self.window)
            return None
        return next(iter(self._checked))

    def _associate_pdf(self):
        index = self._attachment_target()
        if index is None:
            return
        paper = self._papers[index]
        initial = Path(paper.pdf_path).parent if paper.pdf_path else self.get_output_dir()
        filename = filedialog.askopenfilename(
            parent=self.window, title=f"关联 PDF：{paper.title}", initialdir=str(initial), filetypes=[("PDF", "*.pdf")],
        )
        if not filename:
            return
        try:
            path = associate_pdf(paper, filename)
        except (OSError, ValueError) as error:
            messagebox.showerror("无法关联 PDF", str(error), parent=self.window)
            return
        self._statuses[index] = "已关联 PDF"
        self._row_statuses[index] = "已关联 PDF"
        self._detail_statuses.pop(index, None)
        self._operation_status.set(f"已关联：{path.name}")
        self._populate()

    def _unlink_pdf(self):
        index = self._attachment_target()
        if index is None:
            return
        self._papers[index].pdf_path = ""
        self._statuses.pop(index, None)
        self._row_statuses.pop(index, None)
        self._operation_status.set("PDF 关联已解除，文件仍保存在原位置。")
        self._populate()

    @property
    def alive(self) -> bool:
        return self._download_session.alive or (self._worker is not None and self._worker.is_alive())

    def _start_operation(self, operation, *, label: str, needs_browser: bool = False):
        if self.busy or not self._checked:
            return
        if not self.can_run():
            messagebox.showinfo("任务正在运行", "请在当前抓取任务结束后处理论文。", parent=self.window)
            return
        if needs_browser and not self.prepare_browser():
            return
        items = [(index, copy.deepcopy(self._papers[index])) for index in sorted(self._checked)]
        self.cancel.clear()
        self.busy = True
        self._operation_status.set(label)
        self._open_button.configure(state=tk.DISABLED)
        self._stop_button.configure(text="停止处理", state=tk.NORMAL)
        self._stop_button.pack(side=tk.RIGHT)
        self._update_summary()

        def worker():
            message = label
            try:
                message = operation(items)
            except Exception as error:
                logging.getLogger("cnkibug.gui.results").exception("论文操作失败")
                message = str(error)
                if not self.cancel.is_set():
                    self._events.emit("paper_task_error", error=message)
            finally:
                self._worker = None
                self._events.emit("paper_task_finished", message=message, stopped=self.cancel.is_set())

        self._worker = Thread(target=worker, name="cnkibug-paper-operation", daemon=True)
        self._worker.start()

    def _fetch_details(self):
        self._start_operation(
            lambda items: fetch_selected_details(items, self.settings, self.paths, self._events),
            label="正在补抓所选论文详情", needs_browser=True,
        )

    def _send_zotero(self):
        self._start_operation(lambda items: send_papers_to_zotero(items, self._events), label="正在连接 Zotero")

    def _download(self):
        if self.busy or not self._checked:
            return
        if not self.can_run():
            messagebox.showinfo("任务正在运行", "请在当前抓取任务结束后下载论文。", parent=self.window)
            return
        if not self.prepare_browser():
            return
        dialog = DownloadDialog(self.window, self.settings.download_auth_wait_sec, self._webvpn_url)
        if not dialog.show():
            return
        if dialog.webvpn_url:
            self._webvpn_url = dialog.webvpn_url
        try:
            directory = self._destination()
        except (OSError, ValueError) as error:
            messagebox.showerror("保存位置不可用", str(error), parent=self.window)
            return
        items = [(i, copy.deepcopy(self._papers[i])) for i in sorted(self._checked)]
        self.busy = True
        self._stop_button.configure(text="停止下载", state=tk.NORMAL)
        self._stop_button.pack(side=tk.RIGHT)
        self._open_button.configure(state=tk.DISABLED)
        self._update_summary()

        self._download_session.submit(items, directory, self.settings, dialog.webvpn_url)

    def _drain(self):
        try:
            while True:
                event = self._queue.get_nowait()
                payload = event.payload
                if event.name == "paper_zotero":
                    self._zotero_statuses[payload["index"]] = payload["status"]
                    self._set_row_status(payload["index"], payload["status"])
                elif event.name == "paper_details":
                    index = payload["index"]
                    for key, value in payload["updates"].items():
                        setattr(self._papers[index], key, value)
                    self._detail_statuses[index] = payload["status"]
                    self._set_row_status(index, payload["status"])
                elif event.name in {"paper_operation_progress", "activity_started"}:
                    self._operation_status.set(payload["message"])
                elif event.name == "verify_required":
                    answers = payload.get("response_queue")
                    answer = False if self._closing or self.cancel.is_set() else messagebox.askokcancel(
                        "需要手动验证", "请在浏览器中完成安全验证，然后继续。", parent=self.window,
                    )
                    if answers is not None:
                        answers.put(answer)
                    if not answer:
                        self.cancel.set()
                elif event.name == "paper_download":
                    index = payload["index"]
                    self._detail_statuses.pop(index, None)
                    self._statuses[index] = payload["status"]
                    if payload["path"]:
                        self._papers[index].pdf_path = payload["path"]
                    self._set_row_status(index, payload["status"])
                elif event.name == "download_preparing":
                    remaining = payload["remaining"]
                    self._continue_button.configure(text="立即继续")
                    self._operation_status.set("正在打开机构 WebVPN" if payload.get("webvpn") else "正在打开知网首页" if remaining is None else f"知网首页等待：{remaining} 秒")
                    if remaining is not None:
                        self._continue_button.pack(side=tk.LEFT, padx=(8, 0), before=self._stop_button)
                elif event.name == "download_webvpn_login":
                    self._operation_status.set("请在浏览器中完成机构 WebVPN 登录")
                    self._continue_button.configure(text="登录完成，继续")
                    self._continue_button.pack(side=tk.LEFT, padx=(8, 0), before=self._stop_button)
                elif event.name == "download_manual_access":
                    self._operation_status.set("请留意浏览器中的登录或安全验证，完成后点击继续")
                    self._continue_button.configure(text="继续")
                    self._continue_button.pack(side=tk.LEFT, padx=(8, 0), before=self._stop_button)
                elif event.name == "download_prepared":
                    self._continue_button.pack_forget()
                    self._operation_status.set("正在下载所选论文")
                elif event.name == "confirm_requested":
                    answer = False if self._closing or self.cancel.is_set() else messagebox.askokcancel("请确认", payload["prompt"], parent=self.window)
                    payload["response_queue"].put(answer)
                elif event.name in {"download_error", "paper_task_error"} and not (self._closing or self._hide_when_done):
                    messagebox.showerror("论文处理结束", payload["error"], parent=self.window)
                elif event.name in {"download_finished", "paper_task_finished"}:
                    self.busy = False
                    self._continue_button.pack_forget()
                    self._stop_button.configure(state=tk.DISABLED)
                    self._stop_button.pack_forget()
                    self._open_button.configure(state=tk.NORMAL)
                    message = payload.get("message", "下载已停止" if payload.get("stopped") else "本批下载结束")
                    self._operation_status.set(f"已停止。{message}" if event.name == "paper_task_finished" and payload.get("stopped") else message)
                    self._update_summary()
                    if self._closing:
                        self.window.destroy()
                        return
                    if self._hide_when_done:
                        self._hide_when_done = False
                        self.window.withdraw()
        except Empty:
            pass
        self.window.after(100, self._drain)

    def _open_url(self):
        paper = self._current()
        if paper and paper.detail_url.startswith(("https://", "http://")):
            webbrowser.open(paper.detail_url)

    def _open_doi(self):
        paper = self._current()
        if paper and paper.doi:
            webbrowser.open("https://doi.org/" + paper.doi)

    def close(self):
        if self.busy:
            if not messagebox.askyesno("停止处理", "停止当前处理并关闭论文结果窗口？", parent=self.window):
                return
            self._download_session.close()
            if self.busy:
                self._hide_when_done = True
                return
        self.window.withdraw()

    def shutdown(self):
        self._closing = True
        self._hide_when_done = False
        self._download_session.close()
        self.busy = self.alive
        if not self.busy:
            self.window.destroy()
