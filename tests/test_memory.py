from types import SimpleNamespace

import psutil

from cnkibug.core.memory import (
    MemorySample,
    MemorySampler,
    format_memory,
    format_task_finished_memory,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeProcess:
    def __init__(
        self,
        rss: int,
        children=(),
        error: Exception | None = None,
        name: str = "node",
        cmdline=(),
    ) -> None:
        self.rss = rss
        self._children = list(children)
        self._error = error
        self._name = name
        self._cmdline = list(cmdline)
        self.memory_info_calls = 0

    def memory_info(self):
        self.memory_info_calls += 1
        if self._error is not None:
            raise self._error
        return SimpleNamespace(rss=self.rss)

    def children(self, *, recursive: bool):
        assert recursive is True
        return self._children

    def name(self) -> str:
        if self._error is not None:
            raise self._error
        return self._name

    def cmdline(self) -> list[str]:
        if self._error is not None:
            raise self._error
        return self._cmdline


def test_memory_sampler_sums_parent_and_playwright_browser_root():
    mebibyte = 1024 * 1024
    child = FakeProcess(332 * mebibyte, name="msedge.exe")
    parent = FakeProcess(96 * mebibyte, [child])
    sampler = MemorySampler(process_factory=lambda pid: parent, pid=123)

    sample = sampler.sample()

    assert sample == MemorySample(
        program_bytes=96 * mebibyte,
        browser_bytes=332 * mebibyte,
        total_bytes=428 * mebibyte,
        peak_bytes=428 * mebibyte,
    )


def test_memory_sampler_ignores_disappeared_or_denied_children():
    mebibyte = 1024 * 1024
    parent = FakeProcess(
        96 * mebibyte,
        [
            FakeProcess(332 * mebibyte, name="chromium"),
            FakeProcess(0, error=psutil.NoSuchProcess(1)),
            FakeProcess(0, error=psutil.AccessDenied(2)),
        ],
    )
    sampler = MemorySampler(process_factory=lambda pid: parent)

    sample = sampler.sample()

    assert sample is not None
    assert sample.browser_bytes == 332 * mebibyte


def test_memory_sampler_excludes_chromium_renderer_and_gpu_processes():
    mebibyte = 1024 * 1024
    parent = FakeProcess(
        96 * mebibyte,
        [
            FakeProcess(332 * mebibyte, name="chrome.exe"),
            FakeProcess(
                400 * mebibyte,
                name="chrome.exe",
                cmdline=("--type=renderer",),
            ),
            FakeProcess(
                120 * mebibyte,
                name="chrome.exe",
                cmdline=("--type=gpu-process",),
            ),
            FakeProcess(50 * mebibyte, name="node"),
        ],
    )
    sampler = MemorySampler(process_factory=lambda pid: parent)

    sample = sampler.sample()

    assert sample is not None
    assert sample.browser_bytes == 332 * mebibyte
    assert sample.total_bytes == 428 * mebibyte


def test_memory_sampler_throttles_process_queries():
    clock = FakeClock()
    parent = FakeProcess(10)
    sampler = MemorySampler(clock=clock, process_factory=lambda pid: parent)

    first = sampler.sample()
    second = sampler.sample()
    clock.advance(0.99)
    third = sampler.sample()
    clock.advance(0.01)
    fourth = sampler.sample()

    assert first == second == third
    assert fourth is not None
    assert parent.memory_info_calls == 2

    sampler.sample(force=True)
    assert parent.memory_info_calls == 3


def test_memory_sampler_tracks_peak_and_resets_for_next_task():
    clock = FakeClock()
    parent = FakeProcess(200)
    sampler = MemorySampler(clock=clock, process_factory=lambda pid: parent)

    assert sampler.sample().peak_bytes == 200
    clock.advance(1)
    parent.rss = 100
    assert sampler.sample().peak_bytes == 200

    sampler.reset()
    parent.rss = 120
    assert sampler.sample().peak_bytes == 120


def test_memory_formatting_is_stable_and_handles_unavailable_samples():
    mebibyte = 1024 * 1024
    sample = MemorySample(
        program_bytes=96 * mebibyte + 99,
        browser_bytes=332 * mebibyte + 99,
        total_bytes=428 * mebibyte + 198,
        peak_bytes=615 * mebibyte + 1,
    )

    assert format_memory(sample) == "内存约 428 MB（程序 96 + 浏览器 332）｜本轮峰值 615 MB"
    assert format_task_finished_memory(sample) == "任务结束后内存：428 MB｜本轮峰值 615 MB"
    assert format_memory(None) == "内存：暂不可用"
    assert format_task_finished_memory(None) == "任务结束后内存：暂不可用"


def test_memory_sampler_returns_unavailable_when_parent_cannot_be_sampled():
    parent = FakeProcess(0, error=psutil.AccessDenied(1))
    sampler = MemorySampler(process_factory=lambda pid: parent)

    assert sampler.sample() is None
