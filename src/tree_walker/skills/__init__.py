"""Domain skill loading.

Reads ``domain-skills/<host>/{_sop,selectors,quirks}.md`` and renders them
into injectable text for the agent's state message (``[Domain Skill]`` section).
Path convention aligned with TreeForge ``adapters/treewalker_adapter.py``.
"""
from tree_walker.skills.loader import SkillLoader

__all__ = ["SkillLoader"]
