"""issue #187：依赖声明的契约测试。

anthropic 1.x 线的 Messages.create 移除了 temperature/top_p——本仓 pyproject
的 `<1.0` 上界是防"下游环境重新解析落 1.x"的核心契约（eval 仓 66 评测静默
计 0 事故的根因即无上界）。本文件防止未来的"顺手升级"把上界无声抹掉。
"""

from __future__ import annotations

import tomllib
from pathlib import Path


def _anthropic_requirement() -> str:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with open(pyproject, "rb") as f:
        deps = tomllib.load(f)["project"]["dependencies"]
    reqs = [
        d.split("#")[0].strip()
        for d in deps
        if d.split("#")[0].strip().startswith("anthropic")
    ]
    assert reqs, "anthropic 依赖声明缺失（被删除？）"
    return reqs[0]


class TestAnthropicDependencyCap:
    @staticmethod
    def _specifiers(req: str) -> list[str]:
        """剥包名后逗号分段精确取约束子句——子串匹配会放过 "<1.5" 这类看似
        合规实为放行 1.x 的漂移（review #1：下界同理，">=" in req 挡不住降回
        >=0.104；首段与包名粘连，须先剥 "anthropic" 前缀）。"""
        body = req[len("anthropic"):] if req.startswith("anthropic") else req
        return [p.strip() for p in body.split(",")]

    def test_requirement_has_upper_bound_below_1(self):
        req = _anthropic_requirement()
        specs = self._specifiers(req)
        assert "<1.0" in specs, (
            f"anthropic 依赖缺少 <1.0 上界：{req!r}——1.x 的 Messages.create "
            "移除 temperature/top_p（issue #187），无上界会让下游重新解析落 1.x"
        )

    def test_requirement_has_tested_lower_bound(self):
        req = _anthropic_requirement()
        specs = self._specifiers(req)
        assert ">=0.109.0" in specs, (
            f"anthropic 依赖缺少已测试下界（>=0.109.0，本仓全量实证版本）: {req!r}"
        )
