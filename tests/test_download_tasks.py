from pathlib import Path
import asyncio
import threading
import time

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


def wait_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for download service")


class FakeBrowser:
    calls = []
    gate = None

    def __init__(self, paths, batch, settings, control, report):
        self.control, self.report = control, report

    async def open(self):
        await self.control.check()

    async def download(self, value, incoming, before_click):
        before_click()
        self.calls.append(value.doi)
        self.report("phase", state="transferring", message="local transfer")
        while self.gate is not None and not self.gate.is_set():
            await self.control.check(allow_pause=False)
            await asyncio.sleep(0.02)
        path = incoming / "received.pdf"
        path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        self.report("evidence", transport_completed=True)
        return path

    async def close(self):
        pass


@pytest.fixture
def service(tmp_path):
    from cnkibug.app.runtime import init_runtime
    from cnkibug.downloads.service import DownloadService

    FakeBrowser.calls = []
    FakeBrowser.gate = None
    paths = init_runtime(program_dir=tmp_path, configure_logging=False).paths
    instance = DownloadService(paths, browser_factory=FakeBrowser)
    yield instance
    instance.close()
    assert instance.wait_closed(8)


def submit(service, tmp_path, papers):
    from cnkibug.app.runtime import DEFAULT_CONFIG
    from cnkibug.core.settings import get_scraper_settings

    settings = get_scraper_settings({**DEFAULT_CONFIG, "download_auth_wait_sec": 0})
    return service.submit(papers, tmp_path / "pdf", settings).result(3)["id"]


def test_service_serial_completion_and_existing_pdf_reuse(service, tmp_path):
    batch_id = submit(service, tmp_path, [paper()])
    wait_until(lambda: service.snapshot()["batches"][0]["state"] == "completed")
    assert FakeBrowser.calls == [paper().doi]
    assert service.snapshot()["batches"][0]["counts"]["completed"] == 1
    submit(service, tmp_path, [paper()])
    wait_until(lambda: len(service.snapshot()["batches"]) == 2 and service.snapshot()["batches"][1]["state"] == "completed")
    assert FakeBrowser.calls == [paper().doi]
    assert len(list((tmp_path / "pdf").glob("*.pdf"))) == 1


def test_pause_finishes_transfer_without_starting_next_item(service, tmp_path):
    FakeBrowser.gate = threading.Event()
    batch_id = submit(service, tmp_path, [paper(), paper(2)])
    wait_until(lambda: len(FakeBrowser.calls) == 1)
    service.request("pause").result(3)
    FakeBrowser.gate.set()
    wait_until(lambda: service.snapshot()["batches"][0]["counts"]["completed"] == 1)
    assert service.snapshot()["paused"]
    assert FakeBrowser.calls == [paper().doi]
    service.request("resume", batch_id=batch_id).result(3)
    wait_until(lambda: service.snapshot()["batches"][0]["state"] == "completed")
    assert FakeBrowser.calls == [paper().doi, paper(2).doi]


def test_uncertain_stop_requires_review_before_resume(service, tmp_path):
    FakeBrowser.gate = threading.Event()
    batch_id = submit(service, tmp_path, [paper()])
    wait_until(lambda: len(FakeBrowser.calls) == 1)
    service.request("stop").result(3)
    wait_until(lambda: not service.busy)
    item = service.snapshot()["batches"][0]["items"][0]
    assert item["state"] == "uncertain"
    with pytest.raises(ValueError, match="待确认"):
        service.request("resume", batch_id=batch_id).result(3)
    path = tmp_path / "manual.pdf"
    path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    service.request("associate", item_id=item["id"], path=str(path)).result(3)
    assert service.snapshot()["batches"][0]["items"][0]["state"] == "completed"


def test_restart_recovers_completed_native_file_without_new_click(tmp_path):
    from cnkibug.app.runtime import DEFAULT_CONFIG, init_runtime
    from cnkibug.downloads.files import owned_incoming
    from cnkibug.downloads.service import DownloadService

    paths = init_runtime(program_dir=tmp_path, configure_logging=False).paths
    store = DownloadStore(paths.data_dir / "tasks")
    batch = store.create_batch([paper()], tmp_path / "pdf", DEFAULT_CONFIG)
    item = store.items(batch["id"])[0]
    attempt = store.begin_attempt(item["id"])
    incoming = owned_incoming(tmp_path / "pdf", attempt)
    incoming.mkdir(parents=True)
    guid = "12345678-1234-1234-1234-123456789abc"
    (incoming / guid).write_bytes(b"%PDF-1.4\n%%EOF\n")
    store.update_attempt(attempt, state="transferring", requested=True, guid=guid, incoming=str(incoming))
    store.close()
    instance = DownloadService(paths, browser_factory=FakeBrowser)
    try:
        wait_until(lambda: instance.snapshot()["batches"][0]["items"][0]["state"] == "completed")
        assert instance.snapshot()["paused"]
        assert len(list((tmp_path / "pdf").glob("*.pdf"))) == 1
    finally:
        instance.close()
        assert instance.wait_closed()


def test_registration_failure_is_repaired_without_repeating_download(service, tmp_path, monkeypatch):
    from cnkibug.fileio.pdf_links import PdfIndex

    original = PdfIndex.record
    failed = False

    def interrupted(self, *args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated index write failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PdfIndex, "record", interrupted)
    batch_id = submit(service, tmp_path, [paper()])
    wait_until(lambda: failed and not service.busy)
    assert service.snapshot()["batches"][0]["items"][0]["state"] == "finalizing"
    assert len(list((tmp_path / "pdf").glob("*.pdf"))) == 1
    service.request("recheck", batch_id=batch_id).result(3)
    assert service.snapshot()["batches"][0]["state"] == "completed"
    assert FakeBrowser.calls == [paper().doi]
    assert len(list((tmp_path / "pdf").glob("*.pdf"))) == 1


def test_active_batch_destination_cannot_change_while_paused(service, tmp_path):
    FakeBrowser.gate = threading.Event()
    batch_id = submit(service, tmp_path, [paper()])
    wait_until(lambda: len(FakeBrowser.calls) == 1)
    service.request("pause").result(3)
    with pytest.raises(ValueError, match="先停止"):
        service.request("destination", batch_id=batch_id, destination=str(tmp_path / "elsewhere")).result(3)
    FakeBrowser.gate.set()


def test_invalid_pdf_is_a_failed_download_not_an_uncertain_attempt(service, tmp_path, monkeypatch):
    async def invalid(self, paper, incoming, before_click):
        before_click()
        path = incoming / "received.pdf"
        path.write_text("<html>login</html>", encoding="utf-8")
        return path

    monkeypatch.setattr(FakeBrowser, "download", invalid)
    submit(service, tmp_path, [paper()])
    wait_until(lambda: service.snapshot()["batches"][0]["state"] == "completed")
    item = service.snapshot()["batches"][0]["items"][0]
    assert item["state"] == "failed" and item["error_code"] == "invalid_pdf"


def test_cleanup_keeps_unclaimed_files_in_attempt_directory(tmp_path):
    from cnkibug.downloads.files import discard_incoming, owned_incoming

    attempt = "a" * 32
    incoming = owned_incoming(tmp_path, attempt)
    incoming.mkdir(parents=True)
    (incoming / "ours").write_bytes(b"done")
    other = incoming / "unclaimed"
    other.write_bytes(b"keep")
    discard_incoming(tmp_path, attempt, ["ours"])
    assert not (incoming / "ours").exists()
    assert other.read_bytes() == b"keep"
