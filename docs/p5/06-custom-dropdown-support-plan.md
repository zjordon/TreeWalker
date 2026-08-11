# 自定义下拉框（非原生 / 非 ARIA）agent 录制重放支持方案

> 2026-08-08。P5 续：让 agent 路径下「非原生 / 非 ARIA」的自定义下拉也能被 `select_dropdown` 选中、记进 history、按 CSV 变量替换重放。
> 配合 [`01`](./01-select-as-variable-plan.md)（原生 select 方案）/ [`04`](./04-select-record-replay-mechanism.md)（录制重放原理）/ [`05`](./05-p5-takeaways-and-custom-dropdown-bridge.md)（takeaways + 桥接判断点）阅读。
> issue #160 / 分支 `feat/160-custom-dropdown-replay`。

## 背景与根因

P5「选择类动作变量化」上一阶段（PR #157/#159）只解决了原生 `<select>`。但现代网站（B 站、抖音）表单下拉几乎不用原生 select，`select_dropdown` / `dropdown_options` 对它们全部报 `not a recognized dropdown` → agent 退裸 click → 不记 `select_dropdown` → 进不了 P5 变量链。

已真机坐实 4 例（探针：`examples/debug_custom_dropdown_classify.py` + `_inspect_{bili,czsm,douyin,douyin_hot}.py`），两个正交难度：

| 下拉 | list 相对触发器 | option 形态 | 现有读侧 |
|---|---|---|---|
| B站 分区 | 父节点子树内（兄弟容器 `select-container`） | `<div title>` 无 role | `fetch=None` |
| B站 创作声明 | `.bcc-select` 内兄弟分支（`.bcc-select-option-list`）；**点 inner input 不展开，须点 `.bcc-select` 根** | `<article>` 无 role | `fetch=None` |
| 抖音 选择合集 | `.semi-portal`→body（无 DOM 亲缘、无 aria-controls） | `role=option` ✓ | `fetch(触发器)=None` / `fetch(listbox)=aria+options` |
| 抖音 关联热点 | 同上 + **虚拟化（~10 页）** | `role=option` ✓ | 同上 |

**根因**：闭态判型器（`fetch_dropdown_options` 的 ARIA→custom-class→子树BFS）对这 4 形态全 miss → `source=None`。问题在「识别」不在「执行」——找到 list 节点后，抖音（有 role）现有 ARIA 读/写直接可用（实测 `fetch(listbox)=source:'aria'+options`），B 站（无 role）需 text 匹配。

## 关键结论（已查证）

- **`rerun.py` 无需改动**：重放把 `select_dropdown` 派发到**同一个** `_action_select_dropdown`（`rerun.py:818`→`actions.py:437`），只重定位触发器 `index`（`MatchLevel`）、原样保留 `value`。action/session 层一改，探索 + 重放都受益。变量替换走手工绑定（`_substitute_in_dict`，已支持 `value`）。
- **录音扩展不在范围内**：项目已战略转折到 agent 路径（录制不再优化，见 [[p5-agent-history-variable-target]]）。agent 探索时自产 `select_dropdown`，无需改 `action-recorder.ts`（它只对原生 `<select>` 发 select_dropdown）。
- **prompt 基本无需改**：`DROPDOWN_RULES` 已指示模型对「custom dropdown」用 `select_dropdown` 且「不要先 click」。现状是代码回 "use click to expand manually" 与之矛盾；本方案兜底成功后该矛盾自动消除。

## 方案总览

镜像现有 combobox「open→discover→select→collapse」flow（`expand_and_fetch_combobox_options` `session.py:3598` / `set_combobox_option` `session.py:3640`），新增一对 session 方法，在 action 层作为**兜底**——现有 native / aria / custom-class / combobox 路径先跑（保持精确行为、零回归），它们 miss（`source=None`）时才走新 flow：

```
真实 click 展开（爬到组件根，解决 czsm「点 input 不展开」）
  → JS 发现 list 节点（三档拓扑兜底，返回 RemoteObject）
  → 按 role/text 读或选（有 role 复用 ARIA 逻辑；无 role 走 text 匹配）
  → finally 收起（复用 _collapse_combobox）
```

复用 `_collapse_combobox`（`session.py:3580`）、`_call_setter_on_object`（`session.py:3712`）、三段式 echo（`actions.py:1555-1576`）、`_format_options_result`（`actions.py:791`，source 串键控）。

## 改动

### 1. `src/tree_walker/browser/session.py`（新 JS 常量 + 新方法，置于 combobox 对旁 ~3685）

**新 JS 常量**（都是 `function(){…}` 字面量，callFunctionOn 用；注意 tabs）：

- **`_CUSTOM_LISTBOX_DISCOVER_JS`**（跑在触发器，`returnByValue=False` 返回 list 节点 RemoteObject）——三档优先级发现，option-like 选择器统一 `[role=option],[role=menuitem],li,[data-value],.item,.option,[title],article`：
  1. `aria-controls/owns` → `getElementById`（真 combobox 保险）；
  2. **祖先作用域子树搜**：依次 `trigger.parentElement` → 最近 select-ish wrapper → `wrapper.parentElement`，在各作用域里找 `[role=listbox/menu]` 或「≥2 个 option-like 子节点」的容器（覆盖分区=父、czsm=`.bcc-select`）；
  3. **全局可见 listbox 就近**：`document` 里可见的 option-list，按到触发器 rect 的边距距离取最近（覆盖抖音 portal；`isOptionList` 过滤已隐藏的残留 `.semi-portal`）。
- **`_EFFECTIVE_CLICK_TARGET_JS`**（跑在触发器，`returnByValue=False`）——爬到 **4 层内最外层** select-ish 祖先（regex `/(select|dropdown|combobox|picker)/i` 或 `role=combobox`），否则返回 `this`。取**最外层**（非最近）是为了 czsm 命中真正展开的 `.bcc-select`（而非 `.bcc-select-input-wrap`）；其余 3 例点组件根同样能展开。
- **`_CUSTOM_OPEN_OPTIONS_JS`**（跑在发现的 list，`returnByValue=True`）——统一读：有 `[role=option]` 用之，否则用通用选择器；返回 `{text,value,selected}`，200 上限 + 截断行（与 `_ARIA_OPTIONS_JS` 同形，直接喂 `_format_options_result`）。
- **`_SET_CUSTOM_OPEN_OPTION_JS`**（跑在 list，`arguments=[{value}]`，`returnByValue=True`）——大小写不敏感精确匹配 text/data-value/value/title → 清兄弟 selected → `mousedown/click/mouseup/change` → readback（class/aria "stuck" **或** option 已脱离 DOM=框架选中即收起，B 站/Semi 行为）；miss 返回 `{success:False, availableOptions}`。返回统一 setter dict（与 `_SET_ARIA_JS`/`_SET_COMBOBOX_OPTION_JS` 同形）。
- **`_SCROLL_LISTBOX_JS`**（跑在 list，`returnByValue=True`）——`scrollTop += clientHeight`，返回是否真的滚了（虚拟化用）。
- 模块级 `_CUSTOM_SCROLL_CAP = 10`。

**新方法**（镜像 combobox 对，复用 `_collapse_combobox` / `_call_setter_on_object`）：

- **`_effective_click_bid(trigger_object_id, fallback_bid) -> int`**：跑 `_EFFECTIVE_CLICK_TARGET_JS`（`returnByValue=False`）→ `DOM.describeNode({objectId})→node.backendNodeId`；任一步失败回退 `fallback_bid`。
- **`expand_and_fetch_custom_options(backend_node_id) -> list[dict]`**（读）：`resolveNode`→`_effective_click_bid`→`click_element`→`sleep(.5)`→`callFunctionOn(_CUSTOM_LISTBOX_DISCOVER_JS on 触发器, F)`→取 listbox objectId（无则 raise `RuntimeError("custom dropdown listbox not found after opening")`）→`callFunctionOn(_CUSTOM_OPEN_OPTIONS_JS on listbox, T)`；`finally _collapse_combobox`。
- **`set_custom_dropdown_option(backend_node_id, value) -> dict`**（写）：同上展开 + 发现；`_call_setter_on_object(listbox_oid, _SET_CUSTOM_OPEN_OPTION_JS, value)`；**虚拟化 scroll-until-found**：若 miss 且 `availableOptions is not None`，循环（≤`_CUSTOM_SCROLL_CAP`）`_scroll_listbox` + `sleep(.12)`（让 React 渲染下页）+ 重试 setter，命中即停；`finally _collapse_combobox`。
- **`_scroll_listbox(listbox_object_id) -> bool`**：跑 `_SCROLL_LISTBOX_JS`，best-effort。

### 2. `src/tree_walker/tools/actions.py`（兜底分支）

- **`_action_dropdown_options`**（替换 `~1484-1497` 的 `else`）：先 `fetch_dropdown_options`；`source is not None` → 走原 `_format_options_result`；**`source is None` → 兜底 `expand_and_fetch_custom_options`**（`try/except`：失败回原来的 "not a recognized dropdown" 文案 + 失败原因；空 options 回 "opened but no options"）；成功 → `_format_options_result(..., "custom-open")`。
- **`_action_select_dropdown`**（替换 `~1540-1551` 的 `else`）：`set_dropdown_option` 后若 `source is None` → **兜底 `set_custom_dropdown_option`**；结果仍走现有三段式 echo（读 `success/message/value/availableOptions/error`，新 setter 返回同形 dict）。
- **`_EMPTY_OPTIONS_DIAGNOSTIC`**（`~30`）加 `"custom-open": "Custom dropdown opened but no options found (may load on demand — retry after settle, or scroll)."`。
- `_format_options_result` 无需结构改动（`"custom-open"` 自动产出 `via [CUSTOM-OPEN]`）。

> 兜底**无条件触发**（不加 `_looks_like_dropdown_trigger` 门控）——因为抖音关联热点的 input 无任何 select/选择标记，任何够窄的谓词都会漏它。stray-click 风险低（agent 只在认定的下拉上调 select_dropdown/dropdown_options），且 `try/except` + 收起兜底。

### 3. 测试

- **session 层**（新 `tests/test_custom_dropdown.py`，镜像 `test_combobox_options.py::TestExpandAndFetchComboboxOptions` + `test_select_dropdown.py::TestSetComboboxOption`）：`BrowserSession.__new__` + MagicMock client + 顺序 `callFunctionOn`/`describeNode` `side_effect`，patch `asyncio.sleep`。断言：open→discover→read/set→collapse 顺序（`await_args_list`）、`click_element` 收到组件根 bid（证 effective-target 生效）、listbox-not-found 仍收起、setter 异常仍收起、**虚拟化 scroll 重试到成功 / 不可滚放弃 / 达 cap**、effective-target 解析失败回退原 bid。
- **action 层**（扩 `test_dropdown_options.py` / `test_select_dropdown.py`，镜像 `TestDropdownOptionsComboboxRouting` + `TestSelectDropdownDispatch`）：闭态 dispatcher miss → 路由到新 flow（`assert_awaited_once_with(bid)`），且现有路径命中时**不**触发兜底（`assert_not_awaited`）；兜底异常映射到 error；miss 软回显 availableOptions。
- **fixture + 本地 e2e**：`docs/p5/fixtures/custom-dropdown-fixture.html` **加第 3 个下拉**（portal 拓扑：option 点击时 append 到 body 末尾的容器，复刻抖音），让 `examples/debug_custom_dropdown_local.py` 本地覆盖全部 3 拓扑。**该 example 的断言要翻转**：现在断言「坏」（`source=None`/`select 失败`）；改后闭态 dispatcher 仍 `None`（没动闭态判型——正确），但 action 层兜底让 `dropdown_options`/`select_dropdown` **成功**——断言拆成「闭态 miss（不变）」+「action 兜底成功（新）」。

## 范围

- **MVP（本次）**：发现 JS（3 拓扑 + 就近消歧）、effective-click（组件根）、统一读 + 写（stuck/脱离 readback）、两个 session 方法 + 两个 helper、action 兜底 + 诊断行、**虚拟化写** scroll-until-found、session+action 单测、fixture 加 portal + 翻转本地 example。覆盖全部 4 例。
- **后续 P1**：虚拟化**读**分页（枚举全量 option，非仅首页）；触发器 text/value readback（把触发器 objectId 作为 setter 第 2 参，替「脱离」启发式）；全局发现的 pre-snapshot 增量消歧（点前记可见 listbox，点后 diff→新出现者赢，消就近平局风险）；按需加 `_looks_like_dropdown_trigger` 门控；prompt/tool 文档补一句「自定义下拉现可直接用 select_dropdown」。
- **不做**：录音扩展（agent 路径为主，录制不优化）；rerun 改动（无需）。

## 验证

1. **单测**：`uv run python -m pytest tests/test_custom_dropdown.py tests/test_dropdown_options.py tests/test_select_dropdown.py tests/test_combobox_options.py -x -v` 全过；`uv run python -m pytest tests/ -x -v` 全量回归 + 覆盖率 ≥85%。
2. **本地 e2e**（3 拓扑）：`uv run python examples/debug_custom_dropdown_local.py` → 翻转后的断言全 PASS（czsm 同父 `<li>`、分区兄弟 `<div title>`、新增 portal 下拉都 `select_dropdown` 成功）。
3. **真机**（9222 Chrome，已登录）：
   - B 站：`uv run python examples/debug_custom_dropdown_classify.py`（自动探测触发器）→ 两下拉 `select_dropdown` 成功选中、记 `select_dropdown`。
   - 抖音：`_inspect_douyin*.py` 同理验选择合集 + 关联热点（含虚拟化滚动选中）。
   - 重放：手工标 `value` 为变量后，按 `examples/p5_select_e2e_live.py` 形态用 CSV 行替换重放选中不同项。

## 风险

1. **多 listbox 全局消歧**：MVP 用「到触发器就近」；两个 portal 距离相近可能误选 → 后续 pre-snapshot 增量。
2. **虚拟化滚动**：`_SCROLL_LISTBOX_JS` 滚 listbox 本身；若真正滚的是内层节点则 no-op（仅返首页）。0.12s 渲染 sleep 经验值，可能要按框架调。
3. **effective-click 过爬**：regex 在异常布局可能爬到点不开的祖先；限 4 层 + 失败回退触发器 bid。
4. **readback 假阴性**：既不收起也不打 selected class 的框架会报 "not retained"；触发器 text readback（后续）兜底。
5. **stray click**：无条件兜底会点任何闭态判型 miss 的元素（`try/except` + 收起兜底，风险低）。

## 关键文件

- `src/tree_walker/browser/session.py` — 新 JS 常量（~1040 旁）+ 4 方法（~3685 旁）
- `src/tree_walker/tools/actions.py` — 两兜底分支（~1484 / ~1540）+ `_EMPTY_OPTIONS_DIAGNOSTIC`（~30）
- `tests/test_custom_dropdown.py`（新）+ `tests/test_dropdown_options.py` + `tests/test_select_dropdown.py`
- `docs/p5/fixtures/custom-dropdown-fixture.html`（加 portal 拓扑）+ `examples/debug_custom_dropdown_local.py`（翻转断言）

---

## 实现与方案的偏差（真机验证后的修正，2026-08-08）

> 本节是落地后补的。本地 fixture 只能证明「机制通」，真机 B站/抖音暴露了 4 处方案没料到的坑，逐条修正如下。**真机全验证通过**：抖音选择合集、B站创作声明、B站分区三个下拉 `select_dropdown` 都真正选中（值变化、`error=None`）；全量 2221 测过、fixture 3 拓扑 e2e 过。

### 偏差 1：选中用「真实 CDP click」而非「JS 合成 click」（抖音 Semi UI）

- **方案**：`_SET_CUSTOM_OPEN_OPTION_JS` 里 `found.click()`（JS 合成）+ mousedown/mouseup 序列选中。
- **真机**：合成 click 在抖音 Semi UI（React）上**只给 option 打了 `aria-selected`，不 commit**——值不变、下拉不收起。Semi 的选中/收起 handler 只认**真实（trusted）点击**。
- **修正**：JS 只负责**找** option（`_CUSTOM_FIND_OPTION_JS`，`returnByValue=False` 返回节点 RemoteObject）→ Python `DOM.describeNode` 解析 option `backendNodeId` → **`click_element(option_bid)` 真实 CDP click**。删掉 `_SET_CUSTOM_OPEN_OPTION_JS`；新增 `_find_option_object_id` / `_backend_id_of_object` / `_read_custom_options`。虚拟化 scroll-until-found 也因此从「JS setter 内重试」搬到 Python（`_find_option_object_id` 循环 `_scroll_listbox` + 重试 find）。

### 偏差 2：匹配「精确 → 精确+包含兜底」（抖音 option 文本带附加信息）

- **方案**：大小写不敏感**精确**匹配 text/data-value/value/title。
- **真机**：抖音 option 的 DOM 文本是「TreeWalker合集共14个作品」（名字 + 作品数），调用方传可见名「TreeWalker合集」→ 精确 miss。
- **修正**：`_CUSTOM_FIND_OPTION_JS` 精确不中后，**包含**兜底（target ≥ 2 字防误匹配，取首个命中）。

### 偏差 3：option 选择器去掉 `article`（B站 hover tooltip 冒充 option）

- **方案**：option-like 选择器含 `article`（czsm 真机 inspect 时见过 `<article>` 带选项文本，误以为是 option）。
- **真机**：B站 `<article class="option-hover-tips">` 是 **hover tooltip**（不可点），真 option 是 `<li>`。匹配到 tooltip → 点它无效 → 报「not retained」。
- **修正**：从 3 个选择器（`_CUSTOM_LISTBOX_DISCOVER_JS` 的 OPTION_SEL、`_CUSTOM_OPEN_OPTIONS_JS` 的 GEN_SEL、`_CUSTOM_FIND_OPTION_JS` 的 GEN_SEL）**去掉 `article`**。真 option 形态覆盖：`[role=option]`/`[role=menuitem]`/`li`/`[data-value]`/`.item`/`.option`/`[title]`。

### 偏差 4：去掉「not retained」硬 readback（B站分区假阴性）

- **方案**：readback——option 选中后被移除/收起（`connected=false`）或打 selected 标记 → 成功；否则报 `Custom selection was set but not retained.`（对应上文风险 4）。
- **真机**：B站分区 `<div title>` option 选中后**值已变**，但 option div 仍连着（`connected=true`）、无 selected class → readback **假阴性**报「not retained」。假阴性比假阳性更坏——会让 agent 以为失败去重试（可能反复切换/取消选中）。
- **修正**：删掉 `_OPTION_SELECTED_JS` + `_verify_option_selected`；**真实 click 命中匹配 option 即视为成功**。真正的失败（option 找不到）在 click 之前就处理了（`_find_option_object_id` 返 None → 回显 availableOptions）；偏差 3 去掉 `article` 后，「点中非 option 元素」这条假成功路径也没了。后续若要更强校验，用触发器值 readback（见下）。

### 偏差 5：Semi UI 不响应 Escape → 下拉跑完不收起 → 诱导 agent click（待修）

- **方案**：`expand_and_fetch_custom_options` / `set_custom_dropdown_option` 的 finally 复用 `_collapse_combobox`（Escape + blur）收起下拉，假设它能把下拉关掉。
- **真机**：抖音 Semi UI **不响应 Escape、也不认合成 body-click** → `dropdown_options`/`select_dropdown` 跑完（尤其 select miss 后）**下拉一直开着**。后果：agent 下一步 get_state 看到选项还挂在 DOM 里，就按老习惯去 `click` option（而不是按规则用 `select_dropdown`）；而 Semi option 的 click 要点对 `role=option` 本体才生效，agent 前几次常点外层容器 / 已漂移 index → 不中。两次真机录制（`douying-select-sucess*.txt`）合集都因此试了 5+ 次才选中，且**最终选中靠 click 不是 select_dropdown**。
- **修正（待开工）**：`_collapse_combobox` 对不认 Escape 的框架补**真实 outside-click**（CDP `Input.dispatchMouseEvent` 在下拉外的背景点 press+release，或点页面某中性元素）收起——Semi 这种 React 受控下拉只在「trusted outside pointer 交互」时收起。这是 dropdown_options 留下拉开着、诱导 agent click 的根因；修了它 agent 才会老实走 `select_dropdown`。

### 真机 readback 经验（给后续）

读「是否选中」别用 `.semi-select-selection-text`（抖音它显的是字段 label「合集」不是值）。可靠信号：**placeholder 消失**（如「请选择合集」没了 = 已选）、或触发器 wrap 全文含目标值、或 B站 input.value 变化。

### 与方案一致、未改的部分

- 「open→discover→read/write→collapse」镜像 combobox flow、action 层兜底（闭态 dispatcher miss 才触发）、`_EFFECTIVE_CLICK_TARGET_JS`（爬最外层 select-ish 祖先）、`_CUSTOM_LISTBOX_DISCOVER_JS` 三档发现、`_open_and_discover_listbox` 两次 click 容错、虚拟化 scroll（搬 Python）、rerun 零改动、fixture 加 portal 拓扑——均按方案落地。

