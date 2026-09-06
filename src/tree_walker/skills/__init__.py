"""Domain skill loading.

Reads ``domain-skills/<host>/{_sop,selectors,quirks}.md`` and renders them
into injectable text for the agent's state message (``[Domain Skill]`` section).
Path convention aligned with TreeForge ``adapters/treewalker_adapter.py``.

任务级（P7 路线三，docs/p7/03）：``tasks/<slug>/`` 任务卡按 host_key 扫 catalog、
LLM 匹配命中才装载注入（``[Task Skill]``）——见 task_loader / task_matcher。
"""
from tree_walker.skills.loader import SkillLoader
from tree_walker.skills.task_loader import TaskSkillLoader

__all__ = ["SkillLoader", "TaskSkillLoader"]
