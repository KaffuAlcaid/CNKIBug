from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from ..cnki.models import Paper, deduplicate_papers
from .models import (
    ACTIVE_ITEMS, BATCH_LABELS, ITEM_LABELS, destination_key, encode, new_id,
    paper_data, restore_paper, same_paper, session_key,
)


class StoreInUse(RuntimeError):
    pass


class DownloadStore:
    """One writer owns the database and executor lock for its entire lifetime."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "downloads.sqlite3"
        self._lock = (directory / "executor.lock").open("a+b")
        self._locked = False
        self.db = None
        try:
            self._lock.seek(0, os.SEEK_END)
            if self._lock.tell() == 0:
                self._lock.write(b"0")
                self._lock.flush()
            self._lock.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise StoreInUse("另一个 CNKIBug 实例正在管理此下载任务目录") from error
            self._locked = True
            self.db = sqlite3.connect(self.path, timeout=5)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA synchronous=FULL")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise ValueError(f"下载任务数据库版本 {version} 不受当前程序支持，原文件已保留")
            if version == 0:
                self._create()
        except BaseException:
            self.close()
            raise

    def _create(self):
        existing = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if existing:
            raise ValueError("下载任务数据库缺少版本信息，原文件已保留")
        self.db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE batches (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, destination TEXT NOT NULL,
                destination_key TEXT NOT NULL, settings TEXT NOT NULL, webvpn_url TEXT NOT NULL,
                session_id TEXT NOT NULL, state TEXT NOT NULL, position INTEGER NOT NULL,
                force INTEGER NOT NULL DEFAULT 0, parent_id TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL, updated REAL NOT NULL, message TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE items (
                id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                paper TEXT NOT NULL, position INTEGER NOT NULL, state TEXT NOT NULL,
                active_attempt TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', error_code TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL, updated REAL NOT NULL
            );
            CREATE TABLE attempts (
                id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                state TEXT NOT NULL, requested INTEGER NOT NULL DEFAULT 0,
                metadata TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL, updated REAL NOT NULL
            );
            CREATE TABLE artifacts (
                id TEXT PRIMARY KEY, item_id TEXT REFERENCES items(id) ON DELETE SET NULL,
                attempt_id TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL, sha256 TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY, batch_id TEXT REFERENCES batches(id) ON DELETE CASCADE,
                item_id TEXT NOT NULL DEFAULT '', attempt_id TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL, message TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, entry TEXT NOT NULL, profile TEXT NOT NULL DEFAULT '',
                browser TEXT NOT NULL DEFAULT '', updated REAL NOT NULL
            );
            CREATE INDEX items_batch ON items(batch_id,position);
            CREATE INDEX attempts_item ON attempts(item_id,created);
            CREATE INDEX events_batch ON events(batch_id,id);
            PRAGMA user_version=1;
            COMMIT;
        """)

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self._locked:
            self._lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
            self._locked = False
        self._lock.close()

    @contextmanager
    def transaction(self):
        with self.db:
            yield

    def _event(self, batch_id, kind, message="", item_id="", attempt_id=""):
        self.db.execute(
            "INSERT INTO events(batch_id,item_id,attempt_id,kind,message,created) VALUES(?,?,?,?,?,?)",
            (batch_id, item_id, attempt_id, kind, message, time.time()),
        )

    def create_batch(self, papers: list[Paper], destination: Path, settings: dict, webvpn_url="", *,
                     force=False, source="论文下载", parent_id="") -> dict:
        if not papers:
            raise ValueError("没有选择论文")
        target = destination_key(destination)
        existing = [] if force else self.db.execute(
            "SELECT i.paper FROM items i JOIN batches b ON b.id=i.batch_id "
            "WHERE b.destination_key=? AND b.state!='completed' "
            "AND i.state NOT IN ('completed','failed','skipped','removed')", (target,),
        ).fetchall()
        pending_papers = [restore_paper(row[0]) for row in existing]
        unique = deduplicate_papers(papers)
        accepted = [paper for paper in unique if not any(same_paper(paper, old) for old in pending_papers)]
        if not accepted:
            raise ValueError("所选文献已在此目录的待处理队列中")
        now, batch_id = time.time(), new_id()
        position = self.db.execute("SELECT COALESCE(MAX(position),0)+1 FROM batches").fetchone()[0]
        with self.transaction():
            self.db.execute(
                "INSERT INTO batches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (batch_id, source, str(Path(destination).resolve()), target, encode(settings), webvpn_url,
                 session_key(webvpn_url), "queued", position, int(force), parent_id, now, now, ""),
            )
            for index, paper in enumerate(accepted):
                self._insert_item(batch_id, paper, index, now)
            self._event(batch_id, "created", f"提交 {len(accepted)} 篇，合并或跳过 {len(papers)-len(accepted)} 篇")
        return {"id": batch_id, "accepted": len(accepted), "duplicates": len(papers) - len(accepted)}

    def _insert_item(self, batch_id, paper, position, now):
        self.db.execute(
            "INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (new_id(), batch_id, encode(paper_data(paper)), position, "queued", "", "", "", "", now, now),
        )

    def append(self, batch_id: str, papers: list[Paper]) -> int:
        batch = self.batch(batch_id)
        if batch["state"] == "completed":
            raise ValueError("已结束批次不能追加，请提交新批次")
        existing = [restore_paper(item["paper"]) for item in self.items(batch_id) if item["state"] != "removed"]
        accepted = [p for p in deduplicate_papers(papers) if not any(same_paper(p, old) for old in existing)]
        with self.transaction():
            position = self.db.execute("SELECT COALESCE(MAX(position),0)+1 FROM items WHERE batch_id=?", (batch_id,)).fetchone()[0]
            for offset, paper in enumerate(accepted):
                self._insert_item(batch_id, paper, position + offset, time.time())
            self._event(batch_id, "appended", f"追加 {len(accepted)} 篇")
        return len(accepted)

    def batch(self, batch_id) -> dict:
        row = self.db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if row is None:
            raise ValueError("下载批次不存在")
        result = dict(row)
        result["settings"] = json.loads(result["settings"])
        return result

    def item(self, item_id) -> dict:
        row = self.db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise ValueError("下载任务不存在")
        result = dict(row)
        result["paper"] = json.loads(result["paper"])
        result["attempt_count"] = self.db.execute("SELECT COUNT(*) FROM attempts WHERE item_id=?", (item_id,)).fetchone()[0]
        return result

    def items(self, batch_id) -> list[dict]:
        return [self.item(row[0]) for row in self.db.execute("SELECT id FROM items WHERE batch_id=? ORDER BY position,created", (batch_id,))]

    def snapshot(self) -> dict:
        batches = []
        for row in self.db.execute("SELECT id FROM batches ORDER BY position,created"):
            batch = self.batch(row[0])
            batch["items"] = self.items(batch["id"])
            batch["counts"] = {state: sum(item["state"] == state for item in batch["items"]) for state in ITEM_LABELS}
            batch["total"] = sum(item["state"] != "removed" for item in batch["items"])
            batches.append(batch)
        sessions = [dict(row) for row in self.db.execute("SELECT * FROM sessions ORDER BY updated DESC")]
        return {"batches": batches, "sessions": sessions}

    def set_batch(self, batch_id, state, message=""):
        if state not in BATCH_LABELS:
            raise ValueError("无效的批次状态")
        with self.transaction():
            self.db.execute("UPDATE batches SET state=?,message=?,updated=? WHERE id=?", (state, message, time.time(), batch_id))
            self._event(batch_id, state, message)

    def set_item(self, item_id, state, error="", code="", *, path=None):
        if state not in ITEM_LABELS:
            raise ValueError("无效的下载任务状态")
        item = self.item(item_id)
        with self.transaction():
            self.db.execute(
                "UPDATE items SET state=?,error=?,error_code=?,path=?,updated=? WHERE id=?",
                (state, error, code, item["path"] if path is None else str(path), time.time(), item_id),
            )
            self._event(item["batch_id"], state, error, item_id, item["active_attempt"])

    def begin_attempt(self, item_id, incoming: str = "") -> str:
        item = self.item(item_id)
        attempt_id, now = new_id(), time.time()
        with self.transaction():
            self.db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?)",
                            (attempt_id, item_id, "preparing", 0, encode({"incoming": incoming}), "", now, now))
            self.db.execute("UPDATE items SET active_attempt=?,state='preparing',error='',error_code='',updated=? WHERE id=?",
                            (attempt_id, now, item_id))
            self._event(item["batch_id"], "attempt_started", "", item_id, attempt_id)
        return attempt_id

    def attempt(self, attempt_id) -> dict:
        row = self.db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None:
            raise ValueError("下载尝试不存在")
        value = dict(row)
        value["metadata"] = json.loads(value["metadata"])
        return value

    def update_attempt(self, attempt_id, *, state=None, requested=None, error=None, **metadata) -> bool:
        attempt = self.attempt(attempt_id)
        item = self.item(attempt["item_id"])
        attempt["metadata"].update(metadata)
        current = item["active_attempt"] == attempt_id
        with self.transaction():
            self.db.execute("UPDATE attempts SET state=?,requested=?,metadata=?,error=?,updated=? WHERE id=?",
                            (state or attempt["state"], attempt["requested"] if requested is None else int(requested),
                             encode(attempt["metadata"]), attempt["error"] if error is None else error, time.time(), attempt_id))
            if current and state in ITEM_LABELS:
                self.db.execute("UPDATE items SET state=?,updated=? WHERE id=?", (state, time.time(), item["id"]))
            if state:
                self._event(item["batch_id"], state if current else "late_event", error or "", item["id"], attempt_id)
        return current

    def complete(self, item_id, artifact: dict, attempt_id="", *, source="download"):
        item = self.item(item_id)
        if attempt_id and item["active_attempt"] != attempt_id:
            raise ValueError("下载结果属于旧尝试，未覆盖当前任务")
        paper = restore_paper(item["paper"])
        paper.pdf_path = artifact["path"]
        now = time.time()
        with self.transaction():
            self.db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (
                new_id(), item_id, attempt_id, artifact["path"], artifact["size"], artifact["mtime_ns"], artifact["sha256"], now))
            self.db.execute("UPDATE items SET state='completed',paper=?,path=?,error='',error_code='',updated=? WHERE id=?",
                            (encode(paper_data(paper)), paper.pdf_path, now, item_id))
            if attempt_id:
                attempt = self.attempt(attempt_id)
                attempt["metadata"].update({"artifact": artifact, "source": source})
                self.db.execute("UPDATE attempts SET state='completed',metadata=?,updated=? WHERE id=?",
                                (encode(attempt["metadata"]), now, attempt_id))
            self._event(item["batch_id"], "completed", paper.pdf_path, item_id, attempt_id)

    def history(self, item_id) -> dict:
        return {
            "attempts": [self.attempt(row[0]) for row in self.db.execute("SELECT id FROM attempts WHERE item_id=? ORDER BY created", (item_id,))],
            "events": [dict(row) for row in self.db.execute("SELECT * FROM events WHERE item_id=? ORDER BY id", (item_id,))],
        }

    def recover_states(self):
        with self.transaction():
            for row in self.db.execute("SELECT id FROM items").fetchall():
                item = self.item(row[0])
                if item["state"] not in ACTIVE_ITEMS:
                    continue
                attempt = self.attempt(item["active_attempt"]) if item["active_attempt"] else None
                state = "uncertain" if attempt and attempt["requested"] else "queued"
                self.db.execute("UPDATE items SET state=?,error=?,error_code='interrupted',updated=? WHERE id=?",
                                (state, "上次执行已中断，等待核对" if state == "uncertain" else "", time.time(), item["id"]))
            self.db.execute("UPDATE batches SET state='paused',message='上次任务已保存，请核对后继续',updated=? WHERE state!='completed'",
                            (time.time(),))

    def move(self, record_id, offset, *, item=False):
        table = "items" if item else "batches"
        record = self.item(record_id) if item else self.batch(record_id)
        if record["state"] not in ({"queued"} if item else {"queued", "paused", "stopped"}):
            raise ValueError("只能调整尚未执行的任务或未运行批次")
        if item:
            rows = self.db.execute("SELECT id,position FROM items WHERE batch_id=? AND state='queued' ORDER BY position", (record["batch_id"],)).fetchall()
        else:
            rows = self.db.execute("SELECT id,position FROM batches WHERE state IN ('queued','paused','stopped') ORDER BY position").fetchall()
        index = next(i for i, row in enumerate(rows) if row["id"] == record_id)
        target = index + offset
        if 0 <= target < len(rows):
            with self.transaction():
                self.db.execute(f"UPDATE {table} SET position=? WHERE id=?", (rows[target]["position"], record_id))
                self.db.execute(f"UPDATE {table} SET position=? WHERE id=?", (rows[index]["position"], rows[target]["id"]))

    def remove_item(self, item_id):
        if self.item(item_id)["state"] != "queued":
            raise ValueError("只能移除尚未开始的任务")
        self.set_item(item_id, "removed", "用户移出队列")

    def clear_batch(self, batch_id):
        if self.batch(batch_id)["state"] != "completed":
            raise ValueError("只能清理已结束批次；PDF 文件会保留")
        with self.transaction():
            self.db.execute("DELETE FROM batches WHERE id=?", (batch_id,))

    def set_destination(self, batch_id, destination):
        batch = self.batch(batch_id)
        if batch["state"] == "running":
            raise ValueError("请先停止批次再更换目录")
        if any(item["state"] in {"uncertain", "finalizing"} for item in self.items(batch_id)):
            raise ValueError("请先核对结果待确认的任务，再更换目录")
        target = str(Path(destination).expanduser().resolve())
        with self.transaction():
            self.db.execute("UPDATE batches SET destination=?,destination_key=?,updated=? WHERE id=?",
                            (target, destination_key(target), time.time(), batch_id))
            self._event(batch_id, "destination_changed", target)

    def save_session(self, session_id, entry, profile, browser):
        with self.transaction():
            self.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                            "entry=excluded.entry,profile=excluded.profile,browser=excluded.browser,updated=excluded.updated",
                            (session_id, entry, str(profile), browser, time.time()))
