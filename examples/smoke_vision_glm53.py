"""P0 smoke test（issue #175）：GLM-5.3-Flash 视觉接入前置验证。

背景：
  docs/tools-optimize/screenshot.md 阶段二（LLM 视觉通道）当年被「默认模型
  glm-5.1 纯文本」阻塞；2026-08-26 智谱上线 GLM-5.3-Flash（GLM-5 系列首个
  原生多模态模型）。本脚本实证三件 P0 事，全部走项目生产同款路径
  （Anthropic SDK + 智谱 Anthropic 兼容端点，裸 messages.create 不带
  temperature/thinking 参数，与 llm/client.py get_action 一致）：

  A. 可用性：glm-5.3-flash 在 https://open.bigmodel.cn/api/anthropic 纯文本往返
  B. image block 接受度：标准 Anthropic image block（source.type=base64）+
     视觉答案正确性（纯红 200x120 PNG → 问主色，期待回答含「红」）
  C. thinking 不可关闭的影响：max_tokens=16384（项目默认）与 4096 各测一次，
     观察思考是否挤占输出（stop_reason=max_tokens 且无文本 = 挤占实锤，
     对照 config.py LLMSettings 里「4096 时思考写满额度 → 空响应猝死」的历史注释）
  D. 对照组：glm-5.1 + 同图 —— 预期报错，证明 image block 确实被端点逐模型校验
     （若意外成功同样记录）

用法：
  uv run python examples/smoke_vision_glm53.py
需 ZHIPU_API_KEY 环境变量。只调 LLM API，无浏览器依赖、无副作用。
"""

import base64
import io
import os
import sys
import time

from anthropic import Anthropic, APIError

BASE_URL = "https://open.bigmodel.cn/api/anthropic"
MAX_TOKENS_DEFAULT = 16384  # 与 LLMSettings.max_tokens 一致

MODEL_VISION = "glm-5.3-flash"
MODEL_TEXT = "glm-5.1"  # 对照组：纯文本模型


def make_red_png_b64(width: int = 200, height: int = 120) -> str:
	"""纯红 PNG 的 base64（Pillow 缺失时手工构造等价最小 PNG）。"""
	try:
		from PIL import Image

		buf = io.BytesIO()
		Image.new("RGB", (width, height), (255, 0, 0)).save(buf, format="PNG")
		return base64.b64encode(buf.getvalue()).decode("ascii")
	except ImportError:
		# 手工 PNG：8 字节签名 + IHDR + IDAT(zlib 压缩的每行 filter 0 + RGB) + IEND
		import struct
		import zlib

		def chunk(tag: bytes, data: bytes) -> bytes:
			return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

		raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
		png = (
			b"\x89PNG\r\n\x1a\n"
			+ chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
			+ chunk(b"IDAT", zlib.compress(raw))
			+ chunk(b"IEND", b"")
		)
		return base64.b64encode(png).decode("ascii")


def dump_response(tag: str, resp, elapsed: float) -> str:
	"""统一摘要一行结论 + 关键字段（id/stop_reason/blocks/usage/时延）。"""
	blocks = [getattr(b, "type", type(b).__name__) for b in resp.content]
	usage = getattr(resp, "usage", None)
	usage_extra = dict(getattr(usage, "model_extra", {}) or {})
	thinking_chars = 0
	text = ""
	for b in resp.content:
		btype = getattr(b, "type", "")
		if btype == "thinking":
			thinking_chars += len(getattr(b, "thinking", "") or "")
		elif btype == "text":
			text += getattr(b, "text", "") or ""
	print(f"[{tag}] model={getattr(resp, 'model', '?')} stop_reason={resp.stop_reason}")
	print(f"[{tag}] blocks={blocks} thinking_chars={thinking_chars}")
	print(
		f"[{tag}] usage: input={getattr(usage, 'input_tokens', '?')} "
		f"output={getattr(usage, 'output_tokens', '?')} extra={usage_extra}"
	)
	print(f"[{tag}] elapsed={elapsed:.1f}s text={text.strip()[:120]!r}")
	return text


def call(client: Anthropic, tag: str, model: str, messages: list, max_tokens: int) -> str:
	t0 = time.perf_counter()
	resp = client.messages.create(model=model, max_tokens=max_tokens, messages=messages)
	elapsed = time.perf_counter() - t0
	return dump_response(tag, resp, elapsed)


def main() -> int:
	api_key = os.environ.get("ZHIPU_API_KEY")
	if not api_key:
		print("ZHIPU_API_KEY not set")
		return 1

	client = Anthropic(api_key=api_key, base_url=BASE_URL, max_retries=1, timeout=120.0)
	image_b64 = make_red_png_b64()
	print(f"image: red PNG 200x120, b64 len={len(image_b64)}")
	results: dict[str, str] = {}

	# A. 可用性：纯文本往返
	try:
		results["A"] = call(
			client,
			"A text-only",
			MODEL_VISION,
			[{"role": "user", "content": "回复「OK」两个字即可，不要多余内容。"}],
			MAX_TOKENS_DEFAULT,
		)
	except APIError as e:
		print(f"[A text-only] FAILED: {type(e).__name__} status={getattr(e, 'status_code', '?')} body={e.body}")
		return 2

	# B. image block 接受度 + 视觉答案正确性（项目默认 max_tokens=16384）
	image_messages = [
		{
			"role": "user",
			"content": [
				{
					"type": "image",
					"source": {"type": "base64", "media_type": "image/png", "data": image_b64},
				},
				{"type": "text", "text": "这张图片的主要颜色是什么？只回答颜色名。"},
			],
		}
	]
	try:
		results["B"] = call(client, "B image 16k", MODEL_VISION, image_messages, MAX_TOKENS_DEFAULT)
	except APIError as e:
		print(f"[B image 16k] FAILED: {type(e).__name__} status={getattr(e, 'status_code', '?')} body={e.body}")

	# C. thinking 挤占：同图缩到 4096，观察 stop_reason / 是否仍有文本
	try:
		results["C"] = call(client, "C image 4k", MODEL_VISION, image_messages, 4096)
	except APIError as e:
		print(f"[C image 4k] FAILED: {type(e).__name__} status={getattr(e, 'status_code', '?')} body={e.body}")

	# D. 对照组：纯文本模型 + 同图，预期报错
	try:
		results["D"] = call(client, "D glm-5.1 image", MODEL_TEXT, image_messages, 1024)
		print("[D glm-5.1 image] UNEXPECTED SUCCESS —— 记录并复查")
	except APIError as e:
		print(f"[D glm-5.1 image] rejected as expected: {type(e).__name__} status={getattr(e, 'status_code', '?')} body={str(e.body)[:300]}")

	# 结论汇总
	print("\n=== P0 结论 ===")
	print(f"A 可用性: {'PASS' if 'A' in results else 'FAIL'}")
	b_ok = "B" in results and ("红" in results["B"] or "red" in results["B"].lower())
	print(f"B image block 接受 + 视觉答案正确: {'PASS' if b_ok else 'FAIL/CHECK'}")
	c_ok = "C" in results and results["C"].strip() != ""
	print(f"C 4096 仍有文本输出(思考未挤占): {'PASS' if c_ok else 'FAIL —— 4096 被思考吃满,维持 16384 必要'}")
	return 0


if __name__ == "__main__":
	sys.exit(main())
