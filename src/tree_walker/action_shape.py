"""动作形态的共享工具（issue #173，PR #174 review4 #7/#8）。

LLM 输出 / 历史数据里的「动作」理想形态是 ``{"name": str, "params": dict}``，
畸形形态（裸字符串动作、params 为字符串/null/数字）的归一化与安全访问集中在
本模块——**无依赖叶子模块**（仅 stdlib + logging），client / agent.views /
step / rerun 共同 import：

- 不再让 agent 数据模型层 import llm.client 的下划线私有函数（把 LLM client
  及其模块级 anthropic SDK import 拉进 tools/registry 等的依赖图，且
  client↔views 的无环只是承重的顺序假设）；
- 「params 非 dict → {}」此前以 5 种拼法散落 6+ 处，规则变更只改
  ``normalize_actions_list`` 会漏掉绕过副本——本模块是唯一实现。

日志只记类型不记值：畸形 params 字符串可能含已还原的敏感真值。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["coerce_named_action", "name_of", "normalize_actions_list", "params_of"]


def coerce_named_action(raw: Any) -> dict:
    """裸值 → 命名动作 dict（统一 strip 语义）。

    强转规则的所有入口（client 顶层 else / ``normalize_actions_list`` 列表项 /
    ``views.load_from_dict`` 老格式单 action）共用本函数——同一种 LLM 错误
    （``' click '``）在任何入口得到同一结果。裸 ``"done"`` 不在此特判：诚实失败
    由 ``_action_done`` 的 success 默认值统一兜底（text/data 任一存在才默认
    True），覆盖 dict 畸形 / 裸字符串 / variant A/B 全部形态（review4 #1/#5）。
    """
    return {"name": str(raw).strip(), "params": {}}


def normalize_actions_list(actions_list: list[Any]) -> None:
    """畸形动作归一化（issue #173 的 choke point 实现）——原地修复。

    裸字符串动作（``"click"``）→ 命名动作；dict 但 params 为字符串/null/数字
    （``"params": "561857"``）→ 置空 ``{}``。在 ``get_action`` 构造 result 之前
    与历史加载入口调用，一处修复全部下游，避免散落各处的 isinstance 副本漂移。
    归一化后的条目以「缺必填参数」优雅失败（进校验重试 / failure 计数）。
    """
    for i, a in enumerate(actions_list):
        if not isinstance(a, dict):
            logger.warning(
                "action[%d] malformed (%s) — coerced to named action with empty params",
                i, type(a).__name__,
            )
            actions_list[i] = coerce_named_action(a)
        elif not isinstance(a.get("params"), dict):
            if a.get("params") is not None:
                logger.warning(
                    "action[%d] (%r) params malformed (%s) — coerced to {}",
                    i, a.get("name"), type(a.get("params")).__name__,
                )
            a["params"] = {}


def name_of(action: Any, default: str = "done") -> str:
    """动作名的安全访问（旁路形态兜底）：dict 取 name（非字符串/缺失回默认），
    裸字符串动作取其 strip 值，其余回默认。"""
    if isinstance(action, dict):
        name = action.get("name")
        if isinstance(name, str) and name:
            return name
        return default
    if isinstance(action, str) and action.strip():
        return action.strip()
    return default


def params_of(action: Any) -> dict:
    """「params 非 dict → {}」的唯一实现（review4 #8）——替换散落各处的拼法。"""
    if isinstance(action, dict):
        params = action.get("params", {})
        return params if isinstance(params, dict) else {}
    return {}
