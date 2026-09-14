from cnkibug.cnki.selectors import SELECTOR_NEXT_PAGE
from cnkibug.cnki.pagination import wait_result_page_advanced


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


def test_wait_result_page_advanced_accepts_changed_next_marker():
    next_btn = FakeElement(attrs={"data-curpage": "3"})
    page = FakeElement(single={SELECTOR_NEXT_PAGE: next_btn})

    assert wait_result_page_advanced(page, old_href="", old_next_page="2", timeout=10) is True


def test_wait_result_page_advanced_stops_before_polling():
    class Page:
        def query_selector(self, selector):
            raise AssertionError("cancelled pagination must not inspect the page")

    assert wait_result_page_advanced(
        Page(),
        old_href="",
        old_next_page="2",
        timeout=10,
        stop_requested=lambda: True,
    ) is False
