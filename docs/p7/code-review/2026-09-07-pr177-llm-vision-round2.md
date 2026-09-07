# Code Review（第二轮）：PR #177（feat/175-llm-vision）

- **日期**：2026-09-07
- **目标**：`feat/175-llm-vision` 分支（同[第一轮](2026-09-07-pr177-llm-vision.md)，独立复跑）
- **范围**：PR #177 全量 diff（low 档：单遍、仅 hunk 内运行时正确性）
- **方法**：低档单遍 + 本轮人工逐条实证（核源码/调用链/默认值）
- **验证结论**：2 条候选 → **1 CONFIRMED + 1 REJECTED（前提不成立，降为加固项）**

## Findings

### 1. CONFIRMED — `llm_screenshot_size` 在 load 时按 `LLM_MODEL` env 快照一次，而视觉门每步按**运行时活模型**判定——fallback 单向切换到视觉模型后门开但 size 仍 None，每步发全分辨率截图

- **位置**：`src/tree_walker/config.py:436-441`（快照）；`src/tree_walker/agent/step.py:420`（活模型门）；`src/tree_walker/llm/client.py:87-99`（fallback 换模型）
- **类别**：correctness / efficiency

**机制链（逐环核实）**：

1. `config.py:439-441`：`use_vision=true` 时 size 仅当 `model_supports_vision(os.environ.get("LLM_MODEL", "glm-5.1"))` 才设 `(1400, 850)`；默认 `glm-5.1` 非视觉 → size=None。
2. `step.py:420`：`_vision_gate_open()` 每步查 `getattr(self.llm, "model", None)`——**运行时**模型。
3. `client.py:87-99`：`_try_switch_to_fallback` 单向 `self.model = self._fallback_model`（`FALLBACK_LLM_MODEL` env 可配，`config.py:567`）。
4. `image_utils.py:38`：`resize_screenshot_bytes(data, None)` 原样返回。

**失败场景**：`AGENT_USE_VISION=true` + `LLM_MODEL` 为文本模型（或未设，默认 glm-5.1）+ `FALLBACK_LLM_MODEL` 配成视觉模型（claude-* / glm-5.3-flash）→ 一次限流/API 错误后永久切到 fallback → 门开、size=None → 此后每步发未降采样原图 b64（~MB 级），token/带宽暴涨，恰违背本 PR screenshot.md 自述的"降采样是必要缓解"。程序化注入视觉模型（不走 env）同理。正常路径（env 配视觉模型、或 examples 直连构造显式给 size，如 `vision_mode.py:75`）不受影响——这也是漏网原因。

**建议**（另派修复 agent 参考）：降采样目标改为门控处按活模型解析（如 `_vision_gate_open` 开时 `self._llm_screenshot_size or default_size`），或 client 换模型时同步通知 step 侧重估 size。

### 2. REJECTED（前提不成立）— 候选称 `_save_step_screenshot` 中 `Path(self.rerun_history_dir)` 在 rerun_history_dir 为 None（"未设置默认值"）时抛 TypeError 逃过 `except OSError`、挂掉 _finalize

- **位置**：`src/tree_walker/agent/step.py:1413-1429`
- **类别**：correctness（候选）

**实证反驳**：

- `rerun_history_dir` 的未设置默认值是**字符串** `"rerun-history"`（`config.py:170` dataclass 默认、`config.py:492` env 加载默认 `os.environ.get("AGENT_RERUN_HISTORY_DIR", "rerun-history")`），不是 None。
- 全仓库（src/tests/examples）grep 无任何 `rerun_history_dir=None` 赋值；`Agent.__init__` 恒从 settings 拷贝（`agent.py:84`）；测试 `test_vision_channel.py:448` 也给 str。
- `AGENT_RERUN_HISTORY_DIR=""` → `Path("")` = `Path(".")`，合法不抛。

**残余价值（降为加固项，非本 PR 必修）**：`_save_step_screenshot` docstring 承诺"**任何** IO 问题不得挂 _finalize"，但只捕 `OSError`——若未来字段被置 None/类型漂移，`TypeError`/`AttributeError` 会穿透违诺。加固法：入口 `if not self.rerun_history_dir: return None` 或改捕 `Exception`。留档不阻断合并。

## 结论

- **#1 建议修复后合并**（逻辑失配真实、触发面为 fallback/注入路径，影响成本与文档承诺）；修法小（门控处兜底默认尺寸）。
- **#2 留档**（前提不成立；加固建议供后续）。

两轮 low 档（第一轮 0 发现 + 本轮 2 候选实证后 1 存活）hunk 内一致性尚可；如需覆盖跨 hunk 状态机/并发语义再上 high 档。
