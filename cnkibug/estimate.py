"""抓取耗时预估 —— 纯函数，无副作用，可独立单测。

标定数据（来自实测点位）：4 页 ≈ 36s、8 页 ≈ 88s、20 页 ≈ 208s。
拟合下界约 8~13 秒/页；但 ETA 取“激进保守”档（10~18 秒/页），刻意高估，
让显示时间只会比实际久、不会让用户空等到惊慌。实测三点仍落在新区间内并贴近下界。
多关键词每个词另有一次重导航固定开销（约 12s）。

0.1.8 计划：抓到知网“共找到 N 条”后，用真实结果页数替代用户填写页数，
届时只需改本文件，不影响调用方。
"""

_SEC_PER_PAGE_LOW = 10
_SEC_PER_PAGE_HIGH = 18
_REDIRECT_OVERHEAD = 12


def estimate_seconds(pages: int, keyword_count: int = 1) -> tuple[int, int]:
    """返回 (低, 高) 秒数区间。"""
    per_word_low = pages * _SEC_PER_PAGE_LOW
    per_word_high = pages * _SEC_PER_PAGE_HIGH
    if keyword_count <= 1:
        return per_word_low, per_word_high
    low = keyword_count * (_REDIRECT_OVERHEAD + per_word_low)
    high = keyword_count * (_REDIRECT_OVERHEAD + per_word_high)
    return low, high


def _fmt(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    if m > 0:
        return f"{m} 分 {s} 秒"
    return f"{s} 秒"


def format_eta(low: int, high: int) -> str:
    """把秒数区间转成人话，如 '约 1 分 4 秒 ~ 1 分 44 秒'。"""
    return f"约 {_fmt(low)} ~ {_fmt(high)}"
