from dataclasses import replace

import openpyxl
import pytest

from cnkibug.cnki.models import (
    Paper, append_article_details, deduplicate_papers, paper_from_record, papers_from_results, split_authors,
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


@pytest.mark.parametrize("identifier", ["doi", "detail_url"])
@pytest.mark.parametrize("types", [("期刊", "学术期刊"), ("博士", "学位论文"), ("会议论文", "会议"), ("期刊", "图书")])
def test_dedup_identifiers_take_precedence_over_type_labels(identifier, types):
    values = {identifier: "10.1234/shared" if identifier == "doi" else "https://kns.cnki.net/shared"}
    first = Paper(**values, document_type=types[0], queries=["一"])
    second = Paper(**values, document_type=types[1], queries=["二"])

    papers = deduplicate_papers([first, second])

    assert len(papers) == 1
    assert papers[0].queries == ["一", "二"]
    assert first.queries == ["一"]


def test_dedup_matching_url_does_not_override_conflicting_dois():
    first = Paper(doi="10.1234/one", detail_url="https://kns.cnki.net/shared", document_type="期刊")
    second = replace(first, doi="10.1234/two", document_type="学术期刊")
    assert len(deduplicate_papers([first, second])) == 2


def test_author_separators_preserve_commas_inside_names():
    assert split_authors("Smith, John；Doe, Jane") == ["Smith, John", "Doe, Jane"]


@pytest.mark.parametrize("authors", ["张三；李四", "张三、李四", "张三\n李四"])
@pytest.mark.parametrize("types", [("期刊", "学术期刊"), ("博士", "学位论文"), ("会议论文", "会议")])
def test_dedup_fields_normalize_author_separators_and_type_aliases(authors, types):
    first = Paper(title="题名", authors="张三; 李四", source="来源", publication_date="2026-01-02", document_type=types[0])
    second = replace(first, authors=authors, document_type=types[1])
    assert len(deduplicate_papers([first, second])) == 1


@pytest.mark.parametrize("changes", [
    {"authors": "李四；张三"},
    {"publication_date": "2026-01-03"},
    {"publication_date": "2026"},
    {"document_type": "会议"},
    {"document_type": "硕士"},
])
def test_dedup_fields_preserve_author_order_full_dates_and_type_conflicts(changes):
    first = Paper(title="题名", authors="张三; 李四", source="来源", publication_date="2026-01-02", document_type="博士")
    assert len(deduplicate_papers([first, replace(first, **changes)])) == 2


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


@pytest.mark.parametrize("document_type, ris_type", [
    ("期刊", "JOUR"), ("学术期刊", "JOUR"), ("学位论文", "THES"), ("博士论文", "THES"),
    ("会议", "CONF"), ("会议论文", "CONF"), ("图书", "BOOK"), ("图书章节", "CHAP"),
    ("报纸", "NEWS"), ("专利", "PAT"), ("标准", "STAND"), ("法律法规", "STAT"),
    ("视频", "VIDEO"), ("", "GEN"),
])
def test_ris_preserves_known_resource_types_and_original_labels(tmp_path, document_type, ris_type):
    path = tmp_path / "papers.ris"
    save_papers(path, [Paper(title="题名", authors="张三、李四", document_type=document_type)])
    text = path.read_text(encoding="utf-8-sig")
    assert f"TY  - {ris_type}\n" in text
    assert "AU  - 张三\nAU  - 李四\n" in text
    if document_type:
        assert f"N1  - 文献类型：{document_type}\n" in text


@pytest.mark.parametrize("document_type, source_line", [
    ("学术期刊", "JO  - 来源"), ("学位论文", "PB  - 来源"), ("会议", "JO  - 来源"),
    ("报纸", "T2  - 来源"), ("图书", "N1  - 来源：来源"), ("专利", "N1  - 来源：来源"),
])
def test_ris_preserves_source_without_inventing_resource_metadata(tmp_path, document_type, source_line):
    path = tmp_path / "papers.ris"
    save_papers(path, [Paper(title="题名", source="来源", document_type=document_type)])
    assert source_line + "\n" in path.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("document_type", ["年鉴", "成果", "文库", "未知类型"])
def test_ris_reports_ambiguous_types_without_replacing_existing_export(tmp_path, document_type):
    path = tmp_path / "papers.ris"
    path.write_text("previous export", encoding="utf-8")
    with pytest.raises(ValueError, match=document_type):
        save_papers(path, [Paper(title="题名", document_type=document_type)])
    assert path.read_text(encoding="utf-8") == "previous export"


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
