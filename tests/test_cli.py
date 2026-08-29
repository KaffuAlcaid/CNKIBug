from io import StringIO

import pytest
from rich.console import Console

from cnkibug.app import cli
from cnkibug.app.runtime import get_runtime_paths
from cnkibug.core.memory import MemorySample
from cnkibug.workflow.state import make_task_state


class FakeMemorySampler:
    def __init__(self) -> None:
        self.reset_count = 0

    def reset(self) -> None:
        self.reset_count += 1

    def sample(self, *, force: bool = False) -> MemorySample:
        mebibyte = 1024 * 1024
        peak = 615 if self.reset_count == 1 else 300
        return MemorySample(
            112 * mebibyte,
            0,
            112 * mebibyte,
            peak * mebibyte,
        )


def test_cli_prints_final_memory_after_each_task_and_resets_peak(monkeypatch):
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)
    sampler = FakeMemorySampler()
    calls = []
    monkeypatch.setattr(cli, "_console", console)
    monkeypatch.setattr(cli, "scrape_cnki", lambda *args, **kwargs: calls.append((args, kwargs)))

    cli._run_task(sampler, ["焊接"], 1, "single", settings="settings", paths="paths")
    cli._run_task(sampler, ["铸造"], 2, "single", settings="settings", paths="paths")

    assert sampler.reset_count == 2
    assert [call[0][:3] for call in calls] == [
        (["焊接"], 1, "single"),
        (["铸造"], 2, "single"),
    ]
    assert output.getvalue().splitlines() == [
        "任务结束后内存：112 MB｜本轮峰值 615 MB",
        "任务结束后内存：112 MB｜本轮峰值 300 MB",
    ]


def test_cli_prints_final_memory_when_task_exits_early(monkeypatch):
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)
    sampler = FakeMemorySampler()
    monkeypatch.setattr(cli, "_console", console)
    monkeypatch.setattr(cli, "scrape_cnki", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("failed")))

    with pytest.raises(RuntimeError, match="failed"):
        cli._run_task(sampler, ["焊接"], 1, "single", settings="settings", paths="paths")

    assert "任务结束后内存：112 MB｜本轮峰值 615 MB" in output.getvalue()


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
