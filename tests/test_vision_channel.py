"""Tests for the LLM vision channel (screenshot.md 阶段二, issue #175).

覆盖：
  - build_state_blocks 构造契约（Anthropic image block，非 OpenAI image_url）
  - model_supports_vision / _parse_screenshot_size / load_settings 视觉配置
  - LLMClient 两过滤器对 block list 的适配 + fallback 滤图（P0 静默致盲防线）
  - StepPipeline 视觉门控 / step0 跳过 / b64 组装 / _set_state_message 单图在飞
  - _finalize 截图落盘（screenshot_path 兑现）
  - MessageCompactor / _save_conversation 对 list content 的兼容
"""
from __future__ import annotations

import asyncio
import base64
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tree_walker.config import (
	AgentSettings,
	LLMSettings,
	FallbackLLMSettings,
	TruncationSettings,
	_load_sensitive_data,
	load_settings,
	model_supports_vision,
	_parse_screenshot_size,
)
from tree_walker.llm.client import LLMClient, _strip_image_blocks
from tree_walker.agent.message_compactor import MessageCompactor, _content_text
from tree_walker.agent.step import StepPipeline
from tree_walker.browser.views import BrowserStateSummary, SerializedDOMState


def _png_state(screenshot=None, url="https://example.com", tree="[Page DOM] stuff"):
	"""带可选截图的 BrowserStateSummary（element_tree_text 非空 = 非 step0 空页）。"""
	return BrowserStateSummary(
		url=url,
		title="Test",
		dom_state=SerializedDOMState(
			_root=None,
			selector_map={},
			element_tree_text=tree,
		),
		screenshot=screenshot,
	)


def _image_block(data="aW1hZ2VkYXRh"):
	return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def _text_block(text="hello"):
	return {"type": "text", "text": text}


# ── build_state_blocks 构造契约 ──────────────────────────────────────────


class TestBuildStateBlocks:
	def test_no_screenshot_single_text_block(self):
		from tree_walker.prompts.system_prompt import build_state_blocks, build_state_message
		state = _png_state()
		blocks = build_state_blocks(state, screenshot_b64=None)
		assert isinstance(blocks, list) and len(blocks) == 1
		assert blocks[0]["type"] == "text"
		# 文本部分与 build_state_message 完全一致（kwargs 透传，不重复维护）
		assert blocks[0]["text"] == build_state_message(browser_state=state)

	def test_with_screenshot_anthropic_image_block(self):
		from tree_walker.prompts.system_prompt import build_state_blocks
		blocks = build_state_blocks(_png_state(), screenshot_b64="QUJD", task="do things")
		assert len(blocks) == 2
		img = blocks[1]
		assert img["type"] == "image"
		assert img["source"]["type"] == "base64"
		assert img["source"]["media_type"] == "image/png"
		assert img["source"]["data"] == "QUJD"
		# 反契约：绝不能是 OpenAI 风格 image_url（智谱原生 API 才用那格式）
		assert "image_url" not in str(img)
		assert "[Task] do things" in blocks[0]["text"]

	def test_image_presence_does_not_change_text(self):
		from tree_walker.prompts.system_prompt import build_state_blocks
		state = _png_state()
		without = build_state_blocks(state, screenshot_b64=None)
		with_img = build_state_blocks(state, screenshot_b64="QUJD")
		assert without[0]["text"] == with_img[0]["text"]


# ── model_supports_vision / _parse_screenshot_size / load_settings ───────


class TestModelSupportsVision:
	@pytest.mark.parametrize("model,expected", [
		("glm-5.1", False),
		("glm-5.2", False),
		("glm-5.3", False),          # 旗舰也是文本（5.3-Flash 才是首个原生多模态）
		("glm-4.5-flash", False),    # flash 后缀不代表视觉
		("glm-5.3-flash", True),
		("GLM-5.3-Flash", True),     # 大小写不敏感
		("glm-4v", True),
		("glm-4.6v", True),
		("glm-4.5v", True),
		("glm-4.1v-thinking", True),
		("glm-5v-turbo", True),
		("claude-opus-5", True),
		("claude-haiku-4-5-20251001", True),
		(None, False),
		("", False),
		("   ", False),
	])
	def test_matrix(self, model, expected):
		assert model_supports_vision(model) is expected


class TestParseScreenshotSize:
	def test_empty_is_none(self):
		assert _parse_screenshot_size("") is None
		assert _parse_screenshot_size("   ") is None

	def test_valid(self):
		assert _parse_screenshot_size("1400x850") == (1400, 850)
		assert _parse_screenshot_size(" 800x600 ") == (800, 600)
		assert _parse_screenshot_size("1024×768") == (1024, 768)  # 全角 ×

	def test_invalid(self):
		assert _parse_screenshot_size("abc") is None
		assert _parse_screenshot_size("1400") is None
		assert _parse_screenshot_size("50x40") is None  # 100px 下限


class TestLoadSettingsVision:
	def test_default_off(self, monkeypatch):
		monkeypatch.delenv("AGENT_USE_VISION", raising=False)
		monkeypatch.delenv("AGENT_LLM_SCREENSHOT_SIZE", raising=False)
		monkeypatch.setenv("LLM_MODEL", "glm-5.1")
		s = load_settings()
		assert s.agent.use_vision is False
		assert s.agent.llm_screenshot_size is None

	def test_on_with_vision_model_adaptive_size(self, monkeypatch):
		monkeypatch.setenv("AGENT_USE_VISION", "true")
		monkeypatch.setenv("LLM_MODEL", "glm-5.3-flash")
		monkeypatch.delenv("AGENT_LLM_SCREENSHOT_SIZE", raising=False)
		s = load_settings()
		assert s.agent.use_vision is True
		assert s.agent.llm_screenshot_size == (1400, 850)

	def test_on_with_text_model_no_adaptive_size(self, monkeypatch):
		monkeypatch.setenv("AGENT_USE_VISION", "true")
		monkeypatch.setenv("LLM_MODEL", "glm-5.1")
		monkeypatch.delenv("AGENT_LLM_SCREENSHOT_SIZE", raising=False)
		s = load_settings()
		assert s.agent.use_vision is True
		assert s.agent.llm_screenshot_size is None

	def test_explicit_size_overrides_adaptive(self, monkeypatch):
		monkeypatch.setenv("AGENT_USE_VISION", "true")
		monkeypatch.setenv("LLM_MODEL", "glm-5.3-flash")
		monkeypatch.setenv("AGENT_LLM_SCREENSHOT_SIZE", "800x600")
		s = load_settings()
		assert s.agent.llm_screenshot_size == (800, 600)

	def test_agents_settings_defaults(self):
		a = AgentSettings()
		assert a.use_vision is False
		assert a.llm_screenshot_size is None


# ── LLMClient 过滤器 block list 适配 + fallback 滤图 ─────────────────────


class _FilterClient:
	"""不触发网络的 LLMClient（filters 是纯内存操作）。"""

	def __init__(self):
		self.client_obj = LLMClient(LLMSettings(api_key="test-key"))

	def shorten(self, messages):
		return self.client_obj._shorten_urls_in_messages(messages)

	def filter_sensitive(self, messages, sensitive_map):
		return self.client_obj._filter_sensitive_in_messages(messages, sensitive_map)


class TestFiltersWithBlockLists:
	def test_shorten_replaces_in_text_block_only(self):
		long_url = "https://example.com/very/deep/path/" + "x" * 80
		c = _FilterClient()
		img = _image_block()
		messages = [{"role": "user", "content": [_text_block(f"go {long_url}"), img]}]
		url_map = c.shorten(messages)
		assert "[u0]" in messages[0]["content"][0]["text"]
		assert url_map["[u0]"] == long_url
		# image block 原对象原样透传
		assert messages[0]["content"][1] is img

	def test_sensitive_replaces_in_text_block_only(self):
		c = _FilterClient()
		img = _image_block()
		messages = [{"role": "user", "content": [_text_block("password is secret123"), img]}]
		c.filter_sensitive(messages, {"secret123": "<password>"})
		assert "<password>" in messages[0]["content"][0]["text"]
		assert "secret123" not in messages[0]["content"][0]["text"]
		assert messages[0]["content"][1] is img

	def test_str_content_regression(self):
		"""str 分支行为与旧实现一致（回归守护）。"""
		c = _FilterClient()
		messages = [{"role": "user", "content": "plain string stays"}]
		url_map = c.shorten(messages)
		assert url_map == {}
		assert messages[0]["content"] == "plain string stays"


class TestStripImageBlocks:
	def test_removes_image_keeps_text(self):
		messages = [{"role": "user", "content": [_text_block("keep me"), _image_block()]}]
		_strip_image_blocks(messages)
		assert messages[0]["content"] == [_text_block("keep me")]

	def test_str_untouched(self):
		messages = [{"role": "user", "content": "plain"}]
		_strip_image_blocks(messages)
		assert messages[0]["content"] == "plain"

	def test_image_only_degrades_to_empty_string(self):
		messages = [{"role": "user", "content": [_image_block()]}]
		_strip_image_blocks(messages)
		assert messages[0]["content"] == ""

	def test_no_image_no_rewrite(self):
		content = [_text_block("a"), {"type": "text", "text": "b"}]
		messages = [{"role": "user", "content": content}]
		_strip_image_blocks(messages)
		assert messages[0]["content"] is content  # 未发生重建


class TestFallbackStripsImages:
	"""P0 发现的防线：fallback 切到文本模型后，重试消息不得再带 image block
	（端点不报错只静默致盲——不滤图会得到「我看不见图」的困惑回答）。"""

	def _mock_tool_use_response(self):
		block = MagicMock()
		block.type = "tool_use"
		block.name = "agent_response"
		block.input = {
			"evaluation_previous_goal": "ok",
			"memory": "",
			"next_goal": "done",
			"action": {"name": "done", "params": {"text": "ok", "success": True}},
		}
		response = MagicMock()
		response.content = [block]
		return response

	def test_switch_to_text_fallback_strips_images(self):
		client = LLMClient(LLMSettings(
			model="glm-5.3-flash",  # 主模型：视觉
			api_key="k",
			fallback=FallbackLLMSettings(model="glm-5.1", api_key="k2"),  # fallback：文本
		))
		messages = [{"role": "user", "content": [_text_block("state"), _image_block()]}]

		call_count = 0
		def side_effect(*args, **kwargs):
			nonlocal call_count
			call_count += 1
			if call_count == 1:
				raise __import__("anthropic").RateLimitError(
					message="rate limited",
					response=MagicMock(status_code=429),
					body=None,
				)
			return self._mock_tool_use_response()

		with patch.object(client.client.messages, "create", side_effect=side_effect), \
				patch.object(client._fallback_client.messages, "create", side_effect=side_effect):
			result = asyncio.run(client.get_action("sys", messages, {"name": "tool"}))

		assert result["action"]["name"] == "done"
		assert client._using_fallback is True
		# 滤图生效：重试时文本模型收到的消息无 image block，text 保留
		content = messages[0]["content"]
		assert isinstance(content, list)
		assert [b["type"] for b in content] == ["text"]
		assert content[0]["text"] == "state"

	def test_switch_to_vision_fallback_keeps_images(self):
		"""fallback 也是视觉模型时不滤（能力仍在，图仍有价值）。"""
		client = LLMClient(LLMSettings(
			model="glm-5.1",
			api_key="k",
			fallback=FallbackLLMSettings(model="glm-5.3-flash", api_key="k2"),
		))
		messages = [{"role": "user", "content": [_text_block("state"), _image_block()]}]

		call_count = 0
		def side_effect(*args, **kwargs):
			nonlocal call_count
			call_count += 1
			if call_count == 1:
				raise __import__("anthropic").RateLimitError(
					message="rate limited",
					response=MagicMock(status_code=429),
					body=None,
				)
			return self._mock_tool_use_response()

		with patch.object(client.client.messages, "create", side_effect=side_effect), \
				patch.object(client._fallback_client.messages, "create", side_effect=side_effect):
			asyncio.run(client.get_action("sys", messages, {"name": "tool"}))

		content = messages[0]["content"]
		assert isinstance(content, list)
		assert [b["type"] for b in content] == ["text", "image"]


# ── StepPipeline：视觉门控 / step0 跳过 / b64 组装 / 单图在飞 ────────────


class _GateStub(StepPipeline):
	"""最小桩：只填视觉门控相关属性（StepPipeline 是 mixin，无 __init__）。"""

	def __init__(self, use_vision, model, n_steps=1, size=None):
		self._use_vision = use_vision
		self.llm = SimpleNamespace(model=model)
		self.state = SimpleNamespace(n_steps=n_steps)
		self._llm_screenshot_size = size
		self.messages = []
		self._enable_message_typing = True


class TestVisionGate:
	def test_config_off_closes_gate(self):
		assert _GateStub(False, "glm-5.3-flash")._vision_gate_open() is False

	def test_text_model_closes_gate(self):
		assert _GateStub(True, "glm-5.1")._vision_gate_open() is False

	def test_vision_model_opens_gate(self):
		assert _GateStub(True, "glm-5.3-flash")._vision_gate_open() is True

	def test_gate_follows_model_switch(self):
		"""逐步评估：fallback 切换改 llm.model 后门自动关（client 滤图的双保险）。"""
		stub = _GateStub(True, "glm-5.3-flash")
		assert stub._vision_gate_open() is True
		stub.llm.model = "glm-5.1"
		assert stub._vision_gate_open() is False


class TestIsNewTabStepZero:
	def test_step_zero_blank_page(self):
		stub = _GateStub(True, "glm-5.3-flash", n_steps=0)
		state = BrowserStateSummary(url="about:blank", dom_state=SerializedDOMState(
			_root=None, selector_map={}, element_tree_text=""))
		assert stub._is_new_tab_step_zero(state) is True

	def test_step_zero_real_page_not_skipped(self):
		stub = _GateStub(True, "glm-5.3-flash", n_steps=0)
		state = _png_state(url="https://example.com")
		assert stub._is_new_tab_step_zero(state) is False

	def test_later_steps_not_skipped_even_blank(self):
		stub = _GateStub(True, "glm-5.3-flash", n_steps=3)
		state = BrowserStateSummary(url="", dom_state=None)
		assert stub._is_new_tab_step_zero(state) is False


class TestPrepareScreenshotB64:
	def _shot(self):
		return b"\x89PNG fake bytes"

	def test_gate_closed_returns_none(self):
		stub = _GateStub(False, "glm-5.3-flash")
		assert stub._prepare_state_screenshot_b64(_png_state(self._shot())) is None

	def test_step0_blank_returns_none(self):
		stub = _GateStub(True, "glm-5.3-flash", n_steps=0)
		state = BrowserStateSummary(url="about:blank", dom_state=SerializedDOMState(
			_root=None, selector_map={}, element_tree_text=""))
		assert stub._prepare_state_screenshot_b64(state) is None

	def test_none_screenshot_returns_none(self):
		stub = _GateStub(True, "glm-5.3-flash")
		assert stub._prepare_state_screenshot_b64(_png_state(None)) is None

	def test_happy_path_b64_roundtrip_and_resize_called(self, monkeypatch):
		stub = _GateStub(True, "glm-5.3-flash", size=(1400, 850))
		calls = []
		def fake_resize(data, target):
			calls.append((data, target))
			return data  # identity：本测试不依赖 Pillow
		monkeypatch.setattr("tree_walker.agent.step.resize_screenshot_bytes", fake_resize)

		b64 = stub._prepare_state_screenshot_b64(_png_state(self._shot()))
		assert base64.b64decode(b64) == self._shot()
		assert calls == [(self._shot(), (1400, 850))]


class TestSetStateMessageImageCap:
	def test_older_state_keeps_text_drops_image(self):
		stub = _GateStub(True, "glm-5.3-flash")
		stub._set_state_message([_text_block("state A"), _image_block("QQ==")])
		stub._set_state_message([_text_block("state B"), _image_block("Qg==")])
		states = [m for m in stub.messages if m.get("role") == "user"]
		assert len(states) == 2
		# 旧 state：丢图留文（before/after 对比价值在文本）
		assert [b["type"] for b in states[0]["content"]] == ["text"]
		assert states[0]["content"][0]["text"] == "state A"
		# 新 state：带图（恒定单图在飞）
		assert [b["type"] for b in states[1]["content"]] == ["text", "image"]
		assert states[1]["content"][1]["source"]["data"] == "Qg=="

	def test_str_state_messages_untouched_by_cap(self):
		stub = _GateStub(True, "glm-5.3-flash")
		stub._set_state_message("old text state")
		stub._set_state_message([_text_block("new"), _image_block()])
		states = [m for m in stub.messages if m.get("role") == "user"]
		assert states[0]["content"] == "old text state"  # str 不动
		assert [b["type"] for b in states[1]["content"]] == ["text", "image"]

	def test_typing_off_appends_without_cap(self):
		stub = _GateStub(True, "glm-5.3-flash")
		stub._enable_message_typing = False
		stub._set_state_message([_text_block("a"), _image_block("QQ==")])
		stub._set_state_message([_text_block("b"), _image_block("Qg==")])
		# typing 关 = 原始累积行为（无槽位管理；图片成本由 use_vision 用户自担）
		assert len(stub.messages) == 2
		assert [b["type"] for b in stub.messages[0]["content"]] == ["text", "image"]


# ── _finalize 截图落盘 ──────────────────────────────────────────────────


class _FinalizeStub(StepPipeline):
	def __init__(self, rerun_dir):
		from tree_walker.agent.views import AgentHistoryList
		self.history = AgentHistoryList()
		self.state = SimpleNamespace(n_steps=2)
		self._step_start_time = time.time()
		self._obs_bus = None
		self._obs_session_id = "test"
		self._truncation = TruncationSettings()
		self.rerun_history_dir = str(rerun_dir)

	def _safe_project_interacted_elements(self, *args, **kwargs):
		return None

	def _build_step_metadata(self, now):
		return None

	def _log_step_completion_summary(self, results):
		pass


class TestFinalizeScreenshot:
	def test_screenshot_saved_and_path_recorded(self, tmp_path):
		stub = _FinalizeStub(tmp_path)
		state = _png_state(screenshot=b"\x89PNG-raw-bytes")
		asyncio.run(stub._finalize(state, {"action": {"name": "done", "params": {}}}, []))
		entry = stub.history.history[-1]
		assert entry.screenshot_path is not None
		assert entry.screenshot_path.endswith("step_002.png")
		with open(entry.screenshot_path, "rb") as f:
			assert f.read() == b"\x89PNG-raw-bytes"  # 存原图（非 LLM 降采样版）

	def test_no_screenshot_path_stays_none(self, tmp_path):
		stub = _FinalizeStub(tmp_path)
		state = _png_state(screenshot=None)
		asyncio.run(stub._finalize(state, {"action": {"name": "click", "params": {}}}, []))
		assert stub.history.history[-1].screenshot_path is None

	def test_save_failure_degrades_to_none(self, tmp_path, monkeypatch):
		"""IO 失败只 warning 返回 None，绝不挂 _finalize（PR #174 降级红线）。"""
		stub = _FinalizeStub(tmp_path)
		monkeypatch.setattr(
			"tree_walker.agent.step.Path.write_bytes",
			lambda self, data: (_ for _ in ()).throw(OSError("disk full")),
		)
		state = _png_state(screenshot=b"\x89PNG")
		asyncio.run(stub._finalize(state, {"action": {"name": "click", "params": {}}}, []))
		assert stub.history.history[-1].screenshot_path is None
		assert len(stub.history.history) == 1  # history 本体照常写入


# ── 压缩器 / 对话 dump 的 list content 兼容 ─────────────────────────────


class TestCompactorContentText:
	def test_str_passthrough(self):
		assert _content_text("abc") == "abc"

	def test_block_list_drops_image_keeps_text(self):
		content = [_text_block("aaa"), _image_block(), _text_block("bbb")]
		assert _content_text(content) == "aaa\nbbb"

	def test_other_types_empty(self):
		assert _content_text(None) == ""
		assert _content_text(42) == ""

	def test_maybe_compact_survives_block_messages(self):
		"""Gate 2 的 join 不再因 list content 抛 TypeError（原 :55 会炸）。"""
		from tree_walker.config import MessageCompactionSettings
		settings = MessageCompactionSettings(
			enabled=True, llm=None, compact_every_n_steps=1, trigger_char_count=10**9,
			keep_last_items=4,
		)
		compactor = MessageCompactor(settings, None)
		messages = [
			{"role": "user", "content": [_text_block("state"), _image_block()]},
			{"role": "assistant", "content": "did thing"},
		]
		asyncio.run(compactor.maybe_compact(messages, step_number=1))  # 不抛即过


class TestSaveConversationRender:
	def test_image_block_rendered_as_marker(self, tmp_path):
		"""dump 是人类可读审计件——image block 摘要为大小标记，不落 b64。"""
		b64_data = "QUJD" * 300  # ~1.2KB
		stub = _GateStub(True, "glm-5.3-flash")
		stub._save_conversation_path = str(tmp_path)
		stub._obs_session_id = "conv"
		stub.state.n_steps = 0
		messages = [{"role": "user", "content": [_text_block("state"), _image_block(b64_data)]}]
		StepPipeline._save_conversation(stub, messages, {"action": None})
		dumped = list(tmp_path.rglob("conversation_conv_0.txt"))[0].read_text(encoding="utf-8")
		assert "state" in dumped
		assert "[screenshot:" in dumped
		assert b64_data not in dumped
