from cnkibug.cnki_page import (
    SELECTOR_CURRENT_PAGE,
    SELECTOR_NEXT_PAGE,
    SELECTOR_PAGE_COUNT,
    SELECTOR_RESULT_ROWS,
    SELECTOR_RESULT_TITLE,
)
from cnkibug.cnki_results import (
    get_first_result_href,
    get_next_page_marker,
    get_result_page_numbers,
    wait_result_page_advanced,
)


class FakeElement:
    def __init__(self, attrs=None, single=None, multiple=None, text=""):
        self._attrs = attrs or {}
        self._single = single or {}
        self._multiple = multiple or {}
        self._text = text

    def query_selector(self, selector):
        return self._single.get(selector)

    def query_selector_all(self, selector):
        return self._multiple.get(selector, [])

    def get_attribute(self, name):
        return self._attrs.get(name)

    def text_content(self):
        return self._text


def test_get_first_result_href_reads_first_row_title_href():
    title = FakeElement(attrs={"href": "/detail/1"})
    row = FakeElement(single={SELECTOR_RESULT_TITLE: title})
    page = FakeElement(multiple={SELECTOR_RESULT_ROWS: [row]})

    assert get_first_result_href(page) == "/detail/1"


def test_get_next_page_marker_reads_page_next_data_curpage():
    next_btn = FakeElement(attrs={"data-curpage": "3"})
    page = FakeElement(single={SELECTOR_NEXT_PAGE: next_btn})

    assert get_next_page_marker(page) == "3"


def test_get_result_page_numbers_reads_last_page_markers():
    page_count = FakeElement(attrs={"data-pagenum": "9"}, text="9/9")
    current_page = FakeElement(attrs={"data-curpage": "9"})
    page = FakeElement(single={
        SELECTOR_PAGE_COUNT: page_count,
        SELECTOR_CURRENT_PAGE: current_page,
    })

    assert get_result_page_numbers(page) == (9, 9)


def test_wait_result_page_advanced_accepts_changed_next_marker():
    next_btn = FakeElement(attrs={"data-curpage": "3"})
    page = FakeElement(single={SELECTOR_NEXT_PAGE: next_btn})

    assert wait_result_page_advanced(page, old_href="", old_next_page="2", timeout=10) is True
