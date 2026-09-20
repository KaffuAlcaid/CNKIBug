from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, replace
from typing import Sequence


STATUS_SUCCESS = "success"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"
STATUS_NOT_STARTED = "not_started"

BASE_RECORD_SIZE = 5


def append_article_details(
    record: list,
    keywords: list[str],
    abstract: str,
    metadata: dict[str, str] | None = None,
) -> None:
    extra = record.pop() if record and isinstance(record[-1], dict) else {}
    record.extend(("\n".join(keywords), abstract))
    extra.update(metadata or {})
    if extra:
        record.append(extra)


def record_citation(record: Sequence, include_citation: bool) -> str:
    if not include_citation or len(record) <= BASE_RECORD_SIZE:
        return ""
    value = record[BASE_RECORD_SIZE]
    return str(value) if not isinstance(value, dict) else ""


def record_article_details(
    record: Sequence,
    include_citation: bool,
) -> tuple[str, str]:
    start = BASE_RECORD_SIZE + int(include_citation)
    keywords = str(record[start]) if len(record) > start and not isinstance(record[start], dict) else ""
    abstract = str(record[start + 1]) if len(record) > start + 1 and not isinstance(record[start + 1], dict) else ""
    return keywords, abstract


def record_metadata(record: Sequence) -> dict:
    return dict(record[-1]) if record and isinstance(record[-1], dict) else {}


def normalize_doi(value: str) -> str:
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/|^doi\s*[:：]\s*", "", value.strip(), flags=re.I)
    return value.strip().rstrip(".;；。")


def split_authors(value: str) -> list[str]:
    return [" ".join(part.split()) for part in re.split(r"[;；、\r\n]+", value) if part.strip()]


def document_type_family(value: str) -> str:
    value = value.strip()
    families = {
        "期刊": "journal", "学术期刊": "journal", "学术辑刊": "journal", "特色期刊": "journal",
        "博士": "thesis", "硕士": "thesis", "学位论文": "thesis",
        "博士论文": "thesis", "硕士论文": "thesis", "博士学位论文": "thesis", "硕士学位论文": "thesis",
        "会议": "conference", "会议论文": "conference", "中国会议": "conference", "国际会议": "conference",
        "图书": "book", "图书章节": "book_section", "报纸": "newspaper", "报纸文章": "newspaper",
        "专利": "patent", "标准": "standard", "法律法规": "statute", "视频": "video",
    }
    return families.get(value, value)


def _document_types_conflict(first: str, second: str) -> bool:
    first_family, second_family = document_type_family(first), document_type_family(second)
    if not first_family or not second_family:
        return False
    if first_family != second_family:
        return True
    if first_family == "thesis":
        first_degree = next((degree for degree in ("博士", "硕士") if degree in first), "")
        second_degree = next((degree for degree in ("博士", "硕士") if degree in second), "")
        return bool(first_degree and second_degree and first_degree != second_degree)
    return False


@dataclass
class Paper:
    title: str = ""
    authors: str = ""
    source: str = ""
    publication_date: str = ""
    detail_url: str = ""
    document_type: str = ""
    doi: str = ""
    citation_count: str = ""
    download_count: str = ""
    paper_keywords: str = ""
    abstract: str = ""
    institutions: str = ""
    funds: str = ""
    classification: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    citation: str = ""
    queries: list[str] = field(default_factory=list)
    pdf_path: str = ""


def paper_from_record(record: Sequence, include_citation: bool = False, query: str = "") -> Paper:
    values = [str(value) for value in record[:BASE_RECORD_SIZE] if not isinstance(value, dict)]
    values.extend([""] * (BASE_RECORD_SIZE - len(values)))
    paper = Paper(*values, citation=record_citation(record, include_citation))
    paper.paper_keywords, paper.abstract = record_article_details(record, include_citation)
    allowed = {item.name for item in fields(Paper)} - {"queries"}
    for key, value in record_metadata(record).items():
        if key in allowed and isinstance(value, (str, int)):
            setattr(paper, key, str(value))
    paper.doi = normalize_doi(paper.doi)
    paper.queries = [query] if query else []
    return paper


def deduplicate_papers(papers: list[Paper]) -> list[Paper]:
    result: list[Paper] = []
    indexes: dict[tuple, list[int]] = {}
    for original in papers:
        paper = replace(original, queries=list(original.queries))
        paper.doi = normalize_doi(paper.doi)
        keys = []
        if paper.doi:
            keys.append(("doi", paper.doi.casefold()))
        if paper.detail_url:
            keys.append(("url", paper.detail_url.strip()))
        title, source, publication_date = (
            " ".join(value.split()).casefold()
            for value in (paper.title, paper.source, paper.publication_date)
        )
        authors = tuple(author.casefold() for author in split_authors(paper.authors))
        identity = (title, authors, source, publication_date)
        if all(identity):
            keys.append(("fields", *identity))
        found = None
        for key in keys:
            for index in indexes.get(key, []):
                existing = result[index]
                if existing.doi and paper.doi and existing.doi.casefold() != paper.doi.casefold():
                    continue
                if key[0] == "fields" and _document_types_conflict(existing.document_type, paper.document_type):
                    continue
                found = index
                break
            if found is not None:
                break
        if found is None:
            found = len(result)
            result.append(paper)
        else:
            existing = result[found]
            for item in fields(Paper):
                if item.name != "queries" and not getattr(existing, item.name):
                    setattr(existing, item.name, getattr(paper, item.name))
            existing.queries.extend(query for query in paper.queries if query not in existing.queries)
        for key in keys:
            matches = indexes.setdefault(key, [])
            if found not in matches:
                matches.append(found)
    return result


def papers_from_results(all_results: dict[str, list], include_citation: bool = False) -> list[Paper]:
    return deduplicate_papers([
        paper_from_record(record, include_citation, query)
        for query, records in all_results.items() for record in records
    ])


@dataclass
class KeywordResult:
    keyword: str
    index: int
    total: int
    records: list
    status: str
    reason: str = ""


def make_keyword_result(
    keyword: str,
    index: int,
    total: int,
    records: list,
    status: str,
    reason: str = "",
) -> KeywordResult:
    return KeywordResult(
        keyword=keyword,
        index=index,
        total=total,
        records=records,
        status=status,
        reason=reason,
    )
