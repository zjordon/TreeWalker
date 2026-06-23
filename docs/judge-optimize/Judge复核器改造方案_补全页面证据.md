# Judge 复核器改造方案:补全页面证据(对标 browser-use)

> 对应 issue:#58
> 分支:`feat/judge-phase1-#58`
> 日期:2026-06-23
> 状态:已设计,待实施

## 1. 背景(为什么改)

运行 `examples/basic_agent.py`(google 搜索取前三标题)时,agent **实际成功**了(导航到结果页、读到真实标题),但 Judge 打印 `verdict: FAILED`,理由是"没导航到 google""没读取步骤""标题疑似幻觉"。根因是 **Judge 与主 agent 信息不对称**:

- `_serialize_history`(`src/tree_walker/agent/judge.py:144-169`)只序列化 `(next_goal, action_name(params), result_str)`,看不到 DOM/URL/页面文本/截图。
- `result_str = str(r)` 走 `ActionResult.__str__`,被 `display_max_chars` 截到 500 字(`agent/views.py:32`),丢失 `extracted_content` 真实内容。
- >20 步时丢弃全部中间步(`judge.py:152`)。
- 主 agent 每步看到的 DOM 文本(`element_tree_text`)从不写进 history。
- 初始导航(`agent.py:199-204`)发生在 `_step()` 之前,不写 history → Judge 看不到"已导航"。

实际后果:FAILED 仅打 WARNING,**不影响** `is_done()`/`final_result()`(`agent/views.py:86-96`),但会误导用户,且 Judge 无法承担"质量裁判"职责。

## 2. 架构判断与对标

### browser-use 的做法

browser-use 的 Judge **默认开启**(`use_judge=True`),核心杀器是**把最后 ≤10 张截图(原图)喂给评审 LLM**(`browser_use/agent/judge.py:73-89`),同时把工具的 `extracted_content`(真实页面文本)带进 trace(`agent_steps()`,`views.py:887-915`),结构化输出 `JudgementResult`(verdict/failure_reason/impossible_task/captcha),40000 字符截断且不丢中间步,prompt 显式规则"agent 报告完成但截图显示未完成 → false"。verdict 挂到 `ActionResult.judgement`,**不覆盖** agent 自报 success。

注意:browser-use 在文本维度也把 `state_message`(完整 DOM)**滤掉了**——它赌截图能补;若 `use_vision=False`,judge 退化成压缩摘要(和 TreeWalker 现状一样)。

### TreeWalker 的架构事实

- **DOM 文本路线**:`step.py:130` 写死 `include_screenshot=False`(注释自承"视觉通道尚未打通");`build_state_message` 把 `element_tree_text` 作为唯一页面证据。
- **LLMClient 不支持 image**:`client.py:83/136` 显式 `isinstance(content, str)` 跳过非字符串,无 image block 处理(底层 Anthropic SDK 支持,但封装层限制;且智谱 glm 走 Anthropic 兼容协议是否真吃 image block **未验证**)。
- **截图投递通道完全没打通**:`take_screenshot`/`resize_screenshot_bytes` 能力齐全,但不进主 agent 输入、不进 Judge、不进 history。

### 结论:走"DOM 文本/页面证据喂 Judge",不走截图路线

TreeWalker 是 DOM 文本架构,而页面证据的数据通路**已存在**(`element_tree_text` 每步已生成、`extract` 已产页面文本、`state_summary.url/title` 每步已存)。补齐这些给 Judge 是纯文本链路、低成本、零外部依赖风险。**截图路线**(对标 browser-use 核心杀器)有三重未验证风险(glm image 兼容性、改 LLMClient str 假设、新建截图持久化),留作二期(见 §7)。

## 3. 改动点

### 3.1 `src/tree_walker/config.py` — 默认开启 + 新增截断阈值

- `TruncationSettings`(`:42-49`)新增:`dom_excerpt_max_chars: int = 2000`(step 层采集 DOM 摘要的阈值)。
- `JudgeSettings`(`:52-56`):
  - `enabled: bool = True`(原 `False`,对齐 browser-use 默认开)。
  - 新增 `trace_max_chars: int = 40000`(Judge 整段 trace 截断阈值)。
- `load_settings`(`:235-252`):
  - env `AGENT_TRUNCATE_DOM_EXCERPT` 默认 `"2000"` → `TruncationSettings.dom_excerpt_max_chars`。
  - `AGENT_JUDGE_ENABLED` 默认 `"0"` → **`"1"`**(判定逻辑是 `== "1"`,用户仍可 `AGENT_JUDGE_ENABLED=0` 关闭)。
  - 新增 `AGENT_JUDGE_TRACE_MAX_CHARS` 默认 `"40000"` → `JudgeSettings.trace_max_chars`。

### 3.2 `src/tree_walker/agent/step.py` `_finalize`(`:718-742`)— 持久化 DOM 摘要

`state_summary` 当前只存 `url/title/duration`(`:732-736`)。新增:

```python
state_summary["dom_excerpt"] = (browser_state.dom_state.element_tree_text or "")[: dom_excerpt_chars]
```

- `dom_excerpt_chars` 从 step 已有的 truncation 配置注入(与 `extract_page_max_chars` 同源;实现时确认 Step 类的配置访问方式)。
- `or ""` 兜底断路器开启时的空 DOM(`EMPTY_DOM_STATE`)。

### 3.3 `src/tree_walker/agent/judge.py` — 核心改造

- **`JudgementResult`(`:16-20`)**:新增 `captcha: bool = False`(对齐 browser-use 4 字段)。
- **`_JUDGE_TOOL_SCHEMA`(`:45-70`)**:同步加 `captcha` property。
- **`judge()` 解析(`:103-108`)**:加 `captcha=bool(data.get("captcha", False))`。
- **`JudgeEvaluator.__init__`(`:76-77`)**:改为 `__init__(self, llm, settings)`,存 `self._settings`(读 `max_history_steps`/`trace_max_chars`)。
- **`_serialize_history`(`:144-169`)** — 三处改动:
  1. 删除"前3+后17丢中间步"逻辑(`:149-152`),改为保留全部步。
  2. 每步读 `h.state_summary` 的 `url`、`title`、`dom_excerpt`(当前完全没读 state_summary)。
  3. result 改为直接读 `r.extracted_content` 原值 + `r.error`,**不再用 `str(r)`**(绕开 500 字截断)。
  - 每步格式示例:
    ```
    Step {n}:
      URL: {url}
      Title: {title}
      Goal: {next_goal}
      Action: {action_name}({params})
      Page excerpt: {dom_excerpt}
      Result: {extracted_content 或 error}
    ```
- **`_build_judge_prompt`(`:116-142`)**:trace 拼完后,若超 `trace_max_chars` 则截断并在末尾标 `[trace truncated]`。
- **`_JUDGE_SYSTEM_PROMPT`(`:23-42`)**:增补三条规则(对标 browser-use `judge.py:128/144/170`):
  1. agent 可直接从页面 DOM/可见文本读取内容,**无显式 extract 动作不应单独作为幻觉判据**;若 Page excerpt 显示内容真实存在即采信。
  2. 若 agent 报告动作完成但 Page excerpt/URL 显示未实际完成 → `verdict=false`。
  3. evaluate for action:对每个关键步骤,核对动作是否真发生。
  - 保留现有 "Do NOT blindly trust""high standard""impossible_task" 规则。

### 3.4 `src/tree_walker/agent/agent.py` — 传 settings

- 构造 Judge(`:118-120`):`JudgeEvaluator(llm=self.llm, settings=_settings.judge)`,移除单独的 `self._judge_max_history_steps`(改由 settings 内部读)。
- `_run_judge`(`:306-317`):`judge()` 调用去掉 `max_history_steps=` 参数。其余不变 —— verdict 仍只挂 `ActionResult.judgement` + 打 log,**不覆盖** `is_done()`/`final_result()`(与 browser-use 一致)。

### 3.5 初始导航可见性(无需额外传递)

每步 `state_summary.url` 已反映导航后状态(Step 0 的 url 即 google)。Judge 读 `state_summary.url` 后即可看到 URL 流转,解决"看不到已导航"。**无需**专门传 `initial_url`(可选增强:trace 开头加 `Initial URL:` 行,默认不做)。

## 4. 测试(CLAUDE.md 要求覆盖率 > 85%)

`tests/test_judge.py` 新增/更新:

- `_serialize_history` 输出含每步 `URL`/`Title`/`Page excerpt`。
- `_serialize_history` 读 `extracted_content` 原值:构造 >500 字的 extracted_content,断言 trace 含完整内容(未被 `str(r)` 截断)。
- `_serialize_history` 不丢中间步:构造 > `max_history_steps` 步,断言全部步出现在 trace。
- `_build_judge_prompt` 超长 trace 截断到 `trace_max_chars` 且带 `[truncated]` 标注。
- `JudgementResult` 含 `captcha` 字段;`_JUDGE_TOOL_SCHEMA` 含 captcha property。
- 断言 `_JUDGE_SYSTEM_PROMPT` 含"无 extract 动作不应作为幻觉判据"等关键规则文本。

`tests/test_step.py`(或对应):`_finalize` 持久化 `dom_excerpt`,长度 ≤ `dom_excerpt_max_chars`,且空 DOM 时为 `""`。

`tests/test_config.py`(若有):`JudgeSettings.enabled` 默认 `True`;新配置项默认值正确。

## 5. 验证(端到端)

1. 重跑 `uv run python examples/basic_agent.py`(默认已开 Judge,无需设 env),确认日志输出 `Judge verdict: SUCCESS`(因 Judge 现在能看到 DOM 里的真实标题 + 每步 URL)。
2. `uv run python -m pytest tests/ -x -v` 全绿 + 覆盖率达标。
3. **回归保护**:构造"真幻觉"场景(done 写一条 Page excerpt 里不存在的标题),确认 Judge 仍判 FAILED —— 避免改造后变成"无脑放行"。

## 6. 关键文件清单

| 文件 | 关键位置 |
|---|---|
| `src/tree_walker/config.py` | `TruncationSettings:42-49`、`JudgeSettings:52-56`、`load_settings:235-252` |
| `src/tree_walker/agent/step.py` | `_finalize:718-742` |
| `src/tree_walker/agent/judge.py` | `JudgementResult:16-20`、`_JUDGE_TOOL_SCHEMA:45-70`、`_JUDGE_SYSTEM_PROMPT:23-42`、`_build_judge_prompt:116-142`、`_serialize_history:144-169` |
| `src/tree_walker/agent/agent.py` | 构造 Judge `:118-120`、`_run_judge:306-328` |
| `src/tree_walker/agent/views.py` | `ActionResult.__str__:27-37`、`is_done/final_result:86-96` |
| browser-use 对照 | `browser_use/agent/judge.py`(system prompt 106-190、截图编码 21-31、截断 34-41)、`agent/views.py:887` `agent_steps()`、`agent/service.py:1581` `_judge_trace` |

## 7. 范围外(二期,记录备查)

截图喂 Judge(对标 browser-use 核心杀器):需先实测智谱 glm 走 Anthropic 兼容协议是否接受 image block、改造 `LLMClient` 的 `isinstance(content, str)` 假设、为 `AgentHistory` 新增 screenshot 持久化字段。与 `docs/tools-optimize/screenshot.md` 阶段二一致。本次不做。

## 8. 风险与权衡

- **默认开启**:每次 done 多一次 LLM 调用(成本+延迟)。`AGENT_JUDGE_ENABLED=0` 可关。
- **token 增长**:每步 DOM 摘要 ≤2000 字 × 多步,由 `trace_max_chars=40000` 兜底截断。
- **改 `_serialize_history` 行为**:需同步更新现有 `test_judge.py`(预期内)。
