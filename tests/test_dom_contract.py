from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from cnkibug.cnki.metrics import new_scrape_stats
from cnkibug.cnki.pagination import get_result_page_numbers
from cnkibug.cnki.results import parse_result_rows
from cnkibug.cnki.search import wait_search_outcome
from cnkibug.cnki.selectors import query_first


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        yield page
        browser.close()


def test_result_fixture_matches_selectors_and_parser(page):
    page.goto((FIXTURES / "cnki_results.html").as_uri())
    stats = new_scrape_stats()

    parsed = parse_result_rows(page, set(), stats)

    assert parsed.rows_seen == 2
    assert parsed.records == [
        [
            "Fixture title one",
            "Author A; Author B",
            "Fixture Journal",
            "2026-07-01",
            "https://kns.cnki.net/kcms2/article/abstract?v=fixture-1",
        ],
        [
            "Fixture title two",
            "",
            "Fixture Proceedings",
            "2026-07-02",
            "https://kns.cnki.net/kcms2/article/abstract?v=fixture-2",
        ],
    ]
    assert query_first(page, "next_page") is not None
    assert get_result_page_numbers(page) == (1, 2)
    assert stats["missing_authors"] == 1


def test_no_result_fixture_matches_outcome_contract(page):
    page.goto((FIXTURES / "cnki_no_results.html").as_uri())

    outcome = wait_search_outcome(page, type("Settings", (), {"timeout_selector_ms": 1000})())

    assert outcome == "no_content"


def test_verify_fixture_matches_url_contract(page):
    page.goto((FIXTURES / "verify" / "index.html").as_uri())

    outcome = wait_search_outcome(page, type("Settings", (), {"timeout_selector_ms": 1000})())

    assert outcome == "verify"
