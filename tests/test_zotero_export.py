import json
from unittest.mock import Mock

import pytest

from cnkibug.cnki.models import Paper
from cnkibug.core.events import EventSink
from cnkibug.fileio import zotero


class Events(EventSink):
    def __init__(self):
        self.items = []
        self.cancelled = False

    def emit(self, name, **payload):
        self.items.append((name, payload))

    def confirm(self, prompt, **kwargs):
        return True

    def cancel_requested(self):
        return self.cancelled


def target():
    return {"id": 12, "name": "Research", "libraryID": 1, "libraryName": "Library",
            "libraryEditable": True, "filesEditable": True, "editable": True}


def test_zotero_mapping_preserves_authors_and_query_notes():
    paper = Paper(title="Paper", authors="张三、李四", document_type="期刊", source="Journal",
                  doi="https://doi.org/10.1234/a", paper_keywords="材料；焊接", queries=["A < B"])
    item = zotero.build_zotero_item(paper, "paper-3")
    assert item["itemType"] == "journalArticle" and item["publicationTitle"] == "Journal"
    assert item["creators"] == [{"name": "张三", "creatorType": "author"}, {"name": "李四", "creatorType": "author"}]
    assert item["DOI"] == "10.1234/a"
    assert item["tags"] == [{"tag": "材料", "type": 1}, {"tag": "焊接", "type": 1}]
    assert "A &lt; B" in item["notes"][0]["note"]


def test_zotero_sends_metadata_then_streams_pdf_to_matching_item(monkeypatch, tmp_path):
    pdf = tmp_path / "论文.pdf"
    pdf.write_bytes(b"%PDF-1.7\nattachment")
    calls = []
    metadata = {}

    def request(endpoint, data=None, **kwargs):
        calls.append(endpoint)
        if endpoint == "getSelectedCollection":
            return target()
        if endpoint == "saveItems":
            metadata.update(data)
            assert kwargs["expected"] == 201
        elif endpoint == "updateSession":
            assert data["target"] == "C12"
        elif endpoint == "saveAttachment":
            attachment = json.loads(kwargs["headers"]["X-Metadata"])
            assert attachment["parentItemID"] == metadata["items"][0]["id"]
            assert attachment["sessionID"] == metadata["sessionID"]
            assert kwargs["body"].read() == pdf.read_bytes()
            assert kwargs["expected"] == 201
        return {}

    monkeypatch.setattr(zotero, "_request", request)
    message = zotero.send_papers_to_zotero([(3, Paper(title="Paper", pdf_path=str(pdf)))], Events())
    assert calls == ["getSelectedCollection", "getSelectedCollection", "saveItems", "updateSession", "saveAttachment"]
    assert "PDF 成功 1，失败 0" in message
    assert pdf.exists()


def test_zotero_uses_new_sessions_and_reports_missing_pdf_separately(monkeypatch, tmp_path):
    sessions = []

    def request(endpoint, data=None, **kwargs):
        if endpoint == "getSelectedCollection":
            return target()
        if endpoint == "saveItems":
            sessions.append(data["sessionID"])
        return {}

    monkeypatch.setattr(zotero, "_request", request)
    for _ in range(2):
        events = Events()
        message = zotero.send_papers_to_zotero([(0, Paper(title="Same title", pdf_path=str(tmp_path / "missing.pdf")))], events)
        assert "已发送 1 篇论文；PDF 成功 0，失败 1" in message
        assert any("PDF 未发送" in p["status"] for n, p in events.items if n == "paper_zotero")
    assert len(set(sessions)) == 2


def test_zotero_target_change_prevents_sending(monkeypatch):
    replies = iter([target(), {**target(), "id": 13}])
    request = Mock(side_effect=lambda *args, **kwargs: next(replies))
    monkeypatch.setattr(zotero, "_request", request)
    with pytest.raises(RuntimeError, match="分类已变化"):
        zotero.send_papers_to_zotero([(0, Paper(title="Paper"))], Events())
    assert request.call_count == 2


def test_zotero_cancel_after_metadata_keeps_saved_items_and_skips_pdf(monkeypatch):
    events = Events()

    def request(endpoint, data=None, **kwargs):
        if endpoint == "getSelectedCollection":
            return target()
        if endpoint == "updateSession":
            events.cancelled = True
        assert endpoint != "saveAttachment"
        return {}

    monkeypatch.setattr(zotero, "_request", request)
    message = zotero.send_papers_to_zotero([(0, Paper(title="Paper", pdf_path="paper.pdf"))], events)
    assert "已发送 1 篇论文" in message and "未处理 1" in message


@pytest.mark.parametrize("status, body", [(200, b"Library files are not editable"), (409, b'{"error":"SESSION_EXISTS"}')])
def test_zotero_save_requires_created_response(monkeypatch, status, body):
    connection = Mock()
    connection.getresponse.return_value = Mock(status=status, read=lambda: body)
    monkeypatch.setattr(zotero.http.client, "HTTPConnection", lambda *args, **kwargs: connection)
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        zotero._request("saveAttachment", expected=201)
    connection.close.assert_called_once_with()
