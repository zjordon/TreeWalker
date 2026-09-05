"""Anthropic SDK wrapper for agent action selection via tool_use."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from anthropic import Anthropic, APIError, RateLimitError

from tree_walker.config import LLMSettings

logger = logging.getLogger(__name__)

# URL shortening threshold
_URL_MIN_LENGTH = 100

# R4（P7 02 方案）：text-not-tool_use 重试上限——旧实现无限递归
_TEXT_RETRY_MAX = 2


class LLMClient:
    """Wraps Anthropic SDK with tool_use for the agent loop.

    Uses a single ``agent_response`` tool so that every LLM reply contains
    evaluation, memory, next_goal and the action in one atomic call.
    """

    def __init__(self, settings: LLMSettings | None = None, **overrides: Any) -> None:
        s = settings or LLMSettings()
        if overrides:
            s = LLMSettings(
                model=overrides.get("model", s.model),
                api_key=overrides.get("api_key", s.api_key),
                base_url=overrides.get("base_url", s.base_url),
                max_tokens=overrides.get("max_tokens", s.max_tokens),
                fallback=overrides.get("fallback", s.fallback),
                output_mode=overrides.get("output_mode", s.output_mode),
            )
        self.client = Anthropic(api_key=s.api_key, base_url=s.base_url)
        self.model = s.model
        self.max_tokens = s.max_tokens
        self.output_mode = s.output_mode

        # Fallback LLM support
        self._fallback_client: Anthropic | None = None
        self._fallback_model: str | None = None
        # 与 LLMSettings.max_tokens 同步（fallback 也跑 agent 决策，thinking 计入输出）
        self._fallback_max_tokens: int = 16384
        self._using_fallback: bool = False

        if s.fallback and s.fallback.model:
            self._fallback_client = Anthropic(
                api_key=s.fallback.api_key,
                base_url=s.fallback.base_url,
            )
            self._fallback_model = s.fallback.model
            self._fallback_max_tokens = s.fallback.max_tokens

    def _try_switch_to_fallback(self, error: Exception) -> bool:
        """Switch to fallback LLM on rate limit or API error. One-way switch."""
        if self._using_fallback or not self._fallback_client:
            return False
        self.client = self._fallback_client
        self.model = self._fallback_model
        self.max_tokens = self._fallback_max_tokens
        self._using_fallback = True
        logger.warning(
            "Switched to fallback LLM: %s (due to %s: %s)",
            self.model, type(error).__name__, error,
        )
        return True

    def _shorten_urls_in_messages(self, messages: list[dict[str, Any]]) -> dict[str, str]:
        """Replace URLs >=100 chars in messages with short [uN] markers.

        Identical URLs share the same marker to save tokens.
        """
        url_map: dict[str, str] = {}
        url_to_tag: dict[str, str] = {}
        counter = 0
        url_pattern = re.compile(r'https?://\S+')

        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, str):
                continue

            def _replace(match: re.Match) -> str:
                nonlocal counter
                url = match.group(0)
                if len(url) < _URL_MIN_LENGTH:
                    return url
                if url in url_to_tag:
                    return url_to_tag[url]
                tag = f"[u{counter}]"
                url_map[tag] = url
                url_to_tag[url] = tag
                counter += 1
                return tag

            new_content = url_pattern.sub(_replace, content)
            if new_content != content:
                msg["content"] = new_content

        return url_map

    @staticmethod
    def _restore_urls_in_output(output: dict[str, Any], url_map: dict[str, str]) -> dict[str, Any]:
        """Recursively replace short [uN] markers with original URLs."""
        if not url_map:
            return output

        def _restore(obj: Any) -> Any:
            if isinstance(obj, str):
                for tag, original in url_map.items():
                    obj = obj.replace(tag, original)
                return obj
            if isinstance(obj, dict):
                return {k: _restore(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_restore(item) for item in obj]
            return obj

        return _restore(output)

    def _filter_sensitive_in_messages(
        self,
        messages: list[dict[str, Any]],
        sensitive_map: dict[str, str] | None,
    ) -> dict[str, str] | None:
        """Replace sensitive values in messages with their placeholders."""
        if not sensitive_map:
            return sensitive_map

        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            for real_value, placeholder in sensitive_map.items():
                content = content.replace(real_value, placeholder)
            msg["content"] = content

        return sensitive_map

    @staticmethod
    def _restore_sensitive_in_output(
        output: dict[str, Any],
        sensitive_map: dict[str, str] | None,
    ) -> dict[str, Any]:
        """Replace placeholders in LLM output with original sensitive values."""
        if not sensitive_map:
            return output

        def _restore(obj: Any) -> Any:
            if isinstance(obj, str):
                for real_value, placeholder in sensitive_map.items():
                    obj = obj.replace(placeholder, real_value)
                return obj
            if isinstance(obj, dict):
                return {k: _restore(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_restore(item) for item in obj]
            return obj

        return _restore(output)

    async def get_action(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tool_schema: dict[str, Any],
        *,
        _no_action_retry_used: bool = False,
        _text_retry_count: int = 0,
    ) -> dict[str, Any]:
        """Call the LLM and return a parsed agent response.

        ``_no_action_retry_used``：R1（P7 task1 附三）空响应重试的防递归标志——
        内部重试递归置 True；外部每步调用不传 → 每步重置。

        ``_text_retry_count``：R4（P7 02 方案）text-not-tool_use 重试计数——
        旧实现对「有文本但非工具调用」无限递归重试（runner 只能靠 600s 总闸
        防挂死，且每次重试带全上下文，是 wall-clock 的主要贡献者之一）。上限
        _TEXT_RETRY_MAX 次；超限返回空 dict 哨兵，交 step 层既有的
        clarification 重试 → fallback done 梯子接管（总调用次数有界）。
        """
        # URL shortening
        url_map = self._shorten_urls_in_messages(messages)

        # Sensitive data filtering
        sensitive_map = getattr(self, '_sensitive_map', None)
        self._filter_sensitive_in_messages(messages, sensitive_map)

        try:
            # issue #163：Anthropic 同步客户端的 create 是阻塞调用——直接 await 会卡死整个
            # 事件循环（tw-web 期间所有 HTTP 端点无响应、0 CPU 等同步 socket）。经
            # asyncio.to_thread 丢线程池，事件循环保持可服务（SSE/控制端点/其他任务）。
            response = await asyncio.to_thread(
                self.client.messages.create,
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_prompt,
                messages=messages,
                tools=[tool_schema],
                tool_choice={"type": "tool", "name": "agent_response"},
            )
        except (RateLimitError, APIError) as e:
            # Covers browser-use retryable status codes (service.py:1989-1995:
            # 401/402/429/500/502/503/504) via the SDK exception hierarchy —
            # RateLimitError (429), AuthenticationError (401) and APIStatusError
            # (5xx, 402, ...) are all subclasses of APIError, so this single
            # except is behaviorally equivalent to browser-use's explicit set.
            if self._try_switch_to_fallback(e):
                return await self.get_action(
                    system_prompt, messages, tool_schema,
                    _no_action_retry_used=_no_action_retry_used,
                    _text_retry_count=_text_retry_count,
                )
            raise

        # P6 后续 I2：捕获 SDK 返回的 token usage（透传给 step → ModelResultEvent）。
        # Anthropic Message 自带 .usage（input_tokens/output_tokens）；getattr 防异常 provider。
        _usage = getattr(response, "usage", None)
        _usage_dict = (
            {
                "input_tokens": getattr(_usage, "input_tokens", None),
                "output_tokens": getattr(_usage, "output_tokens", None),
            }
            if _usage is not None
            else None
        )

        # Debug: log raw response content types
        block_types = []
        for block in response.content:
            block_types.append(getattr(block, "type", str(type(block))))
        logger.debug("LLM response blocks: %s", block_types)

        # Extract the tool_use block
        tool_input: dict[str, Any] = {}
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "agent_response":
                tool_input = block.input
                break

        # Fallback: try to parse text content as JSON
        if not tool_input:
            text_content = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text_content += block.text
            if text_content.strip():
                parsed = _try_parse_json(text_content)
                if parsed:
                    logger.info("Parsed LLM text response as JSON")
                    tool_input = parsed
                else:
                    # R4（P7 02 方案）：text-not-tool_use 重试上限——旧实现无限递归
                    # （runner 靠 600s 总闸兜底）。超限返回空 dict 哨兵，step 层
                    # 既有梯子（clarification 重试一次 → fallback done）接管。
                    if _text_retry_count >= _TEXT_RETRY_MAX:
                        logger.warning(
                            "LLM returned text (not tool_use) %d times — returning empty for "
                            "step-level retry ladder",
                            _text_retry_count + 1,
                        )
                        return {}
                    # R4：指令型重试消息（压重试轮的输出长度，旧文案偏解释型）
                    logger.warning(
                        "LLM returned text (not tool_use), retrying with directive prompt (%d/%d)",
                        _text_retry_count + 1, _TEXT_RETRY_MAX,
                    )
                    retry_messages = list(messages) + [
                        {"role": "assistant", "content": text_content},
                        {"role": "user", "content": (
                            "Do not explain. Call the agent_response tool now with your "
                            "evaluation, memory, next goal, and action."
                        )},
                    ]
                    return await self.get_action(
                        system_prompt, retry_messages, tool_schema,
                        _no_action_retry_used=_no_action_retry_used,
                        _text_retry_count=_text_retry_count + 1,
                    )

        if not tool_input:
            # R2（P7 task1 附三）：空响应的关键证据上抛 WARNING——stop_reason 与
            # output_tokens 可直接判别「thinking 耗尽 max_tokens」（stop_reason=
            # max_tokens 且 output_tokens ≈ 上限），不必再靠 DEBUG 复跑推断。
            logger.warning(
                "LLM returned no parseable response (stop_reason=%s, output_tokens=%s, blocks=%s)",
                getattr(response, "stop_reason", None),
                (_usage_dict or {}).get("output_tokens"),
                block_types,
            )
            # R1（P7 task1 附三）：空响应（含 thinking-only）不再直接死刑——对齐上方
            # text-retry 的做法先重试一次；_no_action_retry_used 防递归无界。
            if not _no_action_retry_used:
                retry_messages = list(messages) + [
                    {"role": "user", "content": (
                        "Your previous response contained no action. Respond now with "
                        "the agent_response tool, including your evaluation, memory, "
                        "next goal, and action."
                    )},
                ]
                return await self.get_action(
                    system_prompt, retry_messages, tool_schema,
                    _no_action_retry_used=True,
                    _text_retry_count=_text_retry_count,
                )
            logger.warning("LLM still returned no parseable response after retry, using fallback done")
            result = {
                "evaluation_previous_goal": "No response from LLM",
                "memory": "",
                "next_goal": "Ending task due to empty response",
                "action": {"name": "done", "params": {"text": "No response from LLM", "success": False}},
                "actions": [{"name": "done", "params": {"text": "No response from LLM", "success": False}}],
                "usage": _usage_dict,
            }
            if url_map:
                result = self._restore_urls_in_output(result, url_map)
            # Sensitive data restoration
            if sensitive_map:
                result = self._restore_sensitive_in_output(result, sensitive_map)
            return result

        # Normalize action input: accept list (multi_act) or single dict (legacy).
        # Both `action` (first element, for backward compatibility) and `actions`
        # (full list, for the new multi-action loop) are exposed to downstream.
        raw_action = tool_input.get("action", {})
        if isinstance(raw_action, list):
            actions_list = raw_action
        elif isinstance(raw_action, dict):
            actions_list = [raw_action]
        else:
            actions_list = [{"name": "done", "params": {"text": "Invalid action shape", "success": False}}]

        # Phase A diagnostic: log the actual shape LLM emitted so we can tell
        # whether multi-action is being used. Reads schema maxItems when present
        # so the single-action message can say "schema allowed up to N".
        schema_max = (
            tool_schema.get("input_schema", {})
            .get("properties", {}).get("action", {})
            .get("maxItems")
        )
        if isinstance(raw_action, list):
            names = [a.get("name", "?") for a in raw_action if isinstance(a, dict)]
            logger.info(
                "multi_act: LLM emitted list with %d action(s): %s",
                len(raw_action), names,
            )
        elif isinstance(raw_action, dict):
            logger.info(
                "multi_act: LLM emitted single action %r (schema allows up to %s)",
                raw_action.get("name", "?"),
                schema_max if schema_max else "1",
            )

        for a in actions_list:
            if isinstance(a, dict):
                a.setdefault("params", {})

        result = {
            "evaluation_previous_goal": tool_input.get("evaluation_previous_goal", ""),
            "memory": tool_input.get("memory", ""),
            "next_goal": tool_input.get("next_goal", ""),
            "action": actions_list[0] if actions_list else {},
            "actions": actions_list,
            "usage": _usage_dict,
        }

        # URL restoration
        if url_map:
            result = self._restore_urls_in_output(result, url_map)

        # Sensitive data restoration
        if sensitive_map:
            result = self._restore_sensitive_in_output(result, sensitive_map)

        return result

    async def _extract_call(
        self, *, call_timeout: float | None, **create_kwargs: Any
    ):
        """``messages.create`` 的薄封装：恒经 ``asyncio.to_thread``（issue #163），``call_timeout`` 非空时再包 ``asyncio.wait_for``。

        Anthropic 同步客户端的 ``messages.create`` 是阻塞调用——直接调用会卡死事件循环
        （tw-web 期间所有端点无响应）。超时抛 ``asyncio.TimeoutError``（不会被 ``extract``
        的 RateLimit/APIError 分支捕获），交由 ``_action_extract`` 映射成分级错误。
        """
        coro = asyncio.to_thread(self.client.messages.create, **create_kwargs)
        if call_timeout:
            return await asyncio.wait_for(coro, timeout=call_timeout)
        return await coro

    async def extract(
        self,
        prompt: str,
        content: str,
        *,
        max_content_chars: int = 8000,
        output_schema: dict[str, Any] | None = None,
        already_collected: list[str] | None = None,
        call_timeout: float | None = None,
    ) -> str:
        """Secondary LLM call for page data extraction.

        When ``output_schema`` is a JSON Schema dict (top-level type=object with
        properties), forces structured output via an Anthropic tool and returns
        the validated JSON as a string. Otherwise returns free-text extraction.

        - ``already_collected``：拼进 user message 作为「跳过这些」去重列表（上限 200 条）。
        - ``call_timeout``：单次调用的 ``asyncio.wait_for`` 秒数（None=不加内层超时，交由
          调用方的 ``action_timeout`` 兜底）；超时抛 ``asyncio.TimeoutError``。
        """
        # 最低限度校验 schema；不可用则降级 free-text（对齐 browser-use try/except 降级）
        if output_schema is not None and (
            not isinstance(output_schema, dict)
            or output_schema.get("type") != "object"
            or not output_schema.get("properties")
        ):
            logger.warning("Invalid output_schema, falling back to free-text extraction")
            output_schema = None

        content = content[:max_content_chars]
        collected_block = ""
        if already_collected:
            joined = "\n".join(f"- {c}" for c in already_collected[:200])
            collected_block = (
                "\n\nItems already collected (DO NOT re-extract these, skip exact duplicates):\n"
                + joined
            )
        user_msg = f"{prompt}{collected_block}\n\n---\n{content}"

        if output_schema is not None:
            system_prompt = (
                "You are an expert at extracting structured data from a webpage. "
                "Extract exactly what the query asks for and return it via the "
                "extract_result tool, conforming strictly to the provided JSON Schema. "
                "Omit fields you cannot find rather than guessing."
            )
            tool = {
                "name": "extract_result",
                "description": "Structured extraction result conforming to the given schema.",
                "input_schema": output_schema,
            }
            try:
                response = await self._extract_call(
                    call_timeout=call_timeout,
                    model=self.model,
                    max_tokens=2048,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_msg}],
                    tools=[tool],
                    tool_choice={"type": "tool", "name": "extract_result"},
                )
            except (RateLimitError, APIError) as e:
                if self._try_switch_to_fallback(e):
                    return await self.extract(
                        prompt, content, max_content_chars=max_content_chars,
                        output_schema=output_schema, already_collected=already_collected,
                        call_timeout=call_timeout,
                    )
                raise
            for block in response.content:
                if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "extract_result":
                    if isinstance(block.input, dict):
                        return json.dumps(block.input, ensure_ascii=False)
            # 模型未用工具 → 同一响应里取 text 兜底
            logger.warning("LLM did not use extract_result tool; falling back to free-text")
            text_parts = [b.text for b in response.content if hasattr(b, "text")]
            return "\n".join(text_parts)

        # free-text 路径（prompt 进 user message）
        try:
            response = await self._extract_call(
                call_timeout=call_timeout,
                model=self.model,
                max_tokens=2048,
                messages=[{"role": "user", "content": user_msg}],
            )
        except (RateLimitError, APIError) as e:
            if self._try_switch_to_fallback(e):
                return await self.extract(
                    prompt, content, max_content_chars=max_content_chars,
                    output_schema=output_schema, already_collected=already_collected,
                    call_timeout=call_timeout,
                )
            raise
        text_parts = [b.text for b in response.content if hasattr(b, "text")]
        return "\n".join(text_parts)

    async def structured_call(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: dict[str, Any],
        *,
        max_tokens: int | None = None,
        call_timeout: float | None = None,
    ) -> dict[str, Any] | None:
        """One-shot tool-forced structured output (docs/p7/03 §4.3).

        任务级 skill 匹配等「一次轻量分类调用」的通用底座：Anthropic tool +
        ``tool_choice`` 强制 schema（复用 extract 的 output_schema 机制），模型未用
        工具时 text 兜底 ``_try_parse_json``。返回解析后的 dict；不可解析返回
        None；API 失败先走 fallback 切换（``_try_switch_to_fallback``），仍失败
        向上抛——调用方自行降级（matcher 的一次重试在那一层）。
        ``max_tokens=None`` 回落 ``self.max_tokens``（专用配置如
        AGENT_TASK_SKILL_MAX_TOKENS 生效于此；thinking 模型的思考 token 也计入）。
        """
        effective_max_tokens = max_tokens if max_tokens is not None else self.max_tokens
        tool = {
            "name": "structured_result",
            "description": "Structured result conforming to the given JSON Schema.",
            "input_schema": output_schema,
        }
        try:
            response = await self._extract_call(
                call_timeout=call_timeout,
                model=self.model,
                max_tokens=effective_max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[tool],
                tool_choice={"type": "tool", "name": "structured_result"},
            )
        except (RateLimitError, APIError) as e:
            if self._try_switch_to_fallback(e):
                return await self.structured_call(
                    system_prompt, user_prompt, output_schema,
                    max_tokens=max_tokens, call_timeout=call_timeout,
                )
            raise
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "structured_result":
                if isinstance(block.input, dict):
                    return block.input
        logger.warning("LLM did not use structured_result tool; falling back to text parse")
        text_parts = [b.text for b in response.content if hasattr(b, "text")]
        return _try_parse_json("\n".join(text_parts))


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """Try to extract a JSON object from text that might contain markdown fences."""
    # Try direct parse
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Try extracting from markdown code block
    import re
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None
