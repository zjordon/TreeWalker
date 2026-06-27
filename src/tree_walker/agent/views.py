from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field, model_validator


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


class AgentHistory(BaseModel):
    step_number: int
    model_output: dict[str, Any]
    result: list[ActionResult]
    state_summary: dict[str, Any] | None = None

    class Config:
        arbitrary_types_allowed = True


class AgentHistoryList(BaseModel):
    history: list[AgentHistory] = Field(default_factory=list)

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
