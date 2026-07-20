from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

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


class AgentHistoryList(BaseModel):
    history: list[AgentHistory] = Field(default_factory=list)
    # 存盘时写入，load 侧宽松校验（防 action 注册表漂移导致旧历史读不回）。
    action_registry_version: str | None = None

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
        """从 dict 重建历史。老格式兼容：缺 interacted_element 补 None。"""
        for h in data.get("history", []):
            if h and "interacted_element" not in h:
                h["interacted_element"] = None
        return cls.model_validate(data)


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
