"""抓取耗时预估 —— 纯函数，无副作用，可独立单测。

标定数据（来自实测点位）：4 页 ≈ 36s、8 页 ≈ 88s、20 页 ≈ 208s，
拟合约 8 ~ 13 秒/页（随机 sleep 导致非严格线性，故报区间而非单值）。
多关键词每个词另有一次重导航固定开销（约 8s）。

0.1.8 计划：抓到知网“共找到 N 条”后，用真实结果页数替代用户填写页数，
届时只需改本文件，不影响调用方。
"""

_SEC_PER_PAGE_LOW = 8
_SEC_PER_PAGE_HIGH = 13
_REDIRECT_OVERHEAD = 8  # 每个关键词重导航固定开销（秒）


def estimate_seconds(pages: int, keyword_count: int = 1) -> tuple[int, int]:
    """返回 (低, 高) 秒数区间。"""
    per_word_low = pages * _SEC_PER_PAGE_LOW
    per_word_high = pages * _SEC_PER_PAGE_HIGH
    if keyword_count <= 1:
        return (per_word_low, per_word_high)
    low = keyword_count * (_REDIRECT_OVERHEAD + per_word_low)
    high = keyword_count * (_REDIRECT_OVERHEAD + per_word_high)
    return (low, high)


def _fmt(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    if m > 0:
        return f"{m} 分 {s} 秒"
    return f"{s} 秒"


def format_eta(low: int, high: int) -> str:
    """把秒数区间转成人话，如 '约 1 分 4 秒 ~ 1 分 44 秒'。"""
    return f"约 {_fmt(low)} ~ {_fmt(high)}"
