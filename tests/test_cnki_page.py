from cnkibug import cnki_page


class FakeParent:
    def __init__(self):
        self.single = {"td.name a": "title"}
        self.multiple = {"td.author a.KnowledgeNetLink": ["a1", "a2"]}

    def query_selector(self, selector):
        return self.single.get(selector)

    def query_selector_all(self, selector):
        return self.multiple.get(selector, [])


def test_query_first_uses_selector_group():
    assert cnki_page.query_first(FakeParent(), "title") == "title"


def test_query_all_uses_selector_group():
    assert cnki_page.query_all(FakeParent(), "author") == ["a1", "a2"]


def test_selector_returns_primary_selector():
    assert cnki_page.selector("result_rows") == "table.result-table-list tbody tr"
