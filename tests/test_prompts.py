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
