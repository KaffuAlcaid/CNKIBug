from cnkibug import runtime, task_state
from cnkibug.scrape_report import STATUS_SUCCESS, make_keyword_result


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
