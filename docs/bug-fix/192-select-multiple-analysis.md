# issue #192 分析：select_dropdown 对 `<select multiple>` 是替换语义——三次"成功"调用只剩最后一个

- 日期：2026-09-19；issue：#192（OPEN，tool 类）
- 证据：issue 自述（699 Cart Price Rule 客户组五查挂三、700/701 被迫绕行 JS）＋ 本仓代码/文档核验（见 §2/§3）
- 关联：`docs/tools-optimize/select_dropdown_follow_up.md` 风险 6（多选 P2 再议——本 issue 即 P2 到期）；`docs/bug-fix/186-c2-reopen-analysis.md` 形态③（700/701 自洽验证绕行证据）；`docs/p7/env_issue/01-env-issue-failure-analysis.md` 699 条目（Actions 折叠区另一独立失败链）

## 0. 结论速览

**不是未知 bug，是登记在案的已知限制到期**：`_SELECT_OPTION_JS`（`session.py:904`）的三种写值方式（`element.value=` / `option.selected=` / `element.selectedIndex=`）对 `<select multiple>` **每一种都是"先清空再选中"的替换语义**，且单次调用后的回读验证恒通过 → 工具每次都报成功，但选中集永远等于最后一次调用的目标。叠加 quirks 卡教的"多次调用、每次选一个"，构成系统性翻车：**照卡操作必只剩最后一个值**。

三个独立证据源互相印证：

| 证据 | 位置 | 内容 |
|---|---|---|
| quirks 卡教错方法 | `domain-skills/localhost_7780/tasks/create-spring-sale-price-rule/quirks.md` 第 2 条 | 明文教"选中全部 4 组需连续调用 4 次 select_dropdown，各传一个组的值"——把替换语义当累加语义教 |
| 工具层替换语义 | `session.py:918-920`（三连写）+ `session.py:952`（click fallback） | 见 §2 逐行机制 |
| agent 被迫绕行 | 700/701 轮轨迹（#186-c2 形态③） | 用 evaluate JS 注入 `option.selected` 后同通道回读自证——工具通道走不通才出的下策 |

**修复建议：issue 给的方向 1+2 都做**——方向 1（native select 增加 `values` 列表参数，一次设全）补齐能力；方向 2（动作描述明示替换语义 + quirks 卡改写）是必须同步的防复发件。只做方向 2 治标：agent 仍须手写 JS 完成多选保存（form_interaction 发现 6：手写 JS 语法错误每任务烧 ~1 步，关键步撞上即致命）。

## 1. 现象与时间线（699）

Magento 新建 Cart Price Rule 页的 Customer Groups 是 `<select multiple name="customer_group_ids"]>`（4 个 option：NOT LOGGED IN=0 / General=1 / Wholesale=2 / Retailer=3）。任务要求选 General/Wholesale/Retailer 三组。agent 按 quirks 卡操作：

| 步骤 | 调用 | JS 内部状态 | 选中集 | 工具返回 |
|---|---|---|---|---|
| 1 | `select_dropdown(index, "General")` | `element.value=1` 清空全部→选中 General；`selected=true`/`selectedIndex` 幂等强化 | `{General}` | success ✓ |
| 2 | `select_dropdown(index, "Wholesale")` | `element.value=2` **清空 {General}**→选中 Wholesale | `{Wholesale}` | success ✓ |
| 3 | `select_dropdown(index, "Retailer")` | `element.value=3` **清空 {Wholesale}**→选中 Retailer | `{Retailer}` | success ✓ |
| 4 | Save → 列表页 | — | DB 只存了 Retailer | — |

五查挂三（issue 自述；checker 在评测仓，本仓无法复核分项，机制归因如上成立）。每步回读验证 `element.value !== expectedValue`（`session.py:924`）为何不报警：multiple 的 `value` getter 只返回**第一个**选中项——单次调用后恰好只有目标一项，`value === expectedValue` 恒真。**工具报成功是"诚实"的，错在语义（替换）与 agent 预期（累加）不匹配**——正中 #186-c2 的盲区形态：工具成功 → streak 清零、自评无 "?" → 门禁不拦。

## 2. 机制：为什么三条写值路径全是替换

`_SELECT_OPTION_JS`（`session.py:904-946`）按 HTML 规范逐行：

1. **`element.value = expectedValue`**（`session.py:918`）——value 的 setter 语义：先把**所有** option 的 selectedness 置 false，再选中 value 匹配的第一个 option（与 multiple 无关，value 本身就是"单值"视图）。清空的元凶。
2. **`option.selected = true`**（`session.py:919`）——本身是累加，但上一行已清空，最终 = 只剩目标。
3. **`element.selectedIndex = option.index`**（`session.py:920`）——selectedIndex 的 setter 同样是"全部取消再选中该 index"（规范原文对 multiple 不豁免）。第三重替换。

click fallback `_SELECT_OPTION_CLICK_FALLBACK_JS`（`session.py:952-971`）同构：`select.selectedIndex = optionIndex; option.selected = true`——框架回退场景下同样是替换。

**推论：不存在"少踩一行就没事"**——三条路径殊途同归，修复必须换写法（只用 `option.selected` 路径整组设置），不能在现有三连写里做条件分支微调。

其他 setter（aria/custom/subtree/combobox）均为单选设计，无 multiple 概念——本 issue 范围只涉 native `<select>`（见 §4）。

## 3. 证据链补全

- **quirks 卡是稳定的教错源**：`create-spring-sale-price-rule/quirks.md` 第 2 条写明"连续调用 4 次，各传一个组的值（证据中重复调用了 4 次）"——卡从旧轮轨迹提炼，把某轮的调用形态（可能正是翻车轮）当成功经验固化。只要卡片不改，每轮照卡执行必挂。
- **700/701 的绕行即"自洽验证"形态**（#186-c2 形态③）：`evaluate returned "by_fixed|Fixed amount discount|10"`——agent 用 JS 注入字段值后同 evaluate 回读自证。多选字段同理：`option.selected=true` 手写 JS 是唯一活路，绕行成功但烧步且引入手写 JS 质量风险（form_interaction 发现 6）。
- **699 的另一独立失败链要先划清**：env_issue 分析把 699 归"混合"，其 Actions/Conditions 折叠区 Knockout 组件永不实例化（`edit_form` 为 null）是**环境层**问题。多选修复救回客户组链路（基础表单区），不保证 699 整任务翻盘——Actions 链是否仍挂取决于该轮环境状态。**验收按 issue 口径：客户组三组选中全部持久化**，不承诺 699 全绿。

## 4. 影响面

| 写入口 | multiple 行为 | 在本 issue 范围 |
|---|---|---|
| `set_select_option`（native，`_SELECT_OPTION_JS`） | 替换 | **是**——主战场（Magento admin 的 customer_group_ids / website_ids 均为 native multiple） |
| `_SELECT_OPTION_CLICK_FALLBACK_JS` | 替换 | **是**（同改） |
| `set_aria_option` / `set_custom_option` / `_set_subtree_option` | 单选，无 multiple | 否（aria-multiselectable 场景评测集未出现，扩参数另案） |
| `set_combobox_option` | 单选 | 否（同上） |
| recorder 回放（`event_mapper.py:46` 只产 `value` 单值） | 不涉多选 | 否（旧轨迹回放零影响） |

## 5. 修复方向对比

### 方向 1（主体）：native select 增加 `values` 列表参数，"一次设全"语义

- **参数**：`SelectDropdownParams`（`models.py:267`）增 `values: list[str] | None = None`；`value: str` 保持向后兼容。
  - ⚠️ registry 不做运行时校验（已知架构：Pydantic 只管 schema/LLM 侧，handler 自己守卫）——`_action_select_dropdown` 现在直接 `params["value"]`（`actions.py:1878`），LLM 只传 values 会 KeyError。守卫策略：`value`/`values` 二选一，两者同传或都不传 → 显式 error 回显。
  - 空列表 / 非字符串元素 → 显式 error（**不做"清空全部"**——首版拒绝，防 LLM 误清表单；真需清空再议）。
- **语义裁定：一次设全（替换整组），不做累加**。理由：幂等、与当前状态无关、LLM 一次调用表达完整意图；累加语义与既有单选的替换语义交织（第 2 次调用保留还是替换第 1 次？）必然歧义。这也是 issue 原文 `multi: [v1, v2, ...]` 的本意。
- **调度**：`tag == "SELECT"` 且 `values` 非空 → 新 multi 写路径；`values` 传给非 SELECT（combobox/aria/custom）→ 显式 error（"values only supports native <select> multiple"）——不静默取首个，让 LLM 明确知道走错了通道。
- **JS（新 `_SELECT_OPTION_MULTI_JS`，或单 JS 分支）**：
  ```js
  function(targetTexts) {
      const targets = new Set(targetTexts.map(t => (t||'').toLowerCase()));
      const options = Array.from(element.options);
      const matched = options.filter(o =>
          targets.has((o.text||'').trim().toLowerCase()) || targets.has((o.value||'').toLowerCase()));
      // ① all-or-nothing：任一值 miss → 不写值，返回 availableOptions + missed 列表
      if (matched.length !== targets.size) return { success:false, missed:[...], availableOptions:... };
      element.focus();
      for (const o of options) o.selected = matched.indexOf(o) !== -1;   // ② 唯一写法：整组 selected
      element.dispatchEvent(new Event('input', {bubbles:true, cancelable:true}));   // ③ 整组一次事件
      element.dispatchEvent(new Event('change', {bubbles:true, cancelable:true}));
      element.blur();
      // ④ 回读验证用选中集，不用 element.value（multiple 的 value 只返回第一项）
      const selectedNow = options.filter(o=>o.selected).map(o=>o.value);
      ...
  }
  ```
  关键差异点（对照 §2 的三条替换路径）：只用 `option.selected`；**禁用** `element.value=`/`selectedIndex=`；input/change 整组分派一次（非逐 option）；回读比对选中集合。
- **框架回退（selectionReverted）与 G11 懒加载**：multi 路径回读失败 → 首版返回结构化 error + availableOptions，**不做 click fallback**（multiple 的全手势点击回退复杂度高且场景罕见——先让 error 可见，真实需求出现再补）；G11 全空重试谓词形状相同可直接复用。
- **模型侧三处同步**：`models.py` ACTION 表 description（"for multi-select pass values=[...] in one call"）；`system_prompt.py` DROPDOWN_RULES 补一条 multiple 规则；quirks 卡第 2 条改写为 values 用法。

### 方向 2（防复发件）：语义明示 + quirks 纠错

- 动作 description 注明"each call replaces the selection (including `<select multiple>`)"。
- quirks 卡第 2 条：删掉"连续调用 4 次"教法，改为 values 单次设全；卡内备注旧教法已废止的原因（防后人从轨迹再提炼回去）。
- 评测侧提醒：domain-skills 属 skill-on/off 双模式评测——quirks 纠错改变 skill-on 组基线属"修正教错"，合理，但重跑对照时须知晓。

### 为什么不只做方向 2

手写 JS 绕行 = form_interaction 发现 6 的系统性烧步（每任务 ~1 步语法错误，551 的死因正是收尾步语法错误）；且 quirks 只救 localhost_7780，其他站点的 native multiple 仍无工具通道。

## 6. 实施清单（建议 PR 粒度）

1. `session.py`：`_SELECT_OPTION_MULTI_JS` + `set_select_option_multi(backend_id, values)`（含 all-or-nothing miss 回显、选中集回读验证；复用 G11 全空重试谓词）。
2. `models.py`：`SelectDropdownParams.values` 字段 + description 更新（含替换语义明示）。
3. `actions.py` `_action_select_dropdown`：value/values 二选一守卫；SELECT+values → multi 路径；非 SELECT+values → 显式 error。
4. `system_prompt.py` DROPDOWN_RULES：multiple 用 values 一次设全。
5. `domain-skills/.../create-spring-sale-price-rule/quirks.md` 第 2 条改写。
6. 测试（`tests/test_select_dropdown.py` 扩展，全部 mock CDP 边界、零真机依赖）：
   - session 层：multi 成功（一次 callFunctionOn、arguments 带 values）；部分 miss → all-or-nothing 不写值 + missed 列表回显；回读验证用选中集（桩值须用真实协议形状——#185 教训：CDP 桩的 text/description 形状要按协议来）；
   - action 层：SELECT+values 路由 multi；非 SELECT+values 显式 error；value+values 同传 error；空 values error；只传 value 旧路径零回归（现有 6 个 TestSelectDropdownAction 用例不改全过）；
   - 防护型桩值纪律（#185-c2 durable）：multi miss 用例的桩选"防护移除时恰好放行"的风险形态。
7. 真机验收：699 场景（新 Cart Price Rule）values=["General","Wholesale","Retailer"] 单次调用 → Save → 编辑页回读三组全选中（docker exec mysql 直查 sales_rule_customer_group 更硬）；顺手回归一个单选下拉确认零漂移。

## 7. 风险与边界

1. **GLM 能否稳定生成列表参数**：schema 是 list[str]，真机冒烟须验证 values 产出率。兜底链：生成不出 → LLM 退回单选多次调用（行为=现状，只剩最后一个，不比今天差）→ description 里把 values 写法放显眼位。
2. **value/values 同传**：handler 守卫显式 error（`extra="forbid"` 管不到字段间约束）。
3. **click fallback 不做 multi**：回退场景在 multiple 上未证实存在，先 error 可见；避免首版把全手势多选点击做复杂。
4. **不做"清空全部"语义**：values=[] 显式拒绝，防误清。
5. **aria/custom 多选不在本期**：扩参另案（评测集无场景驱动）。
6. **699 整任务不保证翻盘**：Actions 折叠区 KO 链是独立环境问题（§3），验收只锚客户组持久化。
