import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cnkibug.app.runtime import DEFAULT_CONFIG, get_runtime_paths
from cnkibug.browser.session import ScrapeSession
from cnkibug.cnki import search
from cnkibug.core.events import NULL_EVENTS
from cnkibug.core.search_query import AdvancedQuery, SearchCondition, load_advanced_queries
from cnkibug.core.settings import get_scraper_settings
from cnkibug.workflow import keyword_run, state
from cnkibug.workflow.task import initialize_task


def _query(text="welding", **options):
    return AdvancedQuery((SearchCondition(text=text), SearchCondition("TI", "stress", "NOT", "%")), **options)


def test_query_json_roundtrip_preserves_all_conditions_and_filters():
    query = _query(date_from="2020-01-01", date_to="2026-09-08", bilingual=False,
                   synonym=True, publications=("oa", "funded"))
    restored = AdvancedQuery.from_dict(json.loads(json.dumps(query.to_dict())))
    assert restored == query
    assert restored.conditions[1].operator == "NOT"
    assert "2020-01-01" in restored.summary()


@pytest.mark.parametrize("raw", [
    {"conditions": []},
    {"conditions": [{"field": "UNKNOWN", "text": "x"}]},
    {"conditions": [{"field": "SU", "text": "x", "match": "%"}]},
    {"conditions": [{"text": "x", "operator": "invalid"}]},
    {"conditions": [{"text": " "}]},
    {"conditions": [{"text": "x" * 121}]},
    {"conditions": [{"text": "x"}] * 11},
    {"conditions": [{"text": "x"}], "date_from": "2026-02-30"},
    {"conditions": [{"text": "x"}], "date_from": "2026-02-01", "date_to": "2026-01-01"},
    {"conditions": [{"text": "x"}], "bilingual": "false"},
    {"conditions": [{"text": "x"}], "bilingual": True, "synonym": True},
    {"conditions": [{"text": "x"}], "publications": ["unknown"]},
])
def test_invalid_query_cannot_be_executed_or_restored(raw):
    with pytest.raises(ValueError):
        AdvancedQuery.from_dict(raw)


def test_mixed_task_resume_keeps_distinct_queries_and_page_checkpoints(tmp_path):
    paths = get_runtime_paths(tmp_path)
    queries = {"Advanced 1": _query(), "Advanced 2": _query(date_from="2025-01-01")}
    keywords = ["ordinary", *queries]
    saved = state.make_task_state(keywords, 3, "multi_merge", "TS", advanced_queries=queries)
    records = [["Title", "Author", "Source", "Date", "https://example.test/paper"]]
    state.mark_keyword_progress(saved, "Advanced 1", 1, records)
    assert state.save_last_task(saved, paths) is not None
    loaded = state.load_last_task(paths)
    assert loaded is not None
    task = initialize_task(
        [], 1, "single", loaded, False, False, False,
        get_scraper_settings(DEFAULT_CONFIG), paths, NULL_EVENTS,
    )
    assert task.keywords == keywords
    assert task.advanced_queries == queries
    assert state.keyword_checkpoint(task.state, "Advanced 1") == (1, records)
    assert state.keyword_checkpoint(task.state, "Advanced 2") == (0, [])


def test_version_five_ordinary_task_still_loads(tmp_path):
    paths = get_runtime_paths(tmp_path)
    saved = state.make_task_state(["ordinary"], 1, "single", "TS")
    saved["version"] = 5
    saved.pop("advanced_queries")
    state.save_last_task(saved, paths)
    loaded = state.load_last_task(paths)
    assert loaded is not None
    assert loaded["advanced_queries"] == {}


def test_orphaned_or_invalid_advanced_conditions_are_rejected():
    with pytest.raises(ValueError):
        load_advanced_queries({"missing": _query().to_dict()}, ["ordinary"])
    saved = state.make_task_state(["Advanced 1"], 1, "single", "TS", advanced_queries={"Advanced 1": _query()})
    saved["advanced_queries"]["Advanced 1"]["conditions"] = []
    assert not state._is_valid_task_state(saved)


def test_advanced_submission_uses_advanced_form_and_shared_outcome(monkeypatch):
    settings = get_scraper_settings(DEFAULT_CONFIG)
    session = ScrapeSession()
    session.page = Mock(url=search.CNKI_ADVANCED_URL)
    session.page.is_closed.return_value = False
    ordinary_submit = Mock()
    advanced_submit = Mock()
    open_page = Mock()
    monkeypatch.setattr(search, "open_home_page", Mock())
    monkeypatch.setattr(search, "open_search_page", open_page)
    monkeypatch.setattr(search, "submit_search", ordinary_submit)
    monkeypatch.setattr(search, "submit_advanced_search", advanced_submit)
    monkeypatch.setattr(search, "handle_verify_with_progress", lambda *args: "")
    monkeypatch.setattr(search, "wait_search_outcome", lambda *args: search.SEARCH_RESULTS)
    query = _query()
    result = search.run_keyword_search(session, "Advanced 1", settings, "ref", advanced_query=query)
    assert result.status == search.SEARCH_RESULTS
    open_page.assert_called_once_with(session.page, settings, session.events, advanced=True)
    advanced_submit.assert_called_once_with(session.page, query, settings, session.events)
    ordinary_submit.assert_not_called()


def test_workflow_dispatches_each_query_without_changing_ordinary_calls(monkeypatch):
    scraper = Mock()
    monkeypatch.setattr(keyword_run, "scrape_keyword", scraper)
    query = _query()
    task = SimpleNamespace(
        session=Mock(), settings=get_scraper_settings(DEFAULT_CONFIG),
        keywords=["ordinary", "Advanced 1"], max_pages=2, include_citation=False,
        detail_fetcher=None, advanced_queries={"Advanced 1": query},
    )
    callback = Mock()
    keyword_run._scrape_with_errors(task, "ordinary", "ref", 1, 0, [], callback)
    assert "advanced_query" not in scraper.call_args.kwargs
    keyword_run._scrape_with_errors(task, "Advanced 1", "ref", 2, 1, [["saved"]], callback)
    assert scraper.call_args.kwargs["advanced_query"] == query
    assert scraper.call_args.kwargs["start_page"] == 2


def _list_app(monkeypatch):
    from cnkibug.gui import task_form

    def variable(*, value):
        result = Mock()
        result.get.return_value = value
        result.set.side_effect = lambda text: setattr(result.get, "return_value", text)
        return result

    for name in ("Frame", "Label", "Entry", "Button"):
        monkeypatch.setattr(task_form.ttk, name, Mock(side_effect=lambda *args, **kwargs: Mock()))
    monkeypatch.setattr(task_form.tk, "StringVar", variable)
    monkeypatch.setattr(task_form, "ToolTip", Mock())
    app = task_form.TaskForm.__new__(task_form.TaskForm)
    app.root = Mock()
    app._running = False
    app._keyword_rows = []
    app._advanced_queries = {}
    app._keyword_list = Mock()
    app._keyword_canvas = Mock()
    app._keyword_status_var = Mock()
    app._focus_keyword = Mock()
    app._set_keywords(["ordinary"])
    return app


def test_gui_advanced_add_cancel_edit_delete_preserves_other_items(monkeypatch):
    app = _list_app(monkeypatch)
    first, second, edited = _query(), _query("steel"), _query("alloy")
    dialog = Mock()
    dialog.return_value.show.side_effect = [first, second, None, edited]
    monkeypatch.setattr("cnkibug.gui.task_form.AdvancedSearchDialog", dialog)
    app._open_advanced()
    app._open_advanced()
    assert len(app._keywords) == 3
    first_key, second_key = app._keywords[1:]
    app._open_advanced(app._keyword_rows[1])
    assert app._advanced_queries[first_key] == first
    app._open_advanced(app._keyword_rows[1])
    assert app._keywords == ["ordinary", first_key, second_key]
    assert app._advanced_queries == {first_key: edited, second_key: second}
    dialog.assert_called_with(app.root, first)
    app._delete_keyword(app._keyword_rows[1])
    assert app._keywords == ["ordinary", second_key]
    assert app._advanced_queries == {second_key: second}


def test_gui_inline_edits_and_clear_keep_types_and_conditions_consistent(monkeypatch):
    app = _list_app(monkeypatch)
    warning = Mock()
    monkeypatch.setattr("cnkibug.gui.task_form.messagebox.showwarning", warning)
    query = _query()
    app._advanced_queries = {"Advanced 1": query}
    app._set_keywords(["ordinary", "Advanced 1"])
    ordinary, advanced = app._keyword_rows
    assert advanced.text.get() == query.summary()
    ordinary.text.set("  edited  phrase  ")
    assert app._collect_keywords() == ["edited  phrase", "Advanced 1"]
    assert app._advanced_queries == {"Advanced 1": query}
    ordinary.text.set("Advanced 1")
    assert app._collect_keywords() is None
    warning.assert_called_once()
    ordinary.text.set("  ")
    assert app._collect_keywords() == ["Advanced 1"]
    app._set_keywords([])
    assert app._keywords == []
    assert app._advanced_queries == {}
