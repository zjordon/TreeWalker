"""TUI 重放相关纯函数单测（不起 Textual）。

TUI 交互本身难单测；这里只测两个模块级纯函数：
``_record_filename``（录制文件名）与 ``_parse_rerun_command``（/rerun 命令解析）。
路径校验（绝对/越界）由 ``Agent.load_and_rerun`` 负责，不在本文件覆盖范围。
"""

from __future__ import annotations

from datetime import datetime

from tree_walker.tui.app import _parse_rerun_command, _record_filename


# ── _record_filename ───────────────────────────────────────────────────


def test_record_filename_format():
	assert _record_filename(datetime(2026, 7, 1, 14, 30)) == "202607011430.json"
	# 月/日/时/分 补零
	assert _record_filename(datetime(2026, 1, 2, 3, 4)) == "202601020304.json"


def test_record_filename_uses_now_when_none():
	name = _record_filename()
	assert name.endswith(".json")
	assert len(name) == len("yyyyMMddhhmm.json")  # 4+2+2+2+2 + ".json"


# ── _parse_rerun_command ───────────────────────────────────────────────


def test_parse_rerun_command_with_vars():
	assert _parse_rerun_command("/rerun a.json x=1 y=2") == ("a.json", {"x": "1", "y": "2"})


def test_parse_rerun_command_no_vars():
	assert _parse_rerun_command("/rerun a.json") == ("a.json", {})


def test_parse_rerun_command_no_args_returns_empty():
	assert _parse_rerun_command("/rerun") == ("", {})
	assert _parse_rerun_command("/rerun   ") == ("", {})


def test_parse_rerun_command_not_rerun_returns_none():
	assert _parse_rerun_command("去搜索一下") is None
	assert _parse_rerun_command("/clear") is None     # 其它斜杠命令不拦截
	assert _parse_rerun_command("") is None


def test_parse_rerun_command_passes_absolute_to_validation():
	# 绝对路径 / `..` 原样返回；校验由 load_and_rerun → resolve_rerun_path 负责
	assert _parse_rerun_command("/rerun /abs/x.json") == ("/abs/x.json", {})
	assert _parse_rerun_command("/rerun ../escape.json") == ("../escape.json", {})
