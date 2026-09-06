from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

# 历史加载入口的畸形动作归一化复用无依赖叶子模块 action_shape（review4 #7——
# 不再把 LLM client 及其 anthropic import 拉进 agent 数据模型层/tools 的依赖图），
# 不为历史路径复制第二份强转规则。
from tree_walker.action_shape import coerce_named_action, normalize_actions_list

logger = logging.getLogger(__name__)


class ActionResult(BaseModel):
    display_max_chars: ClassVar[int] = 500

    is_done: bool = False
    success: bool | None = None
    error: str | None = None
    extracted_content: str | None = None
    long_term_memory: str | None = None
    judgement: Any | None = None
    # 阶段二（二.F）：通用结构化副字段（如 evaluate 的 images），默认 None；
    # __str__ 不 dump 它，保持 display_max_chars=500 的有界显示。
    metadata: dict[str, Any] | None = None
    # 阶段二（二.B）：done 附件（绝对路径列表），默认 None；__str__ 不渲染它，
    # 保持 display_max_chars=500 的有界显示（附件清单改走 extracted_content）。
    attachments: list[str] | None = None

    @model_validator(mode='after')
    def validate_success_requires_done(self):
        if self.success is True and self.is_done is not True:
            raise ValueError(
                'success=True can only be set when is_done=True. '
                'For regular actions that succeed, leave success as None.'
            )
        return self

    def __str__(self) -> str:
        parts: list[str] = []
        if self.error:
            parts.append(f"ERROR: {self.error}")
        if self.extracted_content:
            parts.append(f"EXTRACTED: {self.extracted_content[:self.display_max_chars]}")
        if self.is_done:
            parts.append(f"DONE (success={self.success})")
        if not parts:
            parts.append("OK")
        return " | ".join(parts)


class PlanItem(BaseModel):
    text: str
    status: str = 'pending'


class AgentBrain(BaseModel):
    evaluation_previous_goal: str = ""
    memory: str = ""
    next_goal: str = ""


class DownloadInfo(BaseModel):
    filename: str
    url: str
    path: str | None = None


class AgentState(BaseModel):
    n_steps: int = 0
    consecutive_failures: int = 0
    last_result: list[ActionResult] | None = None
    last_model_output: dict[str, Any] | None = None
    stopped: bool = False
    paused: bool = False
    downloaded_files: list[DownloadInfo] = []
    plan: list[PlanItem] | None = None
    current_plan_item_index: int = 0
    plan_generation_step: int = 0
    # _finalize 降级计数（PR #174 review4 #4）：finally 兜底吞掉 _finalize 异常的
    # 次数——确定性 _finalize bug 会让 run 报成功但 history 残缺（最坏是 done 步
    # 不进 history），此计数让降级可观测，run() 连续达阈值时升级终止。
    finalize_degraded_steps: int = 0

    class Config:
        arbitrary_types_allowed = True


class StepMetadata(BaseModel):
    """单步计时信息（重放步间延迟用）。

    ``step_interval`` = 上一步耗时（含 LLM 决策时间），仅 agent 自录路径填充（``step.py``）。
    ``user_pause_seconds`` = 相邻用户操作的真实停顿，仅 recorder 路径填充（``flatten.py``）。
    重放端 ``_compute_step_delay`` 优先用 ``user_pause_seconds``（忠实还原节奏，不封顶），
    回落 ``step_interval``（封顶防 LLM 空等）；两者皆空走 ``delay_between_actions`` 兜底。
    """

    step_start_time: float
    step_end_time: float
    step_number: int
    step_interval: float | None = None
    # 阶段4 / 缺口7：recorder 路径专用（相邻 action timestamp 差），与 step_interval 语义分离。
    # 旧 AgentHistory.json 无此字段 → pydantic 反序列化走默认 None，向后兼容。
    user_pause_seconds: float | None = None

    @property
    def duration_seconds(self) -> float:
        return self.step_end_time - self.step_start_time


class AgentHistory(BaseModel):
    step_number: int
    model_output: dict[str, Any]
    result: list[ActionResult]
    state_summary: dict[str, Any] | None = None
    # 重放所需：每个动作当年交互的元素投影（DOMInteractedElement.to_dict() 产物），
    # 与 model_output 的 actions 列表【等长、按位对应】；无 index 的动作为 None。
    interacted_element: list[dict[str, Any] | None] | None = None
    metadata: StepMetadata | None = None
    # 当步页面截图的存盘路径（phase 5 P1 集成点）。当前恒为 None —— 截图采集依赖
    # screenshot.md 阶段二（LLM 视觉通道），未打通前 _finalize 不写入；字段先就位，
    # 避免 _finalize async 化时再动模型层。
    screenshot_path: str | None = None

    class Config:
        arbitrary_types_allowed = True

    @model_validator(mode="before")
    @classmethod
    def _normalize_malformed_actions(cls, data: Any) -> Any:
        """畸形动作归一化的**构造收口**（issue #173，review5 #10 主线）。

        对齐 ActionResult.validate_success_requires_done 的既有模式：把
        ``normalize_actions_list`` 挂在模型上，使 load_from_file / load_from_dict /
        model_validate / 内存构造**全部路径**统一归一化——此前散点防御
        （client choke point + load_from_dict 预校验 + 逐点访问器）每层都有
        绕过（update_action_params 在畸形形态上 TypeError、自定义 LLM 注入
        绕过 client）。覆盖三种形态：actions 列表（含畸形条目）、老格式
        dict action、老格式裸字符串 action。
        """
        if not isinstance(data, dict):
            return data
        mo = data.get("model_output")
        if not isinstance(mo, dict):
            return data
        actions = mo.get("actions")
        if isinstance(actions, list) and actions:
            normalize_actions_list(actions)
            mo["action"] = actions[0]  # 镜像刷新（老文件可能是畸形原值）
        elif isinstance(mo.get("action"), dict):
            normalize_actions_list([mo["action"]])  # 原地补/修 params
        elif isinstance(mo.get("action"), str) and mo["action"].strip():
            mo["action"] = coerce_named_action(mo["action"])
        return data


# 仅这些「用户填值类」动作的参数需要敏感数据脱敏（动作名 → 待脱敏的参数字段）。
_SENSITIVE_ACTION_FIELDS: dict[str, tuple[str, ...]] = {
    "input_text": ("text",),
    "search": ("query",),
    "extract": ("query",),
}


def redact_sensitive_string(value: str, sensitive_values: dict[str, str]) -> str:
    """把 ``value`` 中出现的真实秘密替换成 ``<secret>key</secret>`` 占位符。

    按【值长度从长到短】排序，避免短秘密先匹配造成泄露（如 ``password`` 先吃掉
    ``password123`` 的前缀）。对齐 browser-use ``utils.py`` 的 ``redact_sensitive_string``。
    """
    if not value:
        return value
    for key, secret in sorted(
        sensitive_values.items(), key=lambda kv: len(kv[1] or ""), reverse=True
    ):
        if secret:
            value = value.replace(secret, f"<secret>{key}</secret>")
    return value


def _redact_history_data(
    data: dict[str, Any], sensitive_data: dict[str, str] | None
) -> None:
    """原地脱敏历史 dump：仅对 input_text/search/extract 的 text/query 过滤；result 不动。

    有意权衡（对齐 browser-use）：``result.extracted_content`` 等含 agent 推理所需信息，
    过滤会损害可读性，故不过滤。若历史文件会外发，需另行评估 result 的脱敏范围。
    """
    if not sensitive_data:
        return
    for h in data.get("history", []):
        mo = h.get("model_output") or {}
        actions = mo.get("actions") or ([mo["action"]] if mo.get("action") else [])
        for action in actions:
            if not isinstance(action, dict):
                continue
            fields = _SENSITIVE_ACTION_FIELDS.get(action.get("name"))
            params = action.get("params")
            if not fields or not isinstance(params, dict):
                continue
            for f in fields:
                if isinstance(params.get(f), str):
                    params[f] = redact_sensitive_string(params[f], sensitive_data)


class ManualVariableBinding(BaseModel):
    """用户在编辑器手动标注的变量绑定（绕过 detect_variables 规则检测）。

    编辑器把"step i / action j / field 的值"标为变量 ``name``；重放时与
    ``detect_variables`` 的结果并集合并（见 rerun.merge_variable_sources），
    天然绕过"精确整串匹配漏子串"盲区——直接按 original_value 替换。
    """

    name: str
    step_number: int
    action_index: int
    field: str = "text"
    original_value: str


class AgentHistoryList(BaseModel):
    history: list[AgentHistory] = Field(default_factory=list)
    # 存盘时写入，load 侧宽松校验（防 action 注册表漂移导致旧历史读不回）。
    action_registry_version: str | None = None
    # P4 可视化编辑：编辑器手动标注的变量绑定。老 JSON 无此键 → pydantic 默认 []，自动兼容。
    manual_variables: list[ManualVariableBinding] = Field(default_factory=list)
    # _finalize 降级步数（review5 #3）：run() 收尾把 AgentState 的计数落到返回的
    # history 上——调用方（web/评测）可区分「正常完成」与「history 残缺的完成」
    # （最坏情形：done 步不进 history）。0 = 无降级。
    finalize_degraded_steps: int = 0

    def final_result(self) -> str | None:
        for item in reversed(self.history):
            for r in item.result:
                if r.is_done and r.extracted_content:
                    return r.extracted_content
        return None

    def is_done(self) -> bool:
        if not self.history:
            return False
        return any(r.is_done for r in self.history[-1].result)

    def is_successful(self) -> bool:
        if not self.history:
            return False
        return any(r.is_done and r.success for r in self.history[-1].result)

    def save_to_file(
        self,
        filepath: str | Path,
        sensitive_data: dict[str, str] | None = None,
        action_registry_version: str | None = None,
    ) -> None:
        """序列化为 JSON（``indent=2, ensure_ascii=False``），含脱敏 + 注册表版本号。"""
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(mode="json")
        _redact_history_data(data, sensitive_data)
        if action_registry_version:
            data["action_registry_version"] = action_registry_version
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load_from_file(cls, filepath: str | Path) -> "AgentHistoryList":
        """从 JSON 读取历史。无需 output_model——action 是纯 dict，直接可用。"""
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        return cls.load_from_dict(data)

    @classmethod
    def load_from_dict(cls, data: dict[str, Any]) -> "AgentHistoryList":
        """从 dict 重建历史。老格式兼容：缺 interacted_element 补 None。

        畸形动作归一化已上收到 ``AgentHistory._normalize_malformed_actions``
        的 model_validator（review5 #10 主线——覆盖 load/validate/内存全部构造
        路径），此处不再持有手工副本。
        """
        for h in data.get("history", []):
            if h and "interacted_element" not in h:
                h["interacted_element"] = None
        return cls.model_validate(data)

    # —— P4 可视化编辑 mutation API ——
    # 核心不变量：每步 model_output.actions 与 interacted_element【等长、按位对应】
    # （见 AgentHistory 注释）。下面的方法都维护此不变量。改 params.text 零风险
    # （text 不参与五级匹配）；但删/合并 click 步可能断后续定位链，调用方须知晓。
    # 假设 model_output 为新格式 {"actions": [...]}；老格式 {"action": {...}} 不支持编辑。

    def _step_index(self, step_number: int) -> int:
        for i, h in enumerate(self.history):
            if h.step_number == step_number:
                return i
        raise KeyError(f"step_number {step_number} 不存在")

    def remove_step(self, step_number: int) -> None:
        """删除指定 step_number 的整步（删整步不影响其他步的等长不变量）。"""
        idx = self._step_index(step_number)
        self.history.pop(idx)

    def update_action_params(
        self, step_number: int, action_index: int, field: str, value: Any
    ) -> None:
        """改某步某 action 的 params[field]。text 字段安全（不参与五级匹配）。"""
        idx = self._step_index(step_number)
        actions = self.history[idx].model_output.get("actions", [])
        if not isinstance(actions, list) or action_index < 0 or action_index >= len(actions):
            raise IndexError(
                f"action_index {action_index} 越界（step {step_number} 有 "
                f"{len(actions) if isinstance(actions, list) else 0} 个动作）"
            )
        params = actions[action_index].setdefault("params", {})
        params[field] = value

    def merge_steps(self, step_a: int, step_b: int) -> None:
        """把 step_b 的 actions + interacted_element 追加到 step_a，删 step_b。

        等长不变量自然维持：a.actions += b.actions，a.interacted_element += b.interacted_element。
        """
        ia = self._step_index(step_a)
        ib = self._step_index(step_b)
        if ia == ib:
            raise ValueError("step_a 和 step_b 不能相同")
        a, b = self.history[ia], self.history[ib]
        a_actions = a.model_output.setdefault("actions", [])
        b_actions = b.model_output.get("actions", [])
        ea = list(a.interacted_element or [None] * len(a_actions))
        eb = list(b.interacted_element or [None] * len(b_actions))
        a.interacted_element = ea + eb
        a_actions.extend(b_actions)
        self.history.pop(ib)

    def add_manual_variable(self, binding: ManualVariableBinding) -> None:
        """登记一个手动变量绑定（同名替换，否则追加）。"""
        self.manual_variables = [
            v for v in self.manual_variables if v.name != binding.name
        ]
        self.manual_variables.append(binding)

    def remove_manual_variable(self, name: str) -> None:
        """按变量名删除手动绑定。"""
        self.manual_variables = [
            v for v in self.manual_variables if v.name != name
        ]


class DetectedVariable(BaseModel):
    """历史中检测到的可替换变量（纯规则检测，不调 LLM）。"""

    name: str
    original_value: str
    type: str = "string"
    format: str | None = None  # email/phone/date/number/url/postal_code


class RerunSummaryAction(BaseModel):
    """重放结束的 AI 摘要（无截图：基于执行证据判定，非视觉判定）。"""

    summary: str = Field(description="本次重放发生了什么（基于执行证据）")
    success: bool = Field(
        description="基于执行证据（步成功/失败计数 + 提取内容）判定是否成功"
    )
    completion_status: Literal["complete", "partial", "failed"] = Field(
        default="complete",
        description="complete(全成功) / partial(部分成功) / failed(未完成)",
    )


class BatchRowResult(BaseModel):
    """CSV 批量重放单行结果（P4 子任务 2）。"""

    row_index: int
    variables: dict[str, str] = Field(default_factory=dict)  # 该行注入的变量
    success: bool = False
    n_steps: int = 0
    extracted_content: str | None = None
    error: str | None = None
