from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    program_dir: Path
    data_dir: Path
    config_path: Path
    cache_dir: Path
    log_dir: Path
    status_dir: Path
