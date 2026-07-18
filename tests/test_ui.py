from io import StringIO

import pytest
from rich.console import Console

from cnkibug.app import ui
from cnkibug.app.ui import EstimatedProgressDisplay
from cnkibug.core.memory import MemorySample


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


def render_text(display: EstimatedProgressDisplay) -> str:
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=False,
        color_system=None,
        width=120,
    )
    console.print(display._render())
    return output.getvalue()


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
        detail_index=7,
        detail_total=20,
    )

    clock.advance(20)
    assert display.percentage == 45
    assert "已用时：00:20" in render_text(display)

    display.pause()
    clock.advance(180)
    assert display.elapsed_seconds == pytest.approx(20)
    assert display.percentage == 45
    assert "已用时：03:20" in render_text(display)
    assert "等待手动验证，任务计时已暂停" in display.status_text

    display.resume()
    clock.advance(20)
    assert display.elapsed_seconds == pytest.approx(40)
    assert display.percentage == 90
    assert "当前关键词：增材制造（3/10）" in display.status_text
    assert "当前页面：第 4/5 页" in display.status_text
    assert "当前详情：7/20" in display.status_text
    assert "已获取：286 条" in display.status_text

    display.saving()
    assert display.percentage == 99
    assert display.status_text.startswith("正在保存结果……")

    display.complete()
    assert display.percentage == 100
    assert display.status_text.startswith("已完成")
    display.finish(235)
    assert "实际用时：03:55" in render_text(display)
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


def test_interrupt_hint_only_appears_while_scraping_can_be_stopped():
    clock = FakeClock()
    display = make_display(clock)
    display.start()

    assert "按 Ctrl+C 可安全停止，已完成页会保存" in render_text(display)
    display.pause()
    assert "按 Ctrl+C 可安全停止，已完成页会保存" in render_text(display)

    display.saving()
    assert "按 Ctrl+C 可安全停止，已完成页会保存" not in render_text(display)
    display.complete()
    assert "按 Ctrl+C 可安全停止，已完成页会保存" not in render_text(display)
    display.close()


def test_estimated_progress_renders_memory_after_records_before_interrupt_hint():
    class StaticMemorySampler:
        def sample(self):
            mebibyte = 1024 * 1024
            return MemorySample(
                96 * mebibyte,
                332 * mebibyte,
                428 * mebibyte,
                615 * mebibyte,
            )

    clock = FakeClock()
    display = EstimatedProgressDisplay(
        40,
        72,
        console=Console(file=StringIO(), force_terminal=False, color_system=None),
        clock=clock,
        memory_sampler=StaticMemorySampler(),
    )
    display.start()
    display.update_status(records=80)
    text = render_text(display)

    assert "内存约 428 MB（程序 96 + 浏览器 332）｜本轮峰值 615 MB" in text
    assert text.index("已获取：80 条") < text.index("内存约 428 MB")
    assert text.index("内存约 428 MB") < text.index("按 Ctrl+C 可安全停止")
    display.close()


def test_verification_copy_distinguishes_manual_verification_from_automation(
    monkeypatch,
):
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=False,
        color_system=None,
        width=120,
    )
    monkeypatch.setattr(ui, "_console", console)

    ui.print_browser_banner()
    ui.print_verify_alert()

    text = output.getvalue()
    assert "必须由你手动完成" in text
    assert "程序不会自动验证" in text
    assert "程序无法自动完成验证" in text
    assert "验证通过后程序会自动继续" in text
