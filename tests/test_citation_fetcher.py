import logging

import pytest
from playwright.sync_api import sync_playwright

from cnkibug.cnki.citation import fetch_gbt_citation


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        yield page
        browser.close()


def _set_page_content(page, quote_rows: str) -> None:
    page.set_content(f"""
        <table><tbody><tr id="result-row"><td>
          <a class="icon-quote disabled" title="引用" href="javascript:void(0)">
            <i style="display:block;width:20px;height:20px"></i>
          </a>
        </td></tr></tbody></table>
        <script>
          document.querySelector('.icon-quote > i').addEventListener('click', () => {{
            document.body.insertAdjacentHTML('beforeend', `
              <div class="layui-layer quote-pop">
                <div class="layui-layer-content"><table><tbody>{quote_rows}</tbody></table></div>
                <a class="layui-layer-close" href="javascript:void(0)">关闭</a>
              </div>
            `);
            document.querySelector('.quote-pop .layui-layer-close').addEventListener(
              'click', event => event.currentTarget.closest('.quote-pop').remove()
            );
          }});
        </script>
    """)


def test_fetch_gbt_citation_clicks_inner_icon_and_closes_popup(page):
    _set_page_content(page, """
        <tr><td class="quote-l">GB/T 7714-2025 格式引文</td>
        <td class="quote-r">[1] 示例引文[J]. 测试期刊,2026.</td></tr>
        <tr><td class="quote-l">APA格式引文</td><td class="quote-r">APA</td></tr>
    """)

    citation = fetch_gbt_citation(
        page,
        page.query_selector("#result-row"),
        log_ref="page=1 row=1",
        timeout_ms=500,
    )

    assert citation == "[1] 示例引文[J]. 测试期刊,2026."
    assert page.locator(".quote-pop").count() == 0


def test_fetch_gbt_citation_returns_empty_when_gbt_row_is_missing(page, caplog):
    caplog.set_level(logging.WARNING, logger="cnkibug.citation_fetcher")
    _set_page_content(
        page,
        '<tr><td class="quote-l">APA格式引文</td><td class="quote-r">APA</td></tr>',
    )

    citation = fetch_gbt_citation(
        page,
        page.query_selector("#result-row"),
        log_ref="page=1 row=1",
        timeout_ms=500,
    )

    assert citation == ""
    assert "GB/T 引文抓取失败" in caplog.text
    assert page.locator(".quote-pop").count() == 0


def test_fetch_gbt_citation_returns_empty_when_button_is_missing(page, caplog):
    caplog.set_level(logging.WARNING, logger="cnkibug.citation_fetcher")
    page.set_content('<table><tbody><tr id="result-row"><td></td></tr></tbody></table>')

    citation = fetch_gbt_citation(
        page,
        page.query_selector("#result-row"),
        log_ref="page=1 row=1",
        timeout_ms=50,
    )

    assert citation == ""
    assert "引用按钮不存在" in caplog.text
