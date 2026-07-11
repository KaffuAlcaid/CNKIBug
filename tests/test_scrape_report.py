from cnkibug.scrape_report import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_SUCCESS,
    TaskReport,
    collect_field_stats,
    make_keyword_result,
)


def test_task_report_counts_statuses_and_records():
    report = TaskReport(total_keywords=3)
    report.add(make_keyword_result("kw1", 1, 3, [["t", "a", "s", "d"]], STATUS_SUCCESS))
    report.add(make_keyword_result("kw2", 2, 3, [], STATUS_EMPTY, "知网无结果"))
    report.add(make_keyword_result("kw3", 3, 3, [], STATUS_FAILED, "结果加载超时"))

    assert report.completed_keywords == 3
    assert report.total_records == 1
    assert report.count_status(STATUS_SUCCESS) == 1
    assert report.count_status(STATUS_EMPTY) == 1
    assert report.count_status(STATUS_FAILED) == 1
    assert [item.reason for item in report.failed_items()] == ["结果加载超时"]


def test_collect_field_stats_counts_missing_fields():
    stats = collect_field_stats({
        "kw": [
            ["title", "author", "source", "date"],
            ["", "", "source", ""],
            ["title", "author"],
        ]
    })

    assert stats.total_records == 3
    assert stats.missing_title == 1
    assert stats.missing_authors == 1
    assert stats.missing_source == 1
    assert stats.missing_date == 2
    assert stats.missing_detail_url == 3
