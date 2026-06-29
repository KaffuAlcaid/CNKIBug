from cnkibug.exporter import _build_single_sheet_workbook, _sanitize_name


def test_sanitize_name_replaces_filename_and_sheet_illegal_chars():
    assert _sanitize_name('a/b:c*?"<>|[x]') == "a_b_c_______x_"


def test_build_single_sheet_workbook_headers_and_rows():
    wb = _build_single_sheet_workbook([["标题", "作者", "来源", "2026-01-01"]])
    ws = wb.active

    assert ws is not None
    assert ws.title == "论文标题"
    assert [cell.value for cell in ws[1]] == ["论文标题", "作者", "来源", "发表日期"]
    assert [cell.value for cell in ws[2]] == ["标题", "作者", "来源", "2026-01-01"]
