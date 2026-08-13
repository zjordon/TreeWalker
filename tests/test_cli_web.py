"""P6 M6：tw-web CLI 子命令单测。

--help 由 click 直接处理，不进 web() 函数体（不 import run_server、不起服务），可安全测试。
"""

from __future__ import annotations

from click.testing import CliRunner

from tree_walker.cli import web


def test_web_help_registers_options():
	r = CliRunner().invoke(web, ["--help"])
	assert r.exit_code == 0
	out = r.output
	for needle in ("--host", "--port", "--cdp-port", "--history-dir"):
		assert needle in out, f"{needle} 缺失"


def test_web_defaults_advertised():
	r = CliRunner().invoke(web, ["--help"])
	assert r.exit_code == 0
	assert "8766" in r.output  # 默认 port
	assert "9223" in r.output  # 默认 cdp-port（web/重放路径约定端口；tw-tui 用 9222）
