from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScraperSettings:
    timeout_goto_ms: int
    timeout_load_ms: int
    timeout_selector_ms: int
    verify_wait_timeout_sec: int
    verify_notice_interval_sec: int
    max_advance_fail: int
    session_cache_enabled: bool
    session_cache_ttl_hours: int
    log_save_path: bool
    log_keywords: bool
    log_scraped_records: bool


def get_scraper_settings(config: dict[str, Any]) -> ScraperSettings:
    return ScraperSettings(
        timeout_goto_ms=int(config["timeout_goto_ms"]),
        timeout_load_ms=int(config["timeout_load_ms"]),
        timeout_selector_ms=int(config["timeout_selector_ms"]),
        verify_wait_timeout_sec=int(config["verify_wait_timeout_sec"]),
        verify_notice_interval_sec=int(config["verify_notice_interval_sec"]),
        max_advance_fail=int(config["max_advance_fail"]),
        session_cache_enabled=bool(config["session_cache_enabled"]),
        session_cache_ttl_hours=int(config["session_cache_ttl_hours"]),
        log_save_path=bool(config["log_save_path"]),
        log_keywords=bool(config["log_keywords"]),
        log_scraped_records=bool(config["log_scraped_records"]),
    )
