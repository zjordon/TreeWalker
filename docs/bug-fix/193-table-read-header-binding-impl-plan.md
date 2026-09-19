# issue #193 实施方案：表格取值路由 read_grid + 合计行捕获与列加和交叉校验

- 日期：2026-09-19；分支 `fix/193-table-read-verification`（自 master `6be5a96`）
- **状态（2026-09-19）：A/B/C/D 已实施**——2775 测试全过、总覆盖 90%、两 JS
  模板 node --check 过；待真机探针（D3）与 107 场景回放验收
- 依据：`docs/bug-fix/193-table-read-header-binding-analysis.md`（证据核验/三层
  根因/方向对比；含 204 证据勘误——方向 3 降级为防复发件）
- 范围：A 工具层 JS（合计行捕获）+ B 工具层 Python（column_sums 与交叉校验
  note）+ C 提示词与动作描述三处 + D 测试与真机探针；后置不做见 §7
- 约束：**不新增 read_grid 参数**（合计校验按 issue 语义"页面存在 Total 行即
  强制"——有 footer 就算，无 footer 零行为变化）；不动 dom-snapshot；不动
  done 门禁

## 0. 现象 → 改动映射

| 问题（分析文档 §2 机制链） | 改动 | 位置 |
|---|---|---|
| 快照无表格结构，目测取数混列（C111 四月取 Sales Items） | **C1** Rules 第 8 条：表格取值路由 read_grid（行按表头绑定） | `system_prompt.py` |
| read_grid 可发现性缺口（C 轮 4 任务 0 调用，V 轮宁写 evaluate） | **C2** 动作描述点名报表结果表 + 列头绑定卖点 | `models.py` |
| 合计校验是假的（C107 断言 67 未算；C111 算 175 未比对 Total） | **A** JS 捕获合计行（tfoot + tbody Total 行，并从 rows 剔除防重复加和）；**B** Python 计算 column_sums 并与 footer 逐列比对，一致/不一致都回显 | `actions.py` |
| "verified" 声称无工具输出支撑（C107 "All verified from live table"） | **C3** Task Completion Rules 第 9 条：算术与验证必须来自工具输出 | `system_prompt.py` |
| 列举答案列全自查（证据薄，防复发件） | **C4** Task Completion Rules 第 2 条补一句 | `system_prompt.py` |
| 回归与验收 | **D** 测试扩展 + `examples/debug_read_grid_totals.py` 真机探针 | tests + examples |

## 1. A：JS 侧合计行捕获

### A1 共享读表核心 `_TABLE_ROWS_CORE_JS`（`actions.py`，模板级常量）

现状：`_LEGACY_GRID_READ_JS`（`:469`，DOMParser 解析 fetch 回的文档）与
`_DOM_TABLE_READ_JS`（`:531`，活 document）各自内联一份"heads/rows 按表头
配对"逻辑，几乎重复。本改动两边都要加 footer 捕获——**先抽共享核心防漂移**
（review 先例：诊断脚本 import 复用生产函数防分叉，同理）：

```js
var _TOTRE = /^\s*(grand\s+)?(total|totals|合计|总计|小计)\s*$/i;
function readRow(tr) {
    var cells = tr.querySelectorAll('td,th');
    var row = {};
    for (var c = 0; c < cells.length; c++) {
        var key = (c < heads.length && heads[c]) ? heads[c] : ('col' + c);
        if (p.fields && p.fields.length && p.fields.indexOf(key) < 0) { continue; }
        row[key] = (cells[c].innerText || cells[c].textContent || '').trim();
    }
    return row;
}
function readTable(root) {
    var rows = [], footer = [];
    var trs = root.querySelectorAll('tbody tr');
    for (var k = 0; k < trs.length; k++) {
        if (!trs[k].querySelectorAll('td').length) { continue; }
        var first = (trs[k].querySelector('td') .innerText
                     || trs[k].querySelector('td').textContent || '').trim();
        if (_TOTRE.test(first)) { footer.push(readRow(trs[k])); continue; }
        rows.push(readRow(trs[k]));
    }
    var ftrs = root.querySelectorAll('tfoot tr');
    for (var f = 0; f < ftrs.length; f++) {
        var fr = readRow(ftrs[f]);
        if (Object.keys(fr).length) { footer.push(fr); }
    }
    return {rows: rows, footer: footer};
}
```

要点：

- **tbody 内 Total 行挪出 rows**：不剔除则 B 的 column_sums 会把合计行再加一
  遍（sum = 2×total），交叉校验必假警报。首格**全字匹配**（非子串）保守
  判定，"Total"/"Grand Total"/"合计"/"总计"/"小计"；数据行首格恰好叫
  "Total" 的误捕风险接受（罕见，且后果只是少一行数据+多一行 footer）。
- **tfoot 全收**：Magento 报表合计行落 `tfoot` 还是 `tbody` 未真机确认
  （分析文档 §7 风险）——两类都接住，实现不赌。
- `readRow` 用 `td,th`（合计行标签格常是 `th`，tfoot 尤甚）；普通行仍走
  原有"有 td 才算行"的过滤。
- fields 过滤沿用原逻辑（footer 也过 fields——agent 只要 Orders 列时 footer
  只回 Orders 格，B 侧比对照常工作）。

### A2 两条通道模板接入

- `_LEGACY_GRID_READ_JS`：heads 解析后调 `readTable(doc)`，返回体加
  `footer: out.footer`；
- `_DOM_TABLE_READ_JS`：最大表选取后调 `readTable(best)`，返回体加
  `footer: out.footer`（原 note 保留）；
- 通道契约：`footer` 为 list[dict]（表头键控），无合计行为 `[]`（不是 null，
  B 侧判空简单）。

### A3 零影响清单（核验过，不改）

- uiregistry 通道（`session.read_ui_grid`）无 footer——B 侧对其零行为变化；
- `rows_returned`、headers、applied/active_before、partial 等字段语义不变；
- `_action_read_grid` 的通道梯/守卫/落盘/`group_count`/零结果信号
  （#186-c2）均不触碰。

## 2. B：Python 侧 column_sums + 交叉校验回显（`actions.py` `_action_read_grid`）

### B1 数值解析 `_parse_grid_number`（模块级，含单测锚点）

```python
_NUM_STRIP_CHARS = " \t\r\n $€£¥%,"


def _parse_grid_number(value: Any) -> float | None:
    """'$1,234.56'→1234.56、'67'→67.0、'12.5%'→12.5；非数值/空→None。

    issue #193 方向 2：合计交叉校验的前置。容忍货币符/千分位/百分号与
    NBSP（Magento 报表格式）；负数/小数照常。前导 '+' 不剥（'+5' 视同非数
    值——报表罕见，宁可漏和不可错和）。
    """
    if value is None:
        return None
    s = str(value).strip().strip(_NUM_STRIP_CHARS)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None
```

### B2 求和与比对（`_action_read_grid` 内，group_counts 块之后）

```python
# issue #193 方向 2：Total 行交叉校验——只在 footer 非空时计算（uiregistry 无
# footer 不产噪声；entity_id 之类字段求和无意义，且无 Total 行可比即无意义）。
# 算术代码化，镜像 group_count 哲学（#185 D：计数不交给上下文 tally，加和同
# 理不交给 LLM 心算——C107 断言 "total 67 matching" 实加 130，C111 算出 175
# 未与 Total 行 94 比对）。
footer_rows = result.get("footer") or []
total_check_line: str | None = None
if footer_rows:
    # 1) 逐列求和：列内所有非空值都可解析才算数值列（混入 'N/A' 即整列跳过，
    #    宁可不算不可错算）；空格跳过但计数（漏行信号）。
    col_vals: dict[str, list[float]] = {}
    col_skipped: dict[str, int] = {}
    for r in result.get("rows") or []:
        for k, v in r.items():
            n = _parse_grid_number(v)
            if n is None:
                if v is None or not str(v).strip():
                    col_skipped[k] = col_skipped.get(k, 0) + 1
                else:            # 非空非数值 → 该列不参与
                    col_vals.pop(k, None)
                    col_skipped[k] = -10**9   # 毒标记：列破产
            elif k in col_vals and col_skipped.get(k, 0) >= 0:
                col_vals[k].append(n)
            elif k not in col_vals and col_skipped.get(k, 0) == 0:
                col_vals[k] = [n]
    sums = {k: sum(v) for k, v in col_vals.items() if v}
    # 2) 与 footer 逐列比对（同列名同键；tolerance 半分钱）
    parts = []
    warn = False
    for frow in footer_rows:
        for k, s in sums.items():
            fcell = _parse_grid_number(frow.get(k))
            if fcell is None:
                continue
            ok = abs(s - fcell) < 0.005
            warn = warn or not ok
            parts.append(f"{k}: sum {s:g} {'==' if ok else '≠'} footer {fcell:g}"
                         + ("" if ok else " ✗"))
    if parts:
        total_check_line = "totals-check: " + " | ".join(parts)
        if warn:
            total_check_line += (" — a column sum that ≠ its Total-row cell means "
                                 "wrong column or missing/extra rows (this read is "
                                 "page-local); re-read before answering")
```

（实现时可把毒标记写得更直白——`col_broken: set[str]`；上示只表意。）

### B3 回显接线（镜像 gc_line 的先例）

- `total_check_line` 与 `gc_line` 同级：`visible` 前置拼接
  （`gc_line` 在前、`total_check_line` 随后，都以 ` | ` 并入）；
  mismatch 警示**不进 `notes`**——`notes` 渲染统一加 `⚠️ ` 前缀，把 ✓ 一致
  的信息也误标成警告（见 `actions.py:2836-2837` 现状）；`total_check_line`
  自带 ✗ 标记，混排一行更省 token。
- `memory` 行追加 `, totals-ok` / `, totals-mismatch`（供轨迹侧读）；
- `text`（JSON 落盘/预览）天然含 `footer` 字段（JS 返回体的一部分），无
  需额外处理；大结果落盘路径零改动。

### B4 行为表（验收基准）

| 场景 | rows | footer | 回显 |
|---|---|---|---|
| 107 报表（读对列） | Orders 8/13/9/8/10/4/5/10 | Total 行 Orders=67 | `totals-check: Orders: sum 67 == footer 67`（无 ✗） |
| 107 报表（取错列当 Orders） | SI 值 25/34/28/… | Total 行 SI=199 | `Sales Items: sum 199 == footer 199`——**各列各比各的 footer 格**；错列靠 C1/C3 让 agent 对"我报的数"与"我声称的列"的 sum 自查暴露 |
| 合计行被误算进 rows（防护回归） | — | — | A1 剔除后不存在；JS 侧结构性断言 + 真机探针把关 |
| 无 Total 行 | 任意 | `[]` | 无 totals-check 行（零噪声） |
| uiregistry 通道 | 原始行 | 无 footer 字段 | 同上，零变化 |
| footer 含 "$1,242.56" | "$1,234.56"+"8" | 同列 "$1,242.56" | `sum 1242.56 == footer 1242.56` |

注（对"错列重读"验收的支撑链）：工具侧给的是**每列 vs 自己 footer 格**的
确定性对账；"agent 报错列"这一侧由 C3 的规则闭合——报告值须与声称列的
tool-output sum 一致，不一致即换列重读。工具无法知道 agent"心里用了哪列"，
这是设计边界而非缺口。

## 3. C：提示词与描述（3 文件 4 处）

### C1 Rules 第 8 条（`system_prompt.py:31-34` 规则 7 之后）

```
8. When a task answer depends on specific values shown in a table (counts, prices, \
quantities, per-row names), read the table with `read_grid` instead of copying \
values off the DOM snapshot — rows come back keyed by column header, and adjacent \
numeric columns are indistinguishable in the flattened snapshot tree.
```

### C2 Task Completion Rules 第 9 条（第 8 条"Unattainable values"之后）

```
9. **Real arithmetic, real verification** — sums and cross-checks must come from \
tool output, never from mental math: `read_grid` reports the table's Total/合计 \
row and computed per-column sums; your per-row values must sum to the Total-row \
cell of the SAME column. A mismatch means wrong column or missing rows — re-read \
before answering. Never state "verified" about numbers unless a tool result from \
this session contains them.
```

### C3 Task Completion Rules 第 2 条扩一句（方向 3 防复发件）

现文（`:66`）"are all items found? Are counts correct?"后接：

```
For list/top-N answers, reconcile the number of items you report against the \
tool's rows_returned/total_records, and state ties explicitly.
```

### C4 read_grid 描述（`models.py:775-786`）

```
"Read structured rows from the page's tables and data grids — UI-component grids, "
"legacy AJAX grids, plain HTML tables, and report result tables. Rows come back "
"keyed by COLUMN HEADER, so adjacent numeric columns cannot be confused — use this "
"instead of reading table values off the DOM snapshot by cell position. When the "
"table has a Total/合计 row, it is returned as footer together with computed "
"per-column sums for cross-checking. Bypasses row-render freezes; returns JSON "
"rows plus metadata (total_records, sorting, active filters). Pass "
"sorting='field desc' for top-N/latest queries — never assume row order. Read-only "
"data channel: does NOT update the page UI (filter chips) — tasks graded on visible "
"filter state must use the Filters panel."
```

（保留原 sorting/只读通道警告语义，仅前置列头绑定与合计校验卖点。）

## 4. D：测试与探针

### D1 `tests/test_read_grid.py` 新类 `TestFooterTotals`（已实施，10 用例）

沿 `_FakeBrowser` 模式（JS 不真跑，evaluate mock 回 channel dict——footer 是
JS 返回体字段，Python 侧逻辑全可测）：

1. `test_footer_match_note`：107 真值形态（8 列值加和 67）→ `totals-check`、
   `Orders: sum 67 == footer 67`、无 `✗`、memory `totals-ok`；
2. `test_footer_mismatch_note`：rows 加和 130 ≠ footer 67 → `≠`、`✗`、
   `re-read before answering`、memory `totals-mismatch`；
3. `test_no_footer_no_totals_line`：uiregistry 成功返回（无 footer）→ 无
   `totals-check`（零回归锚点）；
4. `test_currency_and_thousands_parsing`：`"$1,234.56"`+`"8"` vs
   `"$1,242.56"` → 相等通过；
5. `test_non_numeric_column_excluded`：Name 列混非数值 → 求和跳过该列；
6. `test_empty_cells_skipped`：列内空值跳过仍求和，mismatch 时回显
   `(N empty cells skipped)`；
7. `test_multiple_footer_rows`：双合计行（异常形态）→ 每行都比，输出稳定；
8. `test_footer_all_non_numeric_no_line`：footer 无任何可比数值格 → 不产
   totals-check（无噪声）；
9. `test_footer_non_numeric_cell_skipped`：footer 与求和列同键的格非数值
   （'n/a'）→ 该列不比对不误报；
10. `test_js_templates_capture_footer`（结构性断言，先例 `test_falls_back_
    to_legacy_ajax` 断言 JS 含 `GridJsObject`）：两模板均含 `_gridReadTable(`
    与 `footer.push`；核心含 `tfoot tr`/`_gridIsTotalLabel`；且**无反斜杠**
    （actions.py 模板家规——实施时把方案原稿的 `_TOTRE` 正则改为标签集合
    比对 `_gridIsTotalLabel`，语义不变：首格小写化+trim 后全字匹配）。

### D2 `tests/test_system_prompt.py` 新断言（沿 contains 风格）

- `test_prompt_contains_table_value_routing_rule`（C1 关键句）；
- `test_prompt_contains_real_arithmetic_rule`（C2 关键句）；
- `test_prompt_contains_list_completeness_rule`（C3 关键句）。

### D3 `examples/debug_read_grid_totals.py` 真机探针（沿 `debug_issue185_cdp_shape.py` 体例）

连 9223（P7 红线：别碰 9222），打开 Orders 报表并施加 107 同款筛选
（Period=Month / 5/1/22–12/31/22 / Complete / Show Report——可直接 navigate
到 filter URL），跑 `Tools().execute("read_grid", {})`，打印：

- `channel`（预期 dom_table；顺带回答"报表页 uiRegistry 通道是否存在"）；
- `footer` 内容与合计行落点（**tfoot 还是 tbody——本方案唯一待真机确认项**，
  A1 两类都接住，此项只影响对探针输出的解读，不影响实现）；
- `totals-check` 行（预期 `Orders: sum 67 == footer 67`）。

## 5. 验证步骤

1. `uv run python -m pytest tests/test_read_grid.py tests/test_system_prompt.py -x -v`
2. 全量 `uv run python -m pytest tests/ -x`（CLAUDE.md：全绿才收）+ 覆盖率 ≥85%
   （read_grid/新增行属旧模块，预期不降）；
3. 真机探针（D3）——footer 捕获 + totals-check 对账 + 通道落点确认；
4. **107 场景回放**（issue 验收，eval 环境/Linux 侧）：逐月值与 Total 行加和
   一致；错列路径（人工诱导或坏卡残留）下 mismatch 回显触发换列重读；
   B 轮 111 的 `sales_order_grid` uiregistry 路径回归不受影响（D1-3 锚点）。

## 6. 风险

- **Total 行误捕**：数据行首格全字 "Total" 罕见；后果=该行进 footer（数据
  少一行 + 多一合计行），totals-check 仍自洽。
- **colspan 合计行**：footer 格与表头错位 → 比对取不到同列格（footer.get(k)
  为 None）→ 静默跳过，不误报；取到错位格则 mismatch 警示文案本身含"wrong
  column or missing/extra rows"的开放式指引，可容忍。
- **分页表 page-local 假警报**：dom_table 只读当前页，多页表可见行加和 ≠ 全
  局 footer——警示句已带 "this read is page-local" 缓冲；报表场景（8–10 行
  无分页）不受影响。
- **提示词增量**：+2 规则 +1 句扩写 ≈ 700 字符；与规则 7（大列表 evaluate
  聚合）分工明确（7 管聚合离开，8 管取值通道），无语义冲突。
- **JS 重构面**：legacy/DOM 模板抽共享核心，字符串模板拼接错误在 D1-8 结构
  断言 + 真机探针双保险下暴露。

## 7. 后置不做

- read_grid 表格指定参数（多表页选靶；B 轮 111 已示范 agent 换 namespace 自纠）；
- done 门禁值级校验（无页面访问，射界维持 #186 的不确定标记）；
- dom-snapshot 表格结构化（全站 token 形态影响，read_grid 通道已覆盖）；
- uiregistry 通道 column_sums（无 footer 无对账对象，纯噪声）；
- 卡侧修复与蒸馏自洽校验（evals 仓 B4 清单另案——本方案落地后坏卡值会被
  `sum ≠ footer` 当场暴露，不构成依赖）。
