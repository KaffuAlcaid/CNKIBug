from __future__ import annotations

import copy
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Event
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

import ttkbootstrap as ttk

from ..cnki.downloads import DownloadSession
from ..cnki.models import Paper, deduplicate_papers
from ..fileio.papers import read_papers, save_papers, split_values
from ..fileio.paths import get_real_desktop_path
from .events import GuiEvent, GuiEventSink
from .download_dialog import DownloadDialog


class ResultsWindow:
    def __init__(self, parent, papers: list[Paper], *, settings, paths, output_dir: Path | None = None,
                 get_output_dir=None, initial_format="xlsx", can_download=lambda: True):
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
        self.can_download = can_download
        self.busy = False
        self._closing = False
        self._queue: Queue[GuiEvent] = Queue()
        self.cancel = Event()
        self._continue = Event()
        self._events = GuiEventSink(self._queue, self.cancel)
        self._download_session = DownloadSession(paths, self._events, self.cancel, self._continue)
        self._webvpn_url = ""
        self._checked: set[int] = set()
        self._statuses: dict[int, str] = {}
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
        toolbar.pack(fill=tk.X, pady=(0, 8))
        self._open_button = ttk.Button(toolbar, text="打开文件", command=self.open_file, bootstyle="secondary")
        self._open_button.pack(side=tk.LEFT, padx=(0, 8))
        self.search = tk.StringVar(self.window)
        ttk.Label(toolbar, text="标题 / 作者").pack(side=tk.LEFT, padx=(0, 6))
        search = ttk.Entry(toolbar, textvariable=self.search)
        search.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.search.trace_add("write", lambda *_: self._populate())
        self._download_button = ttk.Button(toolbar, text="下载 PDF", command=self._download)
        self._download_button.pack(side=tk.LEFT)
        self._continue_button = ttk.Button(toolbar, text="立即继续", command=self._continue.set, bootstyle="secondary")
        self._stop_button = ttk.Button(toolbar, text="停止下载", command=self.cancel.set, state=tk.DISABLED, bootstyle="danger-outline")
        self._stop_button.pack(side=tk.LEFT, padx=(8, 0))

        export_row = ttk.Frame(body)
        export_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(export_row, text="导出类型").pack(side=tk.LEFT, padx=(0, 10))
        self.export_format = tk.StringVar(self.window, value=self._initial_format)
        for label, value in (("Excel (.xlsx)", "xlsx"), ("CSV (.csv)", "csv"), ("Zotero (.ris)", "ris")):
            ttk.Radiobutton(export_row, text=label, value=value, variable=self.export_format,
                            command=self._sync_export_type).pack(side=tk.LEFT, padx=(0, 14))
        self._include_pdf = tk.BooleanVar(self.window, value=False)
        self._pdf_export_check = ttk.Checkbutton(export_row, text="包含已下载附件", variable=self._include_pdf)
        self._pdf_export_check.pack(side=tk.LEFT, padx=(0, 10))
        self._export_button = ttk.Button(export_row, text="导出所选", command=self._export, bootstyle="secondary")
        self._export_button.pack(side=tk.RIGHT)
        self._sync_export_type()

        filters = ttk.Frame(body)
        filters.pack(fill=tk.X, pady=(0, 8))
        self.query = tk.StringVar(self.window, value="全部检索项")
        self._query_box = ttk.Combobox(filters, textvariable=self.query, state="readonly", width=24)
        self._query_box.pack(side=tk.LEFT)
        self._query_box.bind("<<ComboboxSelected>>", lambda _: self._populate())
        self.kind = tk.StringVar(self.window, value="全部类型")
        self._kind_box = ttk.Combobox(filters, textvariable=self.kind, state="readonly", width=12)
        self._kind_box.pack(side=tk.LEFT, padx=8)
        self._kind_box.bind("<<ComboboxSelected>>", lambda _: self._populate())
        ttk.Button(filters, text="全选筛选结果", command=self._select_visible, bootstyle="secondary").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(filters, text="清空勾选", command=self._clear_checked, bootstyle="secondary").pack(side=tk.LEFT)
        self._show_detail = tk.BooleanVar(self.window, value=True)
        ttk.Checkbutton(filters, text="显示详情", variable=self._show_detail, command=self._toggle_details).pack(side=tk.RIGHT)

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
        for key, label, size in (("checked", "勾选", 46), ("title", "论文标题", 470), ("authors", "作者", 130), ("source", "来源", 170), ("publication_date", "发表日期", 110), ("status", "下载状态", 120)):
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
        self._pdf_export_check.configure(state=tk.NORMAL if self.export_format.get() == "ris" else tk.DISABLED)

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
        self._papers = deduplicate_papers(copy.deepcopy(papers))
        self._checked.clear()
        self._statuses.clear()
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
        return ("☑" if index in self._checked else "☐", paper.title, display_authors, paper.source, paper.publication_date[:10], self._statuses.get(index, "已下载" if paper.pdf_path else ""))

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
            ))
            selected = self.table.selection()
            status = self._statuses.get(int(selected[0]), "") if selected else ""
            if status:
                content["info"] += f"\n\n下载状态：{status}"
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

    def _download(self):
        if self.busy or not self._checked:
            return
        if not self.can_download():
            messagebox.showinfo("任务正在运行", "请在当前抓取任务结束后下载论文。", parent=self.window)
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
        self._stop_button.configure(state=tk.NORMAL)
        self._open_button.configure(state=tk.DISABLED)
        self._update_summary()

        self._download_session.submit(items, directory, self.settings, dialog.webvpn_url)

    def _drain(self):
        try:
            while True:
                event = self._queue.get_nowait()
                payload = event.payload
                if event.name == "paper_download":
                    index = payload["index"]
                    self._statuses[index] = payload["status"]
                    if payload["path"]:
                        self._papers[index].pdf_path = payload["path"]
                    if self.table.exists(str(index)):
                        self.table.item(str(index), values=self._values(index))
                    if self.table.selection() == (str(index),):
                        self._show_paper()
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
                    answer = False if self._closing or self.cancel.is_set() else messagebox.askokcancel("知网页面", payload["prompt"], parent=self.window)
                    payload["response_queue"].put(answer)
                elif event.name == "download_error" and not self._closing:
                    messagebox.showerror("下载任务结束", payload["error"], parent=self.window)
                elif event.name == "download_finished":
                    self.busy = self._closing
                    self._continue_button.pack_forget()
                    self._stop_button.configure(state=tk.DISABLED)
                    self._open_button.configure(state=tk.NORMAL)
                    self._operation_status.set("下载已停止" if payload.get("stopped") else "本批下载结束")
                    self._update_summary()
                elif event.name == "download_session_closed":
                    self.busy = False
                    self._continue_button.pack_forget()
                    self._stop_button.configure(state=tk.DISABLED)
                    self._open_button.configure(state=tk.NORMAL)
                    self._update_summary()
                    if self._closing:
                        self.window.destroy()
                        return
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
            if messagebox.askyesno("停止下载", "停止下载并关闭论文结果窗口？", parent=self.window):
                self.shutdown()
            return
        self.shutdown()

    def shutdown(self):
        self._closing = True
        self._download_session.close()
        self.busy = self._download_session.alive
        if not self.busy:
            self.window.destroy()
