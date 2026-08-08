# P5 关键知识总结 + 自定义下拉桥接

> 2026-08-08。本次 P5（原生 select 变量化）的关键结论与机制，以及向「自定义下拉框录制重放」下一阶段衔接的判断点。
> 配合 [`01`](./01-select-as-variable-plan.md)～[`04`](./04-select-record-replay-mechanism.md) 阅读。

## 核心结论

**agent 路径下，原生 `<select>` 已天然支持「选中值变量化 + CSV 批量替换」，零 src 改动**——靠现有 P4 手工标注 + 通用替换/重放机制拼出。label、自动检测、录制端采集整套都不需要。

## 三条关键机制（自定义下拉阶段会直接复用）

### 1. `value` = 可见文本 = 选中键 = CSV 替换目标

agent 把 option 的可见文本（「美国」/「架构师」）放进 `select_dropdown.value`；它既是录制时 LLM 的选择键、又是重放时 CSV 可覆盖的变量值。对自定义下拉这只会**更**关键——自定义 option 的 value 属性常拿不到，文本是唯一稳定键。

### 2. 变量化是四环通用链，select 没特权

`记录(select_dropdown) → 手工标注(ManualVariableBinding) → 精确整串替换(_substitute_in_dict) → 指纹重定位(MatchLevel) + text/value 匹配(set_*)`。自定义下拉只要也产 `select_dropdown`，这四环原样适用。

### 3. 执行端多类型分发已存在（重要）

`_action_select_dropdown`（`src/tree_walker/tools/actions.py:1501`）已按类型路由：

- native `<select>` → `set_select_option`（text/value 匹配，已验证）
- combobox（aria-controls/owns）→ `set_combobox_option`
- 其余 aria/custom → `set_dropdown_option`

→ **自定义下拉的「执行侧」大概率已就绪**，下一阶段真正要补的是「识别 + 重定位」，而非「执行」。

## 桥接到自定义下拉（下一阶段的判断点）

| 点 | 现状 / 要查 |
|---|---|
| agent 会把自定义下拉记成 `select_dropdown` 吗？ | combobox 已会（pingkai：`dropdown_options → select_dropdown`）。要验证**纯 div / 无 aria-controls** 的自定义下拉是否也走 select_dropdown，还是会退化成 click。 |
| `set_combobox_option` / `set_dropdown_option` 是否按 text 匹配？ | **下一阶段第一个该查的**——若按 text，value 替换直接复用 P5 全链；若按 value/index，则有 gap。 |
| option 懒渲染 | 自定义 option 不在 DOM（展开才有），重放必须「先 open 再选」，不像 native 一次 `set` 即可。 |
| option 元素无稳定指纹 | 动态 class、懒渲染 → 重定位不能靠 `element_hash`，要靠 `role=option` + 文本匹配（业界通用做法）。 |
| 录制侧（若做） | 无原生 `change` 事件，须从「open 触发器 click + option click」序列**推断** select 语义——与原生最大差异。 |

### 一条捷径

pingkai 的 `dropdown_options → select_dropdown(value="架构师")` agent history 已在 `rerun-history/form_filling-pingkai.json`。下一阶段可直接拿它当夹具，先验证「combobox 的 value 替换 + 重放」通不通——多半比原生还省事（有现成数据，不必现跑 agent）。

## P5 产物索引（下一阶段在此之上接）

- 文档：[`01`](./01-select-as-variable-plan.md) 方案 / [`02`](./02-native-select-test-sites.md) 测试站点 / [`03`](./03-serve-fixture-locally.md) 托管 fixture / [`04`](./04-select-record-replay-mechanism.md) 录制重放原理 + FAQ
- fixture：[`fixtures/native-select-fixture.html`](./fixtures/native-select-fixture.html)（7 case demo 页）
- examples：`p5_agent_records_select.py`（agent 录制确认）/ `p5_select_manual_var_verify.py`（替换侧验证）/ `p5_select_e2e_live.py`（live 重放）
- 测试：`tests/test_rerun_history.py`（+2 回归：select 不自动检测、手工标注 select 替换）
- 现成 agent history 夹具：`rerun-history/agent_history.json`（native department）、`rerun-history/form_filling-pingkai.json`（combobox 职位）
