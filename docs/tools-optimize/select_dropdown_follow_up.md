# select_dropdown P1 进阶蓝图落地规格（follow-up）

> 本文是 [`select_dropdown.md`](./select_dropdown.md) 末尾「P1 进阶蓝图」（`:523-551`）的**落地实现规格**——把草图升级为可直接照搬实施的「完整代码 + 测试骨架」提案文档。
>
> **本文为提案文档（不改源码）**，与 [`select_dropdown.md`](./select_dropdown.md) / [`dropdown_options.md`](./dropdown_options.md) / [`dropdown_options_follow_up.md`](./dropdown_options_follow_up.md) 同属「提案文档」风格。
>
> 范围：**写侧**（ARIA menu/listbox / custom class / combobox 写选择 / 子树搜索写选择）+ **懒加载重试**（G11，native）+ **G9 扩面**（click 误点 ARIA/combobox/custom 降级读选项）。
>
> 与 [`dropdown_options_follow_up.md`](./dropdown_options_follow_up.md) 是**对称的姊妹篇**：那份做「读侧」（已落地，commit `99f9c5f`），本文做「写侧」——同一套多类型架构的另一半。该文档已明确把写侧标注为「平行工作，本文档不展开」（其 `:24`、`:720`），本文即补齐这部分。

---

## 背景（为什么写这份 follow-up）

[`select_dropdown.md`](./select_dropdown.md) 的 P1 蓝图只画了草图（每项给 session 增量思路 + 引用 browser-use 行号，明确「不在本次落地」）。其后 P0（native `<select>` 完整写选择链：scope 修复 + tag 校验 + 三方式设值 + 读回验证 + 框架回退点击回退 + 选项未命中软回显 + 成功回显）已落地，[`dropdown_options_follow_up.md`](./dropdown_options_follow_up.md) 也把**读侧**的多类型（ARIA/custom/combobox/子树）做完。但 `select_dropdown` 的 **P1 写侧**尚未实现——非 `<select>` 元素当前仍走 `_action_select_dropdown` 的 tag 守卫 hard-reject（`actions.py:1126-1134`），提示 LLM「用 click 手动展开选择」。

本 follow-up 把 6 项草图落到**可行的完整实现规格**：每类型新增 `_SET_*_JS` 写常量 + `set_*_option` session 方法 + 写侧 dispatcher，action 层把 tag 守卫放宽为多类型调度（镜像 `_action_dropdown_options`）。

### ⚠️ 现状纠偏（必须先认清，否则会重复造轮子 / 照搬错误前提）

探查确认：**P0 与读侧 P1 都已落地**。本 follow-up 据此把范围收缩到「纯写侧 + 懒加载 + G9 扩面」，并对蓝图草图做必要纠偏。

| 蓝图项 | 状态 | 证据（行号） |
|---|---|---|
| P0 `select_dropdown` 核心（native 写链） | ✅ **已完成** | `_action_select_dropdown` `actions.py:1108-1165`；`set_select_option` `session.py:2660-2713`；`_SELECT_OPTION_JS`/`_SELECT_OPTION_CLICK_FALLBACK_JS` `session.py:583-650`；`SelectDropdownParams{index,value}` `models.py:172-176`；测试 `tests/test_select_dropdown.py` |
| 读侧多类型（ARIA/custom/combobox/子树） | ✅ **已完成**（姊妹篇） | `_ARIA_OPTIONS_JS`/`_CUSTOM_CLASS_OPTIONS_JS`/`_COMBOBOX_OPTIONS_JS`/`_SUBTREE_SEARCH_JS` `session.py:658-775`；`fetch_dropdown_options`（dispatcher）/`_fetch_aria_options`/`_fetch_custom_class_options`/`expand_and_fetch_combobox_options`/`search_children_for_dropdowns` `session.py:2715-2856`；[`dropdown_options_follow_up.md`](./dropdown_options_follow_up.md) |
| 6. click 误点 **native** select 降级（G9） | ✅ **部分完成**（仅 native） | `_action_click` SELECT 分支 `actions.py:417-425`（调 `fetch_select_options` + `_format_options_result(..., "click-select")`）；**未覆盖 ARIA/combobox/custom** —— 本 follow-up P1f 扩面 |

**蓝图草图的 3 处纠偏**（草图是「不在本次落地」的速记，落地须按读侧已确立的架构调整）：

1. **「在 `_SELECT_OPTION_JS` 内追加 ARIA/custom 分支」→ 纠偏为独立 `_SET_*_JS` 常量 + Python 预分类**。`_SELECT_OPTION_JS`（`session.py:583-625`）开头 `element.tagName.toLowerCase() !== 'select'` 早退守卫阻止原地扩展；读侧 P1 已确立「每类型独立 JS 常量 + Python 廉价预分类 + session dispatcher」架构（见 [`dropdown_options_follow_up.md`](./dropdown_options_follow_up.md) D1），写侧对称沿用，**不把写逻辑塞进 `_SELECT_OPTION_JS`**。
2. **「combobox 写侧镜像 browser-use `_handle_aria_combobox_options`」→ 纠偏为自撰**。browser-use **自身缺 combobox 写侧**（已知不一致，[`select_dropdown.md`](./select_dropdown.md) `:542` 已注）；本 follow-up 从读 flow `expand_and_fetch_combobox_options`（`session.py:2780-2831`）推导写法，标**实验性** + 手测门槛。
3. **「子树 `searchChildrenForSelection(startElement, 4)`」→ 纠偏为两阶段编排**。JS `returnByValue` 会剥离对象身份，无法从读侧 `_SUBTREE_SEARCH_JS` 直接拿到子代 `objectId` 去调 setter。故先复用读侧 dispatcher 判出 `child-depth-N`，再用 `_SUBTREE_LOCATE_JS` 取子代 `objectId`+类型，按类型调 `_SET_ARIA_JS`/`_SET_CUSTOM_JS`（避免把写逻辑复制进第三个常量）。

### browser-use 源码未 vendored

仓库内**无** `default_action_watchdog.py` / `service.py` / `vendor/`（[`dropdown_options_follow_up.md`](./dropdown_options_follow_up.md) `:31-33` 已证）。写侧须从**设计描述 + 读侧已移植的 JS** 推导，不能照搬 vendored 代码。且 browser-use 自身的 ARIA/custom/combobox 写测试**全部 skip**——参照路径未充分验证，这是贯穿本文档的风险信号（尤其 combobox 写侧本就缺失）。

---

## 差距再分析（再对齐表）

| 维度 | browser-use（`default_action_watchdog.py`） | TreeWalker 当前 | 差距（本 follow-up 覆盖） |
|---|---|---|---|
| native `<select>` 写 | 精确绑定 + 读回验证 + 点击回退 | ✅ 已对齐（P0） | — |
| **懒加载重试** | 全空 option 时 `focus()`+sleep+重试一次 | ❌ 全空 option 直接走「未命中」软回显 | **G11（P1a）** |
| **ARIA menu/listbox 写** | `aria-selected=true`+classList+`item.click()`+MouseEvent | ❌ 非 select 走 tag 守卫 error | **G12（P1b）** |
| **custom class 写**（Semantic UI 等） | toggle `selected`/`active` + dispatch `click`+`change` | ❌ 同上 | **G12（P1c）** |
| **combobox 写**（aria-controls 独立 listbox） | **browser-use 缺失**（已知不一致） | ❌ 同上 | **G12（P1d，实验性·自撰）** |
| **子树搜索写** | `searchChildrenForSelection(start, 4)` | ❌ 无 | **G12（P1e）** |
| **click 误点 ARIA/combobox 降级** | click 误点下拉形元素自动读选项 | ⚠️ 仅 native select 降级；ARIA/combobox/custom 仍走真实 click（可能误开遮罩） | **G9 扩面（P1f）** |

---

## 已确认的可复用资产（实现须基于这些，不臆造）

| 资产 | 位置 | 复用方式 |
|---|---|---|
| P0 写链（focus→三方式设值→input/change/blur→读回验证→点击回退） | `_SELECT_OPTION_JS` `session.py:583-625`；`_SELECT_OPTION_CLICK_FALLBACK_JS` `session.py:631-650` | 新 `_SET_*_JS` 的写/验证形状照此（dict 形状一致，见 D2） |
| 读侧 JS 匹配逻辑 | `_ARIA_OPTIONS_JS`/`_CUSTOM_CLASS_OPTIONS_JS`/`_COMBOBOX_OPTIONS_JS` `session.py:658-733` | 新 `_SET_*_JS` 的「找选项」段照此（text/value 大小写不敏感精确匹配 + data-value 取值） |
| 读侧 dispatcher（**写侧分类直接复用**） | `fetch_dropdown_options` `session.py:2760-2778`（返回 `{options, source}`，source None=非下拉） | `set_dropdown_option` 先调它拿 source 再路由（D1，零分类逻辑重复） |
| combobox 读 flow（**写 flow 模板**） | `expand_and_fetch_combobox_options` `session.py:2780-2831`（click 展开→sleep→读→finally 收起） | `set_combobox_option` 镜像：把「读」换「写」，复用展开/收起 |
| CDP resolveNode→callFunctionOn 模式 | `set_select_option` `session.py:2677-2691`；`fetch_select_options` `session.py:2634-2656` | 新 session 方法照抄此结构 |
| `callFunctionOn` 用 `arguments:[{value}]` 传参 | `set_select_option` `session.py:2687`；`_is_element_occluded` `session.py:1620` | 需带参的 setter JS 用（`targetText`/`maxDepth`） |
| 真实点击（展开 combobox）/ 真实按键（Escape 收起） | `click_element(backend_node_id)`；`send_keys("Escape")` | combobox 写 flow 复用（**不手搓 dispatchEvent**） |
| combobox 收起 finally 块 | `expand_and_fetch_combobox_options` `session.py:2817-2831` | 抽 `_collapse_combobox` helper 供读/写共用（D4，行为等价） |
| `callFunctionOn` 无参调用（`this.focus()`/`this.blur()`） | `expand_and_fetch_combobox_options` `session.py:2822-2829`（blur）；懒加载 focus 同形 | P1a 懒加载 `this.focus()` 照此 |
| combobox/autocomplete 探测 | `_is_autocomplete_field(entry) -> (is_combo, needs_js_wait)` `actions.py:634-656` | action 层预分类直接复用 |
| `entry.attributes` 携带 `role`/`aria-controls`/`class` | `views.py` `DEFAULT_INCLUDE_ATTRIBUTES`/`STATIC_ATTRIBUTES`（含 role/aria-controls/aria-owns/class） | action 层读 attribute 判类型（`getattr(entry,"attributes",{}) or {}`） |
| 成功/未命中回显三段处理 | `_action_select_dropdown` `actions.py:1144-1165` | **零改动**复用（D2：所有 setter 同 dict 形状） |
| `_describe_dropdown` | `actions.py:562-584` | 回显描述直接复用 |
| `_format_options_result` + `_EMPTY_OPTIONS_DIAGNOSTIC` | `actions.py:586-614` / `:23-28` | P1f click 降级复用 formatter（已有 `click-select` 键；`click-aria` 等缺失键自然回退 hint-only） |
| 测试模式 | `tests/test_select_dropdown.py`（action 层 `_make_browser` mock `set_*`）；session 层 `BrowserSession.__new__`+CDP mock（仿 `tests/test_dropdown_options.py:294+`） | 新测试照抄；`@pytest.mark.asyncio` 逐方法必加（asyncio_mode=strict，无 pyproject 配置） |

---

## 关键技术决策

### D1｜写侧 dispatcher = 直接复用读侧 dispatcher 分类

新增 `set_dropdown_option(backend_node_id, value) -> dict`：**先调既有 `fetch_dropdown_options`**（`session.py:2760`）拿 `source`（`aria`/`custom`/`child-depth-N`/`None`），再按 source 路由到 `set_aria_option`/`set_custom_option`/`_set_subtree_option`；`source=None` 返回带 `source:None` 的 dict，action 层据此返友好 error。

两种选择：

- **(A) 复用读侧 dispatcher 分类（推荐）**：`set_dropdown_option` 内调 `fetch_dropdown_options` 判型。读写**同一份 JS 判型**（零漂移，靠构造保证而非约定）；写侧零分类逻辑（DRY，调既有已测方法）。代价：分类时读一次选项（丢弃）、setter 再 resolveNode+写一次 —— aria-hit = 2 `resolveNode` + 2 `callFunctionOn`。
- **(B) setter 自带 `notMyType` 标志，dispatcher 顺序试**：每个 `_SET_*_JS` 开头 shape 检查返回 `{success:false, notMyType:true}`（无副作用，早退在匹配循环前）。CDP 更省（aria-hit = 1+1），但分类谓词被复制进每个 setter JS，**与读侧 `_ARIA_OPTIONS_JS` 等有漂移风险**。

> 选 **(A)**：非 native 路径本就罕见且昂贵（combobox ~8 次 CDP），多 1 次 resolveNode 不关键；而「读写分类零漂移」是正确性强保证，值此代价。combobox 因需真实 click/Escape 仍独立为 `set_combobox_option`（Python flow，镜像读侧）。

### D2｜setter 统一 dict 形状（action 层零改动）

所有 `_SET_*_JS` 返回与 `_SELECT_OPTION_JS` 同形：`{success, message?, value?, availableOptions?, error?}`。→ action 层成功回显 / 未命中软回显 `availableOptions` / 裸 error 三段（`_action_select_dropdown:1144-1165`）**对所有 setter 类型字节级一致，零改动**。

- 非 native setter **省略 `selectionReverted`**：div 控件无 `element.value`，读回验证改用 `aria-selected==='true' || classList.contains('selected'|'active')`「是否粘住」。action 层从不读 `selectionReverted`，省略不破坏接口。
- 「未粘住」按普通未命中处理（带 `availableOptions` 软回显），**不引入点击回退**（aria/custom/combobox 的 setter 已含真实 `item.click()`；框架吞掉 click 即报为带选项的 miss，由 LLM 换工具自纠）。

### D3｜独立 `_SET_*_JS` 常量（不扩展 `_SELECT_OPTION_JS`）

见「现状纠偏 1」。`_SELECT_OPTION_JS` 的 `tagName!=='select'` 早退守卫 + 读侧 P1 已确立的「每类型独立常量」架构，决定写侧也用独立 `_SET_ARIA_JS`/`_SET_CUSTOM_JS`/`_SET_COMBOBOX_OPTION_JS`（紧邻读侧 `_*_OPTIONS_JS`，`session.py:775` 之后）。新常量仅做「写」，分类交给 dispatcher。

### D4｜combobox 写侧自撰 + 抽 `_collapse_combobox`（实验性）

browser-use 缺 combobox setter（已知不一致）。`set_combobox_option(backend_node_id, value)` 镜像读 flow `expand_and_fetch_combobox_options`：

1. `await self.click_element(backend_node_id)` 展开（复用 `click_element`，不手搓 `dispatchEvent`）。
2. `await asyncio.sleep(0.5)` 等懒加载（与读侧 D4 一致；轮询推迟）。
3. resolveNode(combobox) → `callFunctionOn(_COMBOBOX_LISTBOX_ID_JS, returnByValue=False)` 取 **listbox 的 objectId**（setter 须跑在 listbox 对象上，而非 combobox——listbox 常是 React Portal 挂在 `document.body` 外的独立元素）。
4. `callFunctionOn(_SET_COMBOBOX_OPTION_JS)` 在 listbox 上写选项。
5. **`finally` 强制收起**：抽 `_collapse_combobox(object_id)`（`send_keys("Escape")` + JS `this.blur()`），读/写 flow 共用（把读侧 `expand_and_fetch_combobox_options:2817-2831` 的 finally 块替换为调此 helper，行为等价 —— D6 零回归）。

> 标**实验性**：CDP `returnByValue=False` 返回 DOM 节点的 RemoteObject shape 是本 follow-up 最高风险点（见「CDP-shape 敏感点」），须实现期验证 + 专属 session 测试 + 手测门槛。

### D5｜子树写侧两阶段编排（不写 `_SET_SUBTREE_JS`）

见「现状纠偏 3」。JS `returnByValue` 剥离对象身份，无法从读侧 `_SUBTREE_SEARCH_JS` 拿子代 objectId。故：dispatcher 经 `fetch_dropdown_options` 判出 `child-depth-N` → `_SUBTREE_LOCATE_JS`（同 BFS+classify，`returnByValue=False` 返回首个命中后代的 RemoteObject + 类型）→ `_set_subtree_option` 按类型调 `_SET_ARIA_JS`/`_SET_CUSTOM_JS`。

> 不写 `_SET_SUBTREE_JS`：会把 `_SET_ARIA_JS`/`_SET_CUSTOM_JS` 的 ~40 行写逻辑复制进第三个常量，维护负担。两阶段多 1 次 resolveNode+callFunctionOn（locate），但子树是最罕见路径（目标非 native/aria/custom/combobox 才走），CDP 成本不关键。

### D6｜读侧零改动（仅一处行为等价重构）

写 dispatcher / combobox flow **复用**读方法做分类与收起，**不修改** `fetch_select_options`/`_fetch_*`/`fetch_dropdown_options`/`_ARIA_OPTIONS_JS` 等内部逻辑。唯一对读侧的触碰：把 `expand_and_fetch_combobox_options` 的 finally 收起块（`session.py:2817-2831`）替换为调 `_collapse_combobox`（**行为等价**，纯 DRY 抽取）→ `dropdown_options` 与 click-SELECT 零回归。

### D7｜G9 扩面复用 formatter 不重路由（仿读侧 D7）

`_action_click` 识别到下拉形元素（native select / `role` in listbox·menu·… / `class` 含 dropdown / combobox）→ 调读侧方法读选项 + `_format_options_result(..., "click-<source>")`，**不重定向**到 `dropdown_options`（click 已「点过」，重路由混淆 LLM 工具选择信号）。`_EMPTY_OPTIONS_DIAGNOSTIC`（`actions.py:23-28`）已有 `click-select` 键；`click-aria`/`click-combobox`/`click-custom` 缺失键自然回退 hint-only（记为次要已知限制，不扩键）。非下拉元素落回真实 click（防 class 假阳性误降级）。

### D8｜不改 `models.py` / `views.py`

`SelectDropdownParams{index, value}`（`models.py:172-176`）不变——`value` 同时承载 text 或 value，大小写不敏感精确匹配（D1 of [`select_dropdown.md`](./select_dropdown.md)）。`ActionResult`（`views.py:8-37`）不引入 `include_extracted_content_only_once`（改动面与风险不成比例）。

---

## 架构与调度流程

### action 层 `_action_select_dropdown`（P1 后重写为多类型调度，镜像 `_action_dropdown_options`）

```text
entry, error = _get_element_by_index(index)
if error: return error
tag = entry.tag_name.upper(); backend_id = entry.backend_node_id
value = params["value"]
is_combo, _ = _is_autocomplete_field(entry)        # 复用 actions.py:634-656
attrs = getattr(entry, "attributes", {}) or {}

try:
  if tag == "SELECT":                              # native：P0 路径零改动
      result = await browser.set_select_option(backend_id, value)
  elif is_combo and (attrs.get("aria-controls") or attrs.get("aria-owns")):   # combobox
      result = await browser.set_combobox_option(backend_id, value)
  else:                                            # aria / custom / 子树：session 写 dispatcher
      result = await browser.set_dropdown_option(backend_id, value)
      if result.get("source") is None:            # 真阴性：非任何已知下拉类型
          return ActionResult(error=f"Index {index} is a [{tag}], not a recognized dropdown ...")
except Exception as e:
  return ActionResult(error=f"Failed to select option: {e}")

# 成功 / 未命中 / 裸 error 三段（与 P0 字节一致，对所有 setter 类型通用 —— D2）
if result.get("success"): return ActionResult(extracted_content=message, long_term_memory=f"Selected {json.dumps(value)} in {desc}")
if result.get("availableOptions"): return <软回显可用选项 + 用法提示>
return ActionResult(error=result.get("error"))
```

> 关键：tag 守卫从 P0 的「`tag != "SELECT"` hard-reject」放宽为多类型调度。**成功/未命中/error 处理段零改动**（D2 的回报）——`source` 字段**仅**用于区分「非下拉」（source=None → 硬 error）与「是下拉但 miss/未粘住」（source 有值 → 软回显），不进入成功/未命中回显。

### session 层写 dispatcher `set_dropdown_option`（复用读侧分类）

```text
classified = await fetch_dropdown_options(backend_node_id)   # 复用读 dispatcher 判型（D1）
source = classified["source"]
if source == "aria":    result = await set_aria_option(bid, value)
elif source == "custom": result = await set_custom_option(bid, value)
elif source startswith "child-depth-": result = await _set_subtree_option(bid, value)
else: return {"success": False, "source": None, "error": "not a recognized dropdown"}
result["source"] = source
return result
```

> combobox 不进此 dispatcher（需真实 click/Escape，由 action 层 Python 预分类直调 `set_combobox_option`），与读侧 `expand_and_fetch_combobox_options` 独立于 `fetch_dropdown_options` 对称。

---

## 分阶段改动（完整代码 + 测试骨架）

> 每阶段独立可交付、各自带测试、低风险优先。依赖：P1a 无依赖 → P1b/P1c 依赖 dispatcher → P1d 独立 Python flow（依赖 `_collapse_combobox` 抽取）→ P1e 依赖 P1b/P1c 的 setter + locator → P1f 依赖读侧（已就绪）。

---

### P1a — 懒加载重试（G11，native-only）

#### session 层：`set_select_option` 内插入重试块

定位：`set_select_option`（`session.py:2660-2713`），在 `selection = result.get(...)`（`:2692`）之后、`if selection.get("selectionReverted"):`（`:2696`）之前插入。**重试先于点击回退**（懒加载是比框架回退更廉价的假设）。

全空判定：`success=False` 且 `availableOptions` 非空列表且每项 text 与 value 都空白。

```python
        selection = result.get("result", {}).get("value", {}) or {}

        # G11 懒加载重试：select 有 option 但全部为空（text 与 value 都空白）→ option
        # 多半异步填充。focus() + sleep 1.0s + 重跑 _SELECT_OPTION_JS 一次。
        # 仅 native（镜像 browser-use default_action_watchdog.py:3509-3547）。
        avail = selection.get("availableOptions") or []
        all_empty = (
            not selection.get("success")
            and isinstance(avail, list)
            and len(avail) > 0
            and all(
                not (o.get("text") or "").strip() and not (o.get("value") or "").strip()
                for o in avail
            )
        )
        if all_empty:
            await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": object_id,
                    "functionDeclaration": "function(){ try{ this.focus(); } catch(e){} }",
                    "returnByValue": True,
                },
                session_id=self.current_session_id,
            )
            await asyncio.sleep(1.0)
            retry = await self.client.send.Runtime.callFunctionOn(
                {
                    "objectId": object_id,
                    "functionDeclaration": _SELECT_OPTION_JS,
                    "arguments": [{"value": value}],
                    "returnByValue": True,
                },
                session_id=self.current_session_id,
            )
            selection = retry.get("result", {}).get("value", {}) or {}

        # Framework reverted the programmatic value set -> click fallback ...
        if selection.get("selectionReverted"):
            ...  # 原逻辑不变
```

> `asyncio` 已在 `session.py` 顶部导入（`expand_and_fetch_combobox_options:2794` 已用）。无新 import。

#### 测试（session 层，扩 `tests/test_select_dropdown.py::TestSetSelectOption`）

```python
class TestSetSelectOptionLazyLoadRetry:
	def _make_session(self, first_value, second_value=None):
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
		# 第一次 _SELECT_OPTION_JS 返回全空；focus() 与重跑分别返回占位
		values = [
			{"result": {"value": first_value}},                       # 首次 _SELECT_OPTION_JS
			{"result": {"value": {}}},                                 # this.focus()
		]
		if second_value is not None:
			values.append({"result": {"value": second_value}})        # 重跑 _SELECT_OPTION_JS
		client.send.Runtime.callFunctionOn = AsyncMock(side_effect=values)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_all_empty_triggers_focus_sleep_retry(self, monkeypatch):
		# mock asyncio.sleep 不真等
		sleeps: list[float] = []
		async def fake_sleep(s): sleeps.append(s)
		monkeypatch.setattr("tree_walker.browser.session.asyncio.sleep", fake_sleep)
		s, client = self._make_session(
			first_value={"success": False, "availableOptions": [{"text": "", "value": ""}]},
			second_value={"success": True, "message": "Selected option: X (value: x)", "value": "x"},
		)
		out = await s.set_select_option(7, "x")
		assert out["success"] is True
		assert 1.0 in sleeps                                   # 重试前 sleep 1.0s
		assert client.send.Runtime.callFunctionOn.await_count == 3   # select + focus + retry

	@pytest.mark.asyncio
	async def test_no_retry_when_options_populated(self):
		# miss 但有真实 availableOptions → 仅 1 次 callFunctionOn，不重试
		s, client = self._make_session(
			first_value={"success": False, "availableOptions": [{"text": "US", "value": "us"}]},
		)
		out = await s.set_select_option(7, "zz")
		assert client.send.Runtime.callFunctionOn.await_count == 1
		assert out["success"] is False

	@pytest.mark.asyncio
	async def test_no_retry_on_success(self):
		s, client = self._make_session(first_value={"success": True, "message": "ok", "value": "x"})
		await s.set_select_option(7, "x")
		assert client.send.Runtime.callFunctionOn.await_count == 1

	@pytest.mark.asyncio
	async def test_retry_once_only(self, monkeypatch):
		async def fake_sleep(s): pass
		monkeypatch.setattr("tree_walker.browser.session.asyncio.sleep", fake_sleep)
		# 重跑仍全空 —— 不第三次重试
		s, client = self._make_session(
			first_value={"success": False, "availableOptions": [{"text": "", "value": ""}]},
			second_value={"success": False, "availableOptions": [{"text": "", "value": ""}]},
		)
		await s.set_select_option(7, "x")
		assert client.send.Runtime.callFunctionOn.await_count == 3   # select + focus + 1 retry
```

> **同步回归**：既有 `TestSetSelectOption` 用例（success/miss/reverted-fallback）须仍绿——重试块只在「全空」谓词为真时介入，常规 miss/success/reverted 不触发。

---

### P1b — ARIA menu / listbox 写（G12）

#### session 层：JS 常量 `_SET_ARIA_JS` + 方法 `set_aria_option`

JS 常量定位：紧邻 `_SUBTREE_SEARCH_JS`（`session.py:775`）之后、`class BrowserSession`（`:778`）之前。匹配段镜像 `_ARIA_OPTIONS_JS`（`:658-679`），写/验证段镜像 `_SELECT_OPTION_JS`（`:583-625`）。

```python
# ARIA menu/listbox 写脚本（写侧对应 _ARIA_OPTIONS_JS）。匹配段镜像 reader（text/value
# 大小写不敏感精确）；写段：单选清兄弟 aria-selected/selected/active → 选目标 → 真实
# item.click() + MouseEvent → 读回验证（div 无 element.value，改查 aria-selected/classList
# 「是否粘住」）。返回与 _SELECT_OPTION_JS 同形 dict（省略 selectionReverted —— D2）。
_SET_ARIA_JS = """
function(targetText) {
	const root = this;
	const role = root.getAttribute('role');
	const isAriaContainer = ['listbox', 'menu', 'menubar', 'tree', 'grid'].indexOf(role) !== -1;
	const hasAriaOptions = !!root.querySelector('[role="option"],[role="menuitem"]');
	if (!isAriaContainer && !hasAriaOptions) {
		return { success: false, error: 'Element is not an ARIA listbox/menu' };
	}
	const all = Array.from(root.querySelectorAll('[role="menuitem"],[role="option"]'));
	const availableOptions = all.map(function(n) {
		return { text: (n.textContent || '').trim(), value: n.getAttribute('data-value') || n.getAttribute('value') || (n.textContent || '').trim() };
	});
	const targetLower = (targetText || '').toLowerCase();
	for (const item of all) {
		const textLower = (item.textContent || '').trim().toLowerCase();
		const valLower = (item.getAttribute('data-value') || item.getAttribute('value') || '').toLowerCase();
		if (textLower === targetLower || valLower === targetLower) {
			root.dispatchEvent(new Event('focus', { bubbles: true, cancelable: true }));
			all.forEach(function(o) {
				o.setAttribute('aria-selected', 'false');
				o.classList.remove('selected');
				o.classList.remove('active');
			});
			item.setAttribute('aria-selected', 'true');
			item.classList.add('selected');
			item.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
			item.click();
			item.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
			root.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
			const stuck = item.getAttribute('aria-selected') === 'true' || item.classList.contains('selected') || item.classList.contains('active');
			const chosenValue = item.getAttribute('data-value') || item.getAttribute('value') || (item.textContent || '').trim();
			if (!stuck) {
				return {
					success: false,
					error: 'Selection was set but not retained. The dropdown may require a different interaction.',
					targetOption: { text: (item.textContent || '').trim(), value: chosenValue },
					availableOptions: availableOptions,
				};
			}
			return {
				success: true,
				message: 'Selected option: ' + (item.textContent || '').trim() + ' (value: ' + chosenValue + ')',
				value: chosenValue,
			};
		}
	}
	return {
		success: false,
		error: 'Option with text or value \\'' + targetText + '\\' not found in ARIA listbox/menu',
		availableOptions: availableOptions,
	};
}
"""
```

为避免三个 setter 重复 15 行 resolveNode+callFunctionOn 样板，抽私有 helper（定位：紧邻 `set_select_option`，`session.py:2713` 之后）：

```python
async def _call_setter_on_node(
	self, backend_node_id: int, function_declaration: str, value: str,
) -> dict:
	"""resolveNode + callFunctionOn(setter JS, value) -> dict。set_select_option /
	set_aria_option / set_custom_option 共用。返回 setter 原始 dict（success/
	message/value/availableOptions/error）。CDP/JS 异常上抛（caller 友好包装）。
	镜像 fetch_select_options:2634 的 resolveNode+callFunctionOn 形状。"""
	resolve = await self.client.send.DOM.resolveNode(
		{"backendNodeId": backend_node_id},
		session_id=self.current_session_id,
	)
	object_id = resolve["object"]["objectId"]
	result = await self.client.send.Runtime.callFunctionOn(
		{
			"objectId": object_id,
			"functionDeclaration": function_declaration,
			"arguments": [{"value": value}],
			"returnByValue": True,
		},
		session_id=self.current_session_id,
	)
	return result.get("result", {}).get("value", {}) or {}

async def set_aria_option(self, backend_node_id: int, value: str) -> dict:
	"""在 ARIA menu/listbox（backendNodeId 绑定）中选 option（_fetch_aria_options 的
	写侧对应）。返回与 set_select_option 同形 dict。CDP/JS 异常上抛（caller 包装）。"""
	return await self._call_setter_on_node(backend_node_id, _SET_ARIA_JS, value)
```

> **可选低风险重构**：`set_select_option` 首次 `callFunctionOn`（`session.py:2683-2691`）也可改调 `_call_setter_on_node(backend_node_id, _SELECT_OPTION_JS, value)`，把 click-fallback 块作为唯一的 post-call 逻辑。既有 `TestSetSelectOption` mock 的是 CDP 边界而非 helper，仍绿。懒加载重试块（P1a）保留在 `set_select_option` 内（native 专属）。

#### action 层

`_action_select_dropdown`（`actions.py:1108-1165`）按「架构与调度流程」整段重写为多类型调度（删掉 `tag != "SELECT"` hard-reject，非 SELECT 走 dispatcher）。本阶段先保证 `set_dropdown_option` dispatcher（下文 P1b 末总装）能路由到 `set_aria_option`。

#### session 层 dispatcher 总装（P1b 引入 `set_dropdown_option`，P1c/P1e 续填分支）

```python
async def set_dropdown_option(self, backend_node_id: int, value: str) -> dict:
	"""写侧 dispatcher（镜像 fetch_dropdown_options:2760）。复用读侧 dispatcher
	做分类（同一份 JS 判型，读写零漂移 —— D1），再按 source 路由到对应 setter。
	返回 setter dict + 'source'（'aria'|'custom'|'child-depth-N'|None）；source=None
	表示非任何已知下拉类型（action 层据此返友好 error）。CDP/JS 异常上抛（caller 包装）。"""
	classified = await self.fetch_dropdown_options(backend_node_id)
	source = classified["source"]
	if source == "aria":
		result = await self.set_aria_option(backend_node_id, value)
	elif source == "custom":                       # P1c 填
		result = await self.set_custom_option(backend_node_id, value)
	elif source is not None and str(source).startswith("child-depth-"):  # P1e 填
		result = await self._set_subtree_option(backend_node_id, value)
	else:
		return {"success": False, "source": None, "error": "not a recognized dropdown"}
	result["source"] = source
	return result
```

#### 测试

**session 层** `TestSetAriaOption`（仿 `tests/test_dropdown_options.py::TestFetchAriaOptions`，mock CDP 边界）：

```python
class TestSetAriaOption:
	def _make_session(self, value):
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "obj-1"}})
		client.send.Runtime.callFunctionOn = AsyncMock(return_value={"result": {"value": value}})
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_success_passes_through_scoped_to_backend_id(self):
		s, client = self._make_session({"success": True, "message": "Selected option: A (value: a)", "value": "a"})
		out = await s.set_aria_option(99, "a")
		assert out["success"] is True
		client.send.DOM.resolveNode.assert_awaited_once_with({"backendNodeId": 99}, session_id="sid")
		args = client.send.Runtime.callFunctionOn.await_args.args[0]
		assert args["arguments"] == [{"value": "a"}]

	@pytest.mark.asyncio
	async def test_miss_returns_available_options(self):
		s, _ = self._make_session({"success": False, "availableOptions": [{"text": "A", "value": "a"}], "error": "not found"})
		out = await s.set_aria_option(7, "zz")
		assert out["success"] is False
		assert out["availableOptions"] == [{"text": "A", "value": "a"}]
```

**action 层** `TestSelectDropdownDispatch`（扩 `tests/test_select_dropdown.py`，`_make_browser` 新增 `set_dropdown_option`/`set_combobox_option` mock）：

```python
def _make_browser(*, select=None, combo=None, dropdown=None, raises=None) -> MagicMock:
	bs = MagicMock()
	bs.set_select_option = AsyncMock(return_value=select or {})
	bs.set_combobox_option = AsyncMock(return_value=combo or {})
	bs.set_dropdown_option = AsyncMock(return_value=dropdown or {})
	bs.get_state = AsyncMock(return_value=_make_state({}))
	return bs


class TestSelectDropdownDispatch:
	@pytest.mark.asyncio
	async def test_aria_listbox_routes_through_set_dropdown_option(self):
		entry = _make_entry(tag="UL", backend_node_id=7, attributes={"role": "listbox"})
		state = _make_state({3: entry})
		browser = _make_browser(dropdown={
			"success": True, "message": "Selected option: A (value: a)", "value": "a", "source": "aria",
		})
		result = await Tools().execute("select_dropdown", {"index": 3, "value": "a"}, browser, browser_state=state)
		browser.set_dropdown_option.assert_awaited_once_with(7, "a")
		assert result.error is None
		assert "Selected \"a\"" in result.long_term_memory
		browser.set_select_option.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_native_select_still_uses_set_select_option_no_regression(self):
		entry = _make_entry(tag="SELECT", backend_node_id=7, attributes={"aria-label": "Country"})
		state = _make_state({3: entry})
		browser = _make_browser(select={"success": True, "message": "ok", "value": "ca"})
		await Tools().execute("select_dropdown", {"index": 3, "value": "ca"}, browser, browser_state=state)
		browser.set_select_option.assert_awaited_once_with(7, "ca")
		browser.set_dropdown_option.assert_not_awaited()

	@pytest.mark.asyncio
	async def test_source_none_returns_friendly_error(self):
		entry = _make_entry(tag="DIV", backend_node_id=7)  # 非下拉
		state = _make_state({3: entry})
		browser = _make_browser(dropdown={"success": False, "source": None, "error": "not a recognized dropdown"})
		result = await Tools().execute("select_dropdown", {"index": 3, "value": "x"}, browser, browser_state=state)
		assert result.error is not None
		assert "[DIV]" in result.error
		# 不走未命中软回显（无 availableOptions 泄漏）
		assert result.extracted_content is None
```

> **需同步更新既有用例**：`test_non_select_element_returns_error_without_select`（原断言 tag 守卫 hard-reject）改为断言走 `set_dropdown_option`（source None）返回友好 error；`test_native_select_success_echo` 等 native 用例断言不变。

---

### P1c — custom class 写（Semantic UI 等）（G12）

#### session 层：JS 常量 `_SET_CUSTOM_JS` + 方法 `set_custom_option`

```python
# custom-class 下拉写脚本（写侧对应 _CUSTOM_CLASS_OPTIONS_JS，Semantic UI / Foundation）。
# 非 custom-shaped（无 dropdown/ui class）返回 error；命中后 toggle selected/active +
# 真实 click + change + 读回验证（classList「是否粘住」）。返回与 _SELECT_OPTION_JS 同形。
_SET_CUSTOM_JS = """
function(targetText) {
	const root = this;
	if (!(root.classList.contains('dropdown') || root.classList.contains('ui'))) {
		return { success: false, error: 'Element is not a custom-class dropdown' };
	}
	const all = Array.from(root.querySelectorAll('.item, .option, [data-value]'));
	const availableOptions = all.map(function(n) {
		return { text: (n.textContent || '').trim(), value: n.getAttribute('data-value') || n.getAttribute('value') || (n.textContent || '').trim() };
	});
	const targetLower = (targetText || '').toLowerCase();
	for (const item of all) {
		const textLower = (item.textContent || '').trim().toLowerCase();
		const valLower = (item.getAttribute('data-value') || item.getAttribute('value') || '').toLowerCase();
		if (textLower === targetLower || valLower === targetLower) {
			root.dispatchEvent(new Event('focus', { bubbles: true, cancelable: true }));
			all.forEach(function(o) { o.classList.remove('selected'); o.classList.remove('active'); });
			item.classList.add('selected');
			item.classList.add('active');
			item.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
			item.click();
			item.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
			root.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
			const stuck = item.classList.contains('selected') || item.classList.contains('active');
			const chosenValue = item.getAttribute('data-value') || item.getAttribute('value') || (item.textContent || '').trim();
			if (!stuck) {
				return {
					success: false,
					error: 'Selection was set but not retained. The dropdown may require a different interaction.',
					targetOption: { text: (item.textContent || '').trim(), value: chosenValue },
					availableOptions: availableOptions,
				};
			}
			return {
				success: true,
				message: 'Selected option: ' + (item.textContent || '').trim() + ' (value: ' + chosenValue + ')',
				value: chosenValue,
			};
		}
	}
	return {
		success: false,
		error: 'Option with text or value \\'' + targetText + '\\' not found in custom dropdown',
		availableOptions: availableOptions,
	};
}
"""

async def set_custom_option(self, backend_node_id: int, value: str) -> dict:
	"""在 custom-class 下拉（Semantic UI 等，backendNodeId 绑定）中选 option
	（_fetch_custom_class_options 的写侧对应）。返回与 set_select_option 同形 dict。"""
	return await self._call_setter_on_node(backend_node_id, _SET_CUSTOM_JS, value)
```

dispatcher `set_dropdown_option` 的 `custom` 分支（见 P1b 总装）现已可路由。

#### 测试（要点）

- session 层 `TestSetCustomOption`：`classList` 含 `dropdown` → setter JS 命中返回 success；命中后 miss → 返回 `availableOptions`；参数 `arguments=[{"value":...}]`。
- action 层：dispatcher 回退链——`fetch_dropdown_options` 返回 `source="custom"` → `set_custom_option` 被调，long_term_memory 回显与 native 字节一致（D2 验证：`Selected "<v>" in ...`）。

---

### P1d — combobox 写（G12，**实验性·自撰**）

> ⚠️ **实验性阶段**：browser-use 自身跳过全部 combobox/ARIA 测试且缺 combobox setter，参照路径未充分验证。保守实现 = 固定 sleep + finally 强制收起 + 200 上限 + 手测验收门槛。CDP `returnByValue=False` RemoteObject shape 是最高风险点（见「CDP-shape 敏感点」）。

#### session 层：抽 `_collapse_combobox` + 定位 JS + setter JS + Python flow 方法

**① 抽 `_collapse_combobox`（读/写共用，D6 行为等价重构）**。把 `expand_and_fetch_combobox_options:2817-2831` 的 finally 块抽出为方法（定位：紧邻 `expand_and_fetch_combobox_options`，`session.py:2780` 之前）：

```python
async def _collapse_combobox(self, object_id: str | None) -> None:
	"""强制收起已展开的 combobox（Escape + blur）。load-bearing：残留展开的遮罩
	会拦截后续 click。best-effort（失败仅 debug 日志）—— 读/写 combobox flow 的
	finally 共用。"""
	try:
		await self.send_keys("Escape")
		if object_id is not None:
			await self.client.send.Runtime.callFunctionOn(
				{
					"objectId": object_id,
					"functionDeclaration": "function(){ try{ this.blur(); } catch(e){} }",
					"returnByValue": True,
				},
				session_id=self.current_session_id,
			)
	except Exception as e:
		logger.debug("combobox collapse failed: %s", e)
```

`expand_and_fetch_combobox_options` 的 finally 块改为 `await self._collapse_combobox(object_id)`（行为等价）。

**② listbox 定位 JS `_COMBOBOX_LISTBOX_ID_JS`**（返回 listbox 节点本身，`returnByValue=False` 由 caller 设）：

```python
# 解析 combobox 的 aria-controls listbox 为 RemoteObject。returnByValue=False 时
# CDP 把返回的 DOM 节点序列化为 {result:{type:'object',subtype:'node',objectId:...}}；
# 返回 null 则 result 无 objectId。caller 据此判 listboxFound 并取 objectId 跑 setter。
_COMBOBOX_LISTBOX_ID_JS = """
function() {
	const combo = this;
	const controlsId = combo.getAttribute('aria-controls') || combo.getAttribute('aria-owns');
	if (!controlsId) return null;
	return document.getElementById(controlsId);   // 节点或 null
}
"""
```

**③ listbox 写 JS `_SET_COMBOBOX_OPTION_JS`**（跑在 listbox 对象上，匹配段镜像 `_COMBOBOX_OPTIONS_JS:712-733`）：

```python
# combobox listbox 写脚本（写侧对应 _COMBOBOX_OPTIONS_JS）。跑在 listbox 对象上（由
# caller 经 _COMBOBOX_LISTBOX_ID_JS 解析后传入 objectId）。单选清兄弟 aria-selected →
# 选目标 → 真实 click → 读回验证（aria-selected「是否粘住」）。返回与 _SELECT_OPTION_JS 同形。
_SET_COMBOBOX_OPTION_JS = """
function(targetText) {
	const listbox = this;
	const all = Array.from(listbox.querySelectorAll('[role="option"], li'));
	const availableOptions = all.map(function(n) {
		return { text: (n.textContent || '').trim(), value: n.getAttribute('data-value') || n.getAttribute('value') || (n.textContent || '').trim() };
	});
	if (!all.length) {
		return { success: false, error: 'combobox listbox has no [role=option] or li', availableOptions: [] };
	}
	const targetLower = (targetText || '').toLowerCase();
	for (const item of all) {
		const textLower = (item.textContent || '').trim().toLowerCase();
		const valLower = (item.getAttribute('data-value') || item.getAttribute('value') || '').toLowerCase();
		if (textLower === targetLower || valLower === targetLower) {
			all.forEach(function(o) { o.setAttribute('aria-selected', 'false'); });
			item.setAttribute('aria-selected', 'true');
			item.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
			item.click();
			item.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
			const stuck = item.getAttribute('aria-selected') === 'true';
			const chosenValue = item.getAttribute('data-value') || item.getAttribute('value') || (item.textContent || '').trim();
			if (!stuck) {
				return {
					success: false,
					error: 'Combobox selection was set but not retained.',
					targetOption: { text: (item.textContent || '').trim(), value: chosenValue },
					availableOptions: availableOptions,
				};
			}
			return {
				success: true,
				message: 'Selected option: ' + (item.textContent || '').trim() + ' (value: ' + chosenValue + ')',
				value: chosenValue,
			};
		}
	}
	return {
		success: false,
		error: 'Option with text or value \\'' + targetText + '\\' not found in combobox listbox',
		availableOptions: availableOptions,
	};
}
"""
```

**④ Python flow 方法 `set_combobox_option`**（定位：紧邻 `expand_and_fetch_combobox_options`，`session.py:2831` 之后）：

```python
async def set_combobox_option(self, backend_node_id: int, value: str) -> dict:
	"""在 combobox 的 aria-controls listbox 中选 option。Python flow 镜像
	expand_and_fetch_combobox_options：展开（真实 click）→ settle → 解析 listbox
	objectId（_COMBOBOX_LISTBOX_ID_JS，returnByValue=False）→ 在 listbox 上写
	（_SET_COMBOBOX_OPTION_JS）→ finally 强制收起。NOTE：browser-use 缺 combobox
	写侧（已知不一致），此为从读 flow 自撰（D4）。返回与 set_select_option 同形 dict。
	CDP/JS 异常上抛；收起仍于 finally 跑。"""
	await self.click_element(backend_node_id)
	await asyncio.sleep(0.5)
	combo_object_id = None
	try:
		combo_resolve = await self.client.send.DOM.resolveNode(
			{"backendNodeId": backend_node_id},
			session_id=self.current_session_id,
		)
		combo_object_id = combo_resolve["object"]["objectId"]
		# 定位 listbox（returnByValue=False 取 RemoteObject）
		lb = await self.client.send.Runtime.callFunctionOn(
			{
				"objectId": combo_object_id,
				"functionDeclaration": _COMBOBOX_LISTBOX_ID_JS,
				"returnByValue": False,
			},
			session_id=self.current_session_id,
		)
		lb_result = lb.get("result", {}) or {}
		listbox_object_id = lb_result.get("objectId")   # 节点 → 有 objectId；null → 无
		if not listbox_object_id:
			return {
				"success": False,
				"error": "combobox listbox not found (no aria-controls/aria-owns target)",
				"availableOptions": [],
			}
		# 在 listbox 上写
		result = await self.client.send.Runtime.callFunctionOn(
			{
				"objectId": listbox_object_id,
				"functionDeclaration": _SET_COMBOBOX_OPTION_JS,
				"arguments": [{"value": value}],
				"returnByValue": True,
			},
			session_id=self.current_session_id,
		)
		return result.get("result", {}).get("value", {}) or {}
	finally:
		await self._collapse_combobox(combo_object_id)
```

#### action 层

dispatcher（见「架构与调度流程」）combobox 分支：`is_combo and (aria-controls or aria-owns)` → `set_combobox_option`。`listboxNotFound`（返回 `{success:false, error:...}`）被 action 层按「裸 error」处理 → `ActionResult(error="Failed to select option: ...")` 经 `except` 或直接 error 分支。`set_combobox_option` 抛异常 → `except` → 友好 error（不裸抛中断 agent loop）；收起仍于 finally 跑。

#### 测试（完整骨架，**调用顺序不变量 + CDP shape 是重点**）

```python
class TestSetComboboxOption:
	def _make_session(self, *, listbox_found=True, setter_value=None, setter_raises=None):
		s = BrowserSession.__new__(BrowserSession)
		s.current_session_id = "sid"
		s.click_element = AsyncMock()
		s.send_keys = AsyncMock()
		client = MagicMock()
		client.send.DOM.resolveNode = AsyncMock(return_value={"object": {"objectId": "combo-1"}})
		# _COMBOBOX_LISTBOX_ID_JS（returnByValue=False）→ {result:{objectId:...}} 或 {result:{value:None}}
		lb_ret = {"objectId": "listbox-1"} if listbox_found else {"value": None}
		call_results = [{"result": lb_ret}]
		if setter_raises is not None:
			call_results.append(setter_raises)
		elif setter_value is not None:
			call_results.append({"result": {"value": setter_value}})
		client.send.Runtime.callFunctionOn = AsyncMock(side_effect=call_results)
		s.client = client
		return s, client

	@pytest.mark.asyncio
	async def test_expand_locate_set_collapse_order(self, monkeypatch):
		async def fake_sleep(s): pass
		monkeypatch.setattr("tree_walker.browser.session.asyncio.sleep", fake_sleep)
		s, client = self._make_session(setter_value={"success": True, "message": "Selected: A", "value": "a"})
		out = await s.set_combobox_option(7, "a")
		assert out["success"] is True
		s.click_element.assert_awaited_once_with(7)             # 1. 展开
		# 2. callFunctionOn 链：locate listbox → set on listbox
		assert client.send.Runtime.callFunctionOn.await_count == 2
		first = client.send.Runtime.callFunctionOn.await_args_list[0].args[0]
		assert first["returnByValue"] is False                 # locate 用 returnByValue=False
		second = client.send.Runtime.callFunctionOn.await_args_list[1].args[0]
		assert second["objectId"] == "listbox-1"               # setter 跑在 listbox 上
		assert second["arguments"] == [{"value": "a"}]
		s.send_keys.assert_awaited_once()                       # 3. finally 收起

	@pytest.mark.asyncio
	async def test_listbox_not_found_returns_error_still_collapses(self):
		s, client = self._make_session(listbox_found=False)
		out = await s.set_combobox_option(7, "a")
		assert out["success"] is False
		assert "listbox not found" in out["error"]
		s.send_keys.assert_awaited_once()                       # D4 不变量：仍收起

	@pytest.mark.asyncio
	async def test_setter_raises_still_collapses(self, monkeypatch):
		async def fake_sleep(s): pass
		monkeypatch.setattr("tree_walker.browser.session.asyncio.sleep", fake_sleep)
		s, _ = self._make_session(setter_raises=RuntimeError("detached"))
		with pytest.raises(RuntimeError):
			await s.set_combobox_option(7, "a")
		s.send_keys.assert_awaited_once()                       # finally load-bearing
```

action 层：`is_combo + aria-controls` → 调 `set_combobox_option`；mock `side_effect=RuntimeError` → 友好 error。

> **回归**：`expand_and_fetch_combobox_options` 经 `_collapse_combobox` 重构后须仍收起（既有 `TestExpandAndFetchComboboxOptions` 用例的 `send_keys` 断言不变）。

#### 手测门槛（合并前必过，**不靠单测兜底框架差异**）

1. **WAI-ARIA Combobox with Listbox Popup** 示例 → 选项选上 + 调用后 combobox 收起。
2. **React-Select v5** demo（Portal 渲染 listbox 到 body）→ `_COMBOBOX_LISTBOX_ID_JS` 经 `getElementById(aria-controls)` 命中 listbox objectId → 写成功。
3. **Semantic-UI dropdown**（若 `role=combobox`）→ combobox 路径；否则走 custom-class（P1c）。
4. 故意 mock Escape 失败 → 确认 `blur()` JS 回退兜底，不残留遮罩。

---

### P1e — 子树搜索 写（G12）

#### session 层：定位 JS `_SUBTREE_LOCATE_JS` + 方法 `_set_subtree_option` + helper

**① `_SUBTREE_LOCATE_JS`**（同 `_SUBTREE_SEARCH_JS:741-775` 的 BFS+classify，但返回首个命中后代的节点 + 类型，`returnByValue=False`）：

```python
# 子树定位 JS（写侧对应 _SUBTREE_SEARCH_JS）。同 BFS+classify，但返回首个命中后代
# 的节点 + 类型（returnByValue=False 由 caller 设，节点以 RemoteObject 存活），供 Python
# 写 flow 按类型 callFunctionOn 对应 setter。depth 0（start 自身）跳过。
_SUBTREE_LOCATE_JS = """
function(maxDepth) {
	const start = this;
	function classify(el) {
		if (el.querySelector('[role="option"],[role="menuitem"]')) return 'aria';
		if ((el.classList.contains('dropdown') || el.classList.contains('ui')) && el.querySelector('.item,.option,[data-value]')) return 'custom';
		return null;
	}
	var queue = [[start, 0]];
	while (queue.length) {
		var pair = queue.shift(); var el = pair[0]; var d = pair[1];
		if (d > 0) {
			var t = classify(el);
			if (t) return { found: true, type: t, node: el, depth: d };
		}
		if (d < maxDepth) {
			for (var i = 0; i < el.children.length; i++) queue.push([el.children[i], d + 1]);
		}
	}
	return { found: false, type: null, node: null };
}
"""
```

**② objectId-变体 helper**（子代/combobox listbox 由 JS locator 解析，只有 objectId 无 backendNodeId）：

```python
async def _call_setter_on_object(
	self, object_id: str, function_declaration: str, value: str,
) -> dict:
	"""同 _call_setter_on_node 但直接接 remote objectId（子树/combobox 子代经 JS
	locator 解析）。返回 setter dict。CDP/JS 异常上抛。"""
	result = await self.client.send.Runtime.callFunctionOn(
		{
			"objectId": object_id,
			"functionDeclaration": function_declaration,
			"arguments": [{"value": value}],
			"returnByValue": True,
		},
		session_id=self.current_session_id,
	)
	return result.get("result", {}).get("value", {}) or {}
```

**③ `_set_subtree_option`**（被 dispatcher `set_dropdown_option` 的 `child-depth-N` 分支调）：

```python
async def _set_subtree_option(self, backend_node_id: int, value: str) -> dict:
	"""定位子代下拉（BFS，镜像 search_children_for_dropdowns 但返回子代 objectId+类型），
	按类型调 _SET_ARIA_JS / _SET_CUSTOM_JS。返回 setter dict（无 source —— dispatcher
	补）。CDP/JS 异常上抛。"""
	resolve = await self.client.send.DOM.resolveNode(
		{"backendNodeId": backend_node_id}, session_id=self.current_session_id,
	)
	parent_object_id = resolve["object"]["objectId"]
	located = await self.client.send.Runtime.callFunctionOn(
		{
			"objectId": parent_object_id,
			"functionDeclaration": _SUBTREE_LOCATE_JS,
			"arguments": [{"value": 4}],
			"returnByValue": False,   # 需子代 RemoteObject
		},
		session_id=self.current_session_id,
	)
	payload = located.get("result", {}) or {}
	if not payload.get("found"):
		return {"success": False, "error": "subtree child dropdown vanished between read and write"}
	setter = _SET_ARIA_JS if payload.get("type") == "aria" else _SET_CUSTOM_JS
	# CDP-shape：returnByValue=False 下嵌套节点 objectId（见「CDP-shape 敏感点」）
	child_object_id = payload.get("objectId") or (payload.get("node") or {}).get("objectId")
	if not child_object_id:
		return {"success": False, "error": "could not resolve subtree child objectId"}
	return await self._call_setter_on_object(child_object_id, setter, value)
```

dispatcher `set_dropdown_option` 的 `child-depth-N` 分支（见 P1b 总装）现已可路由。

#### 测试（要点）

- session 层 `TestSetSubtreeOption`：`_SUBTREE_LOCATE_JS` 返回 `{found:true, type:'aria', objectId:'child-1'}` → `_SET_ARIA_JS` 以 `objectId='child-1'` 调；type `custom` → `_SET_CUSTOM_JS`；`{found:false}` → error；**断言 CDP-shape 抽取兼容顶层 `objectId` 与嵌套 `node.objectId`**。
- action 层：dispatcher 经 `fetch_dropdown_options` 返回 `source="child-depth-2"` → `set_dropdown_option` 路由 `_set_subtree_option`。

---

### P1f — G9 扩面：click 误点 ARIA/combobox/custom 降级读选项

#### action 层：`_action_click` 下拉降级分支扩面

定位：`_action_click` SELECT 分支（`actions.py:417-425`）。把「仅 native select 降级」扩为「识别所有下拉形元素 → 降级读选项；非下拉落回真实 click」。

```python
        # 2. 下拉降级（G9）：目标是下拉形元素（native select / ARIA listbox·menu / custom
        #    dropdown / combobox）时，click 要么 no-op（native select 忽略 click）要么误开
        #    遮罩（ARIA/combobox）。降级为读选项给 LLM（镜像 dropdown_options 调度）。
        tag = entry.tag_name.upper()
        is_combo, _ = self._is_autocomplete_field(entry)
        attrs = getattr(entry, "attributes", {}) or {}
        is_dropdown_target = (
            tag == "SELECT"
            or (attrs.get("role") in ("listbox", "menu", "menubar", "tree", "grid"))
            or ("dropdown" in (attrs.get("class") or "").lower())
            or (is_combo and (attrs.get("aria-controls") or attrs.get("aria-owns")))
        )
        if is_dropdown_target:
            try:
                if tag == "SELECT":
                    options = await browser.fetch_select_options(backend_id)
                    return self._format_options_result(options, entry, params["index"], "click-select")
                if is_combo and (attrs.get("aria-controls") or attrs.get("aria-owns")):
                    options = await browser.expand_and_fetch_combobox_options(backend_id)
                    return self._format_options_result(options, entry, params["index"], "click-combobox")
                dispatched = await browser.fetch_dropdown_options(backend_id)
                if dispatched["source"] is None:
                    pass   # 假阳性：判错了，落回真实 click
                else:
                    return self._format_options_result(
                        dispatched["options"], entry, params["index"], "click-" + dispatched["source"],
                    )
            except Exception as e:
                return ActionResult(error=f"Failed to read dropdown options: {e}")
        # 3. 普通点击：highlight -> click_element ...
```

> **D7**：不重路由到 `dropdown_options`（click 已「点过」）。`source="click-select"` 已在 `_EMPTY_OPTIONS_DIAGNOSTIC`（`actions.py:27`）；`click-combobox`/`click-aria`/`click-custom`/`click-child-depth-N` 缺失键自然回退 hint-only（`_format_options_result:602-603` 的 `.get(source)` → None → 仅 hint）。`is_dropdown_target` 用 `class` 含 `dropdown` 的保守判定防假阴性；`dispatched["source"] is None` 兜底防假阳性（落回真实 click）。

#### 测试（扩 `tests/test_click.py::TestClickSelectBranch`）

```python
@pytest.mark.asyncio
async def test_click_aria_listbox_degrades_to_options(self):
	entry = _make_entry(tag="UL", backend_node_id=7, attributes={"role": "listbox"})
	state = _make_state({3: entry})
	browser = _make_browser_for_click(dispatch={"options": [{"value": "a", "text": "A", "selected": False}], "source": "aria"})
	result = await Tools().execute("click", {"index": 3}, browser, browser_state=state)
	assert result.error is None
	assert "0: text=" in result.extracted_content
	assert "via [CLICK-ARIA]" in result.long_term_memory

@pytest.mark.asyncio
async def test_click_combobox_degrades_via_expand(self):
	entry = _make_entry(tag="INPUT", backend_node_id=7, attributes={"role": "combobox", "aria-controls": "lb"})
	state = _make_state({3: entry})
	browser = _make_browser_for_click(combo_options=[{"value": "a", "text": "A", "selected": False}])
	result = await Tools().execute("click", {"index": 3}, browser, browser_state=state)
	assert "via [CLICK-COMBOBOX]" in result.long_term_memory

@pytest.mark.asyncio
async def test_click_native_select_unchanged_no_regression(self):
	# P0 native 路径字节一致
	entry = _make_entry(tag="SELECT", backend_node_id=7, attributes={"aria-label": "Country"})
	state = _make_state({3: entry})
	browser = _make_browser_for_click(fetch_options=[{"value": "us", "text": "US", "selected": True}])
	result = await Tools().execute("click", {"index": 3}, browser, browser_state=state)
	browser.fetch_select_options.assert_awaited_once_with(7)
	assert "via [CLICK-SELECT]" in result.long_term_memory

@pytest.mark.asyncio
async def test_click_non_dropdown_falls_through_to_real_click(self):
	# 普通 <button> 不被降级守卫捕获 → 走真实 click_element
	entry = _make_entry(tag="BUTTON", backend_node_id=7)
	state = _make_state({3: entry})
	browser = _make_browser_for_click(clicked=True)
	await Tools().execute("click", {"index": 3}, browser, browser_state=state)
	browser.click_element.assert_awaited_once_with(7)
```

> 既有 `test_select_uses_scoped_fetch_not_global_query` 等仍绿（native 路径不变）；新增用例覆盖 ARIA/combobox/非下拉落回。

---

## CDP 调用清单（变更后）

| 路径 | CDP 链 | 次数 | 备注 |
|---|---|---|---|
| native 命中（P0，不变） | `resolveNode` → `callFunctionOn(_SELECT_OPTION_JS)` | 2 | |
| native + 懒加载重试（G11） | resolveNode → callFunctionOn(select) → callFunctionOn(`focus`) → **sleep 1.0** → callFunctionOn(select 重跑) | 4 | 仅「全空」谓词为真 |
| native + 框架回退（P0，不变） | resolveNode → callFunctionOn(select) → callFunctionOn(click-fallback) | 3 | |
| ARIA 命中 | resolveNode → callFunctionOn(`_ARIA_OPTIONS_JS` 读探) → resolveNode → callFunctionOn(`_SET_ARIA_JS` 写) | 4 | dispatcher 复用读侧分类 |
| custom 命中（最坏非 combobox） | resolveNode → callFunctionOn(aria 探, None) → resolveNode → callFunctionOn(custom 探) → resolveNode → callFunctionOn(`_SET_CUSTOM_JS`) | 6 | |
| combobox 命中 | `click_element`(自身链) → **sleep 0.5** → resolveNode(combobox) → callFunctionOn(`_COMBOBOX_LISTBOX_ID_JS`, returnByValue=False) → callFunctionOn(`_SET_COMBOBOX_OPTION_JS`) → `send_keys("Escape")` → callFunctionOn(`blur`) | ~8 + click 内部 | finally 必收起 |
| 子树命中 | resolveNode(父) → callFunctionOn(aria 探) → resolveNode → callFunctionOn(custom 探) → resolveNode → callFunctionOn(`_SUBTREE_SEARCH_JS`) → resolveNode(父) → callFunctionOn(`_SUBTREE_LOCATE_JS`, returnByValue=False) → callFunctionOn(`_SET_ARIA_JS`/`_SET_CUSTOM_JS` 于子代) | ~8 | 最罕见路径 |

> 非常规路径（2~8 次 CDP）换取**正确的范围绑定 + 多类型写支持**。native 常规路径不变（仍 2 次）。回退/重试仅在特定失败模式触发。

---

## 涉及文件清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `src/tree_walker/browser/session.py` | **改** | 新增模块级 JS 常量 `_SET_ARIA_JS`/`_SET_CUSTOM_JS`/`_SET_COMBOBOX_OPTION_JS`/`_COMBOBOX_LISTBOX_ID_JS`/`_SUBTREE_LOCATE_JS`（紧邻 `session.py:775`）；新增方法 `_call_setter_on_node`/`_call_setter_on_object`/`_collapse_combobox`/`set_aria_option`/`set_custom_option`/`set_combobox_option`/`set_dropdown_option`/`_set_subtree_option`（紧邻 `session.py:2713`~`2831`）；`set_select_option`（`:2692` 后）插入懒加载重试块（P1a）；`expand_and_fetch_combobox_options` finally 块改调 `_collapse_combobox`（D6 行为等价） |
| `src/tree_walker/tools/actions.py` | **改** | `_action_select_dropdown`（`:1108-1165`）重写为多类型调度（镜像 `_action_dropdown_options`，删 tag hard-reject）；`_action_click` 下拉降级分支（`:417-425`）扩面到 ARIA/combobox/custom（P1f） |
| `tests/test_select_dropdown.py` | **改/扩** | `_make_browser` 加 `set_dropdown_option`/`set_combobox_option` mock；新增 `TestSetSelectOptionLazyLoadRetry`/`TestSetSelectDropdownDispatch`/`TestSetAriaOption`/`TestSetCustomOption`/`TestSetDropdownOptionDispatcher`/`TestSetSubtreeOption`；同步既有用例（非 select 现走 dispatcher） |
| `tests/test_select_combobox.py` | **新增** | `TestSetComboboxOption`（调用顺序不变量 + returnByValue=False shape + finally 收起 + listbox 未命中）——独立文件因顺序不变量 + CDP shape 值得专属套件（仿读侧 `test_combobox_options.py`） |
| `tests/test_click.py` | **扩** | `TestClickSelectBranch` 加 ARIA/combobox/非下拉落回断言（P1f） |
| `tests/test_dropdown_options.py` | **改**（回归） | 断言 `expand_and_fetch_combobox_options` 经 `_collapse_combobox` 重构后仍收起 |
| `src/tree_walker/tools/models.py` | 不改 | `SelectDropdownParams{index, value}` 不变（类型自动探测） |
| `src/tree_walker/agent/views.py` | 不改 | `ActionResult` 不引入 `include_extracted_content_only_once` |
| `docs/Tools技术细节/04_动作清单与CDP映射.md` | **改**（文档同步） | §4.19 补 ARIA/custom/combobox/子树 写的 CDP 行（DOM.resolveNode + Runtime.callFunctionOn；combobox 的 returnByValue=False RemoteObject） |

---

## CDP-shape 敏感点（实现期必验 + 专属测试 + 手测门槛）

本 follow-up 引入两处**仓库内无先例**的 CDP 用法 —— `Runtime.callFunctionOn` 配 `returnByValue=False` 返回 DOM 节点的 RemoteObject，用于在 JS locator 找到的子元素（combobox listbox / 子树下拉）上跑后续 setter。须实现期验证 + 专属 session 测试：

1. **combobox listbox objectId**（P1d）：`_COMBOBOX_LISTBOX_ID_JS` 返回节点时，CDP 给 `{result:{type:'object',subtype:'node',objectId:'...'}}`；返回 `null` 时 result 无 objectId。Python 抽取 `lb_result.get("objectId")` 判定。**回退**（若 CDP client 不友好）：改两步走标准 returnByValue=True —— JS 返回 listbox 的 element id，Python 用 `DOM.querySelector({selector:'#<id>'})` → `DOM.resolveNode` 拿 objectId → callFunctionOn setter。
2. **子代 objectId**（P1e）：`_SUBTREE_LOCATE_JS` 返回 `{found,type,node}` 时，`returnByValue=False` 下 `node` 字段是嵌套 RemoteObject（`result.value.node.objectId`，非顶层）—— 注意：返回包装对象时 CDP 可能把整个 `{found,type,node}` 也当 RemoteObject。`_set_subtree_option` 的抽取须兼容 `payload.objectId`（顶层节点）与 `payload.node.objectId`（嵌套）两种 shape。**回退**：同上，JS 返回子代的可重定位描述（如 element id / data-backend-id），Python 用 DOM.* 重解析。

> RemoteObject 句柄用完应 `Runtime.releaseObject` 释放（防泄漏）；单次工具调用内不释放可接受（导航时 GC）。实现期视 CDP client 封装决定是否补 release。

---

## 风险与已知限制

1. **combobox 写侧框架多样性 + 自撰**（P1d 最高风险）：browser-use 缺 setter、自身 skip 测试。React Portal 把 listbox 渲染到 `body` 外，`getElementById(aria-controls)` 能定位但 `querySelectorAll` 不能；Material/Downshift/headless-ui 在 `aria-controls`/`aria-owns`/`data-*` 上各有差异。P1d 对 vanilla + WAI-ARIA 可靠，React/Material 为 best-effort，**靠手测门槛兜底**。
2. **CDP `returnByValue=False` RemoteObject shape**（P1d/P1e）：仓库内无先例，参照路径未验证 → 标敏感点，须实现期验证 + 专属测试 + 回退方案（见「CDP-shape 敏感点」）。
3. **懒加载重试仅 native**（P1a）：aria/custom/combobox 的 miss 返回真实 DOM 选项，「全空」信号在那里语义模糊（可能是未展开的 listbox），故重试仅 native（D1 of [`select_dropdown.md`](./select_dropdown.md) 蓝图原意亦为 native）。combobox 有自己的 `asyncio.sleep(0.5)` settle。
4. **懒加载时序 flake**（P1a）：`focus()`+固定 `sleep 1.0` 是起点非保证；不引入轮询（与读侧 D4 一致，轮询推迟到按真实 flake 反馈再开）。
5. **500 字符 `__str__` 截断 × 大菜单**：选项很多时 `extracted_content` 被 `ActionResult.__str__` 截到 500；靠 `long_term_memory` 摘要 + 工具可重调缓解。不引入 `include_extracted_content_only_once`（D8）。
6. **单选 / 不支持 `<select multiple>`**：所有 setter 单选（P0 限制沿袭；browser-use 亦单选）。多选需扩参数，P2 再议。
7. **精确匹配 / 无模糊**：text OR value 大小写不敏感精确匹配（与 browser-use 一致）。LLM 误传时靠软回显的 `availableOptions` 自纠。
8. **`_EMPTY_OPTIONS_DIAGNOSTIC` 不扩 `click-aria`/`click-combobox`/`click-custom` 键**（P1f）：click 降级空选项的边角情况回退 hint-only（无类型诊断）。次要，记为已知限制。
9. **custom-class 假阳性**（P1c）：非下拉的 `div.ui` 可能返回杂散 `.item`；限于子树，可接受。click 降级用 `dispatched["source"] is None` 兜底防误降级（P1f）。
10. **无 `<optgroup>` 感知**：选项扁平匹配（P0 行为沿袭）。

---

## 验证步骤

### 自动化测试（按 CLAUDE.md 用 `uv run python -m pytest`）

```powershell
# 逐阶段
uv run python -m pytest tests/test_select_dropdown.py -v                       # P1a/b/c/e + dispatcher
uv run python -m pytest tests/test_select_combobox.py -v                        # P1d（实验性）
uv run python -m pytest tests/test_click.py::TestClickSelectBranch -v           # P1f

# 读写闭环回归（dropdown_options / select_dropdown / click SELECT 共享 fetch_select_options +
# _format_options_result + expand_and_fetch_combobox_options）
uv run python -m pytest tests/test_select_dropdown.py tests/test_select_combobox.py tests/test_dropdown_options.py tests/test_combobox_options.py tests/test_click.py -x -v

# 全量 + 覆盖率（CLAUDE.md 目标 >85%）
uv run python -m pytest tests/ -x -v
uv run python -m pytest tests/ --cov=tree_walker.tools.actions --cov=tree_walker.browser.session --cov-report=term-missing
```

预期：新用例全绿；既有 `test_select_dropdown.py`/`test_dropdown_options.py`/`test_combobox_options.py`/`test_click.py` 不回归（native 路径字节一致；`_collapse_combobox` 重构行为等价）；combobox 的 `finally` 收起分支覆盖率须显示已覆盖（用 `side_effect=Exception` mock）。

### P1d 手测门槛（合并前必过，见 P1d 节）

WAI-ARIA combobox / React-Select Portal / Semantic-UI / Escape 失败回退四项。

### 手测步骤（其余阶段）

1. **懒加载重试**（P1a）：选项异步填充的 native select（首次 option 全空）→ `select_dropdown(index=N, value=...)` → 重试后选中成功（观察 session 层多 1 次 `focus`+sleep+重跑）。
2. **ARIA listbox 写**：WAI-ARIA Listbox Example → `dropdown_options(index=N)` 读选项 → `select_dropdown(index=N, value=<某 option>)` → 选项被选中（`aria-selected=true`），页面状态变化。
3. **custom class 写**：Semantic-UI dropdown demo → `select_dropdown` → `.item` 被选中（`selected`/`active` class 切换）。
4. **combobox 写**（P1d，实验性）：React-Select demo → `select_dropdown` → 展开后选中 listbox option → 调用后收起。
5. **多类型页面范围正确**：含 native select + ARIA listbox + combobox 的页面 → 各自 index 用各自类型写（不混杂）。
6. **选项未命中软回显**（跨类型）：`select_dropdown(index=N, value="不存在")` → 不报 error，回显该类型可用选项 + 用法提示（格式与 native 一致 —— D2）。
7. **非下拉元素报错**：对普通 `<div>`（无 role）的 index 调 `select_dropdown` → `error="Index N is a [DIV] element, not a recognized dropdown ..."`。
8. **click 误点 ARIA/combobox 降级**（P1f）：对 `role=listbox` 的 index 调 `click` → 输出选项列表（`via [CLICK-ARIA]`），而非误开遮罩；对普通 `<button>` 调 `click` → 仍走真实 click。
