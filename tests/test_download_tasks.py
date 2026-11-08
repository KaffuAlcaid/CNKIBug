from pathlib import Path

import pytest

from cnkibug.cnki.models import Paper
from cnkibug.downloads.files import inspect_pdf, publish_file
from cnkibug.downloads.store import DownloadStore, StoreInUse


def paper(number=1):
    return Paper(title=f"Paper {number}", doi=f"10.1234/paper{number}", detail_url=f"https://kns.cnki.net/paper/{number}")


@pytest.fixture
def store(tmp_path):
    instance = DownloadStore(tmp_path / "tasks")
    yield instance
    instance.close()


def test_store_round_trip_and_cross_batch_dedup(store, tmp_path):
    first = store.create_batch([paper(), paper(), paper(2)], tmp_path / "pdf", {})
    assert first["accepted"] == 2 and first["duplicates"] == 1
    with pytest.raises(ValueError, match="待处理队列"):
        store.create_batch([paper()], tmp_path / "pdf", {})
    forced = store.create_batch([paper()], tmp_path / "pdf", {}, force=True)
    assert forced["accepted"] == 1
    items = store.items(first["id"])
    store.move(items[1]["id"], -1, item=True)
    assert store.items(first["id"])[0]["id"] == items[1]["id"]
    store.remove_item(items[0]["id"])
    assert store.item(items[0]["id"])["state"] == "removed"


def test_conflicting_known_dois_are_not_merged(store, tmp_path):
    first, second = paper(), paper(2)
    second.detail_url = first.detail_url
    result = store.create_batch([first, second], tmp_path, {})
    assert result["accepted"] == 2


def test_attempt_is_committed_before_click_and_old_events_cannot_replace_new_attempt(store, tmp_path):
    batch = store.create_batch([paper()], tmp_path, {})
    item = store.items(batch["id"])[0]
    first = store.begin_attempt(item["id"])
    store.update_attempt(first, state="requested", requested=True, incoming="first")
    reader = __import__("sqlite3").connect(store.path)
    assert reader.execute("SELECT requested FROM attempts WHERE id=?", (first,)).fetchone()[0] == 1
    reader.close()
    second = store.begin_attempt(item["id"])
    assert store.update_attempt(first, state="completed", error="late event") is False
    assert store.item(item["id"])["state"] == "preparing"
    assert store.item(item["id"])["active_attempt"] == second


def test_recovery_separates_unstarted_and_potentially_submitted_requests(store, tmp_path):
    batch = store.create_batch([paper(), paper(2)], tmp_path, {})
    first, second = store.items(batch["id"])
    store.begin_attempt(first["id"])
    attempt = store.begin_attempt(second["id"])
    store.update_attempt(attempt, state="requested", requested=True)
    store.recover_states()
    assert store.item(first["id"])["state"] == "queued"
    assert store.item(second["id"])["state"] == "uncertain"
    assert store.batch(batch["id"])["state"] == "paused"


def test_artifact_completion_and_history_cleanup_leave_pdf(store, tmp_path):
    batch = store.create_batch([paper()], tmp_path, {})
    item = store.items(batch["id"])[0]
    attempt = store.begin_attempt(item["id"])
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    store.complete(item["id"], inspect_pdf(pdf), attempt)
    store.set_batch(batch["id"], "completed")
    store.clear_batch(batch["id"])
    assert pdf.exists()
    assert store.snapshot()["batches"] == []


def test_single_executor_lock_is_released_after_close(tmp_path):
    first = DownloadStore(tmp_path)
    with pytest.raises(StoreInUse):
        DownloadStore(tmp_path)
    first.close()
    second = DownloadStore(tmp_path)
    second.close()


def test_unknown_database_version_preserves_database(tmp_path):
    import sqlite3

    database = tmp_path / "downloads.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA user_version=99")
        db.execute("CREATE TABLE preserved(value TEXT)")
        db.execute("INSERT INTO preserved VALUES('keep')")
    with pytest.raises(ValueError, match="版本"):
        DownloadStore(tmp_path)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT value FROM preserved").fetchone()[0] == "keep"


def test_publication_never_overwrites_existing_file(tmp_path):
    source, target = tmp_path / "received", tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4\n%%EOF\n")
    target.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        publish_file(source, target)
    assert target.read_bytes() == b"existing"
    target.unlink()
    publish_file(source, target)
    assert inspect_pdf(target)["sha256"] == inspect_pdf(source)["sha256"]


def test_pdf_validation_rejects_html_download(tmp_path):
    path = tmp_path / "not-a-pdf"
    path.write_bytes(b"<html>login</html>")
    with pytest.raises(ValueError, match="不是 PDF"):
        inspect_pdf(path)
