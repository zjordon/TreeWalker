"""冒烟验证：skill 注入链路是否真的工作（不依赖 Chrome / LLM / 网络）。

用真实的 ``domain-skills/www.bilibili.com/`` 跑一遍：
  1. SkillLoader 读四文件并渲染
  2. extract_host 解析 host
  3. Agent._build_skill_description 取到真实文本
  4. 开关关时调用点短路（即使文件在也不注入）
  5. build_state_message 渲染 [Domain Skill] 段

跑：uv run python examples/skill/_smoke_skill_injection.py
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker.skills import SkillLoader
from tree_walker.browser.url_utils import extract_host
from tree_walker.browser.views import BrowserStateSummary, SerializedDOMState
from tree_walker.config import AgentSettings
from tree_walker.agent.agent import Agent
from tree_walker.prompts.system_prompt import build_state_message

SKILLS_DIR = "domain-skills"
# 用 agent 实际访问的 host：示例 task 从 member.bilibili.com/platform/home 起步，全程 member 子域。
# （早期版本用 www.bilibili.com 验证是假阳性——www 与实际 task URL 的 host 不匹配。）
HOST = "member.bilibili.com"
URL = "https://member.bilibili.com/platform/upload/video/frame"

print("=" * 60)
print("1. SkillLoader.load_for_host —— 真实读 domain-skills/www.bilibili.com/")
print("=" * 60)
loader = SkillLoader(SKILLS_DIR)
text = loader.load_for_host(HOST)
tags = ("[SOP]", "[SELECTORS]", "[QUIRKS]")
print(f"host={HOST}  加载到 {len(text)} 字符")
for t in tags:
    print(f"  {'✓' if t in text else '✗ 缺失'} {t}")
if all(t in text for t in tags):
    print(f"  顺序 OK: [SOP]@{text.index('[SOP]')} < [SELECTORS]@{text.index('[SELECTORS]')} "
          f"< [QUIRKS]@{text.index('[QUIRKS]')}")

print("\n" + "=" * 60)
print("2. extract_host")
print("=" * 60)
print(f"  {URL}\n    -> {extract_host(URL)}")

print("\n" + "=" * 60)
print("3. Agent._build_skill_description (enable_skill_injection=True)")
print("=" * 60)
agent_on = Agent(
    task="投稿B站",
    llm=MagicMock(),
    browser=MagicMock(),
    settings=AgentSettings(enable_skill_injection=True, skills_dir=SKILLS_DIR),
)
desc = agent_on._build_skill_description(URL)
print(f"  返回类型: {type(desc).__name__}, 长度: {len(desc) if desc else 0} 字符")
print(f"  含 [SOP]/[SELECTORS]: {('[SOP]' in desc) and ('[SELECTORS]' in desc) if desc else False}")

print("\n" + "=" * 60)
print("4. 调用点门控 (enable_skill_injection=False → 短路，即使文件在)")
print("=" * 60)
agent_off = Agent(
    task="投稿B站",
    llm=MagicMock(),
    browser=MagicMock(),
    settings=AgentSettings(enable_skill_injection=False, skills_dir=SKILLS_DIR),
)
# 复刻 step.py _prepare_context 的调用点三元
skill_desc_off = (
    agent_off._build_skill_description(URL)
    if agent_off._enable_skill_injection
    else None
)
print(f"  _enable_skill_injection = {agent_off._enable_skill_injection}")
print(f"  调用点 skill_desc = {skill_desc_off!r}  ({'不注入 ✓' if skill_desc_off is None else '异常！'})")

print("\n" + "=" * 60)
print("5. build_state_message —— [Domain Skill] 段是否渲染进 state message")
print("=" * 60)
bs = BrowserStateSummary(
    url=URL,
    title="bilibili",
    dom_state=SerializedDOMState(_root=None, selector_map={}, element_tree_text="dom"),
)
msg = build_state_message(browser_state=bs, task="投稿B站视频", skill_description=desc)
has_section = "[Domain Skill]" in msg
print(f"  state message 含 [Domain Skill]: {'✓' if has_section else '✗'}")
# 段位置：Task < Domain Skill < Available Secrets（这里无 secrets，看 Task < Domain Skill）
print(f"  [Task]@{msg.find('[Task]')} < [Domain Skill]@{msg.find('[Domain Skill]')}: "
      f"{msg.find('[Task]') < msg.find('[Domain Skill]')}")
print("\n  [Domain Skill] 段预览（前 500 字）:")
start = msg.find("[Domain Skill]")
preview = msg[start:start + 500]
for line in preview.splitlines():
    print(f"    {line}")

print("\n" + "=" * 60)
print("结论：", "✓ skill 真的注入了" if has_section and desc else "✗ 注入失败")
print("=" * 60)
