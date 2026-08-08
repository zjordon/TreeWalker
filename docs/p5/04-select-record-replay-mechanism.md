# 原生 `<select>` 录制与重放原理

> 2026-08-07。讲清「agent 探索时怎么把下拉选中记下来、重放时又怎么按 CSV 换值」的完整机制。
> 配合 [`01-select-as-variable-plan.md`](./01-select-as-variable-plan.md)（方案结论）与
> [`fixtures/native-select-fixture.html`](./fixtures/native-select-fixture.html)（示例页）阅读。
> 全程以 fixture 的 `country` 下拉 `美国 → 日本` 为例。

## 一、Agent 录制时：用什么工具、怎么选中值

### 1. 工具是 `select_dropdown`

模型的 24 个动作里有 `select_dropdown`，参数 `SelectDropdownParams = {index, value}`（`src/tree_walker/tools/models.py:246`）：

- **`index`** = 目标 `<select>` 在 DOM 树里的编号（backendNodeId 衍生）；
- **`value`** = 想选的那个选项。

LLM 要知道选哪个 option——要么**任务指令直接点名**（如「选美国」），要么从页面 DOM 看到（原生 `<option>` 本就在 DOM 里，不像自定义 combobox 要展开才有）。它把想选的**可见文本**填进 `value`（实测：`美国` / `Technical Support` / `架构师`，都是 text、不是 value 码）。

### 2. 执行：`_action_select_dropdown` → `set_select_option`

`_action_select_dropdown`（`src/tree_walker/tools/actions.py:1501`）发现目标是原生 `<select>`（`tag==SELECT`）→ 调 `browser.set_select_option(backend_id, value)`（`src/tree_walker/browser/session.py:3344`）。

### 3.「选中对应的值」靠注入页面的 JS（`_SELECT_OPTION_JS`，`session.py:726`）

`set_select_option` 先用 CDP `DOM.resolveNode` 把 `index` 解析成那个具体 `<select>` 节点，再 `Runtime.callFunctionOn` 把这段 JS 跑在它上面。核心匹配逻辑：

```js
target = value.toLowerCase()                   // "美国"
for (option of select.options) {
    text = option.text.toLowerCase()           // "美国"
    val  = option.value.toLowerCase()          // "us"
    if (text === target || val === target) {   // ← 按 text 或 value，精确相等（大小写不敏感）
        element.value = option.value;          // 把 select 的 value 设成命中 option 的 value("us")
        option.selected = true;
        element.selectedIndex = option.index;
        dispatch input / change / blur;         // 让页面框架感知到变化
        回读 element.value 校验……
    }
}
```

**所以「选中对应的值」=**：JS 拿给定的 `value`（"美国"），在下拉所有 option 里逐个比，谁的**文本**（或 value 属性）等于 "美国"，就把那个 option 设为选中，并派发 `change` 事件。

两个兜底：① 若设完被框架改回（`selectionReverted`）→ 走点击回退（mousedown/click/mouseup 模拟手势）；② option 全空（懒加载）→ `focus()` + 等 1s 重跑一次。

### 4. 落进 history

这一步记成：`model_output.actions[].{name:"select_dropdown", params:{index:3, value:"美国"}}`，外加 `interacted_element[]` 存那个 `<select>` 的**指纹**（`id="country"`、`element_hash`、`x_path`…）。

---

## 二、重放时：CSV 里的值怎么设到下拉框

关键认知：录制下的 `value:"美国"` 只是个**普通字符串**。重放要按 CSV 每行换它，靠的是「变量替换 + 元素重定位」——**都不是 select 专属逻辑**。

### 1. 先把 `value` 手工标成变量

select_dropdown 的 `value` 不在自动检测字段里（`_FIELDS = ("text","query")`，`variable_detector.py:15`），所以在编辑器里选中这一步、把 `value` 标为变量 `country`、`original_value="美国"` → 存进 `history.manual_variables`（`ManualVariableBinding`，`views.py:173`）。

### 2. CSV → 替换映射（精确整串）

`batch_rerun`（`rerun.py:304`）读 CSV，**列头 = 变量名**（`merge_variable_sources(自动检测 ∪ 手工)`，`variable_detector.py:146`，故含 `country`）。一行 `country=日本` → `variables={"country":"日本"}`。

`_substitute_variables_in_history`（`rerun.py:1495`）把变量名经 merge 解出原值：`country → "美国"`，得到 `replacements = {"美国":"日本"}`；再用 `_substitute_in_dict`（`rerun.py:72`，**精确整串**：相等才换、子串不动）扫所有 action 的 params → select_dropdown 步的 `params.value` 从 `"美国"` 变成 `"日本"`。

### 3. 重放：重定位元素 + 用新 value 再选一次

`rerun_history` **不用录制的旧 `index`**（backendNodeId 跨页面加载会漂移），而是拿 `interacted_element` 里的**指纹**，在当前页 selector_map 里六级匹配（`EXACT→STABLE→XPATH→AX_NAME→ATTRIBUTE→CLASS`，`rerun.py` `MatchLevel`）找到那个 `<select>`，取它**当前**的 index；然后执行**同一个** `select_dropdown(value:"日本")` → 又走 `set_select_option` → 那段 JS 在**当前页**的下拉里按 `text=="日本"` 找到对应 option 选中。

> 重放回显形如 `Selected option: General Information (value: general)`——给的是文本，JS 按 text 命中，回显顺带告诉你它真实的 value 属性是 `general`。

**所以 CSV 一行 `日本` → 替换进 params.value → 重放在真实下拉里按 `日本` 文本匹配选中。** 每行 CSV 重复一遍，每行选不同项。

---

## 三、为什么这条链这么干净（关键洞察）

| 维度 | 录制时 | 重放时 |
|---|---|---|
| `value` | LLM 选的键（可见文本） | CSV 可覆盖的变量值（同一个人可读文本） |
| 找元素 | DOM 树里的 `index` | 指纹重定位（`id`/`hash`/`xpath`，稳定）→ 当前 index |
| 选中 option | `set_select_option` 按 text/value 匹配 | **同一个** `set_select_option`，按替换后的 text/value 再匹配一次 |

两个解耦点让它成立：

1. **元素靠指纹重定位、值靠 text/value 在重放当下重新匹配**——所以「换了页面实例」和「换了值」互不干扰；
2. 整条链里**唯一 select 专属的代码就是 `set_select_option`**（录制、重放共用）；变量替换、元素重定位都是通用机制，`select_dropdown` 没有特殊待遇——**这正是 P5 在 agent 路径上零代码改动的根因**。

一句话：`value` 既是录制时 LLM 的选择键、又是重放时 CSV 可替换的变量；中间没有 select 专属的替换逻辑，全靠「字符串精确替换 + 指纹重定位 + text/value 通用匹配」三件已有机制拼出来。

---

## FAQ

**Q：模型不是用 `dropdown_options` 读下拉的所有值吗？为什么回放文件里没有这个动作？**

history 只记录 agent **实际执行过**的动作。`dropdown_options` 用了就会记——实测 pingkai 的 `role=combobox` 职位框，动作序列含 `dropdown_options → select_dropdown`。原生 `<select>` 之所以没有，是因为这次 agent **没调用**它：

- 原生 `<option>` 本就在页面 DOM 里（静态 HTML），且 DOM 树构建时会把 options 以 `compound_components=(...options=A|B|C)` **内联到 select 节点上**，LLM 一眼可见。agent 想选哪个直接 `select_dropdown(value=可见文本)`，`set_select_option` 按 text/value 匹配命中；万一填错，miss 时回显 `availableOptions` 供 LLM 重试——不需要先枚举。何况任务常常直接点名要选的值（如「选美国」）。
- `dropdown_options` 是给**自定义/combobox 下拉**用的。这类控件常是「combobox button + 空 `<select />` 壳」的混合体（如 pingkai 职位框：`[9]<button role=combobox ...>` + `[47]<select />`），真 options 懒渲染在弹层里、没展开就**不在 DOM**，LLM 看到的是空壳——不调 `dropdown_options` 就看不到选项，所以会调、也会被记录。

一句话：分水岭不是「有没有 `<select>` 标签」（两边都有），而是「这个 select 在 LLM 的 DOM 树里**有没有把 options 的值带出来**」——原生 select 内联带出（`compound_components=...options=...`）→ 不调；自定义控件是空壳、options 懒渲染 → 调 `dropdown_options`。

---

## 相关代码索引

| 环节 | 位置 |
|---|---|
| `select_dropdown` 参数模型 | `src/tree_walker/tools/models.py:246` |
| 执行分发（native→set_select_option） | `src/tree_walker/tools/actions.py:1501` |
| 选中值（CDP resolveNode + callFunctionOn） | `src/tree_walker/browser/session.py:3344` |
| 选中值的核心 JS（text/value 匹配） | `src/tree_walker/browser/session.py:726` |
| 自动检测字段白名单（不含 select） | `src/tree_walker/agent/variable_detector.py:15` |
| 手工变量绑定 | `src/tree_walker/agent/views.py:173` |
| 变量源合并（detect ∪ manual） | `src/tree_walker/agent/variable_detector.py:146` |
| 精确整串替换 | `src/tree_walker/agent/rerun.py:72` |
| 变量名→原值→替换 | `src/tree_walker/agent/rerun.py:1495` |
| CSV 批量重放 / 单跑重放 | `src/tree_walker/agent/rerun.py:304` / `:280` |
| 重放元素六级匹配 | `src/tree_walker/agent/rerun.py:55`（`MatchLevel`） |

验证脚本：`examples/p5_agent_records_select.py`（录制侧）、`examples/p5_select_manual_var_verify.py`（替换侧）、`examples/p5_select_e2e_live.py`（live 重放）；回归测试：`tests/test_rerun_history.py`。
