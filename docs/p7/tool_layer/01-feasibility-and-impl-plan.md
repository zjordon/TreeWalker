# 方向 2 可行性分析与实施方案：KO 网格数据通道 / 排序保证 / 表单保持

> **实施状态（2026-08-28）**：B0-B3 全部落地并通过验证——
> B0 quirks.md 通道修正 ✅；B1 `read_grid` 动作（models.py ReadGridParams /
> session.py `read_ui_grid` / actions.py `_action_read_grid` 三级通道）✅；
> B2 快照 `[Grid]` 元信息（session `_read_grid_meta` → views `grid_meta` →
> system_prompt 渲染，`AGENT_ENABLE_GRID_META` 开关）✅；B3 保存消息
> `_read_page_messages` + 校验误报收窄 ✅。
> 验证：单测 24 项（tests/test_read_grid.py）+ 全量回归 2397 过 + 真机自测
> 全绿（examples/p7_read_grid_selftest.py：行序 created_at desc 实证、
> fresh 清残留 total 153→308、legacy 通道评论网格 5 行、[Grid] 渲染带首行值）。
> 后续：`run_category.ps1 -Category data_truncated -Force` n=3 验证轮（本计划 §5）。

> 日期：2026-08-28。评审对象：`evals\webarena\docs\plan-tool-layer-grid-and-sorting.md`（2026-08-27，
> 来源 data_truncated 复盘 `data-truncated-review-retrospective-20260827.md`）。
> 方法：8/26 重跑日志（带 skill 版）逐条对照 × **活环境探针实证**（本仓库新增
> `examples/p7_probe_grid_channels.py` + `examples/p7_probe_registry_fields.py`，
> Chrome 9223 独立实例 + shopping_admin 容器，全程只读）× 代码挂点核查。
> 结论先行：**三项全部可行，但 2.1 的通道优先级必须反转**——方案假定的主通道
> （mui/index/render POST）在探针与两轮轨迹里 100% 失败，真正可靠的通道是方案
> 里的"回落"项（uiRegistry）。2.2 的字段前提全部证实。2.3 维持方案原判断（小增量）。

---

## 〇、结论速览

| 子项 | 方案原设想 | 实证结果 | 可行性裁定 |
|---|---|---|---|
| 2.1 read_grid | mui POST 主通道 → uiRegistry 回落 → DOM 兜底 | **mui 四变体 × 两网格全部返回脚手架 HTML**（探针）；uiRegistry `ds.data.items` 是结构化行数组、字段齐全（探针）；评论网格 legacy AJAX 可用（探针） | ✅ 可行，**通道序反转为 uiRegistry → legacy AJAX → DOM(kick 后)** |
| 2.2 快照网格元信息 | 从 uiRegistry 读 sorting 与总数标注快照 | `ds.params` 暴露 `{filters, paging, sorting}`、`ds.data.totalRecords/items` 全部可读（探针实测值见 §2） | ✅ 可行，字段前提 100% 证实；另发现**活动过滤器残留可检**（意外收益） |
| 2.3 保存成功信号 | 捕获 `.message-success` / URL 变化 | click 后效检测块已存在（fingerprint+form digest），插入点明确 | ✅ 可行，小增量；脏检查同意"观察后再定" |
| （附带）quirks.md 通道 1 | mui render 配方作为免 DOM 通道 | 探针证伪（本环境不可用） | ⚠️ 需修正手册，防 skill 误导（8/26 已误导 64/679 各烧 1 步） |

---

## 一、证据基础（新增实证）

### 1.1 探针实测（2026-08-28，Chrome 9223 独立实例，全部只读）

**A. mui/index/render：四种变体全部失败**（`p7_probe_grid_channels.py`）：

| 变体 | 参数 | sales_order_grid | product_listing |
|---|---|---|---|
| v1 POST 极简 | form_key + namespace | 200 / text/html / 脚手架 | — |
| v2 POST + XHR 头 | + isAjax + paging | 200 / text/html / 脚手架 | — |
| v3 POST + sorting | + sorting[created_at]=desc | 200 / text/html / 脚手架 | — |
| v4 GET | 全参数 | 200 / text/html / 脚手架 | — |
| search 配方（quirks.md 通道 1 原样） | namespace=product_listing&search=Ingrid | — | 200 / text/html / 脚手架 |

form_key 来源双确认（`input[name=form_key]` 与 `window.FORM_KEY` 均在，len=16），
失败与 form_key 无关。响应体首 160 字符均为 `<div class="admin__data-grid-outer-wrap" data-bin…`
——脚手架模板，无行数据。**与两轮 agent 轨迹一致**（8/20：107/128/186/64 共 9+ 次尝试全败；
8/26：64 S1 "mui render fetch returned HTML (not JSON)"、679 S1、121 S1 同型失败）。
quirks.md 通道 1 配方在本 Magento（shopping_admin_final_0719 镜像）上不可用。

**B. uiRegistry：字段面完整证实**（`p7_probe_registry_fields.py`）：

```
sales_order_grid.sales_order_grid_data_source (UiClass):
  data      = { items, totalRecords, showTotalRecords, errorMessage }   ← items 是行数组
  params    = { namespace, search, keywordUpdated, filters, paging, sorting }
  params.paging  = {"pageSize":400,"current":1}        ← 8/26 任务 64 留下的书签残留
  params.sorting = {"field":"created_at","direction":"desc"}
  params.filters = {"placeholder":true,"status":"complete"}   ← 679 留下的过滤残留
  data.totalRecords = 153（非 308——过滤器生效中的直接证据）
  data.items[0..2]  = 230(2023-05-19) / 256(2023-05-14) / 9(2023-05-07)  ← 确按 created_at desc

product_listing.product_listing_data_source: 同构
  params.sorting = {"field":"qty","direction":"asc"}
  data.totalRecords = 32（非 2040——疑似 782 残留搜索词污染）
```

组件树完全可枚举：`<ns>.<ns>_data_source` / `<ns>_<ns>_data_source_storage`（行缓存，
按 entity_id 键控）/ `<ns>.<ns>`（grid）/ `…listing_top.{bookmarks,listing_paging,listing_filters}` /
`<ns>.<ns>.<ns>_columns.<field>`（**列组件名 = 数据字段名**，可自动生成字段清单）。
枚举排除 `notification_area.*`（通知区组件，本页常驻干扰源）。

**C. 评论网格（legacy ExtJS）AJAX 通道可用**（`p7_probe_grid_channels.py` C 段）：
`window.reviewGridJsObject` 存在，持 `{url, pageVar, sortVar, dirVar, filterVar, useAjax…}`；
`GET <url>?isAjax=true&limit=20` → 200，HTML 片段含 20 行（首行 "353 Apr 24, 2023…"，139KB）。
DOMParser 可解析。`sortVar/dirVar/filterVar` 表明 legacy 通道**同样支持服务端排序/过滤**。

**D. 意外发现：书签污染跨浏览器实例存活。** 探针用全新 user-data-dir 的 Chrome 首次
进入订单网格，看到的即 679（8/26）留下的 status=complete 过滤（153 行）与 64 留下的
pageSize=400——过滤/分页/排序状态存**服务端 ui_bookmark（按 admin 用户）**，与浏览器实例
无关。这把 data_truncated 报告发现 6（跨任务污染）的严重度上调：**不重置容器/书签，
任何新会话都继承上次任务的全部网格视图状态**。

### 1.2 8/26 重跑日志的通道统计（带 skill 版，本方案的直接动因）

| 任务 | mui 尝试 | uiRegistry data source | 结果 |
|---|---|---|---|
| 64 | 1 次（HTML 失败） | `ds.set` pageSize 400 + reload + 读 items，3 步摸索字段名后 6 步完成 | ✅ 数据全对（Emma Davis/Veronica Costello） |
| 679 | 1 次（HTML 失败） | ds.set filter status=complete + pageSize 1000，2 步成功 | ✅ 数据对；❌ UI 芯片不更新 → checker 挂 |
| 128 | — | —（读 DOM 首两行） | ❌ 假设"已按日期排序"，实际任意序（180/184，2022 年） |
| 121 | 3 次（全失败，含语法错） | —（走 storefront fetch 死路） | ❌ 三层叠加失败 |

**结论**：成功的全靠 uiRegistry，失败税（语法错 + 摸字段名 + 通道试错）每任务 2-4 步。
read_grid 的价值主张成立；但主通道必须是 uiRegistry。

---

## 二、§2.1 read_grid：可行，接口与通道序修订后实施

### 2.1.1 对方案的四处修订

1. **通道序反转**：`uiRegistry data source（主）→ legacy grid AJAX（review 类）→ DOM 表格（kick 后兜底）`。mui 通道从实现中剔除（探针 + 两轮轨迹 0 成功）。
2. **命名空间推断**：不依赖 `data-mage-init` 解析——直接枚举 uiRegistry 组件取**非
   `notification_area` 前缀的首个 `<ns>.<ns>_data_source`**（探针实证两网格均成立），
   比方案设想的推断路径更简单可靠。
3. **新增 `fresh` 语义与状态回报**（探针发现 D 的直接对策）：read_grid 必须**在返回
   元信息中报告当前活动 filters/search/sorting**（如 `filters={"status":"complete"}`，
   totalRecords=153≠全集），并支持 `fresh=true` 先清残留再查询——否则 agent 在污染
   视图上查询而不自知（64 若非全程 308 恰好无残留，8/26 也可能翻车）。
4. **「读数据 vs 留痕迹」边界写进动作描述**：read_grid 走 uiRegistry 时只改数据源
   不更新 UI 过滤芯片（679 实证）——凡任务判分依赖页面留下过滤痕迹（program_html
   读 `admin__data-grid-filters-current`），必须走 Filters 弹窗；read_grid 只用于取数。
   此边界已写入 quirks.md（复盘四-4），动作描述里再钉一遍。

### 2.1.2 接口（修订版）

```python
class ReadGridParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: str | None = Field(default=None,
        description="Grid namespace (e.g. 'sales_order_grid'). Omit → auto-detect "
                    "from the current page's uiRegistry (first non-notification grid).")
    filters: dict[str, Any] | None = Field(default=None,
        description="Grid filters, e.g. {'status':'complete'} or {'qty':{'from':2,'to':3}}. "
                    "Replaces current filters. Pass {} to clear.")
    search: str | None = Field(default=None, description="Fulltext keyword (replaces current).")
    sorting: str | None = Field(default=None,
        description="'field direction', e.g. 'created_at desc'. REQUIRED for "
                    "top-N/latest tasks — never assume row order.")
    page_size: int = Field(default=200, ge=1, le=2000)
    page: int = Field(default=1, ge=1)
    fields: list[str] | None = Field(default=None,
        description="Row fields to return (e.g. ['entity_id','increment_id','created_at','status']). "
                    "Omit → all grid columns.")
    fresh: bool = Field(default=True,
        description="True (default): clear leftover bookmark filters/search before applying "
                    "the given params — grids inherit filters from previous sessions "
                    "(server-side bookmarks). False: apply on top of current state.")
```

返回（`extracted_content` 摘要 + `metadata`）：

```
rows: [ {entity_id, increment_id, created_at, status, ...}, ... ]     ← 结构化，计数不再靠 LLM 数行
meta: { namespace, total_records, rows_returned, page, page_size,
        applied: {filters, search, sorting},        ← 实际生效的查询状态
        active_before: {filters, search, sorting},  ← 查询前的残留（污染可见化）
        channel: "uiRegistry" | "legacy_ajax" | "dom_table" }
```

超长结果复用 evaluate 的大结果落盘机制（`actions.py:2388` `tr.eval_output_dir/evaluate_*.txt`
+ 截断提示），308 行 × 40 字段这类量级直接走文件。

### 2.1.3 实现规格（挂点全部核查过）

| 件 | 位置 | 内容 |
|---|---|---|
| 参数模型 | `tools/models.py` | `ReadGridParams`（上节），随 `EvaluateParams` 风格 |
| 注册 | `tools/actions.py` `ACTION_DEFINITIONS`（:532 `_register_all` 样板） | 新增条目 `(ReadGridParams, 描述, terminates=True)`（数据源 reload 会触发网格重渲染，按终止处理保守正确） |
| 处理器 | `tools/actions.py` `_action_read_grid` | 见下方伪代码 |
| 浏览器侧 | `browser/session.py` | 新增 `read_ui_grid(namespace?, filters?, search?, sorting?, page_size?, page?, fresh?) -> dict`（内部 `self.evaluate`，`await_promise=True, timeout_ms=30000`） |
| 大结果落盘 | 复用 `_action_evaluate` 的 spill 逻辑（抽出共用函数） | rows JSON 序列化超阈值 → 文件 |
| 单测 | `tests/test_read_grid.py`（新） | 见 §5 验收 |

`read_ui_grid` 注入 JS 的核心（一次 evaluate 完成，IIFE + try/catch，伪代码——
reload 等待策略以实现时实测为准）：

```js
(async function(){
  var reg = await new Promise(function(r){ require(['uiRegistry'], r); });
  // 1) 定 namespace：显式 > 枚举（排除 notification_area.*）
  // 2) 取 ds = reg.get(ns + '.' + ns + '_data_source')
  // 3) fresh: 记录 active_before = {filters: ds.params.filters, search: ds.params.search,
  //            sorting: ds.params.sorting}；清 filters/search（保留 placeholder:false 语义）
  // 4) 应用查询：ds.set('params.filters', …) / ('params.search', …)
  //    / ('params.sorting', {field, direction}) / ('params.paging', {pageSize, current})
  // 5) 触发重载并等待：ds.reload()（或 ds.set('params.t'，Date.now()）——实现时二选一），
  //    轮询 data.totalRecords / data.items.length 稳定（≤8s，200ms 步进）
  // 6) 字段裁剪 + 返回 {items, totalRecords, applied, active_before}
})()
```

**通道回退**（同一处理器内，按页特征选择，不串行尝试——探针已给出确定性判据）：

1. 页面存在 `require('uiRegistry')` 且枚举到非 notification 的 `_data_source` → uiRegistry 通道；
2. 否则存在 `window.<x>GridJsObject`（legacy ExtJS：reviewGridJsObject 等，持
   `url/pageVar/sortVar/filterVar`）→ `GET url?isAjax=true&<pageVar>=…&<sortVar>…`
   + form_key，DOMParser 解析行（探针 C 实证）；
3. 否则 DOM 通道：读 `table` 行为结构化数组（通用站点受益——消灭"LLM 在膨胀序列化
   长表上数行漏尾"，128 上轮 5 行数出 4 的病根）。KO 冻结页先经 kick（settle 钩子
   已自动做；read_grid 入口再查一次空行并补 kick）。

### 2.1.4 已知风险与对策

| 风险 | 证据/推理 | 对策 |
|---|---|---|
| ds.set+reload 会写服务端书签 → 跨任务污染视图 | 探针 D：64/679 的 pageSize/filter 残留存活至今 | ① meta 返回 `active_before` 使污染可见；② 动作尾部**还原**进入前 params（再 reload 一次）成本高——首版不做，改为评测装置每任务开局 reset（data_truncated 报告建议 8）+ read_grid 默认 fresh；文档明示 |
| reload 后 KO 行仍可能冻结（DOM 不渲染） | kick 只在 settle 后跑，ds.reload 不触发 settle | read_grid 不依赖 DOM——直接读 `ds.data.items`（冻结只影响渲染，不影响组件数据层；64/679 实证） |
| 等待 reload 完成的轮询不收敛 | 64 S1 用 60s timeout 成功 | 轮询 data 指纹稳定 + 总时长上限 10s，超时返回当前 items + `partial: true` 标记 |
| namespace 多网格页（如 dashboard 内嵌） | 未在本批任务出现 | 枚举取全部非 notification 的 `_data_source`，>1 个且未指定时在返回中列出候选让 LLM 下轮带 namespace 重试（不猜） |
| 语义漂移：LLM 用 read_grid 做"留痕迹"的过滤任务 | 679 型 | 动作 description 明写 "read-only data channel; does NOT update the page UI (filter chips) — for tasks graded on the page's visible filter state, use the Filters panel" |

### 2.1.5 预期收益（对齐 8/26 复盘的真实失败清单）

- 128：`read_grid(sorting='created_at desc', page_size=2)` 一步锁定最近两单（根治乱序脑补），两单视图读 qty 共 ~5 步。
- 62/64/107：`read_grid(page_size=1000, fields=[…])` 一步全量（64 已实证 308 行可行）。
- 186：`read_grid(filters={'qty':{'from':2,'to':3}}, fields=['name','sku','qty'])` 一步。
- 121/116：legacy 通道一步全量评论（116 上轮靠 AJAX 通道 22 步才补齐的活，压缩到 2-3 步）。
- 每任务省 2-10 步语法税/通道税；计数类彻底摆脱"LLM 数行"。
- **不解决**：679 型留痕任务（走 UI）、214/782 型判分缺陷（与通道无关）。

---

## 三、§2.2 快照网格元信息：可行（字段前提 100% 证实），实施要点在成本与时机

### 3.1 证实情况

方案设想"从 uiRegistry 读 datasource 的 sorting 与总数"——探针证实全部存在且一次
evaluate 可取齐：`params.{filters,search,paging,sorting}` + `data.totalRecords` +
`data.items.length`。方案示例标注格式可以直接落地，还应加上**活动过滤器**（探针发现 D）：

```
[Grid] sales_order_grid | rows 153 of 153 (page 1/1) | sorted: created_at desc
       | active filters: status=complete ← leftover from a previous session, not yours
```

128 型失败的根治逻辑：LLM 看到 `sorted: <field> <dir>` 就不会自评 "sorted by Purchase
Date desc"（8/26 S1 的脑补）；看到 rows X of Y 就有翻页/全量的自检锚点（data_truncated
报告建议 2 的"数据完整性对账"从此有免费数据源）。

### 3.2 实施要点（方案未展开的部分）

1. **成本控制**：uiRegistry 读取是一次异步 evaluate（~100-300ms），每步都跑浪费。
   挂点选 `get_state`（`session.py:1828`）——仅当 DOM 探测到 `.admin__data-grid` /
   `data-grid` 容器时才做 registry 读取，并按 `url + paging.current` 缓存（同 URL 同页
   不重读；filters 变化由 URL/芯片 DOM 变化间接反映，缓存键加 `data-mage-init` 容器的
   innerText 长度指纹，取巧但够用）。
2. **数据通道**：注意读的是 `ds.data.items`（数组）而不是 `ds.data`（对象）——本探针
   第一版就踩了这个坑；`totalRecords` 在 `ds.data.totalRecords`，不在 `ds.totalRecords`。
3. **渲染挂点**：走现有 page_stats 通道最省——`step.py:259` 已经把 `dom_state.page_stats`
   传入 `build_state_message`（`system_prompt.py:200` 渲染 `[Page Stats]`）。grid 元信息
   作为独立键 `grid` 并入 page_stats dict，`system_prompt.py` 的渲染分支加两行。
   （不改 DOM 收集器——那个管线重、跑在每一步。）
4. **冻结容错**：uiRegistry 读取**不依赖行渲染**（探针在行文本全空的冻结态下读数成功），
   这正是它优于 DOM 观察的地方；读取失败静默跳过（grid 元信息是增强不是依赖）。
5. **排序状态与实际行序的一致性**：探针当前态 `params.sorting` 与 items 实际序一致，
   但 128（8/26）显示初始加载可能不应用书签排序。因此标注应**同时带首个可见行的
   字段值**（如 `first row: 2023-05-19`）——LLM 可一眼校验声明排序与实际一致；不一致
   时标注 `sorted: UNVERIFIED`。这是对方案的一处小加固。

### 3.3 read_grid 的 sorting 参数（方案第 2 层）

接口已含（§2.1.2），服务端排序由 `ds.set('params.sorting', …)` 承载——探针实证该参数
存在于两网格且值随书签变化，方向/字段语法与 Magento 列字段名一致（`created_at`/`qty`）。
128 类"最值"任务从"相信行序"变为"指定排序"，一步根治。

---

## 四、§2.3 保存成功信号：可行，落点已存在；脏检查降级为观察项（同意方案）

1. **落点**：`actions.py` click 处理器的后效检测块（:752-780）已有 `fp_before/fp_after`
   指纹 + 表单值摘要的比较逻辑——保存成功检测是同块的**第三次读取**：

   ```python
   # R7-3（本方案）：保存/提交通知的显式确认——指纹比较只能区分"变/没变"，
   # 无法区分"保存成功弹了 toast"与"其他变化"。Magento 成功态是
   # .message-success / data-ui-id=messages 的浮层。
   toast = await self._read_page_messages(browser)   # .message-success/.message-error/.message-warning 文本
   if toast:
       memory += f"  ✅ Page message: {toast}"       # 成功→明确"已保存"；错误→当步自愈素材
   ```

   `_read_page_messages` 为新的小 helper（一次 evaluate 读
   `.message-success, .message-error, [data-ui-id="messages"] .message` 的 innerText，
   截断 200 字符）。780/782 轨迹里 "You saved the product." 目前只能靠 LLM 从快照里
   自己发现——产品化后所有保存类操作获得确定性反馈，直接减少保存后的多余验证操作
   （709 型"表单被折腾重置"的源头之一）。
2. **顺带修**：780/782 轨迹中价格字段每次 `input_text` 都被误报 INVALID
   （"This is a required field."——来自表单其他未填字段，非价格字段本身）。
   `_read_validation_state`（actions.py:1162）应把定位收窄到**目标字段自身**的
   mage-error，否则狼来了效应会让 LLM 无视真警告。列入同批小修。
3. **脏检查（导航前 beforeunload 提示）**：同意方案原判断——成本高收益存疑，等 709
   重跑确证后再定。本轮不实施。
4. **诚实边界重申**（方案已写）：终态问题的另一半在判分侧（program_html 的 url:"last"
   读的是任务结束时的页面），工具层无法代偿。这是评测侧议题，不混入本批。

---

## 五、实施批次、验收与验证

### 批次划分（对齐方案"1、2 同批，3 顺手"，按依赖微调）

| 批 | 内容 | 规模 | 依赖 |
|---|---|---|---|
| **B0（零代码，先行）** | quirks.md 修正：通道 1（mui render）标注"本环境不可用（探针 4 变体证伪）"，通道 2（uiRegistry ds.set+reload）升为主配方并附 `ds.data.items`/`params` 字段说明；同步把"read_grid 落地后本条退役"写明 | 改 1 个 md | 无 |
| **B1（核心）** | ① `browser/session.py: read_ui_grid`；② `tools/models.py: ReadGridParams` + `actions.py: _action_read_grid` + ACTION_DEFINITIONS 注册 + 大结果落盘复用；③ `tests/test_read_grid.py` | ~200 行 + 单测 | B0 的字段结论 |
| **B2（同批）** | 快照网格元信息：`get_state` 的 grid 探测 + uiRegistry 读取（带缓存）+ `system_prompt.py` 渲染 `[Grid]` 行 | ~80 行 + 单测 | 与 B1 共用字段常量（可抽 `browser/grid_meta.py` 小模块或 session 内私有常量） |
| **B3（顺手）** | 保存成功信号 `_read_page_messages` + validation_state 收窄到目标字段 | ~40 行 + 单测 | 无 |

### 验收标准（细化方案的两条）

1. **单测**（`tests/test_read_grid.py` + `test_grid_meta.py`）：
   - mock evaluate 返回：namespace 自动检测（含 notification_area 排除）、fresh 清残留
     （active_before 正确回报）、filters/sorting/paging 参数拼装、items→rows 字段裁剪、
     超长落盘路径；
   - 回退判据：无 uiRegistry → 检测 `*GridJsObject`；两者皆无 → DOM 表格；
   - 失败路径：reload 轮询超时 → `partial: true` + 不 raise；
   - grid meta：冻结态（items 有数据但 DOM 行空）元信息仍产出；非网格页零 evaluate。
2. **真机**：探针脚本扩一个 `--selftest` 模式跑三个只读用例（订单网格全量 308/过滤
   complete 153/product_listing qty 2-3），作为环境冒烟。
3. **评测验证**（方案原文采纳 + 协议强化）：`run_category.ps1 -Category data_truncated -Force`
   重跑，**n=3 协议**（复盘五-1 的评测不稳定性结论），对比口径：
   - 128/62/64/107/186 的步数（目标：数据获取 ≤3 步）与通过数；
   - 8/26 真实完成 10/17 的基线上，验证轮目标 **12-14/17**（复盘六-1 预期 + 本批工具增益）；
   - judge 已修正（temperature=0 + 指引），判分口径与 8/26 复盘一致，直接可比。

### 风险与回退

- read_grid 的 ds.set 书签副作用（§2.1.4）：验证轮若观察到跨任务污染加剧，启用
  "用后还原"（记录 active_before，动作尾部 set 回 + reload）——预留接口，不首版实现。
- 快照元信息每步 300ms 开销：缓存键失效过松导致元信息陈旧 → LLM 决策错——缓存键含
  网格容器指纹 + paging.current，宁可多读不可陈旧；性能不达标则降级为"仅在 grid 页
  首次进入与翻页/过滤动作后读取"。
- 全量 page_size=1000+ 的返回体积：落盘机制兜底 + fields 默认裁剪到常用列；
  LLM 上下文安全（128 上轮 24KB 提取文件 + 分段 read_file 的既有模式）。

---

## 六、与既有结论的关系（避免重复建设）

- form_interaction 报告建议 1（settle 后 kick）已落地——read_grid 的 DOM 兜底通道
  依赖它，无需重建；本方案不改动 kick。
- data_truncated 报告建议 5（evaluate 序列终止规则放宽）：read_grid 落地后，网格读取
  类 evaluate 用量大幅下降，该建议的紧迫性下降，但裸 return 自愈/截断检测
  （建议 6）仍值得做——read_grid 的注入 JS 由工具层写死，不再有 LLM 语法税。
- data_truncated 报告建议 8（评测装置每任务清书签）：探针发现 D 证明其严重度高于
  预期（跨浏览器实例存活），read_grid 的 `fresh` 默认值只能保读取正确，不能保 UI
  任务的初始视图干净——评测侧的 reset 仍需独立落地。
- 679 型留痕任务：read_grid 明确不覆盖（§2.1.4 语义边界），仍走 Filters 弹窗路线。

---

## 附：证据与挂点索引

- 探针（本批新增）：`examples/p7_probe_grid_channels.py`（mui 四变体 + legacy AJAX +
  DOM 基线）、`examples/p7_probe_registry_fields.py`（组件枚举 + ds 字段面 + 行序）
- 轨迹：`evals\webarena\results\logs\data_truncated\{64,679,128,121}.log`（8/26 版）
- 复盘：`evals\webarena\docs\data-truncated-review-retrospective-20260827.md`
- 源方案：`evals\webarena\docs\plan-tool-layer-grid-and-sorting.md`
- 代码挂点：`tools/actions.py:532`（ACTION_DEFINITIONS 注册样板）/:752-780（click 后效
  检测块，B3 插入点）/:2321+2388（evaluate 处理器与大结果落盘）/:1162（validation_state
  收窄点）；`browser/session.py:1828`（get_state，B2 插入点）/:3258（evaluate 签名）；
  `prompts/system_prompt.py:200-208`（[Page Stats] 渲染，B2 扩展点）；`agent/step.py:259`
  （page_stats 传递链）
- 既有机制：`session.py:2703`（_kick_frozen_data_grid）；`domain-skills/localhost_7780/quirks.md`
  （B0 修正对象）
