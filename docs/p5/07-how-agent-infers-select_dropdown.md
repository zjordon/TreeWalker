# agent 如何从 DOM 推断出用 `select_dropdown`

> 2026-08-09。回答一个问题：模型看到自定义下拉的 DOM（如抖音「请选择合集」触发器）时，是怎么推断出该用 `select_dropdown` 工具的？
> 配合 [`04`](./04-select-record-replay-mechanism.md)（录制重放原理）/ [`05`](./05-p5-takeaways-and-custom-dropdown-bridge.md)（takeaways）/ [`06`](./06-custom-dropdown-support-plan.md)（自定义下拉方案）阅读。

## 核心结论

模型**不是靠 DOM 里的某个特殊标记（role/aria/class）推断的**——它靠「**语义识别 + 系统提示里写死的规则**」三件套合起来决定。这也是为什么它能覆盖非 ARIA 的自定义下拉（判型器认不出的形态，LLM 照样认得）。

## 三件套

### 1. 模型看到的 DOM —— 语义识别

模型拿到的是整棵 DOM 树（`element_tree_text`），自定义下拉的触发器在里面长这样（抖音「选择合集」，截取自 `douyin-select-hj-before.txt`）：

```
[126]<div />
        请选择合集
        [127]<svg />
```

模型从这里读出的是**语义**：「请选择合集」（"请选择…"是中文下拉框通用占位词）+ 一个 `<svg>`（下拉箭头 ▾）= **这是一个下拉框触发器**。这是 LLM 从海量网页训练里学到的通用 UI 模式，**不需要 `role=combobox` / `aria-controls`**——`[126]` 就是它要传给工具的 index。

### 2. `select_dropdown` 工具描述 —— `src/tree_walker/tools/models.py:602-607`

模型拿到的工具表里有 `select_dropdown`，描述明确覆盖「custom dropdown」并说「传 index、别先 click」：

> Select an option in a dropdown element (native `<select>`, role=combobox, role=listbox, **or custom dropdown**). Pass the dropdown's index — do not click it first.

模型由此知道：有这么个工具、它能处理自定义下拉、用法是传 index 别先点。

### 3. 系统提示的 `DROPDOWN_RULES` —— `src/tree_walker/prompts/system_prompt.py:99-111`

写死的硬规则（仅当工具表里有 dropdown 动作时才拼进 system prompt）：

> 1. For any dropdown — native `<select>`, role=combobox, role=listbox, **or a custom dropdown** — use `dropdown_options` to read its options and `select_dropdown` to pick a value.
> 2. Do NOT first `click` the dropdown trigger … These tools expand and read/select options themselves.

## 推断链

```
DOM: [126]<div>请选择合集 + <svg>▾
   →（语义识别）"这是个下拉框"
   →（DROPDOWN_RULES 规则 1）"下拉框用 select_dropdown 选值"
   →（工具描述）"传 index、别先 click"
   → 输出: select_dropdown(index=126, value=<任务里给的值>)
```

`value` 的来源：**任务指令**（agent 的 task 里点名要选哪个，如「创作声明: 个人观点，仅供参考」）。任务没点名时，模型会先调 `dropdown_options` 看选项，再 `select_dropdown`。

## 为什么这套能覆盖「非 ARIA 自定义下拉」

判型器（`fetch_dropdown_options` 的 ARIA→custom-class→子树BFS）靠的是**属性标记**——B 站/抖音这些自定义下拉没 role、没框架 class，所以全 miss（详见 [`06`](./06-custom-dropdown-support-plan.md)）。

但 LLM 做动作选择靠的是**语义**：它读「请选择…+箭头」就认出下拉框，跟 DOM 有没有 ARIA 无关。所以即使下拉是非 ARIA 自定义形态，模型照样会发 `select_dropdown`；而 `select_dropdown`（经 [`06`] 的修复）现在能真正选中它。**这正是用 LLM 做动作选择（而不是写死选择器）的价值。**

## 反例：skill 覆盖通用规则（B站 vs 抖音）

- **抖音**：没有 skill 去覆盖 `DROPDOWN_RULES` → 模型按通用规则走 → 直接 `select_dropdown`（实测 step 5 成功）。
- **B站**（修复前）：`domain-skills/member.bilibili.com/_sop.md` 写着「**点击选择**」（旧写法），**domain skill 优先级盖过通用规则** → 模型去 `click` option，没产 `select_dropdown`（回放文件里就没 value）。把 skill 改成 `select_dropdown` 后两边一致。

> 启示：domain skill 是双刃剑——它能给站点特化指导，但**过时的 skill（如「点击选择」）会盖过通用工具规则**，让 agent 退回老路。skill 要随工具能力更新。

## 代码索引

| 环节 | 位置 |
|---|---|
| `select_dropdown` 工具描述（覆盖 custom dropdown） | `src/tree_walker/tools/models.py:602-607` |
| `DROPDOWN_RULES`（任何下拉都用 select_dropdown、别先 click） | `src/tree_walker/prompts/system_prompt.py:99-111` |
| `DROPDOWN_RULES` 拼接条件（有 dropdown 动作才加） | `src/tree_walker/prompts/system_prompt.py:129-130` |
| `select_dropdown` 执行（含自定义下拉兜底） | `src/tree_walker/tools/actions.py`（`_action_select_dropdown`）/ [`06`](./06-custom-dropdown-support-plan.md) |
