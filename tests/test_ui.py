from io import StringIO

import pytest
from rich.console import Console

from cnkibug.app.ui import EstimatedProgressDisplay


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_display(clock: FakeClock) -> EstimatedProgressDisplay:
    console = Console(
        file=StringIO(),
        force_terminal=False,
        color_system=None,
        width=120,
    )
    return EstimatedProgressDisplay(40, 72, console=console, clock=clock)


def test_estimated_progress_pauses_and_only_completes_on_event():
    clock = FakeClock()
    display = make_display(clock)
    display.start()
    display.update_status(
        keyword="增材制造",
        keyword_index=3,
        keyword_total=10,
        page=4,
        page_total=5,
        records=286,
    )

    clock.advance(20)
    assert display.percentage == 45

    display.pause()
    clock.advance(180)
    assert display.elapsed_seconds == pytest.approx(20)
    assert display.percentage == 45
    assert "等待手动验证，任务计时已暂停" in display.status_text

    display.resume()
    clock.advance(20)
    assert display.elapsed_seconds == pytest.approx(40)
    assert display.percentage == 90
    assert "当前关键词：增材制造（3/10）" in display.status_text
    assert "当前页面：第 4/5 页" in display.status_text
    assert "已获取：286 条" in display.status_text

    display.saving()
    assert display.percentage == 99
    assert display.status_text.startswith("正在保存结果……")

    display.complete()
    assert display.percentage == 100
    assert display.status_text.startswith("已完成")
    display.close()


def test_estimated_progress_shows_overtime_at_99_percent():
    clock = FakeClock()
    display = make_display(clock)
    display.start()

    clock.advance(95)

    assert display.percentage == 99
    assert display.status_text.startswith("已超过预计时间，任务仍在运行 +00:23")
    display.stop("测试结束")
    assert display.percentage == 99
    assert display.status_text.startswith("测试结束")
    display.close()
