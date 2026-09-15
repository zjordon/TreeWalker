# issue #187 实施方案：anthropic 依赖封顶 <1.0 + 控温契约锁定

- 日期：2026-09-15；分支 `fix/187-anthropic-sdk-cap`（自 master `e3400a2`）
- 依据：`docs/bug-fix/187-anthropic-sdk-version-analysis.md`（v2，含 eval 侧核验修订）§4 P0——方案经 eval 侧确认"可执行，无保留意见"
- 性质：**依赖契约修复**，非功能开发——全部改动 ≤ 4 个文件 + 锁文件

## 0. 请求项 → 改动映射

| issue 请求项 | 改动 | 层级 |
|---|---|---|
| 使 temperature 等采样参数可透传（含声明已测试范围） | **A** pyproject 封顶 `>=0.109,<1.0` + 注释；**B** 定向锁刷新至 0.x；**C** 透传单测钉契约 | P0 |
| 升级时注意 _extract_call / fallback 兼容性 | **D** spec 守护测试（防未来"顺手升级"抹掉 cap）；兼容性本身经 v2 分析 §1/§3 确认无工作量 | P0 |

## 1. A：pyproject 封顶（`pyproject.toml:8`）

```toml
    "cdp-use>=1.4.5",
    # issue #187：1.x 线的 Messages.create 移除 temperature/top_p（采样参数断裂，
    # 且 GLM 兼容端点为 0.x Messages 形状）——上界防止下游环境重新解析落 1.x
    #（eval 仓 66 评测静默计 0 事故即源于无上界解析到 1.4.0）。
    "anthropic>=0.109.0,<1.0",
```

- 下界从 0.104 提到 **0.109**（本仓全量测试实证版本，"已测试的支持范围"的最小声明）；
- 上界 `<1.0` 是本修复的核心契约。

## 2. B：定向锁刷新

```powershell
uv lock --upgrade-package anthropic
uv sync
uv run python -c "import anthropic; print(anthropic.__version__)"
```

- `--upgrade-package` 只动 anthropic，**不整库升级**（避免 uv.lock 全文件漂移）；
- 落点预期 **0.125.0**（0.x 末版，2026-08-19）：cap 内解析器自然取最新。分析文档 v2 偏好 0.122.0（2026-08-13），**差异不影响诉求**（两者 temperature ✅、同 0.x 线；契约由上界保证而非精确版本）——默认接受解析器结果，若需严格 0.122 用临时 `==0.122.0` 约束锁定后回放宽（列为可选变体，默认不做）；
- 落点若为 0.125：顺手在分析文档 §4.2 补一行"实施落点 0.125.0"的说明（避免文档与锁漂移）；
- 验证：`Messages.create` 签名含 temperature（0.122/0.125 均已实测 ✅）。

## 3. C：透传单测（`tests/test_llm_client.py` 追加）

```python
class TestExtractCallSamplingPassthrough:
    """issue #187：_extract_call 是 **create_kwargs 透传封装——temperature 等
    采样参数必须原样到达 messages.create（eval judge 的确定性依赖此契约；
    SDK 1.x 移除该参数的事故让这个契约需要被钉进测试）。"""

    @pytest.mark.asyncio
    async def test_temperature_reaches_create(self):
        # 沿本文件既有 LLMClient + mock Anthropic 模式（TestGetActionIntegration 同款）：
        # client.messages.create 打桩捕获 kwargs → 断言 temperature=0 原样到达
        ...

    @pytest.mark.asyncio
    async def test_extract_call_call_timeout_wraps(self):
        # 顺带覆盖既有行为（回归护栏）：call_timeout 走 asyncio.wait_for 分支
        ...
```

（实现时按本文件 mock 惯例落位；断言点 = 桩的 call kwargs 含 `"temperature": 0`。）

## 4. D：spec 守护测试（新文件 `tests/test_dependency_spec.py`）

```python
"""issue #187：依赖声明的契约测试——防止未来"顺手升级"把 anthropic 上界抹掉。"""

import tomllib
from pathlib import Path


class TestAnthropicDependencyCap:
    def test_requirement_has_upper_bound_below_1(self):
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        with open(pyproject, "rb") as f:
            deps = tomllib.load(f)["project"]["dependencies"]
        reqs = [d for d in deps if d.split("#")[0].strip().startswith("anthropic")]
        assert reqs, "anthropic 依赖声明缺失"
        req = reqs[0].split("#")[0].replace(" ", "")
        assert "<1" in req, f"anthropic 依赖缺少 <1.0 上界（1.x 移除 temperature）: {req!r}"
        assert ">=" in req, f"anthropic 依赖缺少已测试下界: {req!r}"
```

（tomllib 为 stdlib，`requires-python >=3.12` 无需新增依赖。）

## 5. 实施顺序与提交切分

单 PR 两批（用户授权提交时）：

1. **A+B**：pyproject 封顶 + 定向锁刷新（+分析文档落点注记）；
2. **C+D**：两个测试文件。

每批后跑对应测试，末尾全量 `uv run python -m pytest tests/ -x -v --cov`（覆盖率 >85%）。

## 6. 测试与验收清单

- [ ] `uv run python -c "import anthropic; print(anthropic.__version__)"` → 0.122.0/0.125.0（0.x 线内）
- [ ] `inspect.signature(Messages.create)` 含 `temperature`（已实测 ✅，锁后复验一次）
- [ ] 透传单测：`temperature=0` 原样到达 messages.create 桩
- [ ] 守护测试：人为去掉 `<1.0` 时测试变红（实施时手动验证一次再还原）
- [ ] 全量回归绿、覆盖率 >85%
- [ ] **cap 合入之后**（时序关键）：eval 仓新起干净 venv 重解析 tree_walker → anthropic 落 0.x；eval judge 确定性恢复以此次回归为前提（eval 侧随后二选一：降级依赖恢复 temperature=0，或接受默认温度入档）——此项归 eval 仓执行，TW 侧仅在 PR/issue 注明

## 7. 风险与不做

| 项 | 评估 |
|---|---|
| 锁刷新连带升级 | `--upgrade-package anthropic` 定向；anthropic 0.109→0.125 的传递依赖（httpx/pydantic 等已在范围内）无已知破坏面；全量回归兜底 |
| 升级 1.x | **不做**——与 issue 目标直接冲突（1.x 无 temperature）；GLM 兼容端点不认 1.x 平台参数 |
| 迁移 output_config.effort | 不做（语义不同 + 端点不支持），等真实需求 |
| 本仓调用点加 temperature 形参 | 不做——`_extract_call` 透传已可用，需求方（eval judge）是外部调用方 |
| 下游仍可 `pip install anthropic==1.5` 强装 | spec 管不住强制覆盖；但"重新解析默认落 1.x"的事故路径已封死（这正是事故根因） |

## 8. 缩进与规范

- pyproject TOML 行内注释随列表元素（仓库已有先例：dom-snapshot 行）；
- `tests/test_llm_client.py` 按文件现状缩进（打开实测）；新文件 `tests/test_dependency_spec.py` 用 4 空格；
- 不主动 commit/push（CLAUDE.md）。
