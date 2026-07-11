import json
import logging
from pathlib import Path

from cnkibug import runtime, task_state
from cnkibug.scrape_report import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_STOPPED,
    STATUS_SUCCESS,
    make_keyword_result,
)


def test_last_task_save_load_mark_and_delete(tmp_path):
    runtime.init_runtime(base_dir=tmp_path, configure_logging=False)

    state = task_state.make_task_state(["焊接", "增材"], 3, "multi_merge", "TS")
    task_state.save_last_task(state)

    loaded = task_state.load_last_task()
    assert loaded is not None
    assert loaded["keywords"] == ["焊接", "增材"]
    assert loaded["max_pages"] == 3
    assert loaded["save_mode"] == "multi_merge"

    result = make_keyword_result(
        "焊接",
        1,
        2,
        [["标题", "作者", "来源", "日期"]],
        STATUS_SUCCESS,
    )
    task_state.mark_keyword_done(loaded, result)
    task_state.save_last_task(loaded)

    reloaded = task_state.load_last_task()
    assert reloaded is not None
    assert task_state.completed_results(reloaded) == {
        "焊接": [["标题", "作者", "来源", "日期"]]
    }
    assert "关键词 2 个" in task_state.describe_task(reloaded)
    assert "已完成 1 个" in task_state.describe_task(reloaded)

    assert task_state.delete_last_task() is True
    assert task_state.load_last_task() is None


def test_load_last_task_returns_none_for_invalid_json(tmp_path):
    runtime.init_runtime(base_dir=tmp_path, configure_logging=False)
    path = task_state.get_last_task_path()
    assert path is not None
    path.write_text("{ broken", encoding="utf-8")

    assert task_state.load_last_task() is None


def test_load_last_task_returns_none_for_invalid_shape(tmp_path):
    runtime.init_runtime(base_dir=tmp_path, configure_logging=False)
    path = task_state.get_last_task_path()
    assert path is not None
    path.write_text('{"version": 1, "keywords": "bad"}', encoding="utf-8")

    assert task_state.load_last_task() is None


def test_load_last_task_upgrades_version_one_checkpoint(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="cnkibug.task_state")
    runtime.init_runtime(base_dir=tmp_path, configure_logging=False)
    path = task_state.get_last_task_path()
    assert path is not None
    legacy = task_state.make_task_state(["焊接"], 3, "single", "TS")
    legacy["version"] = 1
    legacy["completed"]["焊接"] = {
        "status": STATUS_FAILED,
        "reason": "旧任务",
        "records": [["标题", "", "", ""]],
    }
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    loaded = task_state.load_last_task()

    assert loaded is not None
    assert loaded["version"] == task_state.TASK_STATE_VERSION
    assert task_state.keyword_checkpoint(loaded, "焊接") == (
        0,
        [["标题", "", "", ""]],
    )
    assert "兼容升级" in caplog.text


def test_keyword_checkpoint_tracks_page_and_survives_failed_status():
    state = task_state.make_task_state(["焊接"], 3, "single", "TS")
    records = [["标题", "作者", "来源", "日期", "https://example.test/1"]]

    task_state.mark_keyword_progress(state, "焊接", 2, records)
    task_state.mark_keyword_done(
        state,
        make_keyword_result("焊接", 1, 1, records, STATUS_FAILED, "第 3 页失败"),
    )

    assert task_state.keyword_checkpoint(state, "焊接") == (2, records)
    assert state["completed"]["焊接"]["status"] == STATUS_FAILED


def test_keyword_checkpoint_restarts_when_page_has_no_records(caplog):
    state = task_state.make_task_state(["焊接"], 3, "single", "TS")
    task_state.mark_keyword_progress(state, "焊接", 2, [])

    assert task_state.keyword_checkpoint(state, "焊接") == (0, [])
    assert "页级断点没有记录" in caplog.text


def test_completed_results_only_contains_terminal_statuses(tmp_path):
    runtime.init_runtime(base_dir=tmp_path, configure_logging=False)
    state = task_state.make_task_state(["成功", "空", "失败", "中止"], 3, "multi_merge", "TS")
    for index, (keyword, status, records) in enumerate((
        ("成功", STATUS_SUCCESS, [["t1", "", "", ""]]),
        ("空", STATUS_EMPTY, []),
        ("失败", STATUS_FAILED, [["partial", "", "", ""]]),
        ("中止", STATUS_STOPPED, [["stopped", "", "", ""]]),
    ), start=1):
        task_state.mark_keyword_done(
            state,
            make_keyword_result(keyword, index, 4, records, status),
        )

    assert task_state.stored_results(state) == {
        "成功": [["t1", "", "", ""]],
        "空": [],
        "失败": [["partial", "", "", ""]],
        "中止": [["stopped", "", "", ""]],
    }
    assert task_state.completed_results(state) == {
        "成功": [["t1", "", "", ""]],
        "空": [],
    }
    assert task_state.task_is_finished(state) is False
    assert "待重试 2 个" in task_state.describe_task(state)


def test_task_is_finished_accepts_success_and_empty(tmp_path):
    runtime.init_runtime(base_dir=tmp_path, configure_logging=False)
    state = task_state.make_task_state(["成功", "空"], 1, "multi_merge", "TS")
    task_state.mark_keyword_done(
        state,
        make_keyword_result("成功", 1, 2, [["t", "", "", ""]], STATUS_SUCCESS),
    )
    task_state.mark_keyword_done(
        state,
        make_keyword_result("空", 2, 2, [], STATUS_EMPTY),
    )

    assert task_state.task_is_finished(state) is True


def test_save_last_task_logs_and_returns_none_on_write_error(monkeypatch, tmp_path, caplog):
    runtime.init_runtime(base_dir=tmp_path, configure_logging=False)
    state = task_state.make_task_state(["焊接"], 1, "single", "TS")

    def fail_write(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", fail_write)

    assert task_state.save_last_task(state) is None
    assert "last_task 保存失败" in caplog.text
