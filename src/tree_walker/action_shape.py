"""动作形态的共享工具（issue #173，PR #174 review4-6）。

LLM 输出 / 历史数据里的「动作」理想形态是 ``{"name": str, "params": dict}``，
畸形形态（裸字符串动作、params 为字符串/null/数字、name 为 null/非字符串）的
归一化与安全访问集中在本模块——**无依赖叶子模块**（仅 stdlib + logging），
client / agent.views / step / rerun 共同 import：

- 不再让 agent 数据模型层 import llm.client 的下划线私有函数（把 LLM client
  及其模块级 anthropic SDK import 拉进 tools/registry 等的依赖图）；
- 「params 非 dict → {}」与「actions 分发」此前以多种拼法散落 12+ 处，
  规则变更只改本模块会漏掉绕过副本——本模块是唯一实现。

日志只记类型不记值：畸形 params 字符串可能含已还原的敏感真值。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "actions_of",
    "coerce_named_action",
    "honest_done_action",
    "is_honest_failure_action",
    "name_of",
    "normalize_actions_list",
    "normalize_model_output",
    "params_of",
]

# 诚实失败标记（review6 #3）：携带者跳过参数校验立即执行——variant B 的
# StructuredDoneParams（extra="forbid"）会拒绝 honest_done 的 text 参数、
# 烧掉 2 次全上下文重试，恰是该 helper 要消除的消耗。
_HONEST_FAILURE_MARKER = "_honest_failure"


def coerce_named_action(raw: Any) -> dict:
    """裸值 → 命名动作 dict（统一 strip 语义）。

    强转规则的所有入口（client 顶层 else / ``normalize_actions_list`` 列表项 /
    ``views.load_from_dict`` 老格式单 action）共用本函数——同一种 LLM 错误
    （``' click '``）在任何入口得到同一结果。裸 ``"done"`` 不在此特判：诚实失败
    由 ``_action_done`` 的 success 默认值统一兜底（text/data 任一存在才默认
    True），覆盖 dict 畸形 / 裸字符串 / variant A/B 全部形态（review4 #1/#5）。
    """
    return {"name": str(raw).strip(), "params": {}}


def honest_done_action() -> dict:
    """非字符串畸形值（null/数字/布尔）的统一去向（review4 #2 / review5 #2）：
    诚实失败的 done——**一次调用**终止（``_HONEST_FAILURE_MARKER`` 使校验放行，
    见 review6 #3），绝不强转成 'None'/'123' 这类模型从未输出过的名字进重试
    梯子（2 次全上下文重试 × 每步，最终 max_failures 终止且无 done）。
    """
    return {
        "name": "done",
        "params": {"text": "Invalid action shape", "success": False},
        _HONEST_FAILURE_MARKER: True,
    }


def is_honest_failure_action(action: Any) -> bool:
    """校验层放行判定（review6 #3）：诚实失败 done 不进参数校验/重试梯子。"""
    return isinstance(action, dict) and bool(action.get(_HONEST_FAILURE_MARKER))


def normalize_actions_list(actions_list: list[Any]) -> None:
    """畸形动作归一化（issue #173 的 choke point 实现）——原地修复。

    位置感知策略（review6 #1/#2）：
    - 裸字符串（像样的动作名）→ 命名动作（任意位置，进缺参重试梯子）；
    - **index 0** 的其他标量（null/数字）→ 诚实失败 done——它是 actions[0]
      镜像、会进参数校验，强转合成名会烧全上下文重试（review4 #2）；单元素
      列表即「一次调用诚实终止」；
    - **index > 0** 的其他标量 → ``str()`` 强转命名动作——中段条目不进校验，
      执行时得到**可见的** Unknown action 错误（guard #3 截断且留 error 结果），
      而非诚实 done 触发 guard #1 的静默截断（无反馈；持久化后重放还会执行
      这个从未执行过的 done 造成分叉——review6 #2）；
    - dict 的 name 键存在但为 null/空串/非字符串 → ``str(name)`` 强转（可见
      错误，绝不伪造 done——review5 #1）；name 键缺失 → 不动（name_of 的
      旧单动作 "done" 缺省语义）；
    - dict 但 params 为字符串/null/数字 → 置空 ``{}``。
    """
    for i, a in enumerate(actions_list):
        if not isinstance(a, dict):
            if isinstance(a, str) and a.strip():
                logger.warning(
                    "action[%d] malformed (%s) — coerced to named action",
                    i, type(a).__name__,
                )
                actions_list[i] = coerce_named_action(a)
            elif i == 0:
                logger.warning(
                    "action[0] malformed (%s) — honest-failure done termination",
                    type(a).__name__,
                )
                actions_list[i] = honest_done_action()
            else:
                logger.warning(
                    "action[%d] malformed (%s) — coerced to named action "
                    "(yields visible Unknown-action error on execute)",
                    i, type(a).__name__,
                )
                actions_list[i] = coerce_named_action(a)
        else:
            name = a.get("name", "")
            if "name" in a and not (isinstance(name, str) and name):
                a["name"] = str(name) if name is not None else "None"
            if not isinstance(a.get("params"), dict):
                if a.get("params") is not None:
                    logger.warning(
                        "action[%d] (%r) params malformed (%s) — coerced to {}",
                        i, a.get("name"), type(a.get("params")).__name__,
                    )
                a["params"] = {}


def name_of(action: Any) -> Any:
    """动作名访问（master 语义，review5 #1：绝不伪造 done）。

    - dict 缺 ``name`` 键 → ``"done"``（旧单动作形态的既有缺省）；
    - dict 的 name 为显式 null / 空串 / 非字符串 → **原样返回**（归一化后正常
      路径不会出现；旁路形态经此处透传给下游 registry → 可见 Unknown action
      错误，而非静默伪造终止动作）。emit 侧请以 ``str(name_of(a) or "")`` 包裹
      （pydantic str 字段不接受 None——review6 #1）；
    - 裸字符串动作 → strip 后的名字；其余 → None。
    """
    if isinstance(action, dict):
        if "name" not in action:
            return "done"
        return action["name"]
    if isinstance(action, str) and action.strip():
        return action.strip()
    return None


def params_of(action: Any) -> dict:
    """「params 非 dict → {}」的唯一实现（review4 #8）——替换散落各处的拼法。"""
    if isinstance(action, dict):
        params = action.get("params", {})
        return params if isinstance(params, dict) else {}
    return {}


def actions_of(model_output: dict[str, Any] | None) -> list[Any]:
    """``model_output`` 的 actions 分发（review6 #9）：列表优先，否则单动作包
    列表（falsy → ``[{}]``，master 语义）。全仓库 12+ 份手写变体的共享实现。
    """
    if not isinstance(model_output, dict):
        return []
    return list(model_output.get("actions") or [model_output.get("action", {})])


def normalize_model_output(model_output: dict[str, Any]) -> dict[str, Any]:
    """管线入口一次性归一化整个 model_output（review6 主线，#9）。

    在 ``_step`` 拿到 response 后（client 构造 result 之后/注入 LLM 绕过时）
    调用：物化 actions 列表（单动作也物化，统一下游分发）→ 逐条归一化（位置
    感知策略见 ``normalize_actions_list``）→ 刷新 ``action`` 镜像。幂等。
    """
    actions = model_output.get("actions")
    if not (isinstance(actions, list) and actions):
        act = model_output.get("action")
        actions = [act] if isinstance(act, (str, dict)) else [{}]
    normalize_actions_list(actions)
    model_output["actions"] = actions
    model_output["action"] = actions[0]
    return model_output
