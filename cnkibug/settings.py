from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .runtime import get_config


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
    log_keywords: bool
    log_scraped_records: bool


def get_scraper_settings(config: dict[str, Any] | None = None) -> ScraperSettings:
    source = get_config() if config is None else config
    return ScraperSettings(
        timeout_goto_ms=int(source["timeout_goto_ms"]),
        timeout_load_ms=int(source["timeout_load_ms"]),
        timeout_selector_ms=int(source["timeout_selector_ms"]),
        verify_wait_timeout_sec=int(source["verify_wait_timeout_sec"]),
        verify_notice_interval_sec=int(source["verify_notice_interval_sec"]),
        max_advance_fail=int(source["max_advance_fail"]),
        session_cache_enabled=bool(source["session_cache_enabled"]),
        session_cache_ttl_hours=int(source["session_cache_ttl_hours"]),
        log_keywords=bool(source["log_keywords"]),
        log_scraped_records=bool(source["log_scraped_records"]),
    )
