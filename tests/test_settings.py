from cnkibug.app.runtime import DEFAULT_CONFIG
from cnkibug.core.settings import ScraperSettings, get_scraper_settings


def test_get_scraper_settings_uses_config_values():
    config = DEFAULT_CONFIG.copy()
    config.update({
        "timeout_goto_ms": 1000,
        "timeout_load_ms": 2000,
        "timeout_selector_ms": 3000,
        "verify_wait_timeout_sec": 40,
        "verify_notice_interval_sec": 5,
        "max_advance_fail": 6,
        "session_cache_enabled": False,
        "session_cache_ttl_hours": 7,
    })

    assert get_scraper_settings(config) == ScraperSettings(
        timeout_goto_ms=1000,
        timeout_load_ms=2000,
        timeout_selector_ms=3000,
        verify_wait_timeout_sec=40,
        verify_notice_interval_sec=5,
        max_advance_fail=6,
        session_cache_enabled=False,
        session_cache_ttl_hours=7,
        log_save_path=True,
        log_keywords=False,
        log_scraped_records=False,
        detail_txt_export=False,
    )
