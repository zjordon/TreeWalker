"""A/B 实测：无 skill vs 有 skill，agent 在 B 站发视频。

- baseline:  enable_skill_injection=False（即使 domain-skills/ 在也不注入）
- treatment: enable_skill_injection=True + domain-skills/www.bilibili.com/ 就位

判据（方案 docs/skill-injection-design.md §九）：成功率提升 ≥ 20pp 或 步数减少 ≥ 30%。

⚠️ 真操作 B 站（发草稿、失败可能残留）+ 消耗 LLM 额度 + 每次约 5-15 分钟。

Usage:
    uv run python examples/skill/ab_test_bilibili.py            # 冒烟：每组 1 次
    uv run python examples/skill/ab_test_bilibili.py --n 3      # 每组 3 次
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.config import AgentSettings, load_settings

VIDEO = r"D:\Videos\test\final\2026-04-29-20-41-59.mp4"
COVER = r"D:\dev\git\claude\skills-deom\ppt\browser-use\横封面.png"


def make_task() -> str:
    return (
        "帮我到B站创作者中心发一个视频，信息如下，先暂存为草稿不要直接发布\n"
        "\n"
        "B站创作者中心网址:https://member.bilibili.com/platform/home\n"
        f"我要发的视频在'{VIDEO}'\n"
        f"封面图片在'{COVER}'\n"
        "标题为：ai浏览器第五期-browse-use\n"
        "创作声明:个人观点，仅供参考\n"
        "分区:科技数码\n"
        "标签:浏览器Agent\n"
        "简介：ai浏览器第五期-browse-use"
    )


def make_settings(base: AgentSettings, enable_skill: bool) -> AgentSettings:
    return AgentSettings(
        max_steps=base.max_steps,
        max_failures=base.max_failures,
        llm_timeout=base.llm_timeout,
        action_timeout=base.action_timeout,
        reconnect_timeout=base.reconnect_timeout,
        truncation=base.truncation,
        enable_planning=True,
        allowed_upload_paths=[VIDEO, COVER],
        enable_skill_injection=enable_skill,
        skills_dir="domain-skills",
    )


async def run_once(label: str, app_settings, agent_settings: AgentSettings, task: str,
                   history_file: str | None = None) -> dict:
    """跑一轮 agent 探索，返回 done/success/steps/elapsed/final_result。

    history_file 给定时落盘回放文件（相对 rerun_history_dir），便于事后复现/分析
    某一轮（如异常轮）。失败不影响指标记录。
    """
    llm = LLMClient(app_settings.llm)
    browser = BrowserSession(app_settings.browser)
    agent = Agent(task=task, llm=llm, browser=browser, settings=agent_settings)
    t0 = time.monotonic()
    history = await agent.run()
    elapsed = time.monotonic() - t0
    saved = None
    if history_file:
        try:
            agent.save_history(history_file)
            saved = history_file
        except Exception as e:
            saved = f"SAVE_FAILED: {e!r}"
    return {
        "label": label,
        "done": history.is_done(),
        "success": history.is_successful(),
        "steps": len(history.history),
        "elapsed_s": round(elapsed, 1),
        "final_result": (history.final_result() or "")[:300],
        "history_file": saved,
    }


async def main():
    parser = argparse.ArgumentParser(description="A/B: 无 skill vs 有 skill")
    parser.add_argument("--n", type=int, default=1, help="每组跑几次（冒烟默认 1）")
    parser.add_argument("--only-treatment", action="store_true",
                        help="只跑 treatment（skill on），跳过 baseline（用历史 baseline 数据对比）")
    args = parser.parse_args()

    settings = load_settings()
    if not settings.llm.api_key:
        print("Error: Set ZHIPU_API_KEY"); sys.exit(1)
    if not settings.browser.ws_url:
        print("Error: Chrome 未以 --remote-debugging-port=9222 启动"); sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    task = make_task()
    results = {"baseline": [], "treatment": []}
    groups = [("treatment", True)] if args.only_treatment else [("baseline", False), ("treatment", True)]

    for group_name, enable_skill in groups:
        for i in range(args.n):
            label = f"{group_name}#{i + 1} (skill={'on' if enable_skill else 'off'})"
            history_file = f"ab_{group_name}_{i + 1}.json"
            print(f"\n{'=' * 60}\n>>> {label}\n{'=' * 60}", flush=True)
            s = make_settings(settings.agent, enable_skill)
            try:
                r = await run_once(label, settings, s, task, history_file=history_file)
            except Exception as e:
                r = {"label": label, "done": False, "success": False, "steps": 0,
                     "elapsed_s": 0, "final_result": f"EXCEPTION: {e!r}", "history_file": None}
            results[group_name].append(r)
            print(f"\n[{label}] done={r['done']} success={r['success']} "
                  f"steps={r['steps']} elapsed={r['elapsed_s']}s "
                  f"history={r.get('history_file')}", flush=True)
            if r["final_result"]:
                print(f"  final_result: {r['final_result']}", flush=True)

    # ── 汇总 ──
    print(f"\n{'=' * 60}\nA/B 汇总\n{'=' * 60}")
    summary = {}
    for g in [name for name, _ in groups]:
        runs = results[g]
        if not runs:
            continue
        sr = sum(1 for r in runs if r["success"]) / len(runs)
        avg_steps = sum(r["steps"] for r in runs) / len(runs)
        avg_t = sum(r["elapsed_s"] for r in runs) / len(runs)
        summary[g] = {"success_rate": sr, "avg_steps": avg_steps, "avg_elapsed": avg_t, "n": len(runs)}
        print(f"  {g:10s}: 成功率={sr * 100:3.0f}%  平均步数={avg_steps:5.1f}  "
              f"平均耗时={avg_t:6.1f}s  (n={len(runs)})")

    # ── 判据 ──
    print(f"\n{'=' * 60}\n判据（成功率 ≥ +20pp 或 步数 ≤ -30%）\n{'=' * 60}")
    if summary.get("baseline") and summary.get("treatment"):
        pp = (summary["treatment"]["success_rate"] - summary["baseline"]["success_rate"]) * 100
        base_steps = summary["baseline"]["avg_steps"]
        # 负数 = treatment 比 baseline 步数更少（更好）
        step_change = ((summary["treatment"]["avg_steps"] - base_steps) / base_steps * 100
                       if base_steps > 0 else 0.0)
        print(f"  成功率提升: {pp:+.0f}pp   (判据 ≥ +20pp)   {'✓ 达标' if pp >= 20 else '✗ 未达'}")
        print(f"  步数变化:   {step_change:+.0f}%   (判据 ≤ -30%，负=减少)   "
              f"{'✓ 达标' if step_change <= -30 else '✗ 未达'}")
    else:
        print("  （本次只跑 treatment，未跑 baseline——跳过对比；baseline 历史数据见")
        print("    ab_result_handwritten.json / ab_result_distilled_n5.json）")
    if args.n < 3:
        print(f"\n  ⚠️ N={args.n} 是冒烟样本：成功率需 N≥3 才有统计意义；")
        print(f"     本次主要看链路是否通 + skill 是否真注入 + 步数的粗略趋势。")

    out = Path("ab_result.json")
    out.write_text(json.dumps({"results": results, "summary": summary},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n详细结果已写入 {out.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
