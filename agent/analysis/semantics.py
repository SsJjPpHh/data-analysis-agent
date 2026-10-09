"""Shared deterministic semantic typing for local dataset profiles."""

from __future__ import annotations

import re


_IDENTIFIER_NAME = re.compile(
    r"(^|_)(id|uuid|identifier|key)($|_)|编号|编码$|样本号|病例号",
    re.IGNORECASE,
)
_DATETIME_NAME = re.compile(r"date|time|year|month|week|日期|时间|时期|波次", re.IGNORECASE)
_CATEGORICAL_NAME = re.compile(
    r"group|arm|treatment|intervention|status|event|class|category|type|sex|gender|site|center|"
    r"race|ethnicity|marriage|marital|education|(^|_)edu($|_)|medication|smok|drink|alcohol|"
    r"exercise|activity|hypertension|diabetes|(^|_)cvd($|_)|depression|occupation|insurance|"
    r"stage|grade|binary|indicator|(^|_)flag($|_)|"
    r"分组|组别|治疗|干预|状态|事件|类别|类型|性别|中心|地区|种族|婚姻|教育|用药|"
    r"吸烟|饮酒|运动|高血压|糖尿病|心血管|抑郁|职业|保险|分期|分级",
    re.IGNORECASE,
)
_STRUCTURAL_CATEGORICAL_NAME = re.compile(
    r"(^|_)(group|strata|category|class|status|stage|grade|band|bucket|bin|quartile|"
    r"quintile|tertile)($|_)|分组|分层|类别|分级|分期|区间",
    re.IGNORECASE,
)
_CONTINUOUS_NUMERIC_NAME = re.compile(
    r"(^|_)(age|height|weight|bmi|score|index|count|number|total|mean|median|rate|ratio|"
    r"amount|dose|duration|day|days|hour|hours|visit|visits|income|cost|pressure|bp|"
    r"temperature|cholesterol|glucose|creatinine|hdl|ldl|triglyceride|value|measure)($|_)|"
    r"年龄|身高|体重|评分|指数|计数|次数|总数|均值|中位数|比率|剂量|时长|天数|小时|"
    r"收入|费用|血压|体温|胆固醇|血糖|肌酐|测量值",
    re.IGNORECASE,
)


def infer_semantic_type(
    name: str,
    type_name: str,
    unique_count: int | None,
    rows: int | None,
) -> str:
    """Infer one stable local semantic type without reading raw cell values."""
    normalized_name = re.sub(
        r"[^a-z0-9\u4e00-\u9fff]+",
        "_",
        str(name or "").casefold(),
    ).strip("_")
    normalized_type = str(type_name or "").casefold()
    if _IDENTIFIER_NAME.search(normalized_name):
        return "identifier"
    if re.search(r"date|time|posix|difftime", normalized_type):
        return "datetime"
    # A numeric column whose name merely mentions 时间 (手术时间, 住院时间)
    # is a duration, not a datetime: storage type wins over the name, and
    # numeric-only template inputs keep these columns as candidates.
    if _DATETIME_NAME.search(normalized_name) and not re.search(
        r"integer|numeric|double|float|decimal", normalized_type
    ):
        return "datetime"
    if re.search(r"integer|numeric|double|float|decimal", normalized_type):
        if _STRUCTURAL_CATEGORICAL_NAME.search(normalized_name):
            return "categorical"
        continuous_name = bool(_CONTINUOUS_NUMERIC_NAME.search(normalized_name))
        if (
            unique_count is not None
            and 2 <= unique_count <= 20
            and _CATEGORICAL_NAME.search(normalized_name)
            and not continuous_name
        ):
            return "categorical"
        return "numeric"
    if re.search(r"logical|factor|ordered|category", normalized_type):
        return "categorical"
    if re.search(r"character|string|text", normalized_type):
        if rows and unique_count is not None and unique_count >= max(20, rows * 0.5):
            return "text"
        return "categorical"
    return "categorical" if unique_count is not None and unique_count <= 20 else "text"
