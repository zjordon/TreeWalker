# issue #192 实施方案：select_dropdown 多选 values 一次设全 + 替换语义明示

- 日期：2026-09-19；分支 `fix/192-select-multiple`（自 master `3b9d55a`）
- 依据：`docs/bug-fix/192-select-multiple-analysis.md`（机制/证据链/方向对比）
- 范围：native `<select multiple>` 的 `values` 列表参数（一次设全语义）+ 模型侧三处描述 + quirks 卡纠错；后置不做见 §7

## 0. 现象 → 改动映射

| 问题（分析文档 §1-§3） | 改动 | 模块 |
|---|---|---|
| `_SELECT_OPTION_JS` 三连写全是替换语义，multiple 下连调只剩最后一个 | **A** 新 `_SELECT_OPTION_MULTI_JS` + `set_select_option_multi`（只用 `option.selected` 整组设置 + 选中集回读验证） | session 层 |
| LLM 无多选通道，被迫手写 JS（700/701） | **B** `SelectDropdownParams.values` 字段 + `_action_select_dropdown` 守卫/路由 | params + action 层 |
| quirks 卡教错（"连续调用 4 次"）+ 动作描述未示替换语义 | **C** DROPDOWN_RULES 第 4 条 + models.py description + quirks.md 第 2 条改写 | prompt + skill |
| 回归风险与真机验收 | **D** 测试扩展（session/action/params/prompt 四层）+ 699 真机探针 | tests + examples |

## 1. A：session 层多选写路径

### A1 `_SELECT_OPTION_MULTI_JS`（`session.py`，与 `_SELECT_OPTION_JS` 并列）

```js
function(targetTexts) {
    const element = this;
    if (!element || element.tagName.toLowerCase() !== 'select') {
        return { success: false, error: 'Element is not a <select>' };
    }
    // 单选 select 误用 values → 明确纠错（LLM 一步自纠的安全网，不用 value=/values= 猜）
    if (!element.multiple) {
        return { success: false, error: 'Element is not a multi-select (no multiple attribute); pass a single value= instead' };
    }
    if (!Array.isArray(targetTexts) || targetTexts.length === 0) {
        return { success: false, error: 'values must be a non-empty list' };
    }
    const targets = [];
    const seen = {};
    for (const t of targetTexts) {
        const k = (t || '').toLowerCase();
        if (k && !seen[k]) { seen[k] = 1; targets.push(k); }
    }
    const options = Array.from(element.options);
    const matches = function(o) {
        const textLower = (o.text || '').trim().toLowerCase();
        const valueLower = (o.value || '').toLowerCase();
        return targets.indexOf(textLower) !== -1 || targets.indexOf(valueLower) !== -1;
    };
    // all-or-nothing：任一 target 无匹配 option → 不写值，missed + availableOptions 回显
    const missed = targets.filter(function(t) {
        return !options.some(function(o) {
            return (o.text || '').trim().toLowerCase() === t || (o.value || '').toLowerCase() === t;
        });
    });
    if (missed.length > 0) {
        return {
            success: false,
            error: 'Options not found: ' + missed.join(', '),
            missed: missed,
            availableOptions: options.map(o => ({ text: (o.text || '').trim(), value: o.value })),
        };
    }
    const matched = options.filter(matches);
    element.focus();
    // 唯一写法：整组设置 selected（§2 的三条替换路径——value=/selectedIndex=——全部禁用）
    for (const o of options) o.selected = matches(o);
    element.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
    element.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
    element.blur();
    // 回读验证用选中集（multiple 的 element.value 只返回第一项，不能用作验证）
    const selectedNow = options.filter(o => o.selected);
    if (!(matched.every(o => o.selected) && selectedNow.length === matched.length)) {
        return {
            success: false,
            error: 'Selection was set but reverted by page framework.',
            availableOptions: options.map(o => ({ text: (o.text || '').trim(), value: o.value, selected: o.selected })),
        };
    }
    return {
        success: true,
        message: 'Selected ' + matched.length + ' options: ' + matched.map(o => (o.text || '').trim()).join(', '),
        values: matched.map(o => o.value),
    };
}
```

设计决策（对照分析文档 §5）：

- **一次设全**：`o.selected = matches(o)` 对全部 option 求值——目标是选中、非目标取消，幂等且与调用前状态无关；
- **all-or-nothing**：部分 miss 不写值（`missed` 列表 + `availableOptions` 供 action 层软回显），避免"写了一半"的中间态；
- **不做 click fallback**：multiple 的框架回退场景未证实存在，回读失败直接结构化 error（可见性优先；真实需求出现再补全手势多选点击）；
- **事件整组一次**：input/change 各 dispatch 一次（非逐 option）；
- **`values` 回显**：成功返回匹配到的 option value 列表（text/value 双通道匹配时以 DOM 真值为准）。

### A2 G11 懒加载重试提取共用（`_lazy_select_call`）

`set_select_option`（`session.py:4285`）的 resolveNode→callFunctionOn→全空重试块与 multi 方法结构相同。提取：

```python
async def _lazy_select_call(self, object_id: str, function_declaration: str, arguments: list[dict]) -> dict:
    """callFunctionOn + G11 全空重试（set_select_option / set_select_option_multi 共用）。

    全空谓词（success=False 且 availableOptions 非空且每项 text/value 都空白）→
    focus() + sleep 1.0s + 重跑一次。单选/多选同谓词。
    """
```

- `set_select_option` 重构为：resolveNode → `_lazy_select_call(原 _SELECT_OPTION_JS)` → selectionReverted click fallback（不动）；
- `set_select_option_multi`：resolveNode → `_lazy_select_call(_SELECT_OPTION_MULTI_JS)` → 原样返回；
- 安全网：现有 `TestSetSelectOption`（4 例）+ `TestSetSelectOptionLazyLoadRetry`（5 例）断言的是 CDP 调用形状（await_count / arguments / resolveNode 参数），不是内部结构——重构后行为不变应全数通过；若有失败即重构引入回归，当场修复而非改测试。

### A3 `set_select_option_multi`

```python
async def set_select_option_multi(self, backend_node_id: int, values: list[str]) -> dict:
    """Set the whole selection of a <select multiple> identified by backendNodeId.

    issue #192：单选链的三连写（value=/selected=/selectedIndex=）对 multiple 全是
    替换语义——本方法只用 option.selected 整组设置，一次设全（幂等）。all-or-nothing：
    任一值 miss 不写值。回读验证比对选中集（element.value 只返回第一项）。
    返回 dict 与 set_select_option 同形（success/message/values/error/missed/availableOptions）。
    Raises on CDP/JS error（caller wraps）。
    """
```

签名/返回与 `set_select_option` 对齐（`values` 替代 `value`），action 层三段处理（成功/miss 软回显/裸 error）复用同构逻辑。

## 2. B：params + action 层

### B1 `SelectDropdownParams`（`models.py:267`）

```python
class SelectDropdownParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(description="ID of the select element, shown in brackets in the DOM tree")
    value: str | None = Field(
        default=None,
        description="Option value to select (single option). Each call REPLACES the selection.",
    )
    values: list[str] | None = Field(
        default=None,
        description="For <select multiple> only: ALL wanted option values in ONE call — "
        "replaces the whole selection (repeated single-value calls keep only the last one)",
    )

    @model_validator(mode="after")
    def _value_xor_values(self):
        if (self.value is None) == (self.values is None):
            raise ValueError("pass exactly one of value (single) or values (multi-select)")
        return self
```

- `value` 必填→Optional：`extra="forbid"` + 二选一由 validator 表达（validator 不进 JSON schema，LLM 侧靠 description + handler 守卫兜底）；
- ⚠️ 已知架构（registry 不用 param_model 校验 execute 路径）：validator 只护直接构造/测试；execute 路径的守卫在 B2。

### B2 `_action_select_dropdown` 守卫与路由（`actions.py:1850`）

现有 `value = params["value"]`（`actions.py:1878`）直接下标——LLM 只传 values 时 KeyError。改为：

```python
value = params.get("value")
values = params.get("values")
# 运行时守卫（registry 不校验 execute 路径，双层防线与 replace_file 的 min_length+if not old 同型）
if value is not None and values is not None:
    return ActionResult(error="Pass either value (single option) or values (multi-select), not both")
if value is None and values is None:
    return ActionResult(error="select_dropdown requires value (single option) or values (multi-select list)")
if values is not None and (not isinstance(values, list) or not values
                           or not all(isinstance(v, str) and v for v in values)):
    return ActionResult(error="values must be a non-empty list of non-empty strings")
```

dispatch 分支（`actions.py:1882` try 块内）：

```python
if values is not None:
    if tag != "SELECT":
        return ActionResult(error="values (multi-select) is only supported for native <select multiple>; "
                                  "for this element use value= (single option)")
    result = await browser.set_select_option_multi(backend_id, values)
elif tag == "SELECT":
    result = await browser.set_select_option(backend_id, value)   # 单选路径零改动
...  # 其余分支不变
```

三段回显对 multi 的适配：

- 成功：`extracted_content` 用 result message（"Selected 3 options: ..."）；`long_term_memory = f"Selected {json.dumps(values)} in {desc}"`（`_describe_dropdown` 复用）；
- miss：availableOptions 回显复用现有行格式；提示语 `Use the values in select_dropdown(index={index}, values=[...])`（**独立于单选提示语**——现有测试断言单选 endswith `value=...)`，不得改动）；missed 列表拼进 error 行；
- 裸 error：透传。

### B3 零影响清单（核验过，不改）

| 触点 | 现状 | 影响 |
|---|---|---|
| `rerun.py:627/737` | 动作名白名单 + params 透传历史 | 历史只有单值 value，透传不变 |
| `recorder/event_mapper.py:46` | 只产 `{"value": str}` | 手工录制多选（ctrl+click）不在映射范围，维持 |
| combobox/aria/custom setter | 单选设计 | `values` 到达前已被 B2 显式拦截 |

## 3. C：模型侧三处 + quirks

### C1 DROPDOWN_RULES（`system_prompt.py:116`）追加第 4 条

```
4. On a multi-select (`<select multiple>`) pass ALL wanted options in ONE call \
as values=[...] — each call replaces the whole selection, so repeated \
single-value calls keep only the last one.
```

### C2 models.py ACTION 表 description（`models.py:696`）

追加：`For <select multiple>, pass all wanted options at once as values=[...]`。

### C3（可选增强，真机冒烟后定）dropdown_options 多选提示

`_action_dropdown_options` native 分支回显若能低成本带上 multiple 标志（读侧 JS 返回 `element.multiple`），追加一行 `multi-select: pass all wanted options as values=[...]`。改动面大（`fetch_select_options` 返回是裸 list）则放弃——B2 的 JS 守卫（非 multiple 明确纠错）已是安全网。

### C4 quirks.md 第 2 条改写（`domain-skills/localhost_7780/tasks/create-spring-sale-price-rule/quirks.md`）

```
2. **customer_group_ids 是多选 `<select>`**（stage new_2，multiselectable=true，
   4 个 option value=0/1/2/3）。多选必须用 `select_dropdown(index, values=[...])`
   一次传全部目标组——每次调用是整组替换语义，逐次单选只剩最后一个
   （旧教法"连续调用 4 次、各传一组"已废止，照做会丢选中）。website_ids 只有
   一个选项，value/values 均可。
```

## 4. D：测试（`tests/test_select_dropdown.py` 扩展 + `tests/test_models_params` / `test_system_prompt.py`）

全部 mock CDP 边界（`client.send.DOM.resolveNode` + `Runtime.callFunctionOn`），零真机依赖。**桩值纪律（#185 durable 教训）**：multi miss 用例的 availableOptions 桩须用真实协议形状，且选"防护移除时恰好放行"的风险形态（例：missed 桩含真实存在的组名，若 all-or-nothing 防护被删，该用例恰好会静默通过半写态）。

### D1 session 层 `TestSetSelectOptionMulti`

| # | 用例 | 断言 |
|---|---|---|
| 1 | multi 成功 | 一次 callFunctionOn；`arguments == [{"value": ["general","wholesale","retailer"]}]`（CDP 数组参数形状）；resolveNode 用目标 backendNodeId；dict 透传 |
| 2 | 部分 miss（all-or-nothing） | 返回 `missed` + `availableOptions`，success=False；仅 1 次 callFunctionOn（不重试不回退） |
| 3 | G11 全空触发重试 | focus + sleep 1.0 + 重跑（3 次 callFunctionOn 链），重试成功返回 |
| 4 | 非 multiple select | JS 守卫错误透传（error 含 "not a multi-select"） |
| 5 | 回读失败（框架回退） | 原样返回结构化 error；**不触发** click fallback（await_count==1） |
| 6 | 空 values | JS 守卫错误（action 层守卫的前置防线） |

### D2 action 层 `TestSelectDropdownMulti`（新增类）

| # | 用例 | 断言 |
|---|---|---|
| 7 | SELECT + values 路由 | `set_select_option_multi.assert_awaited_once_with(backend_id, values)`；不碰 `set_select_option`/dispatcher |
| 8 | SELECT + values 成功回显 | extracted 含 message；memory 含 `json.dumps(values)` + `[SELECT]` + index |
| 9 | multi miss 软回显 | availableOptions 行格式 + endswith `values=[...])`；error 为 None |
| 10 | 非 SELECT + values | 显式 error（"only supported for native"）；不调任何 setter |
| 11 | value+values 同传 / 两者皆无 / 空列表 / 非字符串元素 | 四个守卫 error；不碰 browser |
| 12 | 单选零回归 | 现有 `TestSelectDropdownAction`（6 例）+ `TestSelectDropdownDispatch`（8 例）+ `TestSetSelectOption`/`LazyLoadRetry`（9 例）**不改一行全过** |

### D3 params / prompt

| # | 用例 | 断言 |
|---|---|---|
| 13 | `SelectDropdownParams` 直接构造：只 values ✓ / 只 value ✓ / 两者皆无 ✗ / 两者同传 ✗ | ValidationError 于后两者 |
| 14 | `test_system_prompt.py`：DROPDOWN_RULES 含 `values=[...]` 条目 | prompt 拼接含新第 4 条 |

## 5. 验证步骤

1. `uv run python -m pytest tests/test_select_dropdown.py tests/test_system_prompt.py -x -v`（模块级）；
2. `uv run python -m pytest tests/ -x -v` 全量（基线 2571+，覆盖率 ≥ 现状 89%）；
3. 真机冒烟（Chrome 9223，Magento localhost:7780 admin）：新探针 `examples/debug_192_select_multiple.py`——
   - 打开 new Cart Price Rule 页；
   - `select_dropdown(index=<customer_group_ids>, values=["General","Wholesale","Retailer"])` 单次调用；
   - Save → 回到编辑页回读三组全 selected；
   - 硬校验：`docker exec <mysql> mysql ...` 直查 `sales_rule_customer_group` 三行齐全（分析文档 §6.7）；
   - 顺带单选回归：任一单选下拉（如 website_ids 用 value=）零漂移；
   - GLM 产出率观察：真跑一轮 agent 驱动（非探针直调），看 LLM 能否自发产出 values=[...]（风险 §6.1 的实证）。

## 6. 风险

1. **GLM 列表参数产出率**：schema 是 `list[str]`，GLM 可能仍退回多次单选。兜底链：C1/C2 description 显式教 + B2 JS 守卫一步自纠 + 退回多次单选=现状（只剩最后一个，不更差）。真机冒烟第 5.3 步实证后再定是否加 C3。
2. **重构 set_select_option 提取 `_lazy_select_call`**：行为不变的重构，安全网是 D2#12 的 15 个既有用例；失败即回退重构（multi 方法内联复制 G11 块，多 ~25 行）。
3. **value 必填→Optional 的 schema 漂移**：LLM 可能两个都不传 → B2 守卫显式 error 回显，一步自纠；检查 tools 侧 schema 生成测试是否断言必填性（有则同步更新）。
4. **不做"清空全部"**（values=[] 拒绝）与**不做 multi click fallback**：均为首版边界（分析文档 §5/§7），error 可见即可。

## 7. 后置不做

- aria/custom/combobox 的多选扩参（`aria-multiselectable` 场景评测集未出现）；
- multi click fallback（框架回退在 multiple 上未证实存在）；
- `values=[]` 清空语义；
- C3 dropdown_options 读侧 multiple 提示（视真机冒烟 §5.3 结果）；
- 评测仓 699 全链路重跑（Actions 折叠区 KO 链是独立环境问题，不在本 issue 范围）。
