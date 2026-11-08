from __future__ import annotations

import asyncio
import copy
import csv
import logging
import threading
import time
from dataclasses import asdict
from pathlib import Path

from ..core.events import EventSink
from ..core.settings import ScraperSettings, get_scraper_settings
from ..fileio.pdf_links import PdfIndex, pdf_problem, resolve_pdf_path
from .files import discard_incoming, inspect_pdf, owned_incoming, publish_file, unique_target
from .models import ACTIVE_ITEMS, DownloadProblem, DownloadStopped, restore_paper
from .store import DownloadStore


_logger = logging.getLogger("cnkibug.downloads")


class DownloadControl:
    def __init__(self):
        self.paused = False
        self.stopped = False
        self.skip = False
        self.continued = asyncio.Event()

    async def check(self, *, allow_pause=True):
        while True:
            if self.stopped or self.skip:
                raise DownloadStopped("已跳过" if self.skip else "已停止")
            if not allow_pause or not self.paused:
                return
            await asyncio.sleep(0.1)

    async def wait(self, seconds, *, allow_pause=True):
        remaining = seconds
        while remaining > 0:
            await self.check(allow_pause=allow_pause)
            interval = min(0.1, remaining)
            await asyncio.sleep(interval)
            remaining -= interval


class DownloadService:
    """The UI sends commands; one asyncio thread owns all task and browser state."""

    def __init__(self, paths, events: EventSink | None = None, *, browser_factory=None):
        self.paths = paths
        self.events = events or EventSink()
        self.browser_factory = browser_factory
        self._snapshot = {"batches": [], "sessions": [], "paused": True, "busy": False}
        self._snapshot_lock = threading.Lock()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._thread_main, name="cnkibug-download-tasks", daemon=True)
        self._loop = None
        self._startup_error = None
        self._closed = False
        self._thread.start()
        if not self._ready.wait(10):
            raise RuntimeError("下载任务服务启动超时")
        if self._startup_error:
            raise self._startup_error

    @property
    def alive(self):
        return self._thread.is_alive()

    @property
    def busy(self):
        return self.snapshot().get("busy", False)

    def snapshot(self):
        with self._snapshot_lock:
            return copy.deepcopy(self._snapshot)

    def request(self, action, **kwargs):
        if self._closed or self._loop is None:
            raise RuntimeError("下载任务服务已关闭")
        return asyncio.run_coroutine_threadsafe(self._command(action, **kwargs), self._loop)

    def submit(self, papers, destination, settings: ScraperSettings, webvpn_url="", *, force=False, source="论文下载"):
        return self.request("submit", papers=copy.deepcopy(papers), destination=str(destination),
                            settings=asdict(settings), webvpn_url=webvpn_url, force=force, source=source)

    def close(self):
        if not self._closed and self.alive:
            self.request("shutdown")

    def wait_closed(self, timeout=10):
        self._thread.join(timeout)
        return not self.alive

    def _thread_main(self):
        try:
            asyncio.run(self._main())
        except Exception as error:
            _logger.exception("下载任务服务停止")
            self._startup_error = error
            self.events.emit("download_service_error", error=str(error))
        finally:
            self._closed = True
            self._ready.set()
            self.events.emit("download_service_closed")

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self.store = DownloadStore(self.paths.data_dir / "tasks")
        self._paused = True
        self._external_busy = False
        self._shutdown = False
        self._active_batch = ""
        self._active_item = ""
        self._attempt_id = ""
        self._browser = None
        self._control = None
        self._progress = {}
        self._reconciling = True
        self._last_publish = 0.0
        self._wake = asyncio.Event()
        try:
            self.store.recover_states()
            self._publish()
            self._ready.set()
            await self._reconcile_all()
            self._reconciling = False
            self._publish()
            while not self._shutdown:
                if not self._paused and not self._external_busy:
                    batch = next((b for b in self.store.snapshot()["batches"] if b["state"] == "queued"), None)
                    if batch:
                        await self._execute_batch(batch)
                        continue
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), 0.25)
                except asyncio.TimeoutError:
                    pass
        finally:
            if self._browser:
                await self._browser.close()
            self.store.close()

    def _publish(self, *, progress_only=False):
        if progress_only and time.monotonic() - self._last_publish < 0.25:
            return
        self._last_publish = time.monotonic()
        value = self.store.snapshot()
        value.update(paused=self._paused, busy=bool(self._active_batch), external_busy=self._external_busy,
                     active_batch=self._active_batch, active_item=self._active_item,
                     progress=dict(self._progress), closing=self._shutdown, recovering=self._reconciling)
        with self._snapshot_lock:
            self._snapshot = value
        self.events.emit("download_tasks_changed")

    async def _command(self, action, **kwargs):
        if self._reconciling and action not in {"shutdown", "external_busy", "history"}:
            raise ValueError("正在核对上次下载，请稍后操作")
        try:
            result = await self._apply_command(action, **kwargs)
            self._publish()
            self._wake.set()
            return result
        except Exception as error:
            self.events.emit("download_command_error", action=action, error=str(error))
            raise

    async def _apply_command(self, action, **kw):
        batch_id, item_id = kw.get("batch_id", ""), kw.get("item_id", "")
        if action == "submit":
            from ..cnki.downloads import validate_webvpn_url

            if kw["webvpn_url"]:
                validate_webvpn_url(kw["webvpn_url"])
            previous = self.store.snapshot()["batches"]
            result = self.store.create_batch(kw["papers"], Path(kw["destination"]), kw["settings"], kw["webvpn_url"],
                                             force=kw["force"], source=kw["source"])
            if not any(b["state"] != "completed" for b in previous):
                self._paused = False
            return result
        if action == "external_busy":
            if kw["value"] and self._active_batch:
                raise ValueError("下载任务仍在执行，请先暂停或停止")
            self._external_busy = bool(kw["value"])
        elif action == "pause":
            self._paused = True
            if self._control:
                self._control.paused = True
                self.store.set_batch(self._active_batch, "paused", "当前文件完成后暂停；准备阶段不再发起下载")
        elif action == "resume":
            target = batch_id or self._active_batch
            if target:
                if any(i["state"] in {"uncertain", "finalizing"} for i in self.store.items(target)):
                    await self._reconcile_batch(target)
                if any(i["state"] in {"uncertain", "finalizing"} for i in self.store.items(target)):
                    raise ValueError("此批次有待确认或待登记的文件，请先核对、关联文件或跳过")
                self.store.set_batch(target, "running" if target == self._active_batch else "queued")
            else:
                for batch in self.store.snapshot()["batches"]:
                    if batch["state"] in {"paused", "stopped"}:
                        self.store.set_batch(batch["id"], "queued")
            self._paused = False
            if self._control:
                self._control.paused = False
        elif action == "continue_user":
            if self._control:
                self._control.continued.set()
        elif action == "stop":
            self._paused = True
            if self._control:
                self._control.stopped = True
            elif batch_id:
                self.store.set_batch(batch_id, "stopped", "已停止，任务可继续")
        elif action == "skip":
            if item_id == self._active_item and self._control:
                self._control.skip = True
            else:
                item = self.store.item(item_id)
                if item["state"] == "completed":
                    raise ValueError("已完成的文件无需跳过")
                self.store.set_item(item_id, "skipped", "用户跳过")
        elif action == "retry":
            batch = self.store.batch(batch_id)
            selected = set(kw.get("item_ids", []))
            candidates = [i for i in self.store.items(batch_id)
                          if (i["id"] in selected if selected else i["state"] == "failed")]
            if any(i["id"] == self._active_item for i in candidates):
                raise ValueError("请先结束当前论文的执行")
            if not candidates:
                raise ValueError("没有可重试的论文")
            return self.store.create_batch([restore_paper(i["paper"]) for i in candidates], Path(batch["destination"]),
                                           batch["settings"], batch["webvpn_url"], force=True,
                                           source=f"重试：{batch['title']}", parent_id=batch_id)
        elif action == "append":
            return self.store.append(batch_id, kw["papers"])
        elif action == "move":
            self.store.move(item_id or batch_id, kw["offset"], item=bool(item_id))
        elif action == "remove":
            self.store.remove_item(item_id)
        elif action == "clear":
            self.store.clear_batch(batch_id)
        elif action == "destination":
            self.store.set_destination(batch_id, kw["destination"])
        elif action == "history":
            return self.store.history(item_id)
        elif action == "recheck":
            if batch_id == self._active_batch:
                raise ValueError("请先停止批次再核对文件")
            self._reconciling = True
            try:
                await self._reconcile_batch(batch_id)
            finally:
                self._reconciling = False
        elif action == "associate":
            if item_id == self._active_item:
                raise ValueError("请先结束当前论文的执行")
            artifact = await asyncio.to_thread(inspect_pdf, Path(kw["path"]))
            item = self.store.item(item_id)
            batch = self.store.batch(item["batch_id"])
            paper = restore_paper(item["paper"])
            paper.pdf_path = artifact["path"]
            PdfIndex(batch["destination"]).record(paper, outcome="linked", source="manual")
            attempt_id = self.store.begin_attempt(item_id)
            self.store.complete(item_id, artifact, attempt_id, source="manual")
            self.events.emit("download_task_result", paper=asdict(paper))
        elif action == "export":
            batch = self.store.batch(batch_id)
            rows = self.store.items(batch_id)
            with Path(kw["path"]).open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["标题", "DOI", "详情链接", "状态", "PDF路径", "原因"])
                from .models import ITEM_LABELS
                for item in rows:
                    paper = item["paper"]
                    writer.writerow([paper["title"], paper["doi"], paper["detail_url"], ITEM_LABELS[item["state"]], item["path"], item["error"]])
            return batch["id"]
        elif action == "reset_session":
            if self._browser:
                raise ValueError("请先停止下载再清除浏览器会话")
            import shutil

            key = kw["session_id"]
            if key != "direct" and (not key.startswith("webvpn-") or not all(c in "0123456789abcdef" for c in key[7:])):
                raise ValueError("无效的浏览器会话")
            root = (self.paths.data_dir / "browser_profiles" / "downloads").resolve()
            target = (root / key).resolve()
            if target.parent != root or target.is_symlink():
                raise ValueError("浏览器会话目录无效")
            if target.exists():
                await asyncio.to_thread(shutil.rmtree, target)
            target.mkdir(parents=True, exist_ok=True)
            (target / ".no_legacy_import").touch()
        elif action == "shutdown":
            self._shutdown = self._paused = True
            if self._control:
                self._control.stopped = True
        else:
            raise ValueError("未知的下载任务操作")

    def _browser_event(self, kind, **payload):
        if kind == "session":
            self.store.save_session(payload["session_id"], payload["entry"], payload["profile"], payload["browser"])
        elif kind == "progress":
            self._progress.update(payload)
        elif kind == "phase":
            self._progress.update(stage=payload["state"], message=payload.get("message", ""))
            if self._attempt_id:
                self.store.update_attempt(self._attempt_id, state=payload["state"])
        elif kind == "evidence" and self._attempt_id:
            self.store.update_attempt(self._attempt_id, **payload)
        self._publish(progress_only=kind == "progress")

    async def _execute_batch(self, batch):
        self._active_batch = batch["id"]
        self._control = DownloadControl()
        self._progress = {}
        self.store.set_batch(batch["id"], "running")
        self._publish()
        try:
            while not self._shutdown:
                await self._control.check()
                item = next((i for i in self.store.items(batch["id"]) if i["state"] == "queued"), None)
                if item is None:
                    if any(i["state"] in {"uncertain", "finalizing", "waiting"} for i in self.store.items(batch["id"])):
                        self.store.set_batch(batch["id"], "blocked", "有需要核对或处理的论文")
                        self._paused = True
                    else:
                        self.store.set_batch(batch["id"], "completed")
                    break
                self._active_item = item["id"]
                self._attempt_id = ""
                self._control.skip = False
                self._progress = {}
                await self._execute_item(batch, item)
                self._active_item = self._attempt_id = ""
                self._publish()
                if self._paused and not self._control.paused:
                    break
                if self._browser:
                    await self._control.wait(2.0)
        except DownloadStopped:
            self.store.set_batch(batch["id"], "stopped", "已停止，未完成项可继续")
        except Exception as error:
            _logger.exception("下载批次中断")
            self.store.set_batch(batch["id"], "blocked", str(error))
            self._paused = True
            if self._active_item:
                await self._record_failure(error)
        finally:
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    _logger.exception("关闭下载浏览器失败")
            self._browser = self._control = None
            self._active_item = self._active_batch = self._attempt_id = ""
            self._progress = {}
            self._publish()

    async def _execute_item(self, batch, item):
        paper = restore_paper(item["paper"])
        destination = Path(batch["destination"])
        try:
            destination.mkdir(parents=True, exist_ok=True)
            index = PdfIndex(destination)
            old = index.find(paper)
            if not paper.pdf_path and old and old["outcome"] != "unlinked":
                paper.pdf_path = resolve_pdf_path(old["path"], destination)
            if not batch["force"] and not pdf_problem(paper.pdf_path):
                artifact = await asyncio.to_thread(inspect_pdf, Path(paper.pdf_path))
                index.record(paper, outcome="linked", source=old["source"] if old else "manual")
                self.store.complete(item["id"], artifact, source="existing")
                self.events.emit("download_task_result", paper=asdict(paper))
                return
            self._attempt_id = self.store.begin_attempt(item["id"])
            incoming = owned_incoming(destination, self._attempt_id)
            incoming.mkdir(parents=True, exist_ok=True)
            self.store.update_attempt(self._attempt_id, incoming=str(incoming))
            if self._browser is None:
                if self.browser_factory is None:
                    from .browser import ManagedDownloadBrowser
                    factory = ManagedDownloadBrowser
                else:
                    factory = self.browser_factory
                self._browser = factory(self.paths, batch, get_scraper_settings(batch["settings"]), self._control, self._browser_event)
                await self._browser.open()
            await self._control.check()

            def before_click():
                self.store.update_attempt(self._attempt_id, state="requested", requested=True)
                self._publish()

            source = await self._browser.download(paper, incoming, before_click)
            await self._finalize(item["id"], self._attempt_id, Path(source), destination)
        except Exception as error:
            await self._record_failure(error)
            if isinstance(error, DownloadStopped) and not self._control.skip:
                raise
            if self._control.skip:
                self._control.skip = False
            if self._paused and not self._control.paused:
                self.store.set_batch(batch["id"], "blocked", str(error))

    async def _record_failure(self, error):
        item = self.store.item(self._active_item)
        attempt = self.store.attempt(self._attempt_id) if self._attempt_id else None
        code = getattr(error, "code", "local_error" if isinstance(error, OSError) else "download_failed")
        if attempt and attempt["metadata"].get("target"):
            state = "finalizing"
            self._paused = True
        elif attempt and attempt["requested"] and code not in {"cancelled", "access_denied", "no_pdf"}:
            state = "uncertain"
            self._paused = True
        elif isinstance(error, DownloadStopped) or code == "cancelled":
            state = "skipped" if self._control.skip else "queued"
        else:
            state = "failed"
            if code in {"local_error", "browser_closed", "verification_timeout", "session_error", "rate_limited"}:
                self._paused = True
        if attempt:
            self.store.update_attempt(attempt["id"], state=state, error=str(error))
        self.store.set_item(item["id"], state, str(error), code)
        self._publish()

    async def _finalize(self, item_id, attempt_id, source: Path, destination: Path):
        artifact = await asyncio.to_thread(inspect_pdf, source)
        item = self.store.item(item_id)
        paper = restore_paper(item["paper"])
        target = unique_target(destination, paper.title)
        while True:
            self.store.update_attempt(attempt_id, state="finalizing", source=str(source), target=str(target), artifact=artifact)
            self._publish()
            try:
                await asyncio.to_thread(publish_file, source, target)
                break
            except FileExistsError:
                target = unique_target(destination, paper.title)
        artifact = await asyncio.to_thread(inspect_pdf, target)
        if artifact["sha256"] != self.store.attempt(attempt_id)["metadata"]["artifact"]["sha256"]:
            raise ValueError("正式文件与下载内容校验不一致")
        paper.pdf_path = str(target)
        PdfIndex(destination).record(paper, outcome="downloaded", source="download")
        self.store.complete(item_id, artifact, attempt_id)
        discard_incoming(destination, attempt_id)
        self.events.emit("download_task_result", paper=asdict(paper))

    async def _reconcile_all(self):
        for batch in self.store.snapshot()["batches"]:
            if batch["state"] != "completed":
                await self._reconcile_batch(batch["id"])

    async def _reconcile_batch(self, batch_id):
        batch = self.store.batch(batch_id)
        for item in self.store.items(batch_id):
            attempt = self.store.attempt(item["active_attempt"]) if item["active_attempt"] else None
            metadata = attempt["metadata"] if attempt else {}
            target = item["path"] or metadata.get("target", "")
            expected = metadata.get("artifact", {}).get("sha256", "")
            if target and Path(target).is_file():
                try:
                    artifact = await asyncio.to_thread(inspect_pdf, Path(target))
                    if expected and artifact["sha256"] != expected:
                        self.store.set_item(item["id"], "uncertain", "文件内容与保存记录不一致，请核对", "file_changed")
                        continue
                    paper = restore_paper(item["paper"])
                    paper.pdf_path = target
                    PdfIndex(batch["destination"]).record(paper, outcome="linked", source=metadata.get("source", "download"))
                    if item["state"] != "completed":
                        self.store.complete(item["id"], artifact, item["active_attempt"])
                        self.events.emit("download_task_result", paper=asdict(paper))
                    continue
                except (OSError, ValueError) as error:
                    self.store.set_item(item["id"], "finalizing", str(error), "registration_failed")
                    continue
            if item["state"] == "completed":
                self.store.set_item(item["id"], "failed", "原 PDF 文件缺失，请关联文件或重新下载", "file_missing")
                continue
            if attempt:
                incoming = owned_incoming(batch["destination"], attempt["id"])
                sources = [Path(metadata["source"])] if metadata.get("source") else []
                if incoming.is_dir():
                    sources.extend(p for p in incoming.iterdir() if p.is_file() and p.suffix not in {".crdownload", ".part", ".tmp"})
                for source in dict.fromkeys(sources):
                    if not source.is_file() or source.parent.resolve() != incoming.resolve():
                        continue
                    try:
                        artifact = await asyncio.to_thread(inspect_pdf, source)
                        if expected and artifact["sha256"] != expected:
                            continue
                        # Native GUID files are renamed by Chromium after completion.
                        complete = metadata.get("transport_completed") or source.name == metadata.get("guid") or bool(expected)
                        if not complete:
                            continue
                        await self._finalize(item["id"], attempt["id"], source, Path(batch["destination"]))
                        break
                    except (OSError, ValueError) as error:
                        self.store.set_item(item["id"], "finalizing", str(error), "registration_failed")
                        break
