from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pybtex.database import BibliographyData, Entry, Person
from pybtex.database.output.bibtex import Writer

from ..cnki.models import Paper, deduplicate_papers, document_type_family, normalize_doi, split_authors


_ESCAPES = {
    "\\": r"\textbackslash{}", "{": r"\textbraceleft{}", "}": r"\textbraceright{}",
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
    "^": r"\textasciicircum{}", "~": r"\textasciitilde{}",
}


def _text(value: str) -> str:
    value = " ".join(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value).split())
    return "".join(_ESCAPES.get(character, character) for character in value)


class _PlainTextWriter(Writer):
    def _encode(self, text):
        # Fields are escaped once, before adding structural braces around names and titles.
        return text


def _entry_type(paper: Paper) -> str:
    family = document_type_family(paper.document_type)
    if family == "thesis":
        if "博士" in paper.document_type:
            return "phdthesis"
        if "硕士" in paper.document_type:
            return "mastersthesis"
        return "misc"
    return {
        "journal": "article", "conference": "inproceedings",
        "book": "book", "book_section": "incollection",
    }.get(family, "misc")


def _entry_key(paper: Paper) -> str:
    doi = normalize_doi(paper.doi).casefold()
    if doi:
        identity = ["doi", doi]
    elif paper.detail_url.strip():
        identity = ["url", paper.detail_url.strip()]
    else:
        identity = [" ".join(value.split()).casefold() for value in (
            paper.title, paper.authors, paper.source, paper.publication_date, paper.document_type,
        )]
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "cnki_" + hashlib.sha256(encoded).hexdigest()[:24]


def write_bibtex(path: str | Path, papers: list[Paper]) -> None:
    entries = {}
    for paper in deduplicate_papers(papers):
        kind = _entry_type(paper)
        fields = {}
        values = {
            "title": paper.title, "doi": normalize_doi(paper.doi), "url": paper.detail_url,
            "abstract": paper.abstract, "volume": paper.volume, "number": paper.issue,
            "pages": paper.pages, "keywords": paper.paper_keywords, "date": paper.publication_date,
        }
        year = re.match(r"^\d{4}", paper.publication_date.strip())
        if year:
            values["year"] = year.group()
        source_field = {
            "article": "journal", "inproceedings": "booktitle", "incollection": "booktitle",
            "phdthesis": "school", "mastersthesis": "school",
        }.get(kind)
        if source_field:
            values[source_field] = paper.source
        notes = [
            f"{label}：{value}" for label, value in (
                ("文献类型", paper.document_type), ("作者单位", paper.institutions),
                ("基金", paper.funds), ("分类号", paper.classification), ("引用格式", paper.citation),
            ) if value
        ]
        if paper.source and not source_field:
            notes.append(f"来源：{paper.source}")
        notes.extend(f"命中检索项：{query}" for query in paper.queries if query.strip())
        if notes:
            values["note"] = "；".join(notes)
        for name, value in values.items():
            if value.strip():
                fields[name] = _text(value)
        if "title" in fields:
            fields["title"] = "{" + fields["title"] + "}"
        persons = {"author": [Person(last="{" + _text(name) + "}") for name in split_authors(paper.authors)]}
        key = _entry_key(paper)
        if key in entries:
            raise ValueError("文献标识发生冲突，请分开导出这些文献。")
        entries[key] = Entry(kind, fields=fields, persons=persons)
    content = _PlainTextWriter(encoding="UTF-8").to_string(BibliographyData(entries=entries))
    Path(path).write_text(content, encoding="utf-8", newline="\n")
