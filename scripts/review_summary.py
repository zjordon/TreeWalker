"""open-code-review 结果摘要工具（固定脚本，替代每次现写的临时提取脚本）。

用法::

    uv run python scripts/review_summary.py --issue 194            # 该 issue 最新一轮
    uv run python scripts/review_summary.py --issue 194 --all      # 全部轮次
    uv run python scripts/review_summary.py docs/bug-fix/code-review/review-issue-194-5.json
    # 上述任一加 --full：完整输出 suggestion_code / existing_code

输出要点：
- **range（base..head）**：review 只审已提交的 range（manifest.input）——先核对
  head 是否为当前分支最新提交；分支无提交时跑出 skipped 空档；修复未提交时
  跑新一轮会重复报已修 findings（issue #194 各轮实测）。
- 每条 finding 的 [severity/category] path:start-end + 内容 + 建议代码。
- comment 缺 severity 键（review-issue-194-3 曾出现，schema 违约）时显示 `?`，
  可按该 comment 的 thinking 自述原意手工补键。
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW_DIR = REPO_ROOT / "docs" / "bug-fix" / "code-review"


def _short(sha: str | None) -> str:
	return (sha or "?")[:7]


def _round_of(path: Path | str) -> int:
	m = re.search(r"-(\d+)\.json$", Path(path).name)
	return int(m.group(1)) if m else -1


def find_reviews(issue: str, all_rounds: bool) -> list[Path]:
	paths = sorted(
		glob.glob(str(REVIEW_DIR / f"review-issue-{issue}-*.json")),
		key=_round_of,
	)
	if not paths:
		sys.exit(f"no review files for issue {issue} under {REVIEW_DIR}")
	return [Path(p) for p in (paths if all_rounds else paths[-1:])]


def summarize(path: Path, full: bool) -> None:
	m = json.loads(path.read_text(encoding="utf-8"))
	print("=" * 78)
	print(path.name)
	manifest = m.get("manifest", {})
	inp = manifest.get("input", {})
	print("  status : %s   llm: %s" % (m.get("status"), m.get("llm", {}).get("model")))
	print("  message: %s" % m.get("message"))
	print("  range  : %s..%s  (%s..%s)"
		% (_short(inp.get("resolved_base")), _short(inp.get("resolved_head")),
			inp.get("requested_from"), inp.get("requested_head")))
	print("  run_id : %s" % manifest.get("run_id"))

	comments = m.get("comments", [])
	if not comments:
		print("  findings: 0")
		return
	for i, c in enumerate(comments, 1):
		sev = c.get("severity", "?")
		print()
		print("  #%d [%s/%s] %s:%s-%s" % (
			i, sev, c.get("category", "?"), c.get("path", "?"),
			c.get("start_line", "?"), c.get("end_line", "?"),
		))
		if sev == "?":
			print("      ⚠ 该 comment 缺 severity 键（schema 违约）——按其 thinking "
				"自述原意补键")
		print("  " + c.get("content", "").replace("\n", "\n  "))
		sug = c.get("suggestion_code")
		if sug:
			head = sug if full else sug[:600] + ("\n      …(--full 看全文)" if len(sug) > 600 else "")
			print("  --- suggestion:")
			print("  " + head.replace("\n", "\n  "))
		if full and c.get("existing_code"):
			print("  --- existing:")
			print("  " + c["existing_code"].replace("\n", "\n  "))


def main() -> None:
	# Windows 控制台默认 GBK——中文内容会炸 mojibake/UnicodeEncodeError
	try:
		sys.stdout.reconfigure(encoding="utf-8", errors="replace")
	except Exception:
		pass
	parser = argparse.ArgumentParser(description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("paths", nargs="*", help="review JSON 路径（与 --issue 二选一）")
	parser.add_argument("--issue", help="issue 号（自动定位 review 文件）")
	parser.add_argument("--all", action="store_true", help="该 issue 全部轮次（默认最新一轮）")
	parser.add_argument("--full", action="store_true", help="完整输出 suggestion/existing 代码")
	args = parser.parse_args()

	if args.issue:
		paths = find_reviews(args.issue, args.all)
	elif args.paths:
		paths = [Path(p) for p in args.paths]
	else:
		parser.error("需给路径或 --issue")
	for p in paths:
		summarize(p, args.full)


if __name__ == "__main__":
	main()
