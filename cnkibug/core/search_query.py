from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any


SEARCH_FIELDS = {
    "SU": "主题", "TKA": "篇关摘", "KY": "关键词", "TI": "篇名",
    "FT": "全文", "AU": "作者", "FI": "第一作者", "RP": "通讯作者",
    "AF": "作者单位", "FU": "基金", "AB": "摘要", "CO": "小标题",
    "RF": "参考文献", "CLC": "分类号", "LY": "文献来源", "DOI": "DOI",
}
MATCH_MODES = {"=": "精确", "%": "模糊"}
PUBLICATION_FILTERS = {
    "oa": ("OA出版", "OA=1"),
    "first_release": ("网络首发", "WLSF=2 || NOT!WXZT=2"),
    "enhanced": ("增强出版", "NPM=ZQ"),
    "funded": ("基金文献", "JJWX=Y"),
}


@dataclass(frozen=True)
class SearchCondition:
    field: str = "SU"
    text: str = ""
    operator: str = "AND"
    match: str = "="


@dataclass(frozen=True)
class AdvancedQuery:
    conditions: tuple[SearchCondition, ...]
    date_from: str = ""
    date_to: str = ""
    bilingual: bool = True
    synonym: bool = False
    publications: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 1 <= len(self.conditions) <= 10:
            raise ValueError("高级检索需要 1 至 10 条有效条件。")
        for condition in self.conditions:
            if condition.field not in SEARCH_FIELDS:
                raise ValueError("检索字段无效。")
            if not isinstance(condition.text, str) or not condition.text.strip():
                raise ValueError("检索词不能为空。")
            if len(condition.text.encode("utf-16-le")) // 2 > 120:
                raise ValueError("每条检索条件不能超过 120 个字符。")
            if condition.operator not in {"AND", "OR", "NOT"}:
                raise ValueError("条件关系无效。")
            if condition.match not in MATCH_MODES or (condition.field == "SU" and condition.match != "="):
                raise ValueError("检索字段与匹配方式不兼容。")
        for value in (self.date_from, self.date_to):
            if not isinstance(value, str):
                raise ValueError("日期格式应为 YYYY-MM-DD。")
            if value:
                try:
                    parsed = date.fromisoformat(value)
                except ValueError as error:
                    raise ValueError("日期格式应为 YYYY-MM-DD。") from error
                if parsed.isoformat() != value:
                    raise ValueError("日期格式应为 YYYY-MM-DD。")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("开始日期不能晚于结束日期。")
        if not isinstance(self.bilingual, bool) or not isinstance(self.synonym, bool):
            raise ValueError("检索扩展设置无效。")
        if self.bilingual and self.synonym:
            raise ValueError("中英文扩展与同义词扩展只能选择一项。")
        if any(value not in PUBLICATION_FILTERS for value in self.publications):
            raise ValueError("出版筛选项无效。")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Any) -> AdvancedQuery:
        if not isinstance(raw, dict):
            raise ValueError("高级检索条件格式无效。")
        try:
            values = dict(raw)
            values["conditions"] = tuple(SearchCondition(**item) for item in raw["conditions"])
            values["publications"] = tuple(raw.get("publications", ()))
            return cls(**values)
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("高级检索条件格式无效。") from error

    def summary(self) -> str:
        parts = []
        for index, condition in enumerate(self.conditions):
            prefix = f"{condition.operator} " if index else ""
            parts.append(f"{prefix}{SEARCH_FIELDS[condition.field]}({MATCH_MODES[condition.match]})：{condition.text}")
        if self.date_from or self.date_to:
            parts.append(f"发表时间：{self.date_from or '不限'} 至 {self.date_to or '不限'}")
        parts.extend(PUBLICATION_FILTERS[key][0] for key in self.publications)
        parts.append(f"中英文扩展：{'开' if self.bilingual else '关'}")
        parts.append(f"同义词扩展：{'开' if self.synonym else '关'}")
        return "；".join(parts)


def load_advanced_queries(raw: Any, keywords: list[str]) -> dict[str, AdvancedQuery]:
    if not isinstance(raw, dict) or any(key not in keywords for key in raw):
        raise ValueError("高级检索与任务列表不一致。")
    return {key: AdvancedQuery.from_dict(value) for key, value in raw.items()}
