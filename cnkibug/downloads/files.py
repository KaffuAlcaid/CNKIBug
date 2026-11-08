from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..fileio.pdf_links import pdf_filename_stem, pdf_problem


def inspect_pdf(path: str | Path) -> dict:
    path = Path(path).resolve()
    problem = pdf_problem(path) if path.suffix.lower() == ".pdf" else ""
    if problem:
        raise ValueError(problem)
    with path.open("rb") as stream:
        head = stream.read(1024)
        if b"%PDF-" not in head:
            raise ValueError("文件内容不是 PDF")
        stream.seek(0)
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        size = stream.tell()
        stream.seek(max(0, size - 2048))
        has_eof = b"%%EOF" in stream.read()
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "sha256": digest.hexdigest(), "has_eof": has_eof}


def unique_target(directory: Path, title: str) -> Path:
    stem = pdf_filename_stem(title)
    target = directory / f"{stem}.pdf"
    number = 2
    while target.exists():
        target = directory / f"{stem}_{number}.pdf"
        number += 1
    return target


def publish_file(source: Path, target: Path) -> None:
    """Publish without replacing an existing file; keep the source until registered."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except FileExistsError:
        raise
    except OSError:
        # Filesystems without hard links still need exclusive destination creation.
        # The precommitted source/hash lets recovery detect an interrupted copy.
        with target.open("xb") as output, source.open("rb") as incoming:
            for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())


def owned_incoming(directory: str | Path, attempt_id: str) -> Path:
    if len(attempt_id) != 32 or any(char not in "0123456789abcdef" for char in attempt_id):
        raise ValueError("无效的下载尝试标识")
    return Path(directory).resolve() / ".cnkibug-incoming" / attempt_id


def discard_incoming(directory: str | Path, attempt_id: str) -> None:
    folder = owned_incoming(directory, attempt_id)
    if not folder.exists() or folder.is_symlink():
        return
    for child in folder.iterdir():
        if child.is_file() and not child.is_symlink():
            child.unlink()
    try:
        folder.rmdir()
        folder.parent.rmdir()
    except OSError:
        pass
