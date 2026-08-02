"""变量检测（纯规则，不调 LLM）—— 识别历史中可替换的值。

移植自 browser-use ``variable_detector.py`` 并精简。仅检查动作参数的 ``text``/``query``
字段（即 ``input_text`` 填的值、``search``/``extract`` 的查询）。两条策略：元素属性优先，
值模式正则兜底。设计详见 ``docs/rerun_history/05-变量检测与替换.md``。
"""

from __future__ import annotations

import re

from tree_walker.agent.views import AgentHistoryList, DetectedVariable, ManualVariableBinding

# 仅这些字段是「用户填入的完整值」，可安全作为变量替换目标。
_FIELDS: tuple[str, ...] = ("text", "query")

# HTML5 type 属性 → (变量名, 格式)
_TYPE_MAP: dict[str, tuple[str, str]] = {
    "email": ("email", "email"),
    "tel": ("phone", "phone"),
    "date": ("date", "date"),
    "number": ("number", "number"),
    "url": ("url", "url"),
}


def detect_variables_in_history(
    history: AgentHistoryList,
) -> dict[str, DetectedVariable]:
    """遍历历史，检测可替换变量。同一原始值只检测一次。"""
    detected: dict[str, DetectedVariable] = {}
    seen_values: set[str] = set()
    for item in history.history:
        actions = item.model_output.get("actions") or [item.model_output.get("action", {})]
        interacted = item.interacted_element or []
        for i, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            params = action.get("params") if isinstance(action.get("params"), dict) else {}
            elem = interacted[i] if i < len(interacted) else None
            for field in _FIELDS:
                value = params.get(field)
                if isinstance(value, str) and value and value not in seen_values:
                    dv = _detect_variable(value, elem)
                    if dv is not None:
                        seen_values.add(value)
                        dv.name = _ensure_unique_name(dv.name, detected)
                        detected[dv.name] = dv
    return detected


def _detect_variable(value: str, elem: dict | None) -> DetectedVariable | None:
    attrs = (elem or {}).get("attributes") or {}
    name, fmt = _detect_from_attributes(attrs)  # 策略 1：元素属性（最可靠）
    if name is None:
        name, fmt = _detect_from_value_pattern(value)  # 策略 2：值模式兜底
    if name is None:
        return None
    return DetectedVariable(name=name, original_value=value, format=fmt)


def _detect_from_attributes(attrs: dict[str, str]) -> tuple[str | None, str | None]:
    """基于元素 HTML 属性推断变量名。返回 (name, format)。"""
    t = (attrs.get("type") or "").lower()
    if t in _TYPE_MAP:
        name, fmt = _TYPE_MAP[t]
        return name, fmt

    combined = " ".join(
        [
            attrs.get("id", "") or "",
            attrs.get("name", "") or "",
            attrs.get("placeholder", "") or "",
            attrs.get("aria-label", "") or "",
        ]
    ).lower()
    if not combined.strip():
        return None, None

    if "email" in combined or "e-mail" in combined:
        return "email", "email"
    if any(k in combined for k in ("phone", "tel", "mobile", "cell")):
        return "phone", "phone"
    if "first" in combined and "name" in combined:
        return "first_name", None
    if "last" in combined and "name" in combined:
        return "last_name", None
    if "full" in combined and "name" in combined:
        return "full_name", None
    if "date" in combined or "dob" in combined or "birth" in combined:
        return "date", "date"
    if any(k in combined for k in ("address", "street", "addr")):
        if "billing" in combined:
            return "billing_address", None
        if "shipping" in combined:
            return "shipping_address", None
        return "address", None
    if "city" in combined:
        return "city", None
    if "state" in combined or "province" in combined:
        return "state", None
    if "country" in combined:
        return "country", None
    if any(k in combined for k in ("zip", "postal", "postcode")):
        return "zip_code", "postal_code"
    if "company" in combined or "organization" in combined:
        return "company", None
    if any(k in combined for k in ("comment", "note", "message", "description")):
        return "comment", None
    if "name" in combined:  # 仅 name 兜底
        return "name", None
    return None, None


def _detect_from_value_pattern(value: str) -> tuple[str | None, str | None]:
    """基于值本身的正则兜底推断。返回 (name, format)。"""
    if re.fullmatch(r"[\w.\-]+@[\w.\-]+\.\w+", value):
        return "email", "email"
    if re.fullmatch(r"[\d\s\-\+\(\)]+", value) and len(re.sub(r"[^\d]", "", value)) >= 10:
        return "phone", "phone"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return "date", "date"
    if re.fullmatch(r"\d{1,9}", value):
        return "number", None
    # 姓名：首字母大写、字母/空格/连字符、2-30 字符
    if re.fullmatch(r"[A-Z][A-Za-z\s\-]{1,29}", value):
        words = value.split()
        if len(words) == 1:
            return "first_name", None
        if len(words) == 2:
            return "full_name", None
        return "name", None
    return None, None


def _ensure_unique_name(name: str, detected: dict[str, DetectedVariable]) -> str:
    """若变量名已存在，追加 _2/_3 后缀（从 2 起）。"""
    if name not in detected:
        return name
    i = 2
    while f"{name}_{i}" in detected:
        i += 1
    return f"{name}_{i}"


def merge_variable_sources(
    detected: dict[str, DetectedVariable],
    manual: list[ManualVariableBinding],
) -> dict[str, DetectedVariable]:
    """合并自动检测 + 人工标注的变量源（P4 可视化编辑）。

    ``manual``（编辑器手动标注）覆盖同名 ``detected``——用户显式标注为准。返回
    ``name → DetectedVariable`` 映射，供 ``_substitute_variables_in_history`` 与编辑器
    ``/history/detect`` 端点共用。人工标注天然绕过"精确整串匹配漏子串"盲区：直接按
    ``original_value`` 整串替换，不依赖规则命中。
    """
    merged = dict(detected)
    for binding in manual:
        merged[binding.name] = DetectedVariable(
            name=binding.name, original_value=binding.original_value
        )
    return merged
