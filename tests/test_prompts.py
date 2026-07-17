from pathlib import Path

from cnkibug.app import prompts


def _patch_inputs(monkeypatch, values):
    answers = iter(values)
    monkeypatch.setattr(prompts, "safe_input", lambda prompt="": next(answers))


def test_collect_single_keyword_request(monkeypatch):
    _patch_inputs(monkeypatch, ["1", "焊接", "1", "2", "", "", "1"])

    request = prompts.collect_task_request()

    assert request == prompts.TaskRequest(
        keywords=["焊接"],
        max_pages=2,
        save_mode="single",
        include_citation=False,
        include_details=False,
        detail_txt_export=False,
    )


def test_collect_batch_request_after_preview(monkeypatch):
    _patch_inputs(
        monkeypatch,
        ["2", "1", "焊接", "增材", "", "3", "1", "y", "y", "1"],
    )

    request = prompts.collect_task_request(detail_txt_export=True)

    assert request == prompts.TaskRequest(
        keywords=["焊接", "增材"],
        max_pages=1,
        save_mode="multi_csv",
        include_citation=True,
        include_details=True,
        detail_txt_export=True,
    )


def test_single_keyword_preview_can_return_to_settings(monkeypatch):
    _patch_inputs(
        monkeypatch,
        [
            "1", "旧关键词", "1", "1", "", "", "2",
            "1", "新关键词", "2", "3", "y", "y", "1",
        ],
    )

    request = prompts.collect_task_request()

    assert request == prompts.TaskRequest(
        keywords=["新关键词"],
        max_pages=3,
        save_mode="single_csv",
        include_citation=True,
        include_details=True,
        detail_txt_export=False,
    )


def test_page_count_explains_results_per_page(monkeypatch, capsys):
    _patch_inputs(monkeypatch, ["5"])

    assert prompts._ask_page_count(["焊接"]) == 5

    output = capsys.readouterr().out
    assert "每页通常约 20 条结果" in output
    assert "约 100 条可填写 5 页" in output


def test_task_preview_warns_when_eta_upper_bound_exceeds_ten_minutes(
    monkeypatch,
    capsys,
):
    _patch_inputs(monkeypatch, ["1"])

    action = prompts._preview_task(
        ["焊接"],
        48,
        "single",
        False,
        False,
        False,
        None,
        "手动输入",
        None,
        402,
        601,
    )

    assert action == "start"
    assert "预计耗时上限已超过 10 分钟" in capsys.readouterr().out


def test_task_preview_does_not_warn_at_ten_minutes(monkeypatch, capsys):
    _patch_inputs(monkeypatch, ["1"])

    prompts._preview_task(
        ["焊接"],
        47,
        "single",
        False,
        False,
        False,
        None,
        "手动输入",
        None,
        394,
        600,
    )

    assert "预计耗时上限已超过 10 分钟" not in capsys.readouterr().out


def test_detail_preview_shows_txt_config_path(monkeypatch, capsys):
    _patch_inputs(monkeypatch, ["1"])
    config_path = Path("C:/CNKIBug/config.json")

    prompts._preview_task(
        ["焊接"],
        1,
        "single",
        False,
        True,
        False,
        config_path,
        "手动输入",
        None,
        86,
        197,
    )

    output = capsys.readouterr().out
    assert "关键词 TXT 导出：关闭" in output
    assert str(config_path) in output
    assert "detail_txt_export" in output
    assert "改为" in output
    assert "true" in output
