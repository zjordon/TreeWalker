# Judge 复核器一期问题修复:token 控制 + 截断保尾 + DOM 摘要差异化

> 对应 issue:#58
> 分支:`feat/judge-phase1-#58`
> 日期:2026-06-24
> 状态:已设计,待实施
> 前置:一期《Judge 复核器改造方案:补全页面证据》已合入

## 1. 背景(为什么改)

一期 Judge 改造已实施(每步持久化 `dom_excerpt`、`_serialize_history` 补全页面证据、默认开启、`captcha` 字段)。复查一期实现发现:**长 trace(步骤多 + DOM 复杂)下存在 token 失控 + 截断丢 done 的缺陷**。本文档记录问题诊断与修复方案。

用户提问"每步都把 dom 状态塞进 history 再发给模型,步骤多、DOM 复杂时会不会有问题"——经核查,答案部分成立,其中一条是真实 bug。

## 2. 问题诊断

### 2.1 截断方向 bug(严重,必修)

`judge.py` `_build_judge_prompt` 的截断是 `trace[:max_chars]`(**保头砍尾**)。trace 按 step 1→N 顺序拼接,`done` 步在尾部 → 步骤一多,**砍掉的正是 done 步和最终结果**,Judge 最该看的证据反而丢了。

browser-use 也有此反直觉点(`_truncate_text` 默认 `from_beginning=False` 保头砍尾),但它靠"最后 ≤10 张截图保尾"补偿;**TreeWalker 无截图通道,纯文本保头砍尾 = 必丢 done**,与"补全证据"的初衷直接矛盾。

### 2.2 每步都塞满 DOM 摘要

`_finalize` 每步写 `dom_excerpt`(≤2000 字)。100 步 = 20 万字,远超 `trace_max_chars=40000` → 必然触发截断 → 必然丢 done(与 2.1 叠加恶化)。

### 2.3 持久化侧(无问题,已核查)

`dom_excerpt` **只活在内存** `AgentHistoryList`:
- observability 事件模型(`StepEndEvent`/`SessionEndEvent`/`ModelResultEvent`/`ToolResultEvent`)字段**不含** `state_summary`,JSONL 日志(`jsonl_recorder.py`)不写它;
- `AgentHistoryList` 无 save/dump/to_json 方法,`Agent.run()` 结束只 `return self.history`。

100 步 ≈ 200KB 常驻内存,可忽略。**所以本次只解决"发给模型的 token",不动持久化。**

### 2.4 browser-use 对照

browser-use Judge **根本不喂 DOM**,只喂 `actions + extracted_content`(工具抽取的干净文本)+ 尾部 ≤10 张截图,刻意滤掉完整 DOM(state_message)控 token;system prompt 明说"screenshot is not entire content… 文本可信"。TreeWalker 走文本路线,页面证据靠 `dom_excerpt`(done 步)+ `extracted_content`。

## 3. 修复方案

**核心策略(用户已确认)**:`dom_excerpt` 仅 done 步保留完整摘要(作为验证最终结果真伪的独立页面证据),其余步只存 url/title 轻量元数据。

### 3.1 `src/tree_walker/agent/step.py` `_finalize` — 仅 done 步持久化 DOM 摘要

当前(一期)每步都写 `state_summary["dom_excerpt"]`。改为:仅当 `any(r.is_done for r in results)` 时才写 `dom_excerpt`,其余步 `state_summary` 只有 `url/title/duration`。

- `results` 在 `_finalize` 参数作用域内;空列表时 `any(...)` = False,安全(不写)。
- 兜底逻辑不变(`dom_state` 为 None → `""`)。
- `dom_excerpt_max_chars` 配置沿用一期(默认 2000)。

### 3.2 `src/tree_walker/agent/judge.py` `_build_judge_prompt` — 截断改保尾 + 对齐步边界

一期 `trace[:max_chars]`(保头)→ 改为保尾 `trace[-max_chars:]`,并向前对齐到最近的 `Step N:` 边界(避免从半步中间开始),末尾标注 `[trace truncated, kept most recent steps]`。理由:done/最近步是 Judge 最关键证据,早期步只是导航上下文。

### 3.3 `src/tree_walker/agent/judge.py` `_serialize_history` — 空摘要省略该行

`dom_excerpt` 为空(非 done 步)时不输出 `Page excerpt:` 行,trace 更紧凑;done 步正常输出完整摘要。实现:用列表按需 append `Page excerpt` 行,而非固定拼入。

### 预期效果

token 天然受控:仅 done 步重(≈2KB),其余步轻(<300 字),100 步 ≈ 27KB < 40000,基本不触发截断;即便触发,保尾 = 保 done。Judge 仍能看到 done 步独立页面证据验证最终内容,不再因截断丢失。

## 4. 测试更新

`tests/test_step_finalize.py`(适配"仅 done 步存"):
- `test_persists_dom_excerpt`:改用 done 步(`ActionResult(is_done=True)`)才断言有 `dom_excerpt`。
- 新增 `test_no_dom_excerpt_for_non_done_step`:非 done 步 `state_summary` 不含 `dom_excerpt` key。
- `test_truncates_to_dom_excerpt_max_chars` / `test_empty_string_when_dom_state_is_none`:改用 done 步。
- `test_also_records_url_and_title` / `test_state_summary_none_when_browser_state_is_none` / `test_advances_step_counter`:适配(非 done 步只校验 url/title)。

`tests/test_judge.py`:
- `test_long_trace_is_truncated`:断言文案改为 `[trace truncated, kept most recent steps]`,并新增**保尾断言**(截断后 trace 含 `"Step 40"`、不含 `"Step 1"`)——截断 bug 修复的核心验证。
- 新增:done 步输出 `Page excerpt:`、非 done 步无该行的断言。

## 5. 验证

1. `uv run python -m pytest tests/ -x -v` 全绿 + 覆盖率达标(`judge.py` 维持 ~98%)。
2. 端到端(用户本地,需浏览器 + API):`uv run python examples/basic_agent.py`,确认 Judge 仍能看到 done 步 DOM 证据、日志输出 `Judge verdict: SUCCESS`,且长 session 不丢 done。

## 6. 范围外

- **dom_excerpt 内容质量**:`element_tree_text` 取头部可能错过页面中部内容(如搜索结果标题常在中后部)。次要项,本次不改;后续可考虑 done 步取更大阈值或页面可见文本。
- **截图喂 Judge**(二期):见一期文档"范围外",本次不做。
