r"""P7 任务级 skill 匹配离线回归（docs/p7/04 §七 S3，issue #182）。

不跑浏览器——匹配器是纯 LLM 调用（task_matcher.match_task_skill），直接对
「44 卡 catalog × 184 任务文本」批量跑匹配层，量三率：回放命中 / 泛化正确命中 /
跨模板误命中（+ 降档数 + 分级字段分布）。prompt 迭代在离线完成，真机全量
（run_full.sh --skill-mode C）只跑最终版——省的是每轮 184 任务的完整 agent 跑。

数据对齐（离线保真三要素，docs/p7/04 §七 S3）：
  1. 任务文本组装对齐 evals/webarena/runner.py:349——
     ``task_text = f"{intent}\n\n起始页: {start_url}"``（matcher 收到的就是这个）；
  2. 任务集 = webarena_repo/config_files/test.raw.json 里 sites 含 shopping_admin
     的 184 个（与 run_full.sh --all --sites shopping_admin 同源）；
  3. 回放映射 = eval 仓 config/replay_map.json（44 卡 slug → 本尊 task_id）；
     卡的模板 = 本尊任务的 intent_template_id——正确性按**模板等价类**判
     （docs/p7/04 §4.5：42 模板 44 卡，命中同模板另一张卡算正确命中）。

调用失败（``TaskSkillMatch.call_failed``——API 异常/超时重试后仍失败）在 harness
层重试至 3 次——基础设施故障不是匹配语义，不得计入未命中（match_task_skill
内部只重试一次）。

用法（TreeWalker 仓库根）：
  uv run python examples/p7_task_skill_match_regression.py --eval-root <evals/webarena>
  ... --concurrency 6 --out out/task_skill_match_regression.json --gate

门槛（--gate，docs/p7/04 §七：达标才上真机）：回放正确命中 44/44（漏命中 0）
且泛化正确命中 ≥ 80%（≥112/140）；退出码 1 = 不达标。
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tree_walker import LLMClient, load_settings  # noqa: E402
from tree_walker.skills.loader import SkillLoader  # noqa: E402
from tree_walker.skills.task_loader import TaskSkillLoader  # noqa: E402
from tree_walker.skills.task_matcher import match_task_skill  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# 离线门槛（docs/p7/04 §七）：泛化命中 30% → ≥80%；回放漏命中保持 0。
GATE_REPLAY_TOTAL = 44
GATE_VARIANT_RATE = 0.80

logger = logging.getLogger("regression")


def load_tasks(eval_root: Path) -> list[dict]:
	"""test.raw.json → shopping_admin 184 任务（intent/start_url/template 原样）。"""
	raw = json.loads(
		(eval_root / "webarena_repo" / "config_files" / "test.raw.json").read_text(
			encoding="utf-8"
		)
	)
	tasks = [
		t for t in raw if any(s == "shopping_admin" for s in t.get("sites", []))
	]
	for t in tasks:
		# intent_template_id 与另两者同为统计硬依赖（card_template/rows 直接键访问），
		# 缺失时变体任务会在 184 次调用跑完后才 KeyError——必须 startup 就拦（review-20260910-2）
		if not t.get("intent") or not t.get("start_url") or "intent_template_id" not in t:
			raise SystemExit(
				f"task {t.get('task_id')} 缺 intent/start_url/intent_template_id——数据源不对？"
			)
	return tasks


def load_replay(eval_root: Path, tasks_by_id: dict[int, dict]) -> dict[str, int]:
	"""replay_map.json → {card_slug: 本尊 task_id}；校验本尊都在任务集内。"""
	data = json.loads(
		(eval_root / "config" / "replay_map.json").read_text(encoding="utf-8")
	)
	mapping: dict[str, int] = {}
	for slug, v in data["mapping"].items():
		tid = v["task_id"]
		if tid not in tasks_by_id:
			raise SystemExit(f"replay_map 卡 {slug} 的 task_id={tid} 不在任务集内")
		mapping[slug] = tid
	return mapping


async def match_with_retry(task_text: str, catalog, llm, sem: asyncio.Semaphore):
	"""并发限流跑一次匹配；call_failed 在 harness 层再试 2 次（共 3 次）。"""
	for attempt in range(1, 4):
		# 只在调用本身占并发槽——退避等待期间不占（review-20260910-2：故障突发时
		# 槽被 sleep 占着会让有效并行度塌到零）
		async with sem:
			m = await match_task_skill(task_text, catalog, llm)
		if not m.call_failed:
			return m
		logger.warning("call failed (attempt %d/3): %s", attempt, m.reason)
		if attempt < 3:  # 末次失败直接返回，不空等 2s
			await asyncio.sleep(2.0)
	return m  # type: ignore[possibly-undefined]


async def main() -> int:
	ap = argparse.ArgumentParser(description="任务级 skill 匹配离线回归（docs/p7/04 S3）")
	ap.add_argument("--eval-root", type=Path, required=True, help="evals/webarena 工作区路径")
	ap.add_argument("--host-key", default="localhost_7780", help="任务卡所在 host key")
	ap.add_argument("--concurrency", type=int, default=6, help="匹配调用并发上限")
	ap.add_argument("--out", type=Path, default=REPO_ROOT / "out" / "task_skill_match_regression.json")
	ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个任务（0=全量；冒烟/提速用）")
	ap.add_argument("--gate", action="store_true", help="按 docs/p7/04 门槛判达标（退出码）")
	args = ap.parse_args()

	logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

	# catalog：直用 TaskSkillLoader（skills_dir 指仓库根绝对路径，不依赖 CWD）
	catalog = TaskSkillLoader(SkillLoader(str(REPO_ROOT / "domain-skills"))).catalog(
		args.host_key
	)
	if not catalog:
		raise SystemExit(f"catalog 空（host_key={args.host_key}）——检查 domain-skills 路径")
	logger.info("catalog: %d cards (host_key=%s)", len(catalog), args.host_key)

	# matcher LLM：镜像 agent.py 接线——AGENT_TASK_SKILL_MODEL 未设则复用主 llm
	settings = load_settings()
	matcher_llm = (
		LLMClient(settings.agent.task_skill_llm)
		if settings.agent.task_skill_llm is not None
		else LLMClient(settings.llm)
	)

	tasks = load_tasks(args.eval_root)
	tasks_by_id = {t["task_id"]: t for t in tasks}
	replay = load_replay(args.eval_root, tasks_by_id)
	# fail fast（review-20260910-1）：catalog 里有卡缺 replay 映射的话，card_template
	# 取值会在 184 次 LLM 调用全部完成后的统计阶段 KeyError——整轮结果报废。
	unmapped = {c.slug for c in catalog} - set(replay)
	if unmapped:
		raise SystemExit(
			f"catalog 卡缺 replay 映射: {sorted(unmapped)}——card_template 会 KeyError，"
			f"先补 config/replay_map.json 再跑"
		)
	replay_ids = set(replay.values())
	# 卡的模板 = 本尊任务的 intent_template_id（模板等价类，docs/p7/04 §4.5）
	card_template = {slug: tasks_by_id[tid]["intent_template_id"] for slug, tid in replay.items()}
	logger.info(
		"tasks: %d (replay %d / variants %d); %d cards over %d templates",
		len(tasks), len(replay_ids), len(tasks) - len(replay_ids),
		len(catalog), len({t for t in card_template.values()}),
	)

	sem = asyncio.Semaphore(args.concurrency)
	tasks_to_run = tasks[: args.limit] if args.limit else tasks
	started = time.monotonic()
	results = await asyncio.gather(*[
		match_with_retry(
			# 离线保真：任务文本组装对齐 runner.py:349（load_tasks 已守 start_url 非空，
			# 无需再留 fallback 分支——review-20260910-2）
			f"{t['intent']}\n\n起始页: {t['start_url']}",
			catalog, matcher_llm, sem,
		)
		for t in tasks_to_run
	])
	logger.info("matched %d tasks in %.1fs", len(results), time.monotonic() - started)

	# ── 归类统计 ───────────────────────────────────────────────────────────
	rows = []
	for t, m in zip(tasks_to_run, results):
		tpl = t["intent_template_id"]
		own_slug = next((s for s, tid in replay.items() if tid == t["task_id"]), None)
		row = {
			"task_id": t["task_id"],
			"template_id": tpl,
			"is_replay": t["task_id"] in replay_ids,
			"matched_slug": m.slug,
			"correct_template": m.slug is not None and card_template[m.slug] == tpl,
			"exact_slug": m.slug is not None and m.slug == own_slug,
			"confidence": m.confidence,
			"match_kind": m.match_kind if m.slug else None,
			"task_kind": m.task_kind,
			"downgraded": m.downgraded,
			"reason": m.reason,
			"intent": t["intent"],
		}
		rows.append(row)

	replay_rows = [r for r in rows if r["is_replay"]]
	variant_rows = [r for r in rows if not r["is_replay"]]

	def _hit(rs):
		return sum(1 for r in rs if r["matched_slug"])

	def _correct(rs):
		return sum(1 for r in rs if r["correct_template"])

	metrics = {
		"replay": {
			"total": len(replay_rows),
			"hit": _hit(replay_rows),
			"correct": _correct(replay_rows),
			"exact_slug": sum(1 for r in replay_rows if r["exact_slug"]),
			"missed": [r["task_id"] for r in replay_rows if not r["matched_slug"]],
		},
		"variants": {
			"total": len(variant_rows),
			"hit": _hit(variant_rows),
			"correct": _correct(variant_rows),
			"cross_template": [
				r["task_id"] for r in variant_rows if r["matched_slug"] and not r["correct_template"]
			],
			"missed": sum(1 for r in variant_rows if not r["matched_slug"]),
		},
		"downgraded": sum(1 for r in rows if r["downgraded"]),
		# 分级字段分布（docs/p7/04 §4.2 的实证核对）：本尊命中应 mostly same_task，
		# 变体命中应 mostly same_template——倒挂说明模型没理解分级指令
		"match_kind_of_hits": {
			"replay": _dist(replay_rows, "match_kind"),
			"variants": _dist(variant_rows, "match_kind"),
		},
		"task_kind": _dist(rows, "task_kind"),
	}

	# 按模板聚合（变体侧）：零命中模板 = **有卡**模板的变体 correct=0（issue #182
	# 报告 §3 的 13 个）；无卡模板的变体不可能正确命中，不属此列（单列 no_card）
	templates_with_cards = set(card_template.values())
	per_template = {}
	for r in variant_rows:
		d = per_template.setdefault(
			r["template_id"], {"variants": 0, "hit": 0, "correct": 0, "example": r["intent"]}
		)
		d["variants"] += 1
		d["hit"] += 1 if r["matched_slug"] else 0
		d["correct"] += 1 if r["correct_template"] else 0
	zero_hit_templates = sorted(
		t for t, d in per_template.items() if d["correct"] == 0 and t in templates_with_cards
	)
	no_card_templates = sorted(t for t in per_template if t not in templates_with_cards)

	# ── 报告 ───────────────────────────────────────────────────────────────
	rp, vp = metrics["replay"], metrics["variants"]
	# --limit 前缀可能不含变体任务（review-20260910-1：除零丢整轮结果）
	variant_rate = vp["correct"] / vp["total"] if vp["total"] else 0.0
	print("\n===== 匹配离线回归结果 =====")
	print(f"回放集:  正确命中 {rp['correct']}/{rp['total']}"
	      f"（exact slug {rp['exact_slug']}；漏命中 {len(rp['missed'])}）")
	print(f"泛化集:  命中 {vp['hit']}/{vp['total']}"
	      f" | 正确模板 {vp['correct']}/{vp['total']}"
	      f"（{variant_rate:.1%}）"
	      f" | 跨模板误命中 {len(vp['cross_template'])} | 未命中 {vp['missed']}")
	print(f"降档: {metrics['downgraded']}；命中分级分布: {metrics['match_kind_of_hits']}")
	print(f"零命中模板（有卡且 correct=0）: {len(zero_hit_templates)} 个 -> {zero_hit_templates}")
	if no_card_templates:
		print(f"（另有无卡模板 {len(no_card_templates)} 个: {no_card_templates}——变体无从正确命中，不计零命中）")

	args.out.parent.mkdir(parents=True, exist_ok=True)
	args.out.write_text(
		json.dumps(
			{
				"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
				"catalog_size": len(catalog),
				"host_key": args.host_key,
				"metrics": metrics,
				"zero_hit_templates": zero_hit_templates,
				"no_card_templates": no_card_templates,
				"per_template": {str(k): v for k, v in sorted(per_template.items())},
				"tasks": rows,
			},
			ensure_ascii=False,
			indent=2,
		),
		encoding="utf-8",
	)
	print(f"明细已写 {args.out}（含全部 reason，误命中/漏命中逐例复核用）")

	if args.gate:
		ok = (
			rp["correct"] == GATE_REPLAY_TOTAL == rp["total"]
			and variant_rate >= GATE_VARIANT_RATE
		)
		print(f"\n门槛判定: {'PASS' if ok else 'FAIL'}"
		      f"（要求 回放 {GATE_REPLAY_TOTAL}/{GATE_REPLAY_TOTAL} + 泛化 ≥{GATE_VARIANT_RATE:.0%}）")
		return 0 if ok else 1
	return 0


def _dist(rows: list[dict], key: str) -> dict:
	"""rows 上某字段的取值分布（None 也计）。"""
	out: dict[str, int] = {}
	for r in rows:
		k = str(r[key])
		out[k] = out.get(k, 0) + 1
	return out


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
