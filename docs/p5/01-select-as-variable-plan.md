# P5：原生 `<select>` 选择动作变量化（agent history + 手工标注）

> 2026-08-07 重写。范围经两次调整：① 目标从「手工录制 history」改为「**agent 自探 history**」；
> ② 标识从「label 优先 + 自动检测」改为「**手工标注**」。
> 结论：**agent 路径零代码改动即通**——现有 P4 手工标注机制已覆盖 select_dropdown.value 整条链。
> 原录制向方案（value+label、录制端采集、自动检测分支）已废弃，见文末「历史」。

## Context（目标与范围怎么定的）

ROADMAP P5：让下拉框「选择即值」的动作被识别为变量、CSV 批量重放时按行替换。

范围演进（两次调整，均有实测支撑）：

1. **agent history，非手工录制**。手工录制功能认定为「只做简单录制、不再继续优化」；变量替换面向 **agent 自己探索产生的 history**。
2. **手工标注，非自动检测**。`variable_detector._detect_from_attributes`（`variable_detector.py:62-112`）的关键词名单只覆盖 `country/state/city/email/phone/name/address/zip/...`——`department/position/color` 这类最典型的表单 select 全部漏检，自动检测实用性低；最终必依赖手工标注，故不投入自动检测。

TreeWalker 的 `select_dropdown`（24 动作词表）本就契合业界主流（browser-use / Skyvern / Stagehand / Selenium / Playwright 都有独立 select action），P5 不是「要不要 select」，而是把其**结果变量化**——在 agent 路径上这已被现有机制覆盖。

## 关键事实（实测确认，非推断）

1. **agent 的 `select_dropdown.value` 放的是 option 可见文本，不是 value 属性码**：
   - `rerun-history/agent_history.json`：`value="Technical Support"`（该 option 的 value 属性其实是 `"support"`，见 result 回显 `Selected option: Technical Support (value: support)`）
   - `rerun-history/form_filling-pingkai.json`：`value="架构师"`
   - ⇒ **agent 路径没有 label 概念**——`value` 本身就是人可读、CSV 友好的选中键。原方案「value=码、label=文本」的前提只存在于手工录制（录制器抓 `select.value` 拿到码），agent 不存在此问题。
2. **`browser.set_select_option` 按 text OR value 匹配**（`session.py`，原方案「关键事实 #1」已确认）⇒ CSV 里写人可读文本，重放按 text 命中；写 value 码也能命中。
3. **agent history 的 `interacted_element` 带 `attributes.{id,name}` + `ax_name`**（如 `{id:"department", name:"department", ax_name:"Department"}`）——可供手工标注时人眼识别（不用于自动检测）。

## 为什么零代码改动（逐链坐实）

「手工标注 → 变量合并 → CSV 替换 → 重放」四环均由现有代码覆盖：

| 环节 | 现有机制 | 位置 |
|---|---|---|
| 标注 | 编辑器 `strFields` = 除 `index` 外的字符串字段 ⇒ select_dropdown 的 `value` 直接可标；`mark()` 取 `strFields[0]` 绑定 | `history_editor_ui/src/components/ActionEditor.tsx:40-42, 60-72` |
| 合并 | `merge_variable_sources(detect, manual)` 把 `history.manual_variables` 并入变量集 ⇒ CSV 列头含该变量 | `variable_detector.py:146-162`；`rerun.py:316, 339, 1508-1509` |
| 替换 | `_substitute_in_dict` 字段无关、精确整串：`original_value` 整串命中即换；手工标注「天然绕过精确整串匹配漏子串盲区」 | `rerun.py:72-87, 1495-1529` |
| 重放 | `set_select_option` 按 text OR value 匹配；CSV 写人可读文本即按 text 选 | `session.py`；执行入口 `actions.py` `_action_select_dropdown` |

## 怎么用

1. 用 history 编辑器（`examples/serve_history_editor.py`）打开 agent 产出的 history。
2. 选中 `select_dropdown` 步，点「标注为变量」，给变量名（如 `department`）——`value` 字段自动成为标注目标，`original_value` = 当前 value（agent 写的可见文本）。
3. 按 `merge_variable_sources` 提示的列头（detect ∪ manual）写 CSV，`department` 列填**目标 option 的可见文本**（重放按 text 匹配）。
4. `batch_rerun` 逐行重放：每行把 `department` 替换成该行文本，`set_select_option` 按文本选上。

## 验证（已过）

- **替换侧**：`examples/p5_select_manual_var_verify.py` —— 在真 agent_history.json 上注入手工变量 → 合并 → 模拟 CSV 替换，确认 `params.value` 被精确替换（`Technical Support → Sales`）。
- **live e2e**：`examples/p5_select_e2e_live.py` —— 裁 agent_history 的 department select 步为单步、注入手工变量、`load_and_rerun(variables={"department":"General Information"})`，在真页 `reference-number-form.html` 重放，回显 `Selected option: General Information (value: general)`，确认真实 `<select>` 选中替换项。

## 明确不在范围

- **录制端 label 采集**（扩展 `onSelect` 加 label、`event_mapper` label 映射）—— 录制不再优化。
- **`SelectDropdownParams.label` 字段 / 执行端 prefer-label-over-value / 编辑器默认落 label** —— agent 无 label，全部不需要。
- **`variable_detector` 的 select 自动检测分支** —— 手工标注替代，不做关键词/id-name 自动提取。
- **自定义下拉（AntD/Semi 等）** —— `onSelect` 只匹配原生 `<SELECT>`，自定义走 `role=option` click，是后续增量。
- **checkbox / radio** —— 业界无成功先例（Skyvern 自认失败），变量语义需另设计。

## 配套

- [`02-native-select-test-sites.md`](./02-native-select-test-sites.md) —— 原生 select 测试站点（含为何 2026 年公网直出 select 稀缺的复盘）。
- [`fixtures/native-select-fixture.html`](./fixtures/native-select-fixture.html) —— 原生 select demo 页（各 case 注释；可用作 agent 探索或人工测试目标）。
- [`03-serve-fixture-locally.md`](./03-serve-fixture-locally.md) —— 用内置 `http.server` 本地托管 fixture 的步骤（含给 agent example 复用常驻 URL、nginx 可选）。
- [`04-select-record-replay-mechanism.md`](./04-select-record-replay-mechanism.md) —— 录制（`select_dropdown` 工具 + `set_select_option` 的 text/value 匹配）与重放（CSV 变量替换 + 指纹重定位）的原理图解与代码索引。
- [`05-p5-takeaways-and-custom-dropdown-bridge.md`](./05-p5-takeaways-and-custom-dropdown-bridge.md) —— P5 关键知识总结 + 向「自定义下拉录制重放」下一阶段的桥接判断点。

## 历史

本文件 2026-08-04 版本是**录制为中心**的方案：value+label 双标、录制端 `onSelect` 采 label、`SelectDropdownParams` 加 label 字段、执行端 label 优先、`variable_detector` 加 select 自动检测分支、编辑器默认落 label。该方案在 2026-08-07 经两次范围调整（agent history + 手工标注）后**整体废弃**——agent 的 value 本就是可见文本，label 机器无对象可作用；自动检测实用性不足。原版含一份「业界做法调研」（Selenium/Playwright/browser-use/Skyvern 的 select 建模、label-vs-value、checkbox/radio 证据），论证了 TreeWalker 的 `select_dropdown` 站对主流派，结论仍成立；细节见 git 历史本文件 2026-08-04 版本。
