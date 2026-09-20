from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from ..browser.runtime import open_browser_context
from ..core.events import EventSink
from ..core.runtime import RuntimePaths
from ..core.settings import ScraperSettings
from .guard import VERIFY_CANCELLED, VERIFY_PAGE_CLOSED, VERIFY_TIMEOUT, handle_verify
from .models import Paper, document_type_family


JOURNAL_HOME = "https://navi.cnki.net/knavi"


class JournalQueryStopped(RuntimeError):
    pass


def _journal_key(name: str) -> str:
    return "".join(name.strip().strip("《》").split()).casefold()


def choose_journal(candidates: list[dict], name: str) -> dict:
    matches = {candidate["url"]: candidate for candidate in candidates
               if candidate.get("url") and _journal_key(candidate.get("name", "")) == _journal_key(name)}
    if not matches:
        raise ValueError("未找到名称完全匹配的期刊，请在知网核对期刊名称。")
    if len(matches) != 1:
        raise ValueError("存在多个同名期刊，请在知网核对具体刊物。")
    return next(iter(matches.values()))


def parse_journal_snapshot(snapshot: dict, source_url: str) -> dict:
    lines = [line.strip() for line in snapshot.get("lines", []) if line.strip()]

    def value(label: str) -> str:
        for index, line in enumerate(lines):
            match = re.match(rf"^{re.escape(label)}\s*[：:]\s*(.*)$", line)
            if match:
                if match[1]:
                    return match[1]
                if index + 1 < len(lines) and not re.search(r"[：:]", lines[index + 1]):
                    return lines[index + 1]
        return ""

    metrics = []
    for index, line in enumerate(lines):
        match = re.search(r"(?:复合|综合)影响因子\s*[：:]\s*(.*)$", line)
        if not match:
            continue
        if not match[1] and index + 1 < len(lines) and re.fullmatch(r"\d+(?:\.\d+)?", lines[index + 1]):
            line += lines[index + 1]
        elif not re.match(r"\d+(?:\.\d+)?", match[1]):
            continue
        if index and re.fullmatch(r"[（(]?\d{4}(?:年|版|年版)?[）)]?", lines[index - 1]):
            line = lines[index - 1] + " " + line
        metrics.append(line)
    return {
        "name": snapshot.get("name", ""), "issn": value("ISSN"), "cn": value("CN"),
        "sponsor": value("主办单位"), "frequency": value("出版周期"),
        "indexing": snapshot.get("indexing", "").strip(), "metrics": metrics,
        "source_url": source_url, "queried_at": datetime.now().isoformat(timespec="seconds"),
    }


def _check_verification(page: Any, settings: ScraperSettings, events: EventSink) -> None:
    status = handle_verify(page, settings, events)
    if status in {VERIFY_CANCELLED, VERIFY_PAGE_CLOSED, VERIFY_TIMEOUT} or events.cancel_requested():
        raise JournalQueryStopped("期刊查询已停止，安全验证未完成或浏览器已关闭。")
    challenge = page.locator("#tcaptcha_transform_dy")
    if challenge.count() and challenge.first.is_visible() and challenge.first.evaluate("el => el.getBoundingClientRect().top >= 0"):
        if not events.confirm("请在浏览器中完成安全验证，然后继续。"):
            raise JournalQueryStopped("期刊查询已取消。")
        if challenge.count() and challenge.first.is_visible() and challenge.first.evaluate("el => el.getBoundingClientRect().top >= 0"):
            raise JournalQueryStopped("安全验证尚未完成，请完成验证后再查询。")


def lookup_journal(page: Any, name: str, settings: ScraperSettings, events: EventSink) -> dict:
    page.goto(JOURNAL_HOME, wait_until="domcontentloaded", timeout=settings.timeout_goto_ms)
    _check_verification(page, settings, events)
    search = page.locator('input[placeholder*="检索词"]:visible').first
    search.wait_for(state="visible", timeout=settings.timeout_selector_ms)
    search.fill(name)
    page.locator("input.researchbtn:visible").first.click(timeout=settings.timeout_selector_ms)
    _check_verification(page, settings, events)
    page.wait_for_function("() => /条结果/.test(document.body.innerText)", timeout=settings.timeout_load_ms)
    tab = page.locator("li a").filter(has_text=re.compile(r"^\s*期刊\s*$"))
    if tab.count() and tab.first.is_visible():
        tab.first.click(timeout=settings.timeout_selector_ms)
        page.wait_for_timeout(500)
    _check_verification(page, settings, events)
    candidates = page.locator('a[href*="knavi/detail"]').evaluate_all("""links => links.map(link => ({
        name: (link.innerText || '').trim().split('\\n')[0].trim(), url: link.href
    }))""")
    candidate = choose_journal(candidates, name)
    page.goto(candidate["url"], wait_until="domcontentloaded", timeout=settings.timeout_goto_ms)
    _check_verification(page, settings, events)
    page.locator("h3.titbox, h3.titbox1").first.wait_for(state="visible", timeout=settings.timeout_selector_ms)
    more = page.get_by_text("更多介绍", exact=True)
    if more.count() and more.first.is_visible():
        more.first.click(timeout=settings.timeout_selector_ms)
        page.wait_for_timeout(300)
    snapshot = page.evaluate("""() => {
        const heading = document.querySelector('h3.titbox, h3.titbox1');
        const sections = Array.from(document.querySelectorAll('h4'))
            .filter(node => /该刊被以下数据库收录/.test(node.textContent || ''));
        const parts = [];
        for (const section of sections) {
            let node = section.nextElementSibling;
            while (node && !/^H[1-6]$/.test(node.tagName) && !node.querySelector('h4')) {
                const text = (node.innerText || '').trim();
                if (text) parts.push(text);
                node = node.nextElementSibling;
            }
        }
        return {name: (heading?.innerText || '').trim().split('\\n')[0].trim(),
                lines: document.body.innerText.split('\\n'), indexing: parts.join('\\n')};
    }""")
    if _journal_key(snapshot["name"]) != _journal_key(name):
        raise ValueError("期刊详情页名称与所选论文来源不一致，请在知网核对。")
    return parse_journal_snapshot(snapshot, page.url)


def fetch_journal_info(items: list[tuple[int, Paper]], settings: ScraperSettings,
                       paths: RuntimePaths, events: EventSink) -> str:
    groups: dict[str, tuple[str, list[int]]] = {}
    skipped = 0
    for index, paper in items:
        if document_type_family(paper.document_type) not in {"", "journal"} or not paper.source.strip():
            skipped += 1
            events.emit("journal_result", indices=[index], info={"error": "此条目不含可查询的期刊来源。"}, status="无法查询期刊")
            continue
        key = _journal_key(paper.source)
        groups.setdefault(key, (paper.source, []))[1].append(index)
    succeeded = failed = 0
    if not groups or events.cancel_requested():
        return f"期刊信息：没有可查询的期刊，跳过 {skipped} 篇论文"
    with open_browser_context(settings, paths, events) as context:
        page = context.new_page()
        for position, (name, indices) in enumerate(groups.values(), 1):
            if events.cancel_requested():
                break
            events.emit("paper_operation_progress", message=f"查询期刊 {position}/{len(groups)}：{name}")
            stopped = False
            try:
                info = lookup_journal(page, name, settings, events)
                succeeded += 1
                status = "期刊信息已读取"
            except (PlaywrightError, ValueError, RuntimeError) as error:
                if events.cancel_requested():
                    break
                failed += 1
                stopped = isinstance(error, JournalQueryStopped)
                info = {"name": name, "error": str(error), "source_url": page.url,
                        "queried_at": datetime.now().isoformat(timespec="seconds")}
                status = "期刊查询失败"
            events.emit("journal_result", indices=indices, info=info, status=status)
            if stopped or page.is_closed():
                break
    return f"期刊信息：成功 {succeeded} 种，失败 {failed} 种，未查询 {len(groups) - succeeded - failed} 种；跳过 {skipped} 篇论文"
