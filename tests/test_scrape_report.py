import json

from cnkibug import runtime
from cnkibug.scrape_report import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_SUCCESS,
    TaskReport,
    build_task_report,
    collect_citation_stats,
    collect_field_stats,
    make_keyword_result,
    save_task_report,
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


def test_collect_citation_stats_counts_empty_values_as_failures():
    stats = collect_citation_stats([
        ["标题", "", "", "", "url", "[1] 引文"],
        ["标题2", "", "", "", "url2", ""],
        ["旧记录", "", "", "", "url3"],
    ])

    assert stats == {"success": 1, "failed": 2, "empty": 2}


def test_build_and_save_machine_report_covers_unfinished_keywords(tmp_path):
    runtime.init_runtime(base_dir=tmp_path, configure_logging=False)
    report = TaskReport(total_keywords=3, stopped=True)
    report.add(make_keyword_result(
        "成功",
        1,
        3,
        [["标题", "", "来源", "2026-01-01", ""]],
        STATUS_SUCCESS,
    ))
    task_state = {
        "created_at": "2026-07-13T10:00:00",
        "completed": {
            "执行中": {
                "status": "in_progress",
                "reason": "",
                "records": [["部分标题", "作者", "", "", ""]],
            },
        },
    }
    all_results = {
        "成功": [["标题", "", "来源", "2026-01-01", ""]],
        "执行中": [["部分标题", "作者", "", "", ""]],
    }

    payload = build_task_report(
        report,
        all_results,
        task_state,
        ["成功", "执行中", "未开始"],
        2,
        "multi_csv",
        "TS",
        [str(tmp_path / "results.csv")],
        False,
    )

    assert payload["request"]["theoretical_max_pages"] == 6
    assert payload["execution"]["status_counts"] == {
        "success": 1,
        "empty": 0,
        "failed": 0,
        "stopped": 1,
        "not_started": 1,
    }
    assert [item["status"] for item in payload["keywords"]] == [
        "success",
        "stopped",
        "not_started",
    ]
    assert payload["keywords"][0]["missing_fields"]["authors"] == 1
    assert all("records" not in item for item in payload["keywords"])

    saved_path = save_task_report(payload, "TS")

    report_path = tmp_path / "CNKIBug" / "status" / "cnki_task_report_TS.json"
    assert saved_path == str(report_path.resolve())
    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written["schema_version"] == 2
    assert written["keywords"][2]["keyword"] == "未开始"
