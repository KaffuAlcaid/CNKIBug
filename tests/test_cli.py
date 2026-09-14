from cnkibug.app import cli
from cnkibug.app.runtime import get_runtime_paths
from cnkibug.workflow.state import make_task_state


def test_cli_ignore_checkpoint_deletes_file_and_starts_new_task(monkeypatch, tmp_path):
    paths = get_runtime_paths(tmp_path)
    checkpoint = paths.cache_dir / "last_task.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("{}\n", encoding="utf-8")
    state = make_task_state(["焊接"], 2, "single", "TS")
    monkeypatch.setattr(cli, "load_last_task", lambda _paths: state)
    monkeypatch.setattr(cli, "safe_input", lambda _prompt: "2")
    monkeypatch.setattr(
        cli,
        "_run_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ignoring a checkpoint must not resume it")
        ),
    )

    action = cli._handle_pending_task(paths, object(), object(), object())

    assert action == "new"
    assert not checkpoint.exists()


def test_cli_failed_checkpoint_delete_stays_in_prompt(monkeypatch, tmp_path):
    paths = get_runtime_paths(tmp_path)
    checkpoint = paths.cache_dir / "last_task.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("{}\n", encoding="utf-8")
    choices = iter(["2", "0"])
    state = make_task_state(["焊接"], 2, "single", "TS")
    monkeypatch.setattr(cli, "load_last_task", lambda _paths: state)
    monkeypatch.setattr(cli, "safe_input", lambda _prompt: next(choices))
    monkeypatch.setattr(cli, "delete_last_task", lambda _paths: False)
    monkeypatch.setattr(
        cli,
        "_run_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("a checkpoint that still exists must not be resumed")
        ),
    )

    action = cli._handle_pending_task(paths, object(), object(), object())

    assert action == "exit"
    assert checkpoint.exists()


def test_cli_cancel_keeps_checkpoint_and_exits(monkeypatch, tmp_path):
    paths = get_runtime_paths(tmp_path)
    checkpoint = paths.cache_dir / "last_task.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("{}\n", encoding="utf-8")
    state = make_task_state(["焊接"], 2, "single", "TS")
    monkeypatch.setattr(cli, "load_last_task", lambda _paths: state)
    monkeypatch.setattr(cli, "safe_input", lambda _prompt: "0")
    monkeypatch.setattr(
        cli,
        "_run_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("cancelling resume must not run the checkpoint")
        ),
    )

    action = cli._handle_pending_task(paths, object(), object(), object())

    assert action == "exit"
    assert checkpoint.exists()
