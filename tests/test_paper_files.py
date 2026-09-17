from dataclasses import replace

import openpyxl
import pytest

from cnkibug.cnki.models import (
    Paper, append_article_details, deduplicate_papers, paper_from_record, papers_from_results,
)
from cnkibug.core.search_query import SearchOptions
from cnkibug.fileio.papers import COLUMNS, read_papers, save_papers
from cnkibug.workflow.keyword_run import _merge_record_fields
from cnkibug.workflow.state import make_task_state


def test_metadata_preserves_legacy_record_positions():
    record = ["标题", "作者", "期刊", "2026-01-01", "https://kns.cnki.net/1", "引文", {"doi": "10.1234/test"}]
    append_article_details(record, ["材料", "焊接"], "摘要", {"funds": "基金"})
    paper = paper_from_record(record, True)
    assert record[5:8] == ["引文", "材料\n焊接", "摘要"]
    assert (paper.doi, paper.funds, paper.abstract, paper.citation) == ("10.1234/test", "基金", "摘要", "引文")
    legacy = paper_from_record(record[:8], True)
    assert legacy.abstract == "摘要" and not legacy.doi


def test_retry_merge_keeps_metadata_separate_from_legacy_details():
    old = ["标题", "作者", "来源", "日期", "url", "引文", "关键词", "摘要"]
    current = ["标题", "作者", "来源", "日期", "url", "引文", "", "", {"doi": "10.1234/new"}]
    merged = _merge_record_fields(old, current)
    assert merged[6:8] == ["关键词", "摘要"]
    assert merged[-1] == {"doi": "10.1234/new"}
    assert old == ["标题", "作者", "来源", "日期", "url", "引文", "关键词", "摘要"]


def test_dedup_joins_queries_without_mutating_checkpoint_or_conflicting_dois():
    first = Paper(title="题名", authors="张三", source="期刊", publication_date="2026", doi="https://doi.org/10.1234/A", queries=["一"])
    second = replace(first, doi="10.1234/a", abstract="摘要", queries=["二"])
    different = replace(second, doi="10.1234/b", queries=["三"])
    papers = deduplicate_papers([first, second, different])
    assert len(papers) == 2
    assert papers[0].queries == ["一", "二"]
    assert papers[0].abstract == "摘要"
    assert first.queries == ["一"] and first.abstract == ""


@pytest.mark.parametrize("extension", ["xlsx", "csv"])
def test_file_roundtrip_preserves_doi_multiline_abstract_and_queries(tmp_path, extension):
    original = Paper(title="=标题", authors="张三;李四", source="期刊", publication_date="2026-01-02", doi="10.1234/test", abstract="第一段\n第二段", queries=["一", "二"], document_type="期刊")
    path = tmp_path / f"papers.{extension}"
    save_papers(path, [original])
    assert read_papers(path) == [original]
    if extension == "xlsx":
        workbook = openpyxl.load_workbook(path)
        assert workbook.active["A2"].data_type == "s"
        assert [cell.value for cell in workbook.active[1]] == [label for _, label in COLUMNS]
        workbook.close()


def test_legacy_english_csv_and_multisheet_excel(tmp_path):
    csv_path = tmp_path / "old.csv"
    csv_path.write_text("keyword,title,authors,source,publication_date,detail_url\n查询,标题,作者,来源,2026,https://kns.cnki.net/1\n", encoding="utf-8-sig")
    papers = read_papers(csv_path)
    assert papers[0].queries == ["查询"] and papers[0].title == "标题"
    workbook = openpyxl.Workbook()
    first = workbook.active
    first.title = "查询一"
    first.append(["论文标题", "作者", "来源", "发表日期", "详情链接"])
    first.append(["标题", "作者", "来源", "2026", "https://kns.cnki.net/1"])
    second = workbook.copy_worksheet(first)
    second.title = "查询二"
    xlsx_path = tmp_path / "old.xlsx"
    workbook.save(xlsx_path)
    workbook.close()
    imported = read_papers(xlsx_path)
    assert len(imported) == 1 and imported[0].queries == ["查询一", "查询二"]


def test_ris_exports_authors_type_keywords_and_existing_pdf(tmp_path):
    pdf = tmp_path / "论文.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    paper = Paper(title="论文", authors="张三;李四", source="学校", document_type="博士", doi="10.1234/test", paper_keywords="焊接；材料", pdf_path=str(pdf))
    path = tmp_path / "papers.ris"
    save_papers(path, [paper], include_pdf=True)
    text = path.read_text(encoding="utf-8-sig")
    assert "TY  - THES" in text
    assert "AU  - 张三\nAU  - 李四" in text
    assert "DO  - 10.1234/test" in text
    assert "KW  - 焊接\nKW  - 材料" in text
    assert f"L1  - {pdf.resolve().as_uri()}" in text


def test_result_conversion_does_not_share_checkpoint_dicts():
    metadata = {"doi": "10.1234/example"}
    raw = {"查询": [["标题", "作者", "来源", "2026", "url", metadata]]}
    papers = papers_from_results(raw)
    papers[0].doi = "changed"
    papers[0].queries.append("另一个")
    assert raw["查询"][0][-1] == {"doi": "10.1234/example"}


def test_search_options_serialization_and_old_checkpoint():
    options = SearchOptions(resources=("学术期刊",), page_size=50)
    state = make_task_state(["查询"], 3, "single", "TS", search_options=options)
    assert SearchOptions.from_dict(state["search_options"]) == options
    assert SearchOptions.from_dict(None) is None
    with pytest.raises(ValueError):
        SearchOptions(page_size=100)
