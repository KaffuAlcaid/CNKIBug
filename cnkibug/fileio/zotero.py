from __future__ import annotations

import html
import http.client
import json
from pathlib import Path
from uuid import uuid4

from ..cnki.models import Paper, document_type_family, normalize_doi, split_authors
from ..core.events import EventSink
from .papers import split_values
from .zotero_library import (
    ZoteroReadCancelled, check_library_unchanged, library_prefix, read_library, review_papers,
)


_ITEM_TYPES = {
    "journal": "journalArticle", "thesis": "thesis",
    "conference": "conferencePaper", "book": "book", "book_section": "bookSection",
    "newspaper": "newspaperArticle", "patent": "patent", "standard": "report",
    "statute": "statute", "video": "videoRecording",
}


def build_zotero_item(paper: Paper, item_id: str) -> dict:
    item_type = _ITEM_TYPES.get(document_type_family(paper.document_type), "document")
    creator_type = {"patent": "inventor", "videoRecording": "director"}.get(item_type, "author")
    item = {
        "id": item_id, "itemType": item_type, "title": paper.title,
        "creators": [{"name": name, "creatorType": creator_type} for name in split_authors(paper.authors)],
        "abstractNote": paper.abstract, "date": paper.publication_date, "url": paper.detail_url,
        "DOI": normalize_doi(paper.doi), "libraryCatalog": "CNKI",
        "tags": [{"tag": value, "type": 1} for value in split_values(paper.paper_keywords)],
        "attachments": [],
    }
    source_field = {
        "journalArticle": "publicationTitle", "newspaperArticle": "publicationTitle",
        "conferencePaper": "proceedingsTitle", "bookSection": "bookTitle", "thesis": "university",
    }.get(item_type)
    if source_field and paper.source:
        item[source_field] = paper.source
    if item_type in {"journalArticle", "newspaperArticle", "conferencePaper", "bookSection"}:
        item.update({key: getattr(paper, key) for key in ("volume", "issue", "pages") if getattr(paper, key)})
    if item_type == "thesis" and paper.document_type:
        item["thesisType"] = paper.document_type
    notes = [f"{label}：{value}" for label, value in (
        ("文献类型", paper.document_type), ("来源", paper.source), ("作者", paper.authors),
        ("作者单位", paper.institutions), ("基金", paper.funds), ("分类号", paper.classification),
        ("页码", paper.pages), ("引用格式", paper.citation),
    ) if value]
    notes.extend(f"命中检索项：{query}" for query in paper.queries if query.strip())
    if notes:
        item["notes"] = [{"note": "<p>" + "<br>".join(html.escape(line) for line in notes) + "</p>"}]
    return item


def _request(endpoint: str, data: dict | None = None, *, body=None, headers=None, expected: int = 200):
    request_headers = {"Content-Type": "application/json", "X-Zotero-Connector-API-Version": "3"}
    request_headers.update(headers or {})
    if body is None:
        body = json.dumps(data or {}, ensure_ascii=False).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", 23119, timeout=30)
    try:
        connection.request("POST", "/connector/" + endpoint, body=body, headers=request_headers)
        response = connection.getresponse()
        text = response.read().decode("utf-8", errors="replace")
        try:
            result = json.loads(text) if text else {}
        except ValueError:
            result = text
        if response.status != expected:
            detail = result.get("error", text) if isinstance(result, dict) else result
            raise RuntimeError(f"Zotero {endpoint} 失败（HTTP {response.status}）：{str(detail)[:300]}")
        return result
    except ConnectionRefusedError as error:
        raise RuntimeError("无法连接 Zotero，请确认 Zotero 桌面端已启动。") from error
    except (OSError, http.client.HTTPException) as error:
        raise RuntimeError("Zotero 连接中断或响应超时。请先检查文献库中的结果，再决定是否重新发送。") from error
    finally:
        connection.close()


def _selected_target() -> tuple[str, dict]:
    data = _request("getSelectedCollection")
    if not isinstance(data, dict) or not data.get("libraryID"):
        raise RuntimeError("无法读取 Zotero 的目标文库。")
    if not data.get("libraryEditable") or not data.get("editable"):
        raise RuntimeError("Zotero 当前分类不可写，请选择可写的文库或分类。")
    target = f"C{data['id']}" if data.get("id") else f"L{data['libraryID']}"
    return target, data


def send_papers_to_zotero(items: list[tuple[int, Paper]], events: EventSink) -> str:
    if not items or events.cancel_requested():
        return "没有需要发送的论文"
    target, selected = _selected_target()
    destination = f"{selected['libraryName']} / {selected['name']}" if selected.get("id") else selected["libraryName"]
    events.emit("paper_operation_progress", message="正在读取 Zotero 文库并检查重复文献")
    try:
        snapshot = read_library(library_prefix(selected, events), events)
        rows = review_papers(items, snapshot.papers)
        chosen = events.request("zotero_review_requested", destination=destination, rows=rows)
        if chosen is None or events.cancel_requested():
            return "发送已取消"
        eligible = {row.index for row in rows if row.status != "duplicate"}
        if not isinstance(chosen, list) or any(type(index) is not int or index not in eligible for index in chosen):
            raise RuntimeError("待发送文献选择无效，未发送任何条目。")
        check_library_unchanged(snapshot, events)
    except ZoteroReadCancelled:
        return "发送已取消"
    current, current_selection = _selected_target()
    if current != target or current_selection["libraryID"] != selected["libraryID"]:
        raise RuntimeError("Zotero 的目标分类已变化，请确认分类后重新发送。")
    chosen = set(chosen)
    for row in rows:
        if row.index not in chosen:
            status = "文库已有，已跳过" if row.status == "duplicate" else "未选择发送"
            if row.reason.startswith("本次列表"):
                status = "本次列表重复，已跳过"
            events.emit("paper_zotero", index=row.index, status=status)
    skipped = len(items) - len(chosen)
    items = [(index, paper) for index, paper in items if index in chosen]
    if not items or events.cancel_requested():
        return f"Zotero：未发送条目，跳过 {skipped} 篇"
    pdf_items = [(index, paper) for index, paper in items if paper.pdf_path]
    pdf_count = len(pdf_items)
    session_id = uuid4().hex
    documents = [build_zotero_item(paper, f"paper-{index}") for index, paper in items]
    events.emit("paper_operation_progress", message=f"正在向 Zotero 发送 {len(items)} 篇论文")
    try:
        _request("saveItems", {"sessionID": session_id, "uri": items[0][1].detail_url, "items": documents}, expected=201)
    except RuntimeError:
        for index, _ in items:
            events.emit("paper_zotero", index=index, status="保存结果未确认，请检查 Zotero")
        raise
    for index, paper in items:
        events.emit("paper_zotero", index=index, status="条目已发送，PDF 待处理" if paper.pdf_path else "条目已发送")
    try:
        _request("updateSession", {"sessionID": session_id, "target": target})
    except RuntimeError as error:
        raise RuntimeError(f"条目已写入 Zotero，但目标分类未确认：{error}") from error

    uploaded = failed = 0
    for position, (index, paper) in enumerate(pdf_items, 1):
        if events.cancel_requested():
            break
        events.emit("paper_operation_progress", message=f"发送 PDF {position}/{pdf_count}：{paper.title}")
        try:
            if not selected.get("filesEditable"):
                raise RuntimeError("目标文库不允许保存附件")
            path = Path(paper.pdf_path)
            with path.open("rb") as stream:
                if b"%PDF-" not in stream.read(1024):
                    raise ValueError("关联文件不是 PDF")
                stream.seek(0)
                metadata = {
                    "sessionID": session_id, "parentItemID": f"paper-{index}",
                    "title": path.name, "url": paper.detail_url or path.resolve().as_uri(),
                }
                _request("saveAttachment", body=stream, headers={
                    "Content-Type": "application/pdf", "Content-Length": str(path.stat().st_size),
                    "X-Metadata": json.dumps(metadata, ensure_ascii=True),
                }, expected=201)
            uploaded += 1
            events.emit("paper_zotero", index=index, status="条目与 PDF 已发送")
        except (OSError, ValueError, RuntimeError) as error:
            failed += 1
            events.emit("paper_zotero", index=index, status=f"条目已发送，PDF 未发送：{error}")
    pending = pdf_count - uploaded - failed
    for index, _ in pdf_items[uploaded + failed:]:
        events.emit("paper_zotero", index=index, status="条目已发送，PDF 未处理（已停止）")
    return f"Zotero：已发送 {len(items)} 篇论文，跳过 {skipped} 篇；PDF 成功 {uploaded}，失败 {failed}，未处理 {pending}"
