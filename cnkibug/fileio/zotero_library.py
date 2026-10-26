from __future__ import annotations

import http.client
import json
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlencode

from ..cnki.models import Paper, document_type_family, normalize_doi, split_authors
from ..core.events import EventSink


class ZoteroReadCancelled(Exception):
    pass


@dataclass(frozen=True)
class LibrarySnapshot:
    prefix: str
    version: str
    papers: list[Paper]


@dataclass(frozen=True)
class ZoteroReviewRow:
    index: int
    paper: Paper
    status: str
    reason: str
    existing: Paper | None = None


def _get(path: str) -> tuple[object, dict[str, str]]:
    connection = http.client.HTTPConnection("127.0.0.1", 23119, timeout=10)
    try:
        connection.request("GET", path, headers={"Zotero-API-Version": "3"})
        response = connection.getresponse()
        body = response.read()
        if response.status == 403:
            raise RuntimeError("Zotero 本地 API 未开启，请在 Zotero 设置中允许本机应用访问文库。")
        if response.status != 200:
            raise RuntimeError(f"无法读取 Zotero 文库（HTTP {response.status}），未发送文献。")
        try:
            return json.loads(body), {key.lower(): value for key, value in response.getheaders()}
        except (UnicodeError, ValueError) as error:
            raise RuntimeError("Zotero 返回的文库数据无效，未发送文献。") from error
    except (OSError, http.client.HTTPException) as error:
        raise RuntimeError("无法完整读取 Zotero 文库，请确认 Zotero 已启动并允许本地 API 访问。") from error
    finally:
        connection.close()


def _cancel_if_requested(events: EventSink) -> None:
    if events.cancel_requested():
        raise ZoteroReadCancelled()


def _read_pages(path: str, events: EventSink, *, track_version: bool) -> tuple[list[dict], str]:
    records: list[dict] = []
    version = ""
    total = None
    seen = set()
    while True:
        _cancel_if_requested(events)
        data, headers = _get(path + "?" + urlencode({"limit": 100, "start": len(records)}))
        _cancel_if_requested(events)
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise RuntimeError("Zotero 返回的文库列表无效，未发送文献。")
        current_version = headers.get("last-modified-version", "")
        if track_version:
            if not current_version or (version and current_version != version):
                raise RuntimeError("Zotero 文库版本无法确认或已发生变化，请重新查重。")
            version = current_version
        if "total-results" in headers:
            try:
                current_total = int(headers["total-results"])
            except ValueError as error:
                raise RuntimeError("Zotero 返回的条目总数无效。") from error
            if current_total < 0 or (total is not None and total != current_total):
                raise RuntimeError("Zotero 文库在读取过程中发生变化，请重新查重。")
            total = current_total
        for item in data:
            key = item.get("key") if track_version else item.get("id")
            if not key or key in seen:
                raise RuntimeError("Zotero 分页数据不完整，请重新查重。")
            seen.add(key)
        records.extend(data)
        if track_version:
            events.emit("paper_operation_progress", message=f"正在检查 Zotero 文库：已读取 {len(records)} 条")
        if total is not None:
            if len(records) > total or (not data and len(records) < total):
                raise RuntimeError("Zotero 文库没有完整读取，未发送文献。")
            if len(records) == total:
                return records, version
        elif not data:
            return records, version


def library_prefix(selected: dict, events: EventSink) -> str:
    libraries = [
        target for target in selected.get("targets", [])
        if isinstance(target, dict) and target.get("level") == 0 and str(target.get("id", "")).startswith("L")
    ]
    if not libraries or f"L{selected['libraryID']}" not in {item["id"] for item in libraries}:
        raise RuntimeError("无法确认当前 Zotero 文库，请重新选择发送目标。")
    # The Connector lists the personal library first, then editable group libraries.
    if libraries and libraries[0]["id"] == f"L{selected['libraryID']}":
        return "/api/users/0"
    groups, _ = _read_pages("/api/users/0/groups", events, track_version=False)
    choices = []
    for group in groups:
        data = group.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Zotero 群组信息不完整，请重新读取文库。")
        if str(group.get("id", "")).isdigit() and data.get("name") == selected["libraryName"]:
            choices.append({"id": str(group["id"]), "name": str(data["name"])})
    if not choices:
        raise RuntimeError("无法识别当前 Zotero 文库，请确认选择了可用的个人库或群组库。")
    choice = events.request(
        "zotero_library_requested", libraries=choices, destination=str(selected["libraryName"]),
    )
    _cancel_if_requested(events)
    if choice is None:
        raise ZoteroReadCancelled()
    if choice not in {item["id"] for item in choices}:
        raise RuntimeError("Zotero 群组选择无效，未发送文献。")
    return f"/api/groups/{choice}"


def _paper_from_item(item: dict) -> Paper | None:
    data = item.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Zotero 条目数据不完整，未发送文献。")
    kind = data.get("itemType")
    if kind in {"attachment", "note", "annotation"}:
        return None
    types = {
        "journalArticle": "期刊", "thesis": "学位论文", "conferencePaper": "会议",
        "book": "图书", "bookSection": "图书章节", "newspaperArticle": "报纸",
        "patent": "专利", "statute": "法律法规", "videoRecording": "视频",
    }
    authors = []
    for creator in data.get("creators", []):
        if not isinstance(creator, dict) or creator.get("creatorType") not in {"author", "inventor", "director"}:
            continue
        if creator.get("name"):
            authors.append(str(creator["name"]))
        else:
            first, last = str(creator.get("firstName", "")), str(creator.get("lastName", ""))
            authors.append(last + first if re.search(r"[\u3400-\u9fff]", first + last) else f"{first} {last}".strip())
    return Paper(
        title=str(data.get("title", "")), authors="; ".join(authors),
        source=str(next((data[key] for key in ("publicationTitle", "proceedingsTitle", "bookTitle", "university") if data.get(key)), "")),
        publication_date=str(data.get("date", "")), doi=str(data.get("DOI", "")),
        document_type=str(data.get("thesisType") or types.get(kind, kind or "")),
        detail_url=str(data.get("url", "")),
    )


def read_library(prefix: str, events: EventSink) -> LibrarySnapshot:
    data, version = _read_pages(prefix + "/items/top", events, track_version=True)
    return LibrarySnapshot(prefix, version, [paper for item in data if (paper := _paper_from_item(item)) is not None])


def check_library_unchanged(snapshot: LibrarySnapshot, events: EventSink) -> None:
    _cancel_if_requested(events)
    _, headers = _get(snapshot.prefix + "/items/top?limit=1")
    _cancel_if_requested(events)
    if headers.get("last-modified-version") != snapshot.version:
        raise RuntimeError("Zotero 文库已发生变化，请重新查重后发送。")


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def _authors(value: str) -> tuple[str, ...]:
    names = []
    for author in split_authors(value):
        parts = author.split(",", 1)
        names.append(_normalized(" ".join(reversed(parts)) if len(parts) == 2 else author))
    return tuple(names)


def _year(value: str) -> str:
    match = re.search(r"\b(?:18|19|20|21)\d{2}", value)
    return match.group() if match else ""


def _identity(paper: Paper) -> tuple:
    return (
        _normalized(paper.title), _authors(paper.authors), _year(paper.publication_date),
        _normalized(paper.source), document_type_family(paper.document_type),
    )


def review_papers(items: list[tuple[int, Paper]], existing: list[Paper]) -> list[ZoteroReviewRow]:
    by_doi: dict[str, list[tuple[Paper, bool]]] = {}
    by_title: dict[str, list[tuple[Paper, bool]]] = {}

    def remember(paper: Paper, in_batch: bool) -> None:
        doi, title = normalize_doi(paper.doi).casefold(), _normalized(paper.title)
        if doi:
            by_doi.setdefault(doi, []).append((paper, in_batch))
        if title:
            by_title.setdefault(title, []).append((paper, in_batch))

    for paper in existing:
        remember(paper, False)
    rows = []
    for index, paper in items:
        doi = normalize_doi(paper.doi).casefold()
        candidates = by_doi.get(doi, []) if doi else []
        if candidates:
            matched, in_batch = candidates[0]
            row = ZoteroReviewRow(index, paper, "duplicate", "本次列表中 DOI 一致" if in_batch else "DOI 一致", matched)
        else:
            candidates = by_title.get(_normalized(paper.title), [])
            row = ZoteroReviewRow(index, paper, "new", "未发现匹配条目")
            identity = _identity(paper)
            for matched, in_batch in candidates:
                other_doi = normalize_doi(matched.doi).casefold()
                conflict = bool(doi and other_doi and doi != other_doi)
                degree_conflict = (
                    ("博士" in paper.document_type and "硕士" in matched.document_type)
                    or ("硕士" in paper.document_type and "博士" in matched.document_type)
                )
                if not conflict and not degree_conflict and all(identity) and identity == _identity(matched):
                    reason = "本次列表中题录一致" if in_batch else "题名、作者、年份、来源和类型一致"
                    row = ZoteroReviewRow(index, paper, "duplicate", reason, matched)
                    break
                reason = "题名相同，DOI 不同" if conflict else "题名相同，其他信息不同或不完整"
                row = ZoteroReviewRow(index, paper, "review", reason, matched)
        rows.append(row)
        remember(paper, True)
    return rows
