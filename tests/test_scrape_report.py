import json

from cnkibug.app import runtime
from cnkibug.cnki.models import (
    STATUS_SUCCESS,
    make_keyword_result,
)
from cnkibug.workflow.report import (
    TaskReport,
    build_task_report,
    save_task_report,
)


def test_build_and_save_machine_report_covers_unfinished_keywords(tmp_path):
    paths = runtime.init_runtime(program_dir=tmp_path, configure_logging=False).paths
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

    saved_path = save_task_report(payload, "TS", paths)

    report_path = tmp_path / "CNKIBug-data" / "status" / "cnki_task_report_TS.json"
    assert saved_path == str(report_path.resolve())
    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written["schema_version"] == 3
    assert written["keywords"][2]["keyword"] == "未开始"
