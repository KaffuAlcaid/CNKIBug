from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from ..cnki.models import Paper, document_type_family, normalize_doi
from ..fileio.pdf_links import paper_keys


ITEM_LABELS = {
    "queued": "等待执行", "preparing": "准备中", "waiting": "等待处理",
    "requested": "已发起", "transferring": "下载中", "finalizing": "保存中",
    "completed": "已完成", "failed": "失败", "uncertain": "结果待确认",
    "skipped": "已跳过", "removed": "已移除",
}
BATCH_LABELS = {
    "queued": "排队中", "running": "运行中", "paused": "已暂停",
    "stopped": "已停止", "blocked": "需要处理", "completed": "已结束",
}
TERMINAL_ITEMS = {"completed", "failed", "skipped", "removed"}
ACTIVE_ITEMS = {"preparing", "waiting", "requested", "transferring", "finalizing"}
UNFINISHED_BATCHES = {"queued", "running", "paused", "stopped", "blocked"}


class DownloadProblem(RuntimeError):
    def __init__(self, message: str, code: str = "download_failed", *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class DownloadStopped(DownloadProblem):
    def __init__(self, message: str = "已停止"):
        super().__init__(message, "stopped")


def new_id() -> str:
    return uuid4().hex


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def paper_data(paper: Paper) -> dict:
    return asdict(paper)


def restore_paper(value: str | dict) -> Paper:
    data = json.loads(value) if isinstance(value, str) else value
    allowed = {field.name for field in fields(Paper)}
    return Paper(**{key: value for key, value in data.items() if key in allowed})


def same_paper(first: Paper, second: Paper) -> bool:
    a, b = normalize_doi(first.doi).casefold(), normalize_doi(second.doi).casefold()
    if a and b and a != b:
        return False
    a_type, b_type = document_type_family(first.document_type), document_type_family(second.document_type)
    if a_type and b_type and a_type != b_type:
        return False
    return bool(set(paper_keys(first)) & set(paper_keys(second)))


def destination_key(path: str | Path) -> str:
    import os

    return os.path.normcase(str(Path(path).expanduser().resolve()))


def session_key(webvpn_url: str) -> str:
    from hashlib import sha256

    if not webvpn_url:
        return "direct"
    parsed = urlsplit(webvpn_url)
    # www/kns are two entry points into the same institutional proxy.
    host = (parsed.hostname or "").split(".", 1)[-1]
    identity = f"{parsed.scheme}://{host}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
    return "webvpn-" + sha256(identity.encode()).hexdigest()[:20]
