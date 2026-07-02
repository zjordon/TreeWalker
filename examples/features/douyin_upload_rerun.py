"""Example: 抖音创作者中心发视频 + 历史重放（移植自 browser-use douyin_upload_rerun.py）

结合两个能力：
1. 历史重放：第一次跑完保存历史，第二次重放动作序列
2. CDP 连接 + 文件上传：连上已登录的 Chrome，上传本地视频/封面

场景：到抖音创作者中心发视频并暂存为草稿。第二次换掉视频、封面、标题、合集、
自主声明后再重放一次。

== 为什么用「手动替换映射」而不用 detect_variables() + load_and_rerun(variables=...)？==
TreeWalker 的变量检测器（tree_walker/agent/variable_detector.py）有两层限制，导致抖音
场景几乎检测不到可替换变量：

  限制 1（字段范围）：检测器只扫描 action 参数里的 'text' 和 'query' 两个字段。
    - upload_file 的 path 字段 → 不在扫描范围，文件路径必然漏掉
    - 合集/自主声明多为 click 选择 → 不是 input_text，必然漏掉

  限制 2（值模式）：即使扫到 input_text 的 text，抖音的作品描述是 contenteditable
    富文本框（没有 <input type=email> 之类的语义属性），属性策略识别不出；
    而标题「ai浏览器第五期」不符合邮箱/姓名/日期正则，值模式策略也识别不出。

所以本例采用「统一手动替换映射」：把所有要换的字段（文件路径 + 文本 + 选项）
都写进一个 {旧值: 新值} 字典，程序遍历历史 JSON 做精确整串替换。
替换逻辑与 tree_walker/agent/rerun.py 的 _substitute_in_dict 一致，只是作用范围扩大
到整个历史 JSON（覆盖 upload_file.path、input_text.text、navigate.url 等）。

下方 detect_variables_demo() 会打印检测结果，可直观对比「自动检测」和「手动映射」。

== 前置条件 ==
1. 先手动打开 Chrome 调试端口并登录抖音创作者中心：
   "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" ^
       --remote-debugging-port=9222 ^
       --user-data-dir=%TEMP%\\chrome-debug ^
       --no-first-run
   然后在打开的浏览器里登录 https://creator.douyin.com/
2. 设置 ZHIPU_API_KEY 环境变量（或写入 .env）。
3. 确保第一次运行的所有文件路径真实存在。
4. 把 RERUN 里第二次用到的文件路径/文本改成你自己的。

注意：本例需要真实的抖音登录态与本地视频/封面文件，无法自动跑通验证；请按上述
前置条件准备后手工运行。
"""

import asyncio
import logging
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.agent.views import AgentHistoryList
from tree_walker.config import load_settings


# ===== 第一次运行（录制）的配置 =====

HISTORY_FILE = Path("douyin_upload_history.json")
RERUN_HISTORY_FILE = Path("douyin_upload_history_rerun.json")

# 第一次运行要用的全部数据
FIRST_RUN = {
	"video": r"D:\Videos\test\final\2026-04-29-20-41-59.mp4",
	"heng_cover": r"D:\dev\git\claude\skills-deom\ppt\browser-use\heng.png",
	"shu_cover": r"D:\dev\git\claude\skills-deom\ppt\browser-use\shu.png",
	"main_title": "ai浏览器第五期-browse-use",
	"sub_title": "browse-use体验及技术原理",
	"collection": "AI浏览器合集",
	"declaration": "无需添加自主声明",
}

# 第二次重放要替换成的新值（key 必须与 FIRST_RUN 一致；改成你自己的）
# 凡是不想换的，直接填成和 FIRST_RUN 相同的值即可。
RERUN = {
	"video": r"D:\Videos\test\final\2026-04-29-20-41-59.mp4",
	"heng_cover": r"D:\temp\TreeWalker-竖封面.png",
	"shu_cover": r"D:\temp\TreeWalker-竖封面-new.png",
	"main_title": "ai浏览器第六期-browser-use进阶",
	"sub_title": "browser-use进阶技巧与原理",
	"collection": "AI浏览器合集",  # 不换合集
	"declaration": "无需添加自主声明",  # 不换自主声明
}

TASK_TEMPLATE = """
帮我到抖音创作者中心发一个视频，信息如下，先暂存为草稿不要直接发布，发布完后回到发布视频界面就算完成了不要再点继续编辑进去重复编辑
抖音创作者中心网址：https://creator.douyin.com/
我要发的视频在 '{video}'
作品描述中的主标题为：{main_title}，副标题为：{sub_title}
添加合集到"{collection}"
自主声明选择"{declaration}"
横封面图片在 '{heng_cover}'
竖封面图片在 '{shu_cover}'
""".strip()


# ===== 辅助函数 =====


def check_files_exist(data: dict, keys: list[str], stage: str) -> None:
	"""运行前检查文件是否存在，避免 upload_file 因路径无效报错。"""
	missing = [data[k] for k in keys if not Path(data[k]).is_file()]
	if missing:
		raise FileNotFoundError(
			f"[{stage}] 以下文件不存在，请检查路径：\n  " + "\n  ".join(missing)
		)


FILE_KEYS = ["video", "heng_cover", "shu_cover"]


def _make_agent(
	task: str,
	*,
	upload_paths: list[str] | None = None,
	max_steps: int | None = None,
) -> Agent:
	"""构造 Agent：复用 load_settings() 的 LLM/Browser 配置，连同一个已登录 Chrome。

	TreeWalker 里 upload_file 的路径白名单是 AgentSettings.allowed_upload_paths
	（对齐 browser-use 的 available_file_paths）。
	"""
	settings = load_settings()
	if max_steps is not None:
		settings.agent.max_steps = max_steps  # max_steps 在 settings 里设，不在 run() 里
	if upload_paths is not None:
		settings.agent.allowed_upload_paths = upload_paths
	llm = LLMClient(settings.llm)
	browser = BrowserSession(settings.browser)
	logging.basicConfig(level=logging.INFO)
	return Agent(task=task, llm=llm, browser=browser, settings=settings.agent)


# ===== 第一次运行：录制历史 =====


async def first_run() -> None:
	print("=== 第一次运行：录制历史 ===")
	check_files_exist(FIRST_RUN, FILE_KEYS, "first_run")

	task = TASK_TEMPLATE.format(**FIRST_RUN)
	agent = _make_agent(
		task,
		upload_paths=[FIRST_RUN[k] for k in FILE_KEYS],  # upload_file 校验路径白名单
		max_steps=50,
	)
	await agent.run()
	agent.save_history(HISTORY_FILE)
	print(f"✓ 历史已保存到 {HISTORY_FILE}")


# ===== 对比演示：detect_variables 能识别什么 =====


async def detect_variables_demo() -> None:
	"""打印 detect_variables 的检测结果，直观展示它的局限。

	预期：抖音场景下大概率返回空字典或极少变量——
	- 文件路径（upload_file.path）不在扫描范围
	- contenteditable 富文本框没有语义属性
	- 标题/合集不符合邮箱姓名日期正则
	这正是本例改用「手动替换映射」的原因。
	"""
	from tree_walker.agent.variable_detector import detect_variables_in_history
	from tree_walker.agent.rerun import resolve_rerun_path

	print("\n=== 对比演示：detect_variables 检测结果 ===")
	# 历史文件落在 rerun_history_dir 下（first_run 经 save_history 写入），这里同步解析
	root = load_settings().agent.rerun_history_dir
	history = AgentHistoryList.load_from_file(resolve_rerun_path(root, HISTORY_FILE))
	# 直接对历史做检测，等价于 agent.detect_variables()（agent.detect_variables 内部也是调它）
	variables = detect_variables_in_history(history)
	if variables:
		print(f"detect_variables 识别到 {len(variables)} 个变量：")
		for name, info in variables.items():
			print(f"  • {name}: {info.original_value!r} (format={info.format})")
	else:
		print("detect_variables 未识别到任何变量（符合预期，详见模块 docstring）")
	print("→ 因此本例采用手动替换映射，见下方。")


# ===== 手动替换映射（核心：统一替换所有字段）=====


def build_replacement_map() -> dict[str, str]:
	"""根据 FIRST_RUN 和 RERUN 构造 {旧值: 新值} 映射。

	只保留「实际要换」的字段（新旧值不同），相同值不进映射，避免无谓替换。
	"""
	replacements: dict[str, str] = {}
	for key in FIRST_RUN:
		old, new = FIRST_RUN[key], RERUN[key]
		if old != new:
			replacements[old] = new
	return replacements


def replace_values_in_history_dict(data, replacements: dict[str, str]) -> int:
	"""递归遍历历史 JSON，把等于旧值的字符串精确替换成新值。

	逻辑与 tree_walker/agent/rerun.py 的 _substitute_in_dict 一致：仅「精确整串匹配」
	才替换，不做子串替换。作用范围覆盖整个历史（包括 upload_file.path、input_text.text、
	navigate.url，以及被 input_text 填入的合集/自主声明文本）。
	"""
	count = 0
	if isinstance(data, dict):
		for key in list(data.keys()):
			value = data[key]
			if isinstance(value, str):
				if value in replacements:
					data[key] = replacements[value]
					count += 1
			elif isinstance(value, (dict, list)):
				count += replace_values_in_history_dict(value, replacements)
	elif isinstance(data, list):
		for i, item in enumerate(data):
			if isinstance(item, str):
				if item in replacements:
					data[i] = replacements[item]
					count += 1
			elif isinstance(item, (dict, list)):
				count += replace_values_in_history_dict(item, replacements)
	return count


def build_rerun_history(
	src_history: Path, dst_history: Path, replacements: dict[str, str]
) -> int:
	"""读取已保存的历史 JSON，替换值，写到新文件。返回替换次数。

	src/dst 都是相对路径，解析到 rerun_history_dir 下（与 first_run 的 save_history、
	rerun_with_new_values 的 load_and_rerun 落同一根目录）。
	"""
	from tree_walker.agent.rerun import resolve_rerun_path

	root = load_settings().agent.rerun_history_dir
	src = resolve_rerun_path(root, src_history)
	dst = resolve_rerun_path(root, dst_history)
	with open(src, encoding="utf-8") as f:
		data = json.load(f)
	count = replace_values_in_history_dict(data, replacements)
	dst.parent.mkdir(parents=True, exist_ok=True)
	with open(dst, "w", encoding="utf-8") as f:
		json.dump(data, f, indent=2, ensure_ascii=False)
	return count


# ===== 第二次运行：重放（换值）=====


async def rerun_with_new_values() -> None:
	print("\n=== 第二次运行：重放（替换所有字段）===")
	check_files_exist(RERUN, FILE_KEYS, "rerun")

	replacements = build_replacement_map()
	if not replacements:
		print("⚠️  FIRST_RUN 与 RERUN 完全相同，无需重放")
		return

	print("替换映射：")
	for old, new in replacements.items():
		print(f"  • {old!r}\n      → {new!r}")

	count = build_rerun_history(HISTORY_FILE, RERUN_HISTORY_FILE, replacements)
	print(f"\n✓ 已在历史中替换 {count} 处 → {RERUN_HISTORY_FILE}")
	if count == 0:
		print("⚠️  没有替换到任何值。可能原因：第一次运行时 LLM 没有原样使用提示词里的文本，")
		print("   例如把标题做了改写。精确整串替换要求历史里的值与 FIRST_RUN 完全一致。")

	# 重放用的 Agent：task 留空（动作序列已在历史里）；新文件路径必须放进白名单
	agent = _make_agent(task="", upload_paths=[RERUN[k] for k in FILE_KEYS])
	results = await agent.load_and_rerun(
		RERUN_HISTORY_FILE,
		# 不传 variables——所有值已在 JSON 里手动替换好了
		max_step_interval=5,  # 重放不等 LLM，封顶到 5s（默认 45s 会空等）
		delay_between_actions=1,
		skip_failures=True,  # 多步骤场景下跳过个别失败步骤，提高鲁棒性
	)

	# 最后一项是 AI 摘要（is_done=True）
	if results and results[-1].is_done:
		summary = results[-1]
		print("\n📊 AI 摘要:")
		print(f"  {summary.extracted_content}")
		print(f"  成功: {summary.success}")


async def main():
	# 第一次运行：录制历史。跑过一次后把这行注释掉，避免覆盖。
	# await first_run()
	# 对比演示：自动变量检测的局限
	await detect_variables_demo()
	# 第二次运行：换值重放
	await rerun_with_new_values()


if __name__ == "__main__":
	asyncio.run(main())
