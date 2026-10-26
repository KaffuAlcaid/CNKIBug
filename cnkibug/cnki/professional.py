from __future__ import annotations

import time
from dataclasses import replace

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..core.events import EventSink
from ..core.search_query import ProfessionalQuery
from ..core.settings import ScraperSettings


class ProfessionalSearchError(RuntimeError):
    pass


def prepare_professional_search(page, settings: ScraperSettings, events: EventSink) -> None:
    if events.cancel_requested():
        return
    entry = page.get_by_text("专业检索", exact=True).filter(visible=True).first
    entry.click(timeout=settings.timeout_selector_ms)
    if events.cancel_requested():
        return
    field = page.locator(".search-middle textarea:visible")
    field.first.wait_for(state="visible", timeout=settings.timeout_selector_ms)
    if field.count() != 1:
        raise ProfessionalSearchError("无法确定知网的专业检索输入框。")


def professional_error(page) -> str:
    messages = page.locator(
        ".layui-layer-content:visible, .search-error:visible, [role=alert]:visible",
    ).all_text_contents()
    return next((text.strip()[:300] for text in messages if text.strip()), "知网未接受该专业检索式。")


def submit_professional_search(page, query: ProfessionalQuery, settings: ScraperSettings, events: EventSink) -> None:
    from .search import wait_search_outcome

    if events.cancel_requested():
        return
    fields = page.locator(".search-middle textarea:visible")
    if fields.count() != 1:
        raise ProfessionalSearchError("专业检索输入框不可用，请重新打开检索页面。")
    field = fields.first
    maximum = field.get_attribute("maxlength")
    if maximum and maximum.isdigit() and int(maximum) > 0:
        if len(query.expression.encode("utf-16-le")) // 2 > int(maximum):
            raise ProfessionalSearchError(f"专业检索式超过知网页面允许的 {maximum} 个字符。")
    field.fill(query.expression, timeout=settings.timeout_selector_ms)
    expected = query.expression.replace("\r\n", "\n").replace("\r", "\n")
    if field.input_value() != expected:
        raise ProfessionalSearchError("专业检索式未能完整填写，已停止提交。")
    panel = page.locator(".search-middle").filter(has=page.locator("textarea:visible"))
    if panel.count() != 1:
        raise ProfessionalSearchError("无法确定专业检索表单。")
    for selector in ("input[data-id='EN']", "input[data-id='TY']"):
        for checkbox in panel.locator(selector).all():
            if events.cancel_requested():
                return
            if checkbox.is_checked():
                checkbox.set_checked(False, timeout=settings.timeout_selector_ms)
    submit = panel.locator("input.btn-search:visible, button.btn-search:visible")
    if submit.count() != 1:
        raise ProfessionalSearchError("无法确定专业检索的提交按钮。")
    messages = []

    def on_dialog(dialog):
        messages.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", on_dialog)
    try:
        if events.cancel_requested():
            return
        submit.click(timeout=settings.timeout_selector_ms)
        deadline = time.monotonic() + settings.timeout_selector_ms / 1000
        while time.monotonic() < deadline:
            if events.cancel_requested():
                return
            if messages:
                raise ProfessionalSearchError(f"知网未接受该检索式：{messages[-1][:300]}")
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            try:
                outcome = wait_search_outcome(
                    page, replace(settings, timeout_selector_ms=min(250, remaining)), professional=True,
                )
            except PlaywrightTimeoutError:
                continue
            if messages:
                raise ProfessionalSearchError(f"知网未接受该检索式：{messages[-1][:300]}")
            if outcome == "invalid_query":
                raise ProfessionalSearchError(professional_error(page))
            return
        if messages:
            raise ProfessionalSearchError(f"知网未接受该检索式：{messages[-1][:300]}")
        raise PlaywrightTimeoutError("等待专业检索结果超时。")
    finally:
        page.remove_listener("dialog", on_dialog)
