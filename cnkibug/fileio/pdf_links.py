from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path, PureWindowsPath
from time import time_ns
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from ..cnki.models import Paper, document_type_family, normalize_doi, split_authors
from ..core.events import EventSink


INDEX_NAME = ".cnkibug-downloads.json"
_OUTCOMES = {"downloaded", "linked", "failed", "stopped", "unlinked"}


def pdf_filename_stem(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")[:100] or "论文"
    if name.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
    }:
        name = "_" + name
    return name


def pdf_problem(value: str | Path) -> str:
    if not value:
        return "未关联 PDF"
    path = Path(value)
    try:
        if not path.is_file():
            return "PDF 缺失"
        if path.suffix.lower() != ".pdf":
            return "PDF 无效"
        with path.open("rb") as stream:
            if b"%PDF-" not in stream.read(1024):
                return "PDF 无效"
    except (OSError, ValueError):
        return "PDF 无法读取"
    return ""


def resolve_pdf_path(value: str, directory: Path) -> str:
    if not value:
        return ""
    path = Path(value).expanduser()
    # Keep foreign Windows paths intact on Linux so they can be shown as missing.
    if PureWindowsPath(value).is_absolute() and not path.is_absolute():
        return value
    if not path.is_absolute():
        path = directory / value.replace("\\", "/")
    return str(path.resolve())


def portable_pdf_path(value: str, directory: Path) -> str:
    if not value:
        return ""
    if PureWindowsPath(value).is_absolute() and not Path(value).is_absolute():
        return value
    path = Path(value).expanduser().resolve()
    try:
        return path.relative_to(directory.resolve()).as_posix()
    except ValueError:
        return str(path)


def portable_papers(papers: list[Paper], directory: Path) -> list[Paper]:
    return [replace(paper, pdf_path=portable_pdf_path(paper.pdf_path, directory)) for paper in papers]


def paper_keys(paper: Paper) -> list[str]:
    keys = []
    doi = normalize_doi(paper.doi).casefold()
    if doi:
        keys.append("doi:" + doi)
    try:
        url = urlsplit(paper.detail_url.strip())
        host = (url.hostname or "").lower()
        if host:
            parameters = {key.casefold(): value.casefold() for key, value in parse_qsl(url.query)}
            cnki = host == "cnki.net" or host.endswith(".cnki.net") or host.startswith(("kns-cnki-net-443.", "www-cnki-net-443."))
            if cnki and parameters.get("filename"):
                keys.append("cnki:" + parameters["filename"])
            keys.append("url:" + urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path, url.query, "")))
    except ValueError:
        pass
    identity = [" ".join(value.split()).casefold() for value in (paper.title, paper.source, paper.publication_date)]
    authors = [author.casefold() for author in split_authors(paper.authors)]
    if all(identity) and authors:
        raw = json.dumps([*identity, authors], ensure_ascii=False)
        keys.append("fields:" + sha256(raw.encode("utf-8")).hexdigest())
    return keys


def _compatible(paper: Paper, entry: dict) -> bool:
    doi = normalize_doi(paper.doi).casefold()
    if doi and entry["doi"] and doi != entry["doi"]:
        return False
    first, second = paper.document_type, entry["document_type"]
    if first and second:
        if document_type_family(first) != document_type_family(second):
            return False
        degrees = [next((degree for degree in ("博士", "硕士") if degree in value), "") for value in (first, second)]
        if all(degrees) and degrees[0] != degrees[1]:
            return False
    return True


class PdfIndex:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory).expanduser().resolve()
        self.path = self.directory / INDEX_NAME
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {"version": 1, "entries": []}
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"PDF 索引无法读取：{self.path}") from error
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("entries"), list):
            raise ValueError(f"PDF 索引格式不受支持：{self.path}")
        self.entries = data["entries"]
        for entry in self.entries:
            if (not isinstance(entry, dict) or not isinstance(entry.get("keys"), list)
                    or not entry["keys"] or not all(isinstance(key, str) for key in entry["keys"])
                    or not all(isinstance(entry.get(key), str) for key in ("doi", "document_type", "path", "source", "outcome", "error"))
                    or entry["outcome"] not in _OUTCOMES or not isinstance(entry.get("updated_ns"), int)):
                raise ValueError(f"PDF 索引包含无效记录：{self.path}")
        self._reindex()

    def _reindex(self) -> None:
        self._keys: dict[str, list[int]] = {}
        for index, entry in enumerate(self.entries):
            for key in entry["keys"]:
                self._keys.setdefault(key, []).append(index)

    def find(self, paper: Paper) -> dict | None:
        for key in paper_keys(paper):
            matches = [self.entries[index] for index in self._keys.get(key, []) if _compatible(paper, self.entries[index])]
            if matches:
                if len({entry["doi"] for entry in matches if entry["doi"]}) > 1:
                    return None
                return max(matches, key=lambda entry: entry["updated_ns"])
        return None

    def record(self, paper: Paper, *, outcome: str, source: str = "", error: str = "") -> None:
        from .exporter import _write_atomically

        keys = paper_keys(paper)
        if not keys:
            raise ValueError(f"“{paper.title}”缺少 DOI、详情链接或完整书目信息，无法自动保存 PDF 关联")
        previous = self.find(paper)
        entry = {
            "keys": list(dict.fromkeys([*(previous["keys"] if previous else []), *keys])),
            "doi": normalize_doi(paper.doi).casefold() or (previous["doi"] if previous else ""),
            "document_type": paper.document_type or (previous["document_type"] if previous else ""),
            "path": portable_pdf_path(paper.pdf_path, self.directory),
            "source": source or (previous["source"] if previous else ""),
            "outcome": outcome,
            "error": error,
            "updated_ns": time_ns(),
        }
        entries = [item for item in self.entries if item is not previous] + [entry]
        self.directory.mkdir(parents=True, exist_ok=True)
        content = json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=2) + "\n"
        _write_atomically(str(self.path), lambda temporary: Path(temporary).write_text(content, encoding="utf-8", newline="\n"))
        self.entries = entries
        self._reindex()


def restore_pdf_links(papers: list[Paper], directories: list[Path], *, table_updated_ns: int = 0) -> tuple[dict[int, dict], list[str]]:
    indexes, errors = [], []
    for directory in dict.fromkeys(Path(value).expanduser().resolve() for value in directories):
        try:
            indexes.append(PdfIndex(directory))
        except (OSError, ValueError) as error:
            errors.append(str(error))
    restored = {}
    for index, paper in enumerate(papers):
        matches = [(store, entry) for store in indexes if (entry := store.find(paper)) is not None]
        if not matches:
            continue
        if len({entry["doi"] for _, entry in matches if entry["doi"]}) > 1:
            continue
        store, entry = max(matches, key=lambda pair: pair[1]["updated_ns"])
        # A later exported table can replace or clear an older association.
        if table_updated_ns > entry["updated_ns"] and (not paper.pdf_path or not pdf_problem(paper.pdf_path)):
            continue
        paper.pdf_path = "" if entry["outcome"] == "unlinked" else resolve_pdf_path(entry["path"], store.directory)
        restored[index] = entry
    return restored, errors


@dataclass
class PdfMatch:
    index: int
    paper: Paper
    candidates: list[Path]
    status: str
    reason: str


def scan_pdf_folder(papers: list[Paper], selected: set[int], directory: Path, recursive: bool,
                    events: EventSink) -> tuple[list[PdfMatch], list[str]]:
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"文件夹不存在：{directory}")
    files: set[Path] = set()
    directories, warnings = [], []
    for current, subdirs, names in os.walk(directory, onerror=lambda error: warnings.append(str(error))):
        if events.cancel_requested():
            return [], warnings
        current_path = Path(current)
        if INDEX_NAME in names:
            directories.append(current_path)
        subdirs[:] = sorted(name for name in subdirs if not (current_path / name).is_symlink()) if recursive else []
        files.update((current_path / name).resolve() for name in names if Path(name).suffix.lower() == ".pdf")
    events.emit("paper_operation_progress", message=f"正在匹配 {len(files)} 个 PDF 文件")
    recovered = [replace(paper) for paper in papers]
    entries, errors = restore_pdf_links(recovered, directories)
    warnings.extend(errors)
    stems: dict[str, set[int]] = defaultdict(set)
    for index, paper in enumerate(papers):
        stems[pdf_filename_stem(paper.title).casefold()].add(index)
    candidates: dict[int, list[Path]] = defaultdict(list)
    owners: dict[Path, set[int]] = {}
    for path in sorted(files):
        if events.cancel_requested():
            return [], warnings
        stem = path.stem.casefold()
        matches = set(stems.get(stem, ()))
        numbered = re.fullmatch(r"(.+)_([2-9]|[1-9]\d+)", stem)
        if numbered:
            matches.update(stems.get(numbered[1], ()))
        owners[path] = matches
        for index in matches & selected:
            candidates[index].append(path)
    checked: dict[Path, str] = {}

    def problem(path: Path) -> str:
        if path not in checked:
            checked[path] = pdf_problem(path)
        return checked[path]

    rows = []
    for index in sorted(selected):
        if events.cancel_requested():
            return [], warnings
        paper = papers[index]
        if not pdf_problem(paper.pdf_path):
            rows.append(PdfMatch(index, paper, [Path(paper.pdf_path)], "existing", "已关联有效 PDF"))
            continue
        entry = entries.get(index)
        indexed = Path(recovered[index].pdf_path) if recovered[index].pdf_path else None
        if entry and entry["outcome"] != "unlinked" and indexed in files and not problem(indexed):
            rows.append(PdfMatch(index, paper, [indexed], "indexed", "文献标识与下载索引一致"))
            continue
        valid = [path for path in candidates[index] if not problem(path)]
        if not valid:
            reason = "未找到同名 PDF"
            if candidates[index]:
                reason = "；".join(dict.fromkeys(problem(path) for path in candidates[index]))
            rows.append(PdfMatch(index, paper, [], "missing", reason))
            continue
        exact = len(valid) == 1 and valid[0].stem.casefold() == pdf_filename_stem(paper.title).casefold()
        unique = exact and len(owners[valid[0]]) == 1
        reason = "文件名与文献标题一致"
        if entry and entry["outcome"] == "unlinked":
            unique, reason = False, "此文献曾解除 PDF 关联"
        elif len(valid) > 1:
            reason = f"找到 {len(valid)} 个候选文件"
        elif len(owners[valid[0]]) > 1:
            reason = "文件名对应多篇文献"
        elif not exact:
            reason = "文件名带重复下载编号"
        rows.append(PdfMatch(index, paper, valid, "unique" if unique else "review", reason))
    # A file proposed for multiple selected records always requires a choice.
    proposed: dict[Path, list[PdfMatch]] = defaultdict(list)
    for row in rows:
        for path in row.candidates:
            proposed[path].append(row)
    for matches in proposed.values():
        if len(matches) > 1:
            for row in matches:
                if row.status in {"indexed", "unique"}:
                    row.status, row.reason = "review", "同一文件对应多篇文献"
    return rows, warnings


def associate_pdf_folder(items: list[tuple[int, Paper]], papers: list[Paper], directory: Path,
                         recursive: bool, destination: Path, events: EventSink) -> str:
    rows, warnings = scan_pdf_folder(papers, {index for index, _ in items}, directory, recursive, events)
    if events.cancel_requested():
        return "文件夹扫描已停止"
    selected = events.request("pdf_links_review_requested", directory=str(directory), rows=rows, warnings=warnings)
    if not selected or events.cancel_requested():
        return "未更改 PDF 关联"
    store = PdfIndex(destination)
    originals = {index: paper for index, paper in items}
    linked, failures = 0, []
    used = {Path(paper.pdf_path).resolve() for paper in papers if not pdf_problem(paper.pdf_path)}
    for position, (index, value) in enumerate(selected.items(), start=1):
        if events.cancel_requested():
            break
        events.emit("paper_operation_progress", message=f"正在关联 PDF：{position}/{len(selected)}")
        path = Path(value).resolve()
        issue = pdf_problem(path)
        if issue or path in used:
            failures.append(f"{originals[index].title}：{issue or '文件已关联另一篇文献'}")
            continue
        used.add(path)
        paper = replace(originals[index], pdf_path=str(path))
        error = ""
        try:
            store.record(paper, outcome="linked", source="manual")
        except (OSError, ValueError) as failure:
            error = str(failure)
        events.emit("paper_pdf_link", index=index, path=str(path), saved=not error)
        linked += 1
        if error:
            failures.append(f"关联已更新，但无法自动保存：{error}；请导出 Excel 或 CSV 保存关联")
            break
    if failures:
        events.emit("paper_task_error", error="\n".join(failures))
    return f"已关联 {linked} 篇文献" + (f"，{len(failures)} 项未完成" if failures else "")
