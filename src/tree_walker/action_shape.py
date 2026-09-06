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

# 诚实失败标记（review6 #3 / review7 #4 带外化）：``_HonestDone`` 是 dict 子类，
# ``is_honest_failure_action`` 按 isinstance 判定——**不进动作数据载荷**：
# - LLM 自己输出的 ``_honest_failure`` key 不再有任何特权（无法借它绕过校验）；
# - json 持久化 / pydantic 校验 / web 编辑器往返天然是普通 dict（无私有 key 泄漏）。
class _HonestDone(dict):
    """诚实失败 done 的带外类型标记。序列化为普通 JSON 对象。"""


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
    诚实失败的 done——**一次调用**终止（``_HonestDone`` 带外标记使校验放行，
    见 review6 #3 / review7 #4），绝不强转成 'None'/'123' 这类模型从未输出过
    的名字进重试梯子。
    """
    return _HonestDone({
        "name": "done",
        "params": {"text": "Invalid action shape", "success": False},
    })


def is_honest_failure_action(action: Any) -> bool:
    """校验层放行判定（review6 #3）：诚实失败 done 不进参数校验/重试梯子。"""
    return isinstance(action, _HonestDone)


def _has_invalid_name(action: dict) -> bool:
    """dict 动作的 name 键「存在但无效」（null/空串/非字符串）——与裸标量
    同类的模型畸形（review7 #7：同一错误两种形态应同等待遇）。"""
    if "name" not in action:
        return False
    name = action["name"]
    return not (isinstance(name, str) and name)


def normalize_actions_list(
    actions_list: list[Any], *, context: str = "live"
) -> None:
    """畸形动作归一化（issue #173 的 choke point 实现）——原地修复。

    策略表（形状 × 位置 × 上下文，review7 主线收口——此前策略分散在
    live 入口 / 历史加载 / truncate / client else 各应用不同子集）：

    **live**（实况管线，可执行、可反馈模型）：
    - 裸字符串（像样的动作名）→ 命名动作（任意位置，进缺参重试梯子）；
    - **单元素列表**的其他畸形（标量或 name 无效的 dict）→ 诚实失败 done
      （一次调用终止，不烧重试）；
    - **多元素列表**：畸形条目**原样保留**——头部畸形使镜像过不了
      ``_is_valid_action`` → 走 master 同款澄清重试（模型可重发整个列表，
      review7 #1：不再硬终止丢弃有效尾部）；中段畸形在执行时得到可见的
      Unknown action 错误（review6 #2：不合成 done 造成静默截断/重放分叉）；
    - dict 但 params 为字符串/null/数字 → 置空 ``{}``（两种上下文共用的
      无害化）。

    **history**（历史加载，review7 #2/#3：**不合成可执行动作**——master 时代
    录制的畸形条目在原 run 中从未执行，重放不得替它执行；只做无害化）：
    - 非列表 truthy 的 actions 容器 → ``[action 或 {}]``（防逐字符拆分）；
    - dict 条目修 params；标量/name 畸形条目原样保留（消费方
      ``isinstance(action, dict)`` 跳过 / emit str() 包裹，均无害）。
    """
    single = len(actions_list) == 1
    for i, a in enumerate(actions_list):
        if not isinstance(a, dict):
            if isinstance(a, str) and a.strip() and context == "live":
                logger.warning(
                    "action[%d] malformed (%s) — coerced to named action",
                    i, type(a).__name__,
                )
                actions_list[i] = coerce_named_action(a)
            elif context == "live" and single:
                logger.warning(
                    "action[0] malformed (%s) — honest-failure done termination",
                    type(a).__name__,
                )
                actions_list[i] = honest_done_action()
            else:
                # live 多元素：原样保留（镜像无效 → 澄清重试，review7 #1；中段
                # 畸形执行时得可见错误，review6 #2）；history：不合成可执行
                # 动作（review7 #2）——master 时代录制的裸字符串/标量在原 run
                # 从未执行，重放不得替它执行，消费方 isinstance 跳过即可
                logger.warning(
                    "action[%d] malformed (%s, %s) — left as-is (consumers skip "
                    "or surface visible error)",
                    i, type(a).__name__, context,
                )
        else:
            if _has_invalid_name(a):
                if context == "live" and single:
                    logger.warning(
                        "action[0] has invalid name (%r) — honest-failure done",
                        a.get("name"),
                    )
                    actions_list[i] = honest_done_action()
                elif context == "live":
                    # 多元素列表：无效 name 原样保留（review7 #7：不造 'None'
                    # 合成名进重试梯——镜像过不了 _is_valid_action 走澄清）
                    logger.warning(
                        "action[%d] has invalid name (%r) — left as-is "
                        "(invalid mirror → clarification retry)",
                        i, a.get("name"),
                    )
                else:
                    logger.warning(
                        "history action[%d] has invalid name — left as-is",
                        i,
                    )
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


def normalize_model_output(
    model_output: dict[str, Any], *, context: str = "live"
) -> dict[str, Any]:
    """管线入口一次性归一化整个 model_output（review6 #9 / review7 主线）。

    单一策略表（形状 × 位置 × 上下文，见 ``normalize_actions_list`` docstring）：
    - **live**：在 ``_get_next_action`` 拿到 response 后、校验/truncate/emit
      **之前**调用（review7 #6：归一化晚于 truncate 会让裸元素先崩 emit/log）；
    - **history**：历史加载/构造路径（validator）调用——不合成可执行动作。

    物化 actions 列表（非列表 truthy 容器按 ``[action 或 {}]`` 重置，防
    ``actions_of`` 逐字符拆分——review7 #3）→ 逐条归一化 → 刷新镜像。幂等。
    """
    actions = model_output.get("actions")
    if not (isinstance(actions, list) and actions):
        act = model_output.get("action")
        actions = [act] if isinstance(act, (str, dict)) else [{}]
    normalize_actions_list(actions, context=context)
    model_output["actions"] = actions
    model_output["action"] = actions[0]
    return model_output
