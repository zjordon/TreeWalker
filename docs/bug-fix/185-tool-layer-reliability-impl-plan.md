# issue #185 实施方案：工具层可靠性五修复

- 日期：2026-09-13；分支 `fix/185-tool-layer-reliability`（自 master 33c44ce）
- 依据：`docs/bug-fix/185-tool-layer-reliability-analysis.md`（根因分析，证据链见彼处）
- 目标：一个 PR 覆盖 issue 五个请求项的核心；每项改动独立可测、可单独 revert

## 0. 请求项 → 改动映射

| issue 请求项 | 改动 | 层级 | 涉及文件 |
|---|---|---|---|
| 1. evaluate 语法预检/自动修复/结构化引导 | **A1** 匹配源修复（复活自愈+截断提示）+ **A2** 单测桩改真实形状 + **A3** schema 描述补长度建议 | P0 | session.py / test_p7_form_interaction_fixes.py / models.py |
| 5. screenshot 媒体页专用文案 | **B** TimeoutError 分层捕获满信息文案 | P0 | session.py |
| 3. read_file 预览放宽/摘要 | **C1** 窗口与显示上限对齐（诚实 footer）+ **C2** `__str__` 截断标记 | P0 | actions.py / views.py |
| 2. read_grid 分组计数 | **D** `group_count` 参数（Python 侧 Counter） | P1 | models.py / actions.py |
| 4. read_grid legacy 不兼容提示 | **E** 通道返回 headers + 空行/0 行诊断 note | P1 | actions.py |

后置不做：定界符平衡修复候选、read_file JSON summary 模式、navigate 媒体文档前置提示（见 §8，各有独立风险或需单独验证）。

---

## 1. A1：evaluate 自愈复活——err_text 匹配源（session.py:3584）

### 现状与改法

```python
        if result.get("exceptionDetails"):
            exc = result["exceptionDetails"]
            err_text = str(exc.get("text", ""))
```

改为（拼接 text + description；运行期 promise 拒绝的语义在 text、编译期在 description，取并集）：

```python
        if result.get("exceptionDetails"):
            exc = result["exceptionDetails"]
            # issue #185 根因A：编译期 SyntaxError 的 text 恒为 "Uncaught"，语义
            # 只在 exception.description（examples/debug_issue185_cdp_shape.py 于
            # Chrome 152 实测）；运行期 promise 拒绝则语义在 text。自愈候选与
            # 截断提示两处子串匹配都对拼接后的全量文本，否则永不命中（B 轮
            # 52 次 evaluate 失败中 20 次自愈未触发、18 次提示未触发）。
            err_text = str(exc.get("text", "")) + " " + str(
                exc.get("exception", {}).get("description") or "")
```

`_syntax_repair_candidates` / `"Unexpected end of input" in err_text` / `_format_eval_exception` 均不改——A1 生效后：
- 16 次裸 return + 4 次缺 catch 的确定性自愈开始工作；
- 18 次 EOF 开始收到 "⚠️ The code looks truncated — split it into shorter evaluate calls." 引导。

注意 `err_text` 也被 `err_text.splitlines()[0][:80]` 用于自愈日志（session.py:3604）——拼接后首行仍是 text（"Uncaught "），可接受；若想日志更可读可改为取 description 首行，非必须。

### A2：单测桩改真实 CDP 形状（test_p7_form_interaction_fixes.py）

现状三处桩（74、87、97 行）：

```python
{"exceptionDetails": {"text": "Uncaught SyntaxError: Illegal return statement"}}
```

改为真实协议形状（编译期语义在 description）：

```python
{"exceptionDetails": {
    "text": "Uncaught",
    "exception": {"description": "SyntaxError: Illegal return statement"},
}}
```

纯函数测试（41-47 行直接传 err_text 给 `_syntax_repair_candidates`）不动——函数契约未变。**新增**一条 session 级回归：真实形状下 evaluate 触发自愈重试成功（沿既有 fake client 测试的构造方式），防契约再漂移。

### A3（可选顺手）：EvaluateParams.code 描述补一句

`models.py` code 字段描述尾部追加：`Keep code short (<300 chars) — longer nested code tends to lose brace balance.` B 轮经验阈值，一行零风险。

### 验收

- 新增/修改测试全绿；`uv run python -m pytest tests/test_p7_form_interaction_fixes.py tests/test_evaluate.py -x -v`
- 真机冒烟（9223，探针思路）：`session.evaluate("return 1")` 不再抛 "Illegal return statement" 而是返回 `"1"`（IIFE 自愈生效日志可见 `syntax self-heal applied`）

---

## 2. B：screenshot TimeoutError 分层（session.py:2049-2051）

### 现状与改法

```python
        except Exception as e:
            logger.warning("Page.captureScreenshot failed: %s", e)
            raise
```

改为（TimeoutError 子句在前——Python 3.11+ 它是 OSError 子类，会被 Exception 吞掉；`str()` 为空串正是现象⑤根因）：

```python
        except TimeoutError as e:
            # issue #185 现象⑤：asyncio.TimeoutError 的 str() 是空串，不加这层
            # 日志/ActionResult/LLM 三层只见 "Screenshot failed: "（task_374 三连
            # 10.00s 空消息）。裸媒体文档（image/video 无合成器新帧）与最小化/
            # 遮挡窗口（P6 screencast 零帧同因）都走这里；文案给出成因与出路，
            # 避免 agent 当可重试故障连环试（task_374 为此烧 6 步）。
            msg = (
                f"timed out after {timeout}s waiting for a frame — raw media pages "
                "(image/video URLs have no page DOM to screenshot) and minimized/"
                "occluded windows both cause this; navigate to an HTML page wrapping "
                "the media, or report/save the media URL instead of screenshotting"
            )
            logger.warning("Page.captureScreenshot failed: %s", msg)
            raise RuntimeError(msg) from e
        except Exception as e:
            logger.warning("Page.captureScreenshot failed: %s", e)
            raise
```

actions.py:1731 的 `f"Screenshot failed: {e}"` 不动——消息自动变满。超时值（10s）不动（P6 既定防挂策略）。

### 验收

- tests/test_screenshot.py 新增：fake client 的 captureScreenshot 挂起 + `screenshot_timeout=0.1` → 断言 RuntimeError 消息含 "raw media" 与 "minimized"；既有普通异常路径不回归
- 真机冒烟（9223）：导航裸 jpeg → screenshot 一步拿到满信息文案

---

## 3. C：read_file 双截断链（actions.py:2246 + views.py:48）

### C1：窗口与显示上限对齐（actions.py `_window_and_echo`）

```python
        max_chars = self._truncation.read_file_max_chars
        window = limit if (limit is not None and limit < max_chars) else max_chars
```

改为：

```python
        # issue #185 现象③：窗口必须 ≤ LLM 实际可见上限——ActionResult.__str__
        # 的 display_max_chars 会静默截断 extracted_content，窗口大于它时 footer
        # 报 5000 而 LLM 只见 4000，每块尾 1000 字符永远不可见（task_64 tally
        # 漂移推手）。取 min 保证「footer 说展示了多少 = LLM 真看到多少」。
        max_chars = min(
            self._truncation.read_file_max_chars,
            self._truncation.display_max_chars,
        )
        window = limit if (limit is not None and limit < max_chars) else max_chars
```

offset/limit/footer 逻辑零改动（窗口变量即续读步长，自动一致）。

### C2：`__str__` 截断标记（views.py:43-53，全局安全网）

```python
        if self.extracted_content:
            parts.append(f"EXTRACTED: {self.extracted_content[:self.display_max_chars]}")
```

改为：

```python
        if self.extracted_content:
            visible = self.extracted_content[: self.display_max_chars]
            marker = (
                f" [...display truncated: showing {self.display_max_chars} of "
                f"{len(self.extracted_content)} chars — re-read with smaller window]"
                if len(self.extracted_content) > self.display_max_chars
                else ""
            )
            parts.append(f"EXTRACTED: {visible}{marker}")
```

这是所有工具共用的兜底（extract 8000 字符块同样受益），不只 read_file。

### 行为变更声明（评测口径需知）

1. 默认窗口 5000→4000：大文件步数可能 +1（10609 字符两口径都是 3 块；4001-5000 字符文件从 1 块变 2 块）；
2. 超限 EXTRACTED 尾部多一行标记（LLM 可见文本变化）。
   两者都是 issue 请求项 3 的直接落实（诚实化），非能力收窄：agent 显式 `limit` 仍可在窗口内自定。

### 验收

- tests/test_read_file.py：既有 5000 口径断言更新；新增契约用例「footer 的 showing N == extracted_content 传给 __str__ 后 LLM 可见长度」
- views 测试（grep `EXTRACTED` 定位既有断言文件）：超限标记出现/不出现两态

---

## 4. D：read_grid `group_count`（models.py + actions.py）

### 参数（ReadGridParams 追加）

```python
    group_count: str | None = Field(
        default=None,
        description=(
            "Field name to aggregate on, e.g. 'billing_name': returns exact "
            "per-value row counts (computed in Python, not context tallying). "
            "Works on every channel; combines with filters/sorting/paging. "
            "Pass fields=[that field] to slim returned rows."
        ),
    )
```

### 处理（`_action_read_grid`，参数守卫区 + 回显区各一段）

守卫（与 filters/search 守卫同区）：

```python
        group_field = params.get("group_count")
        if group_field is not None and (
            not isinstance(group_field, str) or not group_field.strip()
        ):
            return ActionResult(error="read_grid failed: group_count must be a non-empty string field name.")
```

聚合（通道回落定案后、大结果落盘前；对 rows 做 Python 侧 Counter——通道无关，legacy/DOM 同样生效）：

```python
        group_counts: list[tuple[str, int]] | None = None
        if group_field is not None:
            counts: dict[str, int] = {}
            for r in (result.get("rows") or []):
                v = r.get(group_field)
                k = "(missing)" if v is None or (isinstance(v, str) and not v.strip()) else str(v)
                counts[k] = counts.get(k, 0) + 1
            group_counts = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
```

回显（**不受 saved_to 分支影响**——rows 落盘时计数仍进可见 echo，否则大网格丢结论）：

```python
        if group_counts is not None:
            rows_read = result.get("rows_returned", len(result.get("rows") or []))
            top = group_counts[:50]
            gc_text = json.dumps(dict(top), ensure_ascii=False)
            gc_line = f"group_count[{group_field}] over {rows_read} rows: {gc_text}"
            if len(group_counts) > len(top):
                gc_line += f" (+{len(group_counts) - len(top)} more values omitted)"
            total = result.get("total_records")
            if isinstance(total, int) and total > rows_read:
                gc_line += (f"  ⚠️ counted {rows_read} of total {total} rows — "
                            "read remaining pages for exact counts")
            if group_counts and group_counts[0][0] == "(missing)" and len(group_counts) == 1:
                gc_line += ("  ⚠️ field not present in returned rows — check the "
                            "field name (legacy/DOM channels use display-name headers)")
            visible = (visible + f"  {gc_line}") if saved_to else (gc_line + " | " + visible)
```

（实现时按现有 meta_bits/notes 的拼装风格落位，上面是语义不是逐字。）

### 验收

tests/test_read_grid.py（沿 `_FakeBrowser` 桩扩展）：
- 正常聚合：308 行含重复名 → 计数精确（Emma Davis=2 类用例，重复值跨行出现）；
- Top-50 截断标记；`(missing)` 归并与字段名不匹配 ⚠️；
- `total_records > rows_read` 的不完整计数 ⚠️；
- 参数校验失败路径；与 saved_to 落盘共存（echo 仍含计数）。
真机冒烟：`group_count=billing_name` 于 sales_order_grid，计数与 docker mysql 地面真值比对（P7 工作流）。

---

## 5. E：read_grid legacy/DOM 不兼容 note（actions.py 两段 JS + 回显）

### JS 侧（`_LEGACY_GRID_READ_JS` / `_DOM_TABLE_READ_JS` 返回值各加一键）

两段 JS 已构建 `heads` 数组，返回 payload 增加 `headers: heads`（Python 侧组 note 用）。

### Python 侧（通道定案后的 notes 区）

```python
        if result.get("channel") in ("legacy_ajax", "dom_table"):
            _rows = result.get("rows") or []
            _heads = result.get("headers") or []
            if _rows and all(not r for r in _rows):
                notes.append(
                    f"all {len(_rows)} rows came back EMPTY: requested fields "
                    f"{fields} match none of this channel's headers "
                    f"{_heads[:12]} — legacy/DOM channels key rows by display-name "
                    "headers; retry without fields or with the listed header names"
                )
            elif not _rows:
                notes.append(
                    "0 rows returned; if the grid visibly shows rows, this channel "
                    "likely does not support the requested filters/search (legacy: "
                    "paging/sorting only) — retry without filters or use the UI "
                    "Filters panel"
                )
```

覆盖 task_112 step6 形态（fields=review_id/sku vs 显示名头 ID/SKU → 全空对象行）。0 行分支只在 legacy/DOM 通道（uiRegistry 通道 0 行通常是真过滤结果，有 total_records 可佐证，不误报）。

### 验收

- tests/test_read_grid.py：legacy 回落用例加 fields 不匹配变体（断言 note 含 headers 名单）；0 行 + 页面有行变体；正常 legacy 读取不出现 note
- 真机：reviewGrid 页 read_grid 带 fields → 收到含 headers 名单的 note（免试止损）

---

## 6. 实施顺序与提交切分

单 PR，按依赖序分批提交（用户授权提交时）：

1. **A1+A2（+A3）**——session.py 一处 + 测试桩三处；独立收益最大（≈38% evaluate 失败消失）
2. **B**——session.py 独立函数，零耦合
3. **C1+C2**——actions.py + views.py；行为变更声明随 commit message 带上
4. **D+E**——同文件（models.py/actions.py/test_read_grid.py）同批

每批后跑对应测试文件，末尾全量 `uv run python -m pytest tests/ -x -v` + 覆盖率检查（>85%）。

## 7. 测试与验收清单（汇总）

- [ ] `uv run python -m pytest tests/test_p7_form_interaction_fixes.py tests/test_evaluate.py -x -v`（A）
- [ ] `uv run python -m pytest tests/test_screenshot.py -x -v`（B）
- [ ] `uv run python -m pytest tests/test_read_file.py -x -v` + views 截断标记两态（C）
- [ ] `uv run python -m pytest tests/test_read_grid.py -x -v`（D/E）
- [ ] 全量 `uv run python -m pytest tests/ -x -v` 全绿，覆盖率 >85%
- [ ] 真机冒烟（Chrome 9223，只读优先）：裸 return 自愈、裸 jpeg screenshot 文案、sales_order_grid group_count 对 mysql 真值、reviewGrid fields note
- [ ] 收益口径复验（非门槛）：task_112 evaluate 15-17 → 1 步；task_64 数数 20 步 → 1-2 步；task_374 止损 6 步 → 1 步

## 8. 后置不做（另立 issue/小步）

| 项 | 不做原因 |
|---|---|
| 定界符平衡修复候选（EOF/多余 token 的栈修复） | 启发式改动语义（提前闭合函数可能返回非预期值）；A1 的引导文案已把损失压到 1 步/次，先观测 |
| read_file JSON summary 模式 | D 的 group_count 已覆盖主场景（数数）；通用摘要等真实需求再上 |
| navigate 媒体文档前置提示 | B 的满信息文案已把止损压到 1 步；前置探测加 navigate 路径分支，收益/风险比偏低 |
| GLM 端点侧"真截断"鉴别（save_conversation 复跑比对） | 仅影响 err 文案措辞，不阻塞本方案；分析文档 §1.4 已记方法 |

## 9. 缩进与规范提醒（实现时）

- session.py / actions.py / views.py / models.py：**tab**；
- test_p7_form_interaction_fixes.py：现状 4 空格（按文件现状跟随，勿混）；
- 其余测试文件以打开时实测为准（memory：缩进 per-file 混用，首行启发式不可靠）；
- 不主动 commit/push（CLAUDE.md）；本方案与分析文档、探针脚本随分支走。
