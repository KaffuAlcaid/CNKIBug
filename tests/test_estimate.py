import pytest

from cnkibug.core.estimate import (
    estimate_active_seconds,
    estimate_progress,
    estimate_seconds,
    format_eta,
)


def test_estimate_single_keyword():
    assert estimate_active_seconds(3, 1) == (24, 36)
    assert estimate_seconds(3, 1) == (42, 61)


def test_estimate_multi_keyword():
    assert estimate_active_seconds(3, 2) == (53, 80)
    assert estimate_seconds(3, 2) == (71, 105)


def test_estimate_with_citations_uses_paired_sample_range():
    assert estimate_active_seconds(1, 1, include_citation=True) == (27, 37)
    assert estimate_seconds(1, 1, include_citation=True) == (45, 62)
    assert estimate_active_seconds(1, 2, include_citation=True) == (59, 82)
    assert estimate_seconds(1, 2, include_citation=True) == (77, 107)


def test_estimate_with_details_uses_conservative_per_record_range():
    assert estimate_active_seconds(1, 1, include_details=True) == (68, 172)
    assert estimate_seconds(1, 1, include_details=True) == (86, 197)
    assert estimate_seconds(
        1,
        1,
        include_citation=True,
        include_details=True,
    ) == (105, 222)


def test_estimate_matches_three_page_paired_samples():
    assert estimate_active_seconds(3, 1) == (24, 36)
    assert estimate_seconds(3, 1) == (42, 61)
    assert estimate_active_seconds(3, 1, include_citation=True) == (81, 111)
    assert estimate_seconds(3, 1, include_citation=True) == (99, 136)


def test_format_eta_seconds_only():
    assert format_eta(10, 18) == "约 10 秒 ~ 18 秒"


def test_format_eta_minutes_and_seconds():
    assert format_eta(84, 132) == "约 1 分 24 秒 ~ 2 分 12 秒"


def test_format_eta_compact_uses_existing_duration_formatting():
    assert format_eta(99, 136, compact=True) == "01:39～02:16"


def test_estimate_progress():
    assert estimate_progress(0, 40, 72) == 0
    assert estimate_progress(-1, 40, 72) == 0
    assert estimate_progress(20, 40, 72) == 45
    assert estimate_progress(40, 40, 72) == 90
    assert estimate_progress(56, 40, 72) == 94
    assert estimate_progress(72, 40, 72) == 99
    assert estimate_progress(100, 40, 72) == 99
    assert estimate_progress(20, 40, 72, completed=True) == 100


def test_estimate_progress_matches_current_paired_samples():
    without_citation = estimate_active_seconds(3)
    with_citation = estimate_active_seconds(3, include_citation=True)

    assert [
        estimate_progress(elapsed, *without_citation)
        for elapsed in (27, 24, 23, 25, 27)
    ] == [92, 90, 86, 90, 92]
    assert [
        estimate_progress(elapsed, *with_citation)
        for elapsed in (77, 84, 83, 82, 86)
    ] == [85, 90, 90, 90, 91]


def test_estimate_progress_keeps_legacy_page_samples_in_tail_range():
    assert estimate_progress(36, *estimate_active_seconds(4)) == 92
    assert estimate_progress(88, *estimate_active_seconds(8)) == 96
    assert estimate_progress(208, *estimate_active_seconds(20)) == 95


def test_estimate_progress_rejects_invalid_ranges():
    with pytest.raises(ValueError):
        estimate_progress(0, 0, 72)
    with pytest.raises(ValueError):
        estimate_progress(0, -1, 72)
    with pytest.raises(ValueError):
        estimate_progress(0, 40, 40)
    with pytest.raises(ValueError):
        estimate_progress(0, 40, 39)
    with pytest.raises(ValueError):
        estimate_progress(0, 0, 72, completed=True)


def test_estimate_progress_does_not_jump_at_boundaries():
    assert [estimate_progress(value, 40, 72) for value in (39.999, 40, 40.001)] == [
        89,
        90,
        90,
    ]
    assert [estimate_progress(value, 40, 72) for value in (71.999, 72, 72.001)] == [
        98,
        99,
        99,
    ]
