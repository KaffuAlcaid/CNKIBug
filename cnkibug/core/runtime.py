from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys


def appimage_path() -> Path | None:
    value = os.environ.get("APPIMAGE")
    if sys.platform == "linux" and getattr(sys, "frozen", False) and value:
        return Path(value).resolve()
    return None


@dataclass(frozen=True)
class RuntimePaths:
    program_dir: Path
    data_dir: Path
    config_path: Path
    cache_dir: Path
    log_dir: Path
    status_dir: Path
