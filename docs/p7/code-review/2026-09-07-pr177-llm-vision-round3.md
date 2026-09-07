# Code Review（第三轮）：PR #177（feat/175-llm-vision）

- **日期**：2026-09-07
- **目标**：`feat/175-llm-vision` 分支（含 round2 后合入的修复提交 `970ade0`；前几轮见 [round1](2026-09-07-pr177-llm-vision.md) / [round2](2026-09-07-pr177-llm-vision-round2.md)）
- **范围**：`master...HEAD` 全量 diff（low 档：单遍、仅 hunk 内运行时正确性、上限 4 条）
- **方法**：低档单遍，重点核对修复提交
- **验证结论**：**无发现**（0 条 finding）

## 修复核对（round2 #1）

`970ade0` 在 `_prepare_state_screenshot_b64` 中改为 `self._llm_screenshot_size or _DEFAULT_LLM_SCREENSHOT_SIZE`，常量与 `load_settings` 共享——门开即有尺寸，load 时快照与活模型门的失配闭合。**修复正确，无回归**。

## 排查过的可疑点（均不成立）

1. **round2 CONFIRMED 的 size 快照/活模型失配** — 如上，已修。
2. **`_strip_image_blocks` 原地改共享消息 dict** — 与 fallback 单向永久切模型、step 侧门控逐步重估的语义一致，非 bug。
3. **`model_supports_vision` 正则** `^glm-\d+(\.\d+)*v` 与 `glm-5.3-flash` 显式前缀，无交叉误判（`glm-5.3` 旗舰正确返回 False）。
4. **`_set_state_message` 滤图顺序** — 先 drop 旧 state、再滤剩余 state 的 image block、最后 append 新消息，新消息不受影响。
5. **session.py 超时** `timeout and timeout > 0`（0=关闭防护）与 docstring 一致；`wait_for` 超时被外层 `except Exception` 捕获 → warning + raise → `get_state` 降级 None，链路闭合。
6. **`build_state_blocks(browser_state, screenshot_b64, **state_kwargs)`** — kwargs 不含 `browser_state`，无重复传参。
7. **`_finalize` 落盘为原图** — resize 在 `_prepare_state_screenshot_b64` 内做且不回写 `browser_state.screenshot`，落盘确为原图。

## 覆盖范围

- `src/tree_walker/config.py` — `model_supports_vision` 名单、`_parse_screenshot_size`、`_DEFAULT_LLM_SCREENSHOT_SIZE`、`use_vision`/`llm_screenshot_size`/`screenshot_timeout` 加载
- `src/tree_walker/agent/step.py` — `_vision_gate_open`、`_prepare_state_screenshot_b64`（含 970ade0 兜底）、`_is_new_tab_step_zero`、`_set_state_message` 滤图、`_save_conversation` dump、`_finalize` 截图落盘
- `src/tree_walker/agent/agent.py` / `views.py` — 配置拷贝、`screenshot_path` 字段
- `src/tree_walker/llm/client.py` — 两过滤器 block list 适配、fallback 滤图 `_strip_image_blocks`
- `src/tree_walker/browser/session.py` — `captureScreenshot` 超时降级
- `src/tree_walker/prompts/system_prompt.py` — `build_state_blocks`
- `src/tree_walker/agent/message_compactor.py` — `_content_text` 丢图留文
- `examples/`×4、`ROADMAP.md`、`docs/tools-optimize/screenshot.md`、docs/p7 归档；tests/ hunks 按 low 档规则跳过

## 结论

Hunk 内未发现反转条件、越界、空解引用、缺失 await、错变量、吞错等运行时正确性问题，亦无重复 helper / 死代码。三轮收官：round1 无发现 → round2 1 CONFIRMED + 1 REJECTED → 修复 `970ade0` → 本轮无发现无回归。**建议可合并。**
