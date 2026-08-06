# pingkai.cn 联系表单选职位——dropdown 工具提示词修复（+ 偶发布局延迟记录）

> 📌 [issue #157](https://github.com/zjordon/TreeWalker/issues/157)。
>
> **本 issue 实际改动：dropdown 工具提示词**（系统提示加引导 + 描述补 combobox），已实施。
> 另发现一个**偶发**的表单布局延迟现象（dom_snapshot 行为正确），**不修**——最近多次测试基本顺利，不值得为此加防御等待。

## 本 issue 的修复：dropdown 工具提示词（已实施）

### 现象
选职位（Radix `role=combobox`）时，模型默认反复 `click` combobox button，而不是用专用工具 `dropdown_options` / `select_dropdown`，要试好几次。

### 根因
1. **系统提示无 dropdown 工具引导**：`prompts/` 全目录 grep `dropdown|combobox` 零命中。对照——`upload_file` 有 `FILE_UPLOAD_RULES`（`system_prompt.py`）引导「禁止 click、改用 upload_file」，模型就守规矩；dropdown 从没做过同样引导。
2. **工具描述没提 combobox**：`models.py` 的 dropdown_options / select_dropdown 描述只写 "dropdown element"，不含 `combobox`。Radix 在 DOM 里是 `[9]<button role=combobox>`，模型扫描述匹配不上。而实现层（`actions.py:1454-1499`）其实全面支持 combobox——能力有，被描述藏起来了。
3. click 字母序居首、描述最泛化，是默认吸引子；`_action_click` 对 combobox 有兜底（降级读 options），所以「试几次最终成功」——运行时补偿模型选错，但已浪费步数。

### 改动
- `src/tree_walker/prompts/system_prompt.py`：新增 `DROPDOWN_RULES`（仿 `FILE_UPLOAD_RULES`），`build_system_prompt` 当 `action_descriptions` 含 dropdown action 时追加。覆盖 native `<select>` / `role=combobox` / `role=listbox` / custom dropdown，明确「不要先 click、combobox 传 button index 给 select_dropdown」。
- `src/tree_walker/tools/models.py`：dropdown_options / select_dropdown 描述补 `role=combobox / role=listbox / custom dropdown`；select_dropdown 加 "Pass the dropdown's index — do not click it first"。
- `tests/test_system_prompt.py`：加 `TestDropdownRules`（追加 / 不追加 / 描述含 combobox）。

### 效果
模型面对 `[9]<button role=combobox>` 直接 `select_dropdown(index=9, ...)`，不再先 click 试错。修后选职位顺利。

## 偶发现象（仅记录，不修）：表单布局延迟

### 现象
navigate 后，表单**有时**短暂处于「在 DOM、CSS 可见（`display=block/visible/opacity=1`）、但无布局框」的状态：`getBoundingClientRect=[0,0,0,0]` + `offsetParent=false` + `offsetWidth=0`（CSS 声明了 `width:100%` 但布局没算出来）。dom_snapshot 用布局框判可见性（dom-snapshot `collector.py:646-647`），rect=0 → **正确**判不可见 → 剪掉整棵表单子树，只剩有布局的 `Loading...` 文本 → agent 看不见表单 → 步数增多。

### 定性：偶发（非高频）
早期小样本曾判高频（4 次中 3 次踩中），但后续多次测试基本顺利，**实为偶发**——疑似冷/热缓存、资源加载时序、或 `content-visibility` + 视口状态的偶发组合。dom_snapshot 行为正确（rect=0 确实不可见），表单也无固有缺陷。

| run | 步数 | step 0 name rect | offsetParent | 结果 |
|---|---|---|---|---|
| A | 4 | `[788,310,616,40]` | true | 顺利 |
| B | 20+ | `[0,0,0,0]` | — | 弯路 |
| 1 | 19 | `[0,0,0,0]` | false | 弯路 |
| 2 | 23 | `[0,0,0,0]` | false | 弯路 |

> 上表是早期小样本，曾据此误判「高频」；后续多次测试基本顺利，修正为**偶发**。

## 调查历程（被推翻的中间假设，留存防重蹈）

1. ❌ navigate 后表单不存在 —— DOM 一直在（JS 查到 8 元素）。
2. ❌ dom_snapshot 抓取 bug / find_elements id 不自洽 —— `examples/debug_find_elements_id_mismatch.py` 证伪，同次一致。
3. ❌ Suspense / display:none hidden —— JS probe：display=block / visible / opacity=1。
4. ❌ actionability 没启用 —— 默认开 + 是元素级动作前检查，不影响 get_state。
5. ❌ 加 wait_settle / networkidle —— 表单 DOM 早就在、readyState 早 complete，无效。
6. ❌❌ 「rect=0 无布局」是确定性根因 —— 被顺利运行推翻，实为偶发。

## 改进建议（暂不实施）

- ~~防御性等布局稳定（等 rect 非零）~~：现象偶发、最近顺利，不值得加等待开销。
- ~~dom_snapshot 保留 rect=0 元素并标记「未布局」~~：开放问题，不动。
- scroll 修法（大 deltaY 打死渲染线程）+ cdp_use 单请求超时：独立问题，另议。
- 回放落盘 selector_map / element_tree_text：已用临时 dump 开关（`step.py` `_maybe_dump_step_dom`，env `AGENT_DEBUG_DUMP_DIR`）解决取证需求。

## 关键源码定位

| 关注点 | 文件:行 |
|---|---|
| **dropdown 工具引导（本 issue 修复）** | `src/tree_walker/prompts/system_prompt.py` `DROPDOWN_RULES` + `build_system_prompt` |
| 对照：upload_file 引导（成熟模式） | `src/tree_walker/prompts/system_prompt.py` `FILE_UPLOAD_RULES` |
| 工具描述（补 combobox） | `src/tree_walker/tools/models.py` `dropdown_options` / `select_dropdown` |
| dom_snapshot rect=0 → invisible（偶发现象的判定点） | `dom-snapshot/src/dom_snapshot/collector.py:646-647` |
| dropdown_options/select_dropdown 的 combobox 实现 | `src/tree_walker/tools/actions.py:1454-1499` |
| 临时 dump 开关（JS probe + tree + HTML） | `src/tree_walker/agent/step.py` `_maybe_dump_step_dom` / `_dump_js_probe` |
| 诊断脚本（id 一致性） | `examples/debug_find_elements_id_mismatch.py` |

> `dom-snapshot` 是独立兄弟仓库（`D:\dev\git\z_jordon\dom-snapshot`），改动需到那边提交。
