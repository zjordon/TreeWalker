"""重放结束的 AI 摘要 prompt（无截图版）。

本方案「无截图」，摘要 LLM 只能依据原文 task + 执行统计 + 各步文字证据判定，
不像 browser-use 那样靠截图视觉判定。详见 docs/rerun_history/06-AI摘要（无截图适配）.md。
"""

from __future__ import annotations


def get_rerun_summary_prompt(
    *,
    original_task: str,
    total_steps: int,
    success_count: int,
    error_count: int,
    evidence: str,
) -> str:
    return f"""你正在分析一次任务重放（rerun）的完成情况。
基于下方【执行证据】（无截图），判断重放是否成功。

原始任务: {original_task}

执行统计:
- 总步数: {total_steps}
- 成功步: {success_count}
- 失败步: {error_count}

执行证据（各步结果摘要）:
{evidence}

请判断:
1. 任务是否按预期完成
2. 最终状态说明了什么
3. 整体完成度（complete/partial/failed）

返回 JSON:
- summary: 重放发生了什么的清晰摘要
- success: 是否成功（true/false）
- completion_status: complete / partial / failed 之一
""".strip()
