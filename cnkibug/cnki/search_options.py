from __future__ import annotations

import re

from ..core.events import EventSink
from ..core.search_query import SearchOptions
from ..core.settings import ScraperSettings


def apply_search_options(page, options: SearchOptions, settings: ScraperSettings, events: EventSink) -> None:
    if events.cancel_requested():
        return
    timeout = settings.timeout_selector_ms
    page.locator("#searchset").click(timeout=timeout)
    panel = page.locator("#searchsetdiv:visible")
    panel.wait_for(state="visible", timeout=timeout)
    chosen = panel.locator(".haschecked-list li")
    for index in range(chosen.count() - 1, -1, -1):
        if events.cancel_requested():
            return
        item = chosen.nth(index)
        if item.locator(".item").inner_text().strip() not in options.resources:
            item.hover(timeout=timeout)
            item.locator(".icon-del").click(timeout=timeout)
    current = set(panel.locator(".haschecked-list .item").all_inner_texts())
    for name in options.resources:
        if events.cancel_requested():
            return
        if name in current:
            continue
        menu = panel.locator(".doctype-list")
        if not menu.is_visible():
            panel.locator(".add-doctype").click(timeout=timeout)
        menu.locator("li").filter(has=page.get_by_text(name, exact=True)).locator("a").click(timeout=timeout)
    for name, value in (("order", options.sort), ("lang", options.language)):
        panel.locator(f'input[name="{name}"][value="{value}"]').check(timeout=timeout)
        if not panel.locator(f'input[name="{name}"][value="{value}"]').is_checked():
            raise RuntimeError("知网排序或语种设置未能正确填写。")
    actual = {value.strip() for value in panel.locator(".haschecked-list .item").all_inner_texts()}
    if actual != set(options.resources):
        raise RuntimeError("知网检索范围与任务设置不一致。")
    panel.locator("#ctrl-s").click(timeout=timeout)
    panel.wait_for(state="hidden", timeout=timeout)


def apply_page_size(page, options: SearchOptions, settings: ScraperSettings) -> None:
    current = page.locator("#perPageDiv .sort-default span")
    current.wait_for(state="visible", timeout=settings.timeout_selector_ms)
    if current.inner_text().strip() == str(options.page_size):
        return
    count_text = page.locator("#countPageDiv").inner_text()
    match = re.search(r"共找到\s*([\d,，]+)", count_text)
    expected = min(options.page_size, int(match.group(1).replace(",", "").replace("，", ""))) if match else None
    page.locator("#perPageDiv .sort-default").click(timeout=settings.timeout_selector_ms)
    page.locator(f'#perPageDiv li[data-val="{options.page_size}"] a').click(timeout=settings.timeout_selector_ms)
    page.wait_for_function("args => document.querySelector('#perPageDiv .sort-default span')?.textContent.trim() === args.size && (args.count === null ? !!document.querySelector('table.result-table-list tbody tr') : document.querySelectorAll('table.result-table-list tbody tr').length === args.count)", arg={"size": str(options.page_size), "count": expected}, timeout=settings.timeout_load_ms)
