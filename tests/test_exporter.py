import csv
import os

import openpyxl
import pytest

from cnkibug.fileio import exporter
from cnkibug.fileio.exporter import (
    _build_single_sheet_workbook,
    _try_save_workbook,
    save_all,
)

BASE_HEADERS = ["论文标题", "作者", "来源", "发表日期", "文献类型", "DOI", "被引次数", "下载次数"]
DETAIL_HEADERS = ["论文关键词", "摘要", "作者单位", "基金", "分类号", "卷", "期", "页码"]


def test_build_workbook_inserts_details_before_citation_and_detail_url():
    detail_url = "https://kns.cnki.net/detail/1"
    citation = "[1] 示例引文"
    wb = _build_single_sheet_workbook(
        [[
            "标题",
            "作者",
            "来源",
            "2026-01-01",
            detail_url,
            citation,
            "铝合金\n晶粒组织",
            "完整摘要\x00内容",
        ]],
        include_citation=True,
        include_details=True,
    )
    ws = wb.active

    assert [cell.value for cell in ws[1]] == BASE_HEADERS + DETAIL_HEADERS + ["引用格式", "详情链接", "命中检索项"]
    assert [cell.value for cell in ws[2]] == [
        "标题",
        "作者",
        "来源",
        "2026-01-01",
        "", "", "", "",
        "铝合金；晶粒组织",
        "完整摘要内容",
        "", "", "", "", "", "",
        citation,
        detail_url,
        "",
    ]
    assert ws["R2"].hyperlink.target == detail_url


# ============ helpers ============
def _patch_desktop(monkeypatch, tmp_path):
    """把导出目录重定向到临时目录，避免污染真实桌面。"""
    monkeypatch.setattr(exporter, "get_real_desktop_path", lambda: str(tmp_path))


def _load(path):
    return openpyxl.load_workbook(path)


# ============ save_all: single ============
def test_save_all_single_writes_file(monkeypatch, tmp_path):
    _patch_desktop(monkeypatch, tmp_path)
    data = [["t1", "a1", "s1", "2026-01-01"], ["t2", "a2", "s2", "2026-02-02"]]
    result = save_all("single", ["焊接"], {"焊接": data}, "TS")

    files = list(tmp_path.glob("cnki_titles_焊接_TS.xlsx"))
    assert len(files) == 1
    assert result.attempted == 1
    assert result.failed == 0
    assert result.saved_paths == [str(files[0].resolve())]
    ws = _load(files[0]).active
    assert [c.value for c in ws[1]] == BASE_HEADERS + ["详情链接", "命中检索项"]
    assert ws.max_row == 3  # 表头 + 2 行数据


def test_save_all_single_no_data_skips_file(monkeypatch, tmp_path):
    _patch_desktop(monkeypatch, tmp_path)
    result = save_all("single", ["焊接"], {"焊接": []}, "TS")
    assert result.attempted == 0
    assert result.failed == 0
    assert result.saved_paths == []
    assert list(tmp_path.glob("*.xlsx")) == []


def test_save_all_single_csv_writes_keyword_column(monkeypatch, tmp_path):
    _patch_desktop(monkeypatch, tmp_path)
    data = [["标题", "作者", "来源", "2026-01-01", "https://example.test/1"]]

    result = save_all("single_csv", ["焊接"], {"焊接": data}, "TS")

    path = tmp_path / "cnki_titles_焊接_TS.csv"
    assert result.saved_paths == [str(path.resolve())]
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    assert rows == [
        BASE_HEADERS + ["详情链接", "命中检索项"],
        ["标题", "作者", "来源", "2026-01-01", "", "", "", "", "https://example.test/1", "焊接"],
    ]


def test_save_all_single_csv_inserts_citation_before_detail_url(monkeypatch, tmp_path):
    _patch_desktop(monkeypatch, tmp_path)
    data = [[
        "标题",
        "作者",
        "来源",
        "2026-01-01",
        "https://example.test/1",
        "[1] 示例引文",
    ]]

    save_all(
        "single_csv",
        ["焊接"],
        {"焊接": data},
        "TS",
        include_citation=True,
    )

    path = tmp_path / "cnki_titles_焊接_TS.csv"
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    assert rows == [
        BASE_HEADERS + ["引用格式", "详情链接", "命中检索项"],
        [
            "标题",
            "作者",
            "来源",
            "2026-01-01",
            "", "", "", "",
            "[1] 示例引文",
            "https://example.test/1",
            "焊接",
        ],
    ]


def test_save_all_single_csv_writes_details_before_citation(monkeypatch, tmp_path):
    _patch_desktop(monkeypatch, tmp_path)
    data = [[
        "标题",
        "作者",
        "来源",
        "2026-01-01",
        "https://example.test/1",
        "[1] 示例引文",
        "关键词一\n关键词二",
        "完整摘要",
    ]]

    save_all(
        "single_csv",
        ["焊接"],
        {"焊接": data},
        "TS",
        include_citation=True,
        include_details=True,
    )

    path = tmp_path / "cnki_titles_焊接_TS.csv"
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    assert rows[0] == BASE_HEADERS + DETAIL_HEADERS + ["引用格式", "详情链接", "命中检索项"]
    assert rows[1][8:] == [
        "关键词一；关键词二",
        "完整摘要",
        "", "", "", "", "", "",
        "[1] 示例引文",
        "https://example.test/1",
        "焊接",
    ]


def test_keyword_txt_preserves_duplicates_and_record_order(monkeypatch, tmp_path):
    _patch_desktop(monkeypatch, tmp_path)
    all_results = {
        "检索词一": [["论文一", "", "", "", "url1", "关键词甲\n关键词乙", "摘要一"]],
        "检索词二": [["论文二", "", "", "", "url2", "关键词甲", "摘要二"]],
    }

    result = save_all(
        "multi_csv",
        list(all_results),
        all_results,
        "TS",
        include_details=True,
        detail_txt_export=True,
    )

    path = tmp_path / "cnki_paper_keywords_TS.txt"
    assert result.keyword_txt_path == str(path.resolve())
    assert path.read_text(encoding="utf-8-sig").splitlines() == [
        "关键词甲",
        "关键词乙",
        "关键词甲",
    ]


def test_keyword_txt_is_not_created_when_all_keywords_are_empty(monkeypatch, tmp_path):
    _patch_desktop(monkeypatch, tmp_path)

    result = save_all(
        "single",
        ["焊接"],
        {"焊接": [["论文", "", "", "", "url", "", "摘要"]]},
        "TS",
        include_details=True,
        detail_txt_export=True,
    )

    assert result.keyword_txt_path is None
    assert not (tmp_path / "cnki_paper_keywords_TS.txt").exists()


def test_keyword_txt_failure_does_not_mark_main_export_failed(monkeypatch, tmp_path):
    _patch_desktop(monkeypatch, tmp_path)

    def fail_txt_write(filepath, lines):
        raise PermissionError("locked")

    monkeypatch.setattr(exporter, "_write_keyword_txt", fail_txt_write)

    result = save_all(
        "single",
        ["焊接"],
        {"焊接": [["论文", "", "", "", "url", "关键词", "摘要"]]},
        "TS",
        include_details=True,
        detail_txt_export=True,
    )

    assert result.saved_paths == [str((tmp_path / "cnki_titles_焊接_TS.xlsx").resolve())]
    assert result.failed == 0
    assert result.keyword_txt_failed is True


# ============ multi_split：每词一文件 ============
def test_save_all_multi_split_one_file_per_keyword(monkeypatch, tmp_path):
    _patch_desktop(monkeypatch, tmp_path)
    all_results = {
        "焊接": [["t", "a", "s", "d"]],
        "增材": [["t2", "a2", "s2", "d2"], ["t3", "a3", "s3", "d3"]],
    }
    save_all("multi_split", list(all_results), all_results, "TS")

    assert (tmp_path / "cnki_titles_焊接_TS.xlsx").exists()
    f2 = tmp_path / "cnki_titles_增材_TS.xlsx"
    assert f2.exists()
    assert _load(f2).active.max_row == 3


def test_save_all_multi_split_avoids_sanitized_name_collision(monkeypatch, tmp_path, caplog):
    _patch_desktop(monkeypatch, tmp_path)
    all_results = {
        "AI/ML": [["first", "a", "s", "d"]],
        "AI:ML": [["second", "a", "s", "d"]],
    }

    result = save_all("multi_split", list(all_results), all_results, "TS")

    first = tmp_path / "cnki_titles_AI_ML_TS.xlsx"
    second = tmp_path / "cnki_titles_AI_ML_2_TS.xlsx"
    assert first.exists()
    assert second.exists()
    assert _load(first).active["A2"].value == "first"
    assert _load(second).active["A2"].value == "second"
    assert len(result.saved_paths) == 2
    assert "分文件保存名冲突" in caplog.text


def test_save_all_multi_split_skips_empty_keyword(monkeypatch, tmp_path):
    _patch_desktop(monkeypatch, tmp_path)
    all_results = {"有": [["t", "a", "s", "d"]], "无": []}
    save_all("multi_split", ["有", "无"], all_results, "TS")

    assert (tmp_path / "cnki_titles_有_TS.xlsx").exists()
    assert not (tmp_path / "cnki_titles_无_TS.xlsx").exists()


# ============ multi_merge：单文件多 Sheet ============
def test_save_all_multi_merge_one_file_multi_sheet(monkeypatch, tmp_path):
    _patch_desktop(monkeypatch, tmp_path)
    all_results = {
        "焊接": [["t", "a", "s", "d"]],
        "增材": [["t2", "a2", "s2", "d2"]],
    }
    save_all("multi_merge", list(all_results), all_results, "TS")

    files = list(tmp_path.glob("cnki_titles_多词汇总_TS.xlsx"))
    assert len(files) == 1
    wb = _load(files[0])
    assert wb.sheetnames == ["焊接", "增材"]
    assert [c.value for c in wb["焊接"][1]] == BASE_HEADERS + ["详情链接", "命中检索项"]


def test_multi_merge_sheet_name_truncated_and_deduped(monkeypatch, tmp_path):
    """Sheet 名超 31 字截断 + 截断后撞名加后缀去重（Excel 硬上限）。"""
    _patch_desktop(monkeypatch, tmp_path)
    k1 = "X" * 35
    k2 = "X" * 31 + "YYYY"  # 前 31 字符与 k1 相同 → 截断后撞名
    all_results = {k1: [["t", "a", "s", "d"]], k2: [["t2", "a2", "s2", "d2"]]}
    save_all("multi_merge", [k1, k2], all_results, "TS")

    wb = _load(tmp_path / "cnki_titles_多词汇总_TS.xlsx")
    names = wb.sheetnames
    assert len(names) == 2
    assert all(len(n) <= 31 for n in names)  # 不超 Excel 31 字上限
    assert len(set(names)) == 2              # 去重成功，未互相覆盖
    assert names[0] == "X" * 31


def test_save_all_multi_csv_writes_flat_utf8_file(monkeypatch, tmp_path):
    _patch_desktop(monkeypatch, tmp_path)
    all_results = {
        "焊接": [["标题一", "作者甲", "来源甲", "2026-01-01", "https://example.test/1"]],
        "增材": [["标题,二", "作者乙", "来源乙", "", ""]],
    }

    result = save_all("multi_csv", list(all_results), all_results, "TS")

    path = tmp_path / "cnki_titles_多词汇总_TS.csv"
    assert result.saved_paths == [str(path.resolve())]
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    assert rows == [
        BASE_HEADERS + ["详情链接", "命中检索项"],
        ["标题一", "作者甲", "来源甲", "2026-01-01", "", "", "", "", "https://example.test/1", "焊接"],
        ["标题,二", "作者乙", "来源乙", "", "", "", "", "", "", "增材"],
    ]


def test_workbook_write_failure_preserves_target_and_cleans_temporary_file(
    monkeypatch,
    tmp_path,
):
    wb = _build_single_sheet_workbook([["new", "", "", ""]])
    target = tmp_path / "out.xlsx"
    target.write_bytes(b"existing-valid-content")
    temporary_paths = []

    def fail_save(path):
        temporary_paths.append(str(path))
        with open(path, "wb") as file:
            file.write(b"partial-content")
        raise PermissionError("simulated write failure")

    monkeypatch.setattr(wb, "save", fail_save)

    assert _try_save_workbook(wb, str(target)) is None
    assert target.read_bytes() == b"existing-valid-content"
    assert len(temporary_paths) == 1
    assert os.path.dirname(os.path.abspath(temporary_paths[0])) == str(tmp_path)
    assert not os.path.exists(temporary_paths[0])


def test_workbook_replace_failure_preserves_target_and_cleans_temporary_file(
    monkeypatch,
    tmp_path,
):
    wb = _build_single_sheet_workbook([["new", "", "", ""]])
    target = tmp_path / "out.xlsx"
    target.write_bytes(b"existing-valid-content")
    temporary_paths = []

    def fail_replace(source, destination):
        temporary_paths.append(source)
        raise PermissionError("simulated replace failure")

    monkeypatch.setattr(exporter.os, "replace", fail_replace)

    assert _try_save_workbook(wb, str(target)) is None
    assert target.read_bytes() == b"existing-valid-content"
    assert len(temporary_paths) == 1
    assert os.path.dirname(os.path.abspath(temporary_paths[0])) == str(tmp_path)
    assert not os.path.exists(temporary_paths[0])


def test_csv_write_failure_preserves_target_and_cleans_temporary_file(monkeypatch, tmp_path):
    target = tmp_path / "out.csv"
    target.write_text("existing-valid-content", encoding="utf-8")
    temporary_paths = []

    def fail_write(filepath, all_results, include_citation=False, include_details=False):
        temporary_paths.append(filepath)
        with open(filepath, "w", encoding="utf-8") as file:
            file.write("partial-content")
        raise PermissionError("simulated write failure")

    monkeypatch.setattr(exporter, "_write_multi_csv", fail_write)

    saved = exporter._try_save_csv(
        str(target),
        {"焊接": [["标题", "", "", ""]]},
    )

    assert saved is None
    assert target.read_text(encoding="utf-8") == "existing-valid-content"
    assert len(temporary_paths) == 1
    assert os.path.dirname(os.path.abspath(temporary_paths[0])) == str(tmp_path)
    assert not os.path.exists(temporary_paths[0])


@pytest.mark.parametrize("use_explicit_directory", [True, False])
def test_unavailable_output_directory_raises_without_cwd_fallback(
    monkeypatch,
    tmp_path,
    use_explicit_directory,
):
    working_dir = tmp_path / "working"
    working_dir.mkdir()
    unavailable_dir = tmp_path / "unavailable"
    monkeypatch.chdir(working_dir)
    if not use_explicit_directory:
        monkeypatch.setattr(exporter, "get_real_desktop_path", lambda: str(unavailable_dir))

    def fail_makedirs(path, exist_ok=False):
        raise PermissionError("simulated unavailable directory")

    monkeypatch.setattr(exporter.os, "makedirs", fail_makedirs)

    output_dir = unavailable_dir if use_explicit_directory else None
    with pytest.raises(OSError, match="无法使用输出目录") as error:
        exporter._get_output_path("out.xlsx", output_dir)

    assert str(unavailable_dir.resolve()) in str(error.value)
    assert list(working_dir.iterdir()) == []


def test_save_all_reports_failed_save(monkeypatch, tmp_path):
    _patch_desktop(monkeypatch, tmp_path)
    monkeypatch.setattr(exporter, "_try_save_workbook", lambda wb, filepath, **kwargs: None)

    result = save_all(
        "single",
        ["焊接"],
        {"焊接": [["t", "a", "s", "d"]]},
        "TS",
    )

    assert result.attempted == 1
    assert result.failed == 1
    assert result.saved_paths == []
    assert list(tmp_path.glob("*.xlsx")) == []


def test_save_all_uses_explicit_output_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(
        exporter,
        "get_real_desktop_path",
        lambda: (_ for _ in ()).throw(AssertionError("desktop should not be used")),
    )
    output_dir = tmp_path / "chosen"

    result = save_all(
        "single",
        ["焊接"],
        {"焊接": [["标题", "", "", ""]]},
        "TS",
        output_dir=output_dir,
    )

    expected = output_dir / "cnki_titles_焊接_TS.xlsx"
    assert result.saved_paths == [str(expected.resolve())]
    assert expected.exists()
