from __future__ import annotations

from typing import Any

from ..core.events import EventSink
from ..core.search_query import AdvancedQuery, PUBLICATION_FILTERS
from ..core.settings import ScraperSettings


def submit_advanced_search(
    page: Any,
    query: AdvancedQuery,
    settings: ScraperSettings,
    events: EventSink,
) -> None:
    timeout = settings.timeout_selector_ms
    form = page.locator(".search-middle").filter(has=page.locator("#gradetxt"))
    rows = form.locator("#gradetxt > dd")
    rows.first.wait_for(state="visible", timeout=timeout)
    if events.cancel_requested():
        return
    form.locator(".btn-reset").click(timeout=timeout)
    for _ in range(max(0, len(query.conditions) - rows.count())):
        if events.cancel_requested():
            return
        form.locator("#gradetxt .add-group").click(timeout=timeout)

    for index in range(rows.count()):
        if events.cancel_requested():
            return
        row = rows.nth(index)
        textbox = row.locator('.input-box > input[type="text"]')
        textbox.fill("", timeout=timeout)
        if index >= len(query.conditions):
            continue
        condition = query.conditions[index]
        row.locator(".reopt .sort-default").click(timeout=timeout)
        row.locator(f'.reopt li[data-val="{condition.field}"] a').click(timeout=timeout)
        if index:
            row.locator(".logical .sort-default").click(timeout=timeout)
            row.locator(f'.logical .sort-list a[value="{condition.operator}"]').click(timeout=timeout)
        if condition.field != "SU":
            row.locator(".special .sort-default").click(timeout=timeout)
            row.locator(f'.special .sort-list a[value="{condition.match}"]').click(timeout=timeout)
        textbox.fill(condition.text, timeout=timeout)

    checks = [(f'input[value="{value}"]', key in query.publications)
              for key, (_, value) in PUBLICATION_FILTERS.items()]
    checks.extend([
        ('input[data-id="EN"]', query.bilingual),
        ('input[data-id="TY"]', query.synonym),
    ])
    for selector, checked in checks:
        if events.cancel_requested():
            return
        form.locator(selector).set_checked(checked, timeout=timeout)

    # CNKI exposes dates through readonly text inputs; keep readonly and notify its form handlers.
    dates = form.locator("input.publishdate")
    for index, value in enumerate((query.date_from, query.date_to)):
        if events.cancel_requested():
            return
        dates.nth(index).evaluate("""(input, value) => {
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setter.call(input, value);
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
        }""", value, timeout=timeout)
        if dates.nth(index).input_value(timeout=timeout) != value:
            raise RuntimeError("高级检索发表日期未能正确填写。")

    for index, condition in enumerate(query.conditions):
        if events.cancel_requested():
            return
        row = rows.nth(index)
        correct = (
            row.locator(".reopt .sort-default span").get_attribute("value") == condition.field
            and row.locator(".special .sort-default span").get_attribute("value") == condition.match
            and row.locator('.input-box > input[type="text"]').input_value() == condition.text
        )
        if index:
            correct = correct and row.locator(".logical .sort-default span").get_attribute("value") == condition.operator
        if not correct:
            raise RuntimeError(f"高级检索第 {index + 1} 条条件未能正确填写。")
    for selector, checked in checks:
        if form.locator(selector).is_checked() != checked:
            raise RuntimeError("知网页面的筛选或扩展选项与任务条件不一致。")
    if not events.cancel_requested():
        form.locator(".search-buttons input.btn-search").click(timeout=timeout)
