"""Example: CSV 批量重放——录制一次，喂 N 行数据跑 N 次（P4 子任务 2）。

工作流：读已录制历史 → 读 CSV（列头=变量名）→ batch_rerun 逐行重放 → 打印汇总。
变量名来自 detect_variables（自动检测）+ 编辑器手动标注（history.manual_variables）；
CSV 缺列 = 该变量用历史原值（宽容）。

Prerequisites:
1. uv sync
2. chrome --remote-debugging-port=9223
3. 设置 ZHIPU_API_KEY
4. 已有一份录制历史（如 examples/features/rerun_history.py 产出的 agent_history.json）

Usage:
    ZHIPU_API_KEY=your_key uv run python examples/csv_rerun.py agent_history.json data.csv
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tree_walker import Agent, BrowserSession, LLMClient
from tree_walker.agent.variable_detector import (
    detect_variables_in_history,
    merge_variable_sources,
)
from tree_walker.agent.views import AgentHistoryList
from tree_walker.config import load_settings,_fetch_ws_url


async def main(history_file: str, csv_path: str) -> None:
    settings = load_settings()
    if not settings.llm.api_key:
        print("Error: Set ZHIPU_API_KEY environment variable")
        sys.exit(1)
    ws_url = _fetch_ws_url("127.0.0.1", 9223)
    if not ws_url:
        print("Error: Chrome 未以 --remote-debugging-port=9223 启动")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    llm = LLMClient(settings.llm)
    browser = BrowserSession(ws_url=ws_url)
    agent = Agent(task="", llm=llm, browser=browser, settings=settings.agent)

    # 提示期望的 CSV 列头（detect ∪ manual）
    history = AgentHistoryList.load_from_file(agent.rerun_path(history_file))
    var_sources = merge_variable_sources(
        detect_variables_in_history(history), history.manual_variables
    )
    if var_sources:
        print(f"期望 CSV 列头: {','.join(var_sources)}")
    else:
        print("(历史无可替换变量，CSV 数据不会生效)")

    print(f"\n=== 批量重放 {history_file} ← {csv_path} ===")
    results = await agent.batch_rerun(history_file, csv_path)

    ok = sum(1 for r in results if r.success)
    print(f"\n📊 {ok}/{len(results)} 行成功")
    for r in results:
        mark = "✓" if r.success else "✗"
        detail = r.extracted_content or r.error or ""
        print(f"  {mark} 行{r.row_index}: {r.variables} → {detail}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: uv run python examples/csv_rerun.py <history.json> <data.csv>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
