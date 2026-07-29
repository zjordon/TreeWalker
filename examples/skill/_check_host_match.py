"""临时验证：示例 URL 的 host 与 skill 目录是否匹配。"""
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tree_walker.browser.url_utils import extract_host
from tree_walker.skills import SkillLoader

urls = [
    "https://member.bilibili.com/platform/home",
    "https://member.bilibili.com/platform/upload/video/frame",
    "https://www.bilibili.com/",
]
print("=== extract_host ===")
for u in urls:
    print(f"  {u}\n     -> {extract_host(u)!r}")

print("\n=== SkillLoader.load_for_host（domain-skills/ 下实际目录）===")
loader = SkillLoader("domain-skills")
for h in ("member.bilibili.com", "www.bilibili.com"):
    t = loader.load_for_host(h)
    print(f"  {h!r} -> {len(t)} 字符 {'(有内容 ✓)' if t else '(空——目录不存在 ✗)'}")
