"""Decision attribution prompt fragment for structured LLM reasoning."""


def get_decision_attribution_prompt() -> str:
    return (
        "\n## 决策规范\n"
        "\n"
        "在 evaluation_previous_goal 中按以下格式输出你的决策过程：\n"
        "目标：<当前步的目标>\n"
        "候选：A(描述)、B(描述)、C(描述)\n"
        "选择：A\n"
        "原因：<为什么选这个>\n"
        "预期：<预期结果>\n"
    )
