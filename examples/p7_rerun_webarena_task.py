r"""P7 轨迹重跑：直接用 TreeWalker Agent 重跑单个 WebArena 任务（轨迹分析用）。

用途：P7 失败归因的下一步验证——按 task_id 原样重跑，观察轨迹漂移/摩擦复现/
死因复现（如 thinking-only 猝死，见 docs/p7/01-task1-trajectory-anatomy.md 附三）。
与评测工作空间（D:\dev\git\z_jordon\evals\webarena）的关系：**只读**其
config_files/<id>.json 与 .auth cookie，不改那边任何程序；官方判分（WebArena
evaluator）仍归那边管——本示例产出轨迹 + agent 自评 + judge 判词 + 参考答案对照，
足够做轨迹分析。

比 eval harness 多做的：tree_walker.llm.client 开 DEBUG——每步记录 LLM 响应块
构成（client.py "LLM response blocks"），空响应/解析失败时能看到模型返回了什么
（2026-08-16 靠它抓到猝死真身：blocks=['thinking']）。

用法（Chrome 需以 --remote-debugging-port=<port> 启动，默认 9223）：
  uv run python examples/p7_rerun_webarena_task.py                      # task 1, 30 步
  uv run python examples/p7_rerun_webarena_task.py --task-id 502 --max-steps 30
  uv run python examples/p7_rerun_webarena_task.py --log-file out.log  # 轨迹落盘

对照地面真值（品牌聚合等）用 examples/p7_verify_bestsellers_brand.py（DB 路径）。
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tree_walker import Agent, BrowserSession, LLMClient, load_settings

DEFAULT_WEBARENA_REPO = Path(r"D:\dev\git\z_jordon\evals\webarena\webarena_repo")


async def inject_cookies(browser: BrowserSession, cookie_file: Path) -> int:
	"""Playwright storage-state → CDP Network.setCookie（url 参数绑定作用域）。

	⚠️ localhost 的 domain cookie 假成功坑：必须用 url 参数，
	domain="localhost" 会返回 success:true 但 cookie 不进 jar。
	"""
	if not cookie_file.exists():
		raise FileNotFoundError(f"cookie 文件不存在: {cookie_file}")
	cookies = json.loads(cookie_file.read_text(encoding="utf-8")).get("cookies", [])
	if browser.client is None or browser.current_session_id is None:
		raise RuntimeError("BrowserSession 未连接（先 await browser.start()）")
	sid = browser.current_session_id
	n_ok = 0
	for c in cookies:
		scheme = "https" if c.get("secure") else "http"
		domain = c.get("domain", "") or ""
		path = c.get("path", "/") or "/"
		params = {
			"name": c["name"], "value": c["value"], "path": path,
			"secure": c.get("secure", False), "httpOnly": c.get("httpOnly", False),
			"sameSite": {"Strict": "Strict", "Lax": "Lax", "None": "None"}.get(
				c.get("sameSite", "Lax"), "Lax"),
			"url": c.get("url") or f"{scheme}://{domain or 'localhost'}{path}",
		}
		expires = c.get("expires", -1)
		if isinstance(expires, (int, float)) and expires > 0:
			params["expires"] = expires
		result = await browser.client.send.Network.setCookie(params, session_id=sid)
		if result.get("success", True):
			n_ok += 1
	print(f"cookie 注入 {n_ok}/{len(cookies)}（{cookie_file.name}）")
	return n_ok


async def main() -> int:
	ap = argparse.ArgumentParser(description="重跑单个 WebArena 任务（轨迹分析用）")
	ap.add_argument("--task-id", type=int, default=1)
	ap.add_argument("--port", type=int, default=9223, help="Chrome CDP 端口（默认 9223）")
	ap.add_argument("--max-steps", type=int, default=30)
	ap.add_argument("--task-timeout", type=int, default=600, help="单任务总超时秒数")
	ap.add_argument("--webarena-repo", type=Path, default=DEFAULT_WEBARENA_REPO)
	ap.add_argument("--log-file", type=Path, default=None, help="轨迹日志落盘路径（可选）")
	args = ap.parse_args()

	# 日志：INFO 为主 + llm client DEBUG（响应块构成）；可选落盘
	handlers: list[logging.Handler] = [logging.StreamHandler()]
	if args.log_file:
		args.log_file.parent.mkdir(parents=True, exist_ok=True)
		handlers.append(logging.FileHandler(args.log_file, mode="w", encoding="utf-8"))
	logging.basicConfig(
		level=logging.INFO,
		format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
		handlers=handlers,
	)
	logging.getLogger("tree_walker.llm.client").setLevel(logging.DEBUG)

	# load_settings 内部读 CDP_PORT（默认 9222）——用 --port 覆盖后再加载
	os.environ["CDP_PORT"] = str(args.port)

	config_file = args.webarena_repo / "config_files" / f"{args.task_id}.json"
	if not config_file.exists():
		print(f"✗ 任务配置不存在: {config_file}")
		return 1
	task = json.loads(config_file.read_text(encoding="utf-8"))
	intent = task.get("intent", "")
	start_url = task.get("start_url", "")
	print(f"task {args.task_id}: {intent}")
	print(f"参考答案: {task.get('eval', {}).get('reference_answers', {})}")

	settings = load_settings()
	print(f"LLM 配置: model={settings.llm.model} max_tokens={settings.llm.max_tokens}"
		f" output_mode={settings.llm.output_mode}")
	browser = BrowserSession(settings.browser)
	await browser.start()
	try:
		if task.get("require_login", True):
			rel = task.get("storage_state", "")
			while rel.startswith("./"):
				rel = rel[2:]
			if rel:
				await inject_cookies(browser, args.webarena_repo / rel)

		task_text = f"{intent}\n\n起始页: {start_url}" if start_url else intent
		# 用 load_settings() 的 agent 设置、只覆盖 max_steps——原先直接
		# AgentSettings(max_steps=...) 会把 env 接线整个丢掉（如
		# AGENT_ENABLE_TASK_SKILL_INJECTION / AGENT_SKILLS_DIR，2026-09-06 踩坑）。
		agent_settings = settings.agent
		agent_settings.max_steps = args.max_steps
		agent = Agent(
			task=task_text,
			llm=LLMClient(settings.llm),
			browser=browser,
			settings=agent_settings,
		)
		try:
			history = await asyncio.wait_for(
				agent.run(keep_alive=True), timeout=args.task_timeout
			)
		except asyncio.TimeoutError:
			print(f"✗ 任务超过 {args.task_timeout}s 被中断")
			return 1

		print("\n=========== 重跑结果 ===========")
		print(f"is_done      : {history.is_done()}")
		print(f"is_successful: {history.is_successful()}")
		print(f"n_steps      : {len(history.history)}")
		print(f"final_result :\n{history.final_result()}")
		# url_match 型任务 reference_answers 为 null——.get 默认值只在键缺失时生效，
		# 键存在但值为 None 时会返回 None，下游 .get("exact_match") 崩（374 实锤）。
		ref = (task.get("eval", {}).get("reference_answers") or {}).get("exact_match")
		if ref:
			contained = str(ref).lower() in str(history.final_result() or "").lower()
			print(f"参考答案对照 : 参考={ref!r}，final_result 含参考值: {contained}"
				"（信息性对照，非官方判分）")
		return 0
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
