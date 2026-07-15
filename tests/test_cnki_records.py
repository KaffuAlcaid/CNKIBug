from cnkibug.cnki_page import (
    SELECTOR_AUTHOR,
    SELECTOR_DATE,
    SELECTOR_RESULT_ROWS,
    SELECTOR_RESULT_TITLE,
    SELECTOR_SOURCE,
)
from cnkibug import cnki_results
from cnkibug.cnki_results import parse_result_rows
from cnkibug.scrape_logging import new_scrape_stats


class FakeElement:
    def __init__(self, text="", attrs=None, single=None, multiple=None):
        self._text = text
        self._attrs = attrs or {}
        self._single = single or {}
        self._multiple = multiple or {}

    def query_selector(self, selector):
        return self._single.get(selector)

    def query_selector_all(self, selector):
        return self._multiple.get(selector, [])

    def get_attribute(self, name):
        return self._attrs.get(name)

    def inner_text(self):
        return self._text

    def text_content(self):
        return self._text


def _page(rows):
    page = FakeElement(multiple={SELECTOR_RESULT_ROWS: rows})
    page.url = "https://kns.cnki.net/kns8s/defaultresult/index"
    return page


def _row(title=None, href=None, authors=None, source="来源", date="2026-01-01"):
    single = {}
    multiple = {}
    if title is not None:
        single[SELECTOR_RESULT_TITLE] = FakeElement(title, attrs={"href": href})
    if authors is not None:
        multiple[SELECTOR_AUTHOR] = [FakeElement(item) for item in authors]
    if source is not None:
        single[SELECTOR_SOURCE] = FakeElement(source)
    if date is not None:
        single[SELECTOR_DATE] = FakeElement(date)
    return FakeElement(single=single, multiple=multiple)


def test_parse_result_rows_extracts_records_and_updates_stats():
    stats = new_scrape_stats()
    seen = set()
    page = _page([
        _row("标题1", "/detail/1", ["作者1", "作者2"], "  期刊   A  ", "2026-01-01"),
        _row("标题1重复", "/detail/1", ["作者3"], "期刊 A", "2026-01-02"),
        _row(None),
    ])

    result = parse_result_rows(page, seen, stats)

    assert result.records == [[
        "标题1",
        "作者1; 作者2",
        "期刊 A",
        "2026-01-01",
        "https://kns.cnki.net/detail/1",
    ]]
    assert result.rows_seen == 3
    assert result.records_added == 1
    assert result.duplicates == 1
    assert result.skipped_no_title == 1
    assert stats["rows_seen"] == 3
    assert stats["duplicates"] == 1
    assert stats["skipped_no_title"] == 1


def test_parse_result_rows_counts_missing_fields():
    stats = new_scrape_stats()
    seen = set()
    page = _page([
        _row("标题", None, [], None, None),
    ])

    result = parse_result_rows(page, seen, stats)

    assert result.records == [["标题", "", "", "", ""]]
    assert stats["missing_title"] == 0
    assert stats["missing_authors"] == 1
    assert stats["missing_source"] == 1
    assert stats["missing_date"] == 1
    assert stats["missing_detail_url"] == 1


def test_parse_result_rows_accepts_none_text_content(caplog):
    stats = new_scrape_stats()
    page = _page([
        _row("标题", "/detail/1", [None], None, None),
    ])
    row = page._multiple[SELECTOR_RESULT_ROWS][0]
    row._single[SELECTOR_SOURCE] = FakeElement(None)
    row._single[SELECTOR_DATE] = FakeElement(None)

    result = parse_result_rows(page, set(), stats)

    assert result.records == [["标题", "", "", "", "https://kns.cnki.net/detail/1"]]
    assert "fields=author,date,source" in caplog.text


def test_parse_result_rows_fetches_citation_after_deduplication(monkeypatch):
    calls = []

    def fetch_gbt_citation(page, row, *, log_ref):
        calls.append((row, log_ref))
        return "[1] 示例引文"

    monkeypatch.setattr(cnki_results, "fetch_gbt_citation", fetch_gbt_citation)
    first = _row("标题", "/detail/1", ["作者"])
    duplicate = _row("重复", "/detail/1", ["作者"])
    page = _page([first, duplicate])

    result = parse_result_rows(
        page,
        set(),
        new_scrape_stats(),
        include_citation=True,
        citation_log_ref="keyword_index=1/1 page=1",
        log_titles=True,
    )

    assert result.records == [[
        "标题",
        "作者",
        "来源",
        "2026-01-01",
        "https://kns.cnki.net/detail/1",
        "[1] 示例引文",
    ]]
    assert result.citation_success == 1
    assert result.citation_failed == 0
    assert calls == [(
        first,
        "keyword_index=1/1 page=1 row=1 title='标题'",
    )]
