"""P4 CSV 批量执行：Agent.batch_rerun 单测。

覆盖：逐行 load_and_rerun、缺列宽容（部分 variables）、空单元格跳过、全局 variables 合并、
单行异常不中断、空 CSV。用 AsyncMock/替身 load_and_rerun（不真起浏览器）。
设计见 docs/p4/01-可视化编辑与CSV批量执行方案.md（子任务 2 / D5 串行 / D6 列名）。
"""

from __future__ import annotations

import csv
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tree_walker.agent.rerun import RerunMixin
from tree_walker.agent.views import ActionResult


def _write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _agent_with_load(load):
    """batch_rerun 只用 self.load_and_rerun，SimpleNamespace 即可（不绑定 self）。"""
    return SimpleNamespace(load_and_rerun=load)


@pytest.mark.asyncio
async def test_batch_rerun_runs_each_row(tmp_path):
    csv_path = tmp_path / "data.csv"
    _write_csv(csv_path, ["email", "name"],
               [{"email": "a@b.com", "name": "A"}, {"email": "c@d.com", "name": "C"}])
    fake = AsyncMock(return_value=[ActionResult(is_done=True, success=True, extracted_content="ok")])
    agent = _agent_with_load(fake)

    results = await RerunMixin.batch_rerun(agent, "demo.json", csv_path)

    assert len(results) == 2
    assert fake.await_count == 2
    assert all(r.success for r in results)
    assert fake.call_args_list[0].kwargs["variables"] == {"email": "a@b.com", "name": "A"}
    assert fake.call_args_list[1].kwargs["variables"] == {"email": "c@d.com", "name": "C"}


@pytest.mark.asyncio
async def test_batch_rerun_empty_csv(tmp_path):
    csv_path = tmp_path / "empty.csv"
    _write_csv(csv_path, ["email"], [])  # 只有表头
    fake = AsyncMock(return_value=[ActionResult(is_done=True, success=True)])
    agent = _agent_with_load(fake)

    results = await RerunMixin.batch_rerun(agent, "demo.json", csv_path)
    assert results == []
    assert fake.await_count == 0


@pytest.mark.asyncio
async def test_batch_rerun_missing_column_passes_partial(tmp_path):
    """CSV 只有 email 列 → 只注入 email（缺列变量用历史原值，宽容）。"""
    csv_path = tmp_path / "data.csv"
    _write_csv(csv_path, ["email"], [{"email": "a@b.com"}])
    captured = []

    async def fake_load(hf, variables=None, **kw):
        captured.append(variables)
        return [ActionResult(is_done=True, success=True)]

    agent = _agent_with_load(fake_load)
    await RerunMixin.batch_rerun(agent, "demo.json", csv_path)
    assert captured == [{"email": "a@b.com"}]


@pytest.mark.asyncio
async def test_batch_rerun_global_variables_merge(tmp_path):
    """全局 variables 作默认，行值优先。"""
    csv_path = tmp_path / "data.csv"
    _write_csv(csv_path, ["email"], [{"email": "a@b.com"}])
    captured = []

    async def fake_load(hf, variables=None, **kw):
        captured.append(variables)
        return [ActionResult(is_done=True, success=True)]

    agent = _agent_with_load(fake_load)
    await RerunMixin.batch_rerun(agent, "demo.json", csv_path, variables={"name": "Global"})
    assert captured == [{"name": "Global", "email": "a@b.com"}]


@pytest.mark.asyncio
async def test_batch_rerun_blank_cell_skipped(tmp_path):
    """空单元格（""）视为不注入（用原值），避免把空串当变量值。"""
    csv_path = tmp_path / "data.csv"
    _write_csv(csv_path, ["email", "name"], [{"email": "a@b.com", "name": ""}])
    captured = []

    async def fake_load(hf, variables=None, **kw):
        captured.append(variables)
        return [ActionResult(is_done=True, success=True)]

    agent = _agent_with_load(fake_load)
    await RerunMixin.batch_rerun(agent, "demo.json", csv_path)
    assert captured == [{"email": "a@b.com"}]  # name="" 被跳过


@pytest.mark.asyncio
async def test_batch_rerun_row_exception_continues(tmp_path):
    """单行 load_and_rerun 抛异常 → 该行失败但批量继续。"""
    csv_path = tmp_path / "data.csv"
    _write_csv(csv_path, ["email"], [{"email": "a@b.com"}, {"email": "c@d.com"}])
    fake = AsyncMock(side_effect=[RuntimeError("boom"), [ActionResult(is_done=True, success=True)]])
    agent = _agent_with_load(fake)

    results = await RerunMixin.batch_rerun(agent, "demo.json", csv_path)
    assert len(results) == 2
    assert results[0].success is False
    assert "boom" in results[0].error
    assert results[1].success is True
