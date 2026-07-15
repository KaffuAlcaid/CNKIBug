from cnkibug.estimate import estimate_seconds, format_eta


def test_estimate_single_keyword():
    assert estimate_seconds(3, 1) == (30, 54)


def test_estimate_multi_keyword():
    assert estimate_seconds(3, 2) == (84, 132)


def test_estimate_with_citations_uses_conservative_per_record_range():
    assert estimate_seconds(1, 1, include_citation=True) == (50, 118)
    assert estimate_seconds(1, 2, include_citation=True) == (124, 260)


def test_format_eta_seconds_only():
    assert format_eta(10, 18) == "约 10 秒 ~ 18 秒"


def test_format_eta_minutes_and_seconds():
    assert format_eta(84, 132) == "约 1 分 24 秒 ~ 2 分 12 秒"
