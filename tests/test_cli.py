from io import StringIO

import pytest
from rich.console import Console

from cnkibug.app import cli
from cnkibug.core.memory import MemorySample


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
