from __future__ import annotations

import copy
import fnmatch
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Iterable

from pydantic import BaseModel

from tree_walker.agent.views import ActionResult

logger = logging.getLogger(__name__)


def _hide_fields_from_schema(schema: dict, fields: Iterable[str]) -> dict:
    """Return a copy of a Pydantic JSON schema with ``fields`` removed from
    ``properties`` and ``required`` (mirrors browser-use
    ``_hide_internal_fields_from_schema``). Used so variant-B done exposes only
    ``data`` to the LLM while the handler still accepts success/files_to_display.
    """
    out = copy.deepcopy(schema)
    props = out.get("properties", {})
    for f in fields:
        props.pop(f, None)
    req = out.get("required")
    if isinstance(req, list):
        out["required"] = [r for r in req if r not in set(fields)]
    return out


@dataclass
class RegisteredAction:
    name: str
    description: str
    param_model: type[BaseModel]
    handler: Callable[..., Awaitable[ActionResult | str | None]]
    terminates_sequence: bool = False
    page_patterns: list[str] | None = None


class ActionRegistry:
    def __init__(self, output_model: type[BaseModel] | None = None) -> None:
        self.actions: dict[str, RegisteredAction] = {}
        # 二.E：done 结构化输出（变体 B）的用户模型；get_tool_schema 据此隐藏
        # success/files_to_display，_register_all 据此选变体 B 参数模型。
        self.output_model = output_model

    def _action_available(self, name: str, page_url: str | None) -> bool:
        if page_url is None:
            return True
        action = self.actions[name]
        if action.page_patterns is None:
            return True
        return any(fnmatch.fnmatch(page_url, p) for p in action.page_patterns)

    def action(
        self,
        name: str,
        description: str,
        param_model: type[BaseModel],
        terminates: bool = False,
    ):
        """Decorator to register an action handler."""

        def decorator(fn: Callable[..., Awaitable[ActionResult | str | None]]):
            self.actions[name] = RegisteredAction(
                name=name,
                description=description,
                param_model=param_model,
                handler=fn,
                terminates_sequence=terminates,
            )
            return fn

        return decorator

    def get_tool_schema(
        self,
        enable_planning: bool = False,
        page_url: str | None = None,
        output_mode: str = "standard",
        include_actions: list[str] | None = None,
        max_actions: int = 1,
    ) -> dict[str, Any]:
        """Build the Anthropic tool_use schema for the agent_response tool.

        When ``max_actions > 1`` the ``action`` field is wrapped as a JSON array
        so the LLM can emit multiple actions per step (multi_act). When
        ``max_actions == 1`` the field stays a single object for backward
        compatibility with single-action execution.
        """
        action_names = sorted(
            name for name in self.actions
            if self._action_available(name, page_url)
            and (include_actions is None or name in include_actions)
        )
        action_descriptions: dict[str, str] = {}
        params_by_action: dict[str, Any] = {}

        for name in action_names:
            act = self.actions[name]
            action_descriptions[name] = act.description
            params_by_action[name] = act.param_model.model_json_schema()

        action_property = {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {
                    "type": "string",
                    "enum": action_names,
                    "description": "The action to execute",
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Action-specific parameters as flat key-value pairs. "
                        "For example: click -> {\"index\": 42}, input_text -> {\"index\": 187, \"text\": \"hello\", \"clear\": true}, "
                        "navigate -> {\"url\": \"https://...\"}. See Available Actions above for each action's expected params."
                    ),
                },
            },
        }

        if max_actions > 1:
            action_field: dict[str, Any] = {
                "type": "array",
                "minItems": 1,
                "maxItems": max_actions,
                "description": (
                    f"One or more actions to execute in order (up to {max_actions}). "
                    "Chain when targeting the same stable DOM: form filling, clearing "
                    "multiple items, sequential scrolls, multi-field extraction. The "
                    "runtime stops the sequence automatically if the page changes."
                ),
                "items": action_property,
            }
        else:
            action_field = action_property

        # Flash mode: minimal schema with only action
        if output_mode == "flash":
            return {
                "name": "agent_response",
                "description": "Respond with the action to take.",
                "input_schema": {
                    "type": "object",
                    "required": ["action"],
                    "properties": {
                        "action": action_field,
                    },
                },
            }

        # Standard and thinking modes: full schema
        properties = {
            "evaluation_previous_goal": {
                "type": "string",
                "description": "Evaluate whether the previous goal was achieved. On the first step, say 'Starting task.'",
            },
            "memory": {
                "type": "string",
                "description": "Key facts and progress to remember across steps. Keep concise.",
            },
            "next_goal": {
                "type": "string",
                "description": "What you plan to do in this step.",
            },
            "action": action_field,
        }

        required = [
            "evaluation_previous_goal",
            "memory",
            "next_goal",
            "action",
        ]

        # Thinking mode: add thinking field
        if output_mode == "thinking":
            properties["thinking"] = {
                "type": "string",
                "description": "Your step-by-step reasoning process. Think through the current state, evaluate options, and explain your decision.",
            }
            required.append("thinking")

        if enable_planning:
            properties["plan_update"] = {
                "type": "array",
                "items": {"type": "string"},
                "description": "Replace the entire plan with these steps. Use when creating a new plan or revising the current plan.",
            }
            properties["current_plan_item"] = {
                "type": "integer",
                "description": "Index of the current plan step to advance to. Steps between current and this index will be marked as done.",
            }

        description = (
            "Respond with your evaluation, memory, next goal, and the action to take."
        )
        if output_mode == "thinking":
            description = "Respond with your thinking process, evaluation, memory, next goal, and the action to take."

        return {
            "name": "agent_response",
            "description": description,
            "input_schema": {
                "type": "object",
                "required": required,
                "properties": properties,
            },
        }

    def get_action_descriptions_text(self, page_url: str | None = None) -> str:
        """Human-readable action list for the system prompt."""
        lines: list[str] = []
        for name in sorted(self.actions.keys()):
            if not self._action_available(name, page_url):
                continue
            act = self.actions[name]
            schema = act.param_model.model_json_schema()
            # 二.E：变体 B（output_model 给定）时对 done 隐藏 success/files_to_display——
            # 这是 LLM 实际看到的参数面（tool schema 的 params 是通用 object，详情走此处文本）。
            if name == "done" and self.output_model is not None:
                schema = _hide_fields_from_schema(schema, ("success", "files_to_display"))
            props = schema.get("properties", {})
            params_str = ", ".join(
                f"{k}: {v.get('description', v.get('type', 'any'))}"
                for k, v in props.items()
            )
            term = " [terminates sequence]" if act.terminates_sequence else ""
            lines.append(f"- **{name}**({params_str}){term}: {act.description}")
        return "\n".join(lines)
