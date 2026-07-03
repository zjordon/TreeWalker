# 准备上下文对齐 browser-use 方案

> 阶段：agent step 5 阶段流程的**第一阶段「准备上下文」**（`_prepare_context`）
> 对照源：`D:\dev\git\z_jordon\browser-use\browser_use\agent\service.py`、`message_manager/service.py`、`prompts.py`
> 本文档只交付**方案**，不含代码落地；落地为后续独立任务。
>
> **实施范围（2026-07-03 确定）**：本期仅落地 **P0 + P1**；**P2（视觉通道 / read_state_images）与 P3（含 Today 日期注入）暂缓，不在本期实施**。P2/P3 章节保留作为对齐全景与未来参考，但路线图与交付顺序只覆盖 P0 + P1。

---

## 1. 背景与范围

TreeWalker 的目标是「所有逻辑都对齐 browser-use」。Agent step 被拆为 5 阶段流水线（`src/tree_walker/agent/step.py`）：

| 阶段 | 方法 | 职责 |
|---|---|---|
| 1. Sense | `_prepare_context()` | 调 LLM 前组装全部输入（系统提示词、历史、工具、页面状态、注入提示） |
| 2. Think | `_get_next_action()` | 调 LLM 拿决策 |
| 3. Act | `_execute_actions()` | 执行动作 |
| 4. Post | `_post_process()` | 更新状态 |
| 5. Final | `_finalize()` | 历史/日志/步数 |

**本文档范围**：仅第一阶段 `_prepare_context()`（`step.py:146-216`）及其调用链。browser-use 对应 `_prepare_context`（`service.py:1075-1148`）+ `MessageManager.prepare_step_state` / `create_state_messages`（`message_manager/service.py`）。**不覆盖** Think/Act/Post/Final 四阶段（后续文档展开）。

经对比，TreeWalker 在此阶段存在 **1 处核心结构性缺失**（消息累积）+ 若干感知/注入通道缺失。下文逐项给出代码级方案。

---

## 2. 现状精确锚点（已核对真实代码）

| 维度 | TreeWalker 现状 | 文件:行 |
|---|---|---|
| messages 容器 | `self.messages: list[dict[str, Any]] = []` 裸数组，**无类型分类** | `agent.py:86` |
| 状态消息写入 | 每步 `self.messages.append({"role":"user","content":state_msg})` **不替换** | `step.py:205` |
| 注入消息写入 | budget / last step / failure 三处 `append`，**每步不清理** | `step.py:267, 278, 294` |
| assistant 消息 | 每步 append 简化文本（`[eval] Goal: ... \| Action: ...`），丢失 memory/params 细节 | `step.py:362-369` |
| 消息修剪 | `_trim_messages` 默认保留末尾 20 条；compactor 启用时返回全量（仅 `max_messages*3` 安全上限） | `agent.py:337-351` |
| 消息压缩 | `MessageCompactor.maybe_compact` 双门限（步数 + 字符数），保留 `[首条, summary, 尾部 N]`，就地 `messages[:] = ...` | `message_compactor.py:38-106`，关键 84-94 |
| 压缩文本拼接 | `full_text = "\n".join(m.get("content","") for m in messages)` —— **content 非 str 会崩** | `message_compactor.py:55` |
| 脱敏（输入） | `_filter_sensitive_in_messages` 按 `str.replace` 就地改 `content`；**非 str content 静默跳过**（`isinstance` 守卫） | `client.py:125-142`（守卫 136） |
| 脱敏（输出） | `_restore_sensitive_in_output` 递归还原占位符 | `client.py:144-164` |
| URL 缩短 | `_shorten_urls_in_messages` 把 ≥100 字符 URL 替换为 `[uN]`；**非 str content 静默跳过** | `client.py:72-104`（守卫 84） |
| 敏感数据告知 | 仅 `agent.py:88-98` 把 task 里真实值替换成占位符；**从不告知 LLM 有哪些 `<secret>` 可用、在哪页用** | `agent.py:88-98` |
| `_safe_task` | task 的脱敏副本（占位符替换后），注入 system prompt 的 `{task}` | `agent.py:91-94` |
| 历史重放 | `RerunMixin` 完全基于 `self.history`，**全文不读 `self.messages`** | `agent/rerun.py` |
| 浏览器事件 | **完全未采集** recent_events（导航/dialog/download/network） | — |
| 页面统计 | `DOMCollectionMetrics` 已采集 `iframe_count`/`element_count`，但 `SerializedDOMState` **不携带**、不传 LLM | `views.py:802-808`（采集）/ `views.py:694-708`（未携带） |
| 工具/动作过滤 | `get_tool_schema(page_url=...)` + `get_action_descriptions_text(page_url=...)` 已按 URL 过滤 | `tools/registry.py:90, 227` |
| `_last(field)` | 从 `state.last_model_output` 取上一步某字段（evaluation/memory/next_goal） | `agent.py:332-335` |
| 截图 | `include_screenshot=False`（断路止血，视觉通道未打通） | `step.py:150` |

**两个决定性结论：**

1. **`RerunMixin` 不读 `self.messages`** → P0 对 messages 结构的改造对历史重放**零影响**，向后兼容负担极小。这是采用轻量方案（而非重构出完整 MessageManager 类）的决定性依据。
2. **client 层已有 `isinstance(content, str)` 守卫** → 多模态 list content **不会让 client 崩溃**，而是**静默跳过**（多模态文本块不被脱敏/缩短，是安全洞）。真正的崩溃点只有 `message_compactor.py:55` 的 `join`。

---

## 3. browser-use 18 子步骤 vs TreeWalker 全景对照

browser-use `_prepare_context`（`service.py:1075-1148`）的 18 个子步骤：

| # | browser-use 子步骤 | TreeWalker 现状 | 判定 |
|---|---|---|---|
| 0 | CAPTCHA 等待 `wait_if_captcha_solving` | 无 CAPTCHA 集成 | ❌ → P3 判断跳过 |
| 1 | 获取浏览器快照（DOM+截图+tabs+recent_events） | `get_state(include_screenshot=False)`；无 recent_events | ⚠️ 部分（缺截图/事件） |
| 2 | 检查新下载文件 → `available_file_paths` | `consume_completed_downloads` → `download_notice` | ✅ |
| 3 | 日志 + 停止/暂停检查 | `_log_step_context`；`stopped/paused` 检查在 `_step`（step.py:110） | ✅ |
| 4 | 动态更新 `ActionModel`/`AgentOutput`（flash/thinking/standard） | `_update_action_models_for_page` 重建 `_tool_schema`/`_system_prompt` | ✅（机制不同但等价） |
| 5 | 页面过滤动作 `get_prompt_description(url)` | `get_action_descriptions_text(page_url)` | ✅ |
| 6 | 不可用技能 `unavailable_skills_info` | 无 skill 概念；静态注册 + page_url 过滤 | ❌ → P3 判断跳过 |
| 7 | 渲染计划描述 `[x]/[>]/[ ]/[-]` | `plan_manager.render_plan_description` | ✅ |
| 8 | `prepare_step_state`：清 context_messages + 更新 history desc + sensitive desc | **无清理、无 history desc、无 sensitive desc** | ❌ → P0 + P1 |
| 9 | 消息压缩 `maybe_compact_messages`（双门限） | `MessageCompactor.maybe_compact`（双门限） | ✅ |
| 10 | `create_state_messages`：截图策略 + `_set_message_with_type('state')` **替换** | `self.messages.append(state_msg)` **追加不替换** | ❌ → P0 核心 |
| 11 | 注入预算警告（≥75%） | `_inject_budget_warning` | ✅（但累积，见 P0） |
| 12 | 注入重计划提醒 | `plan_manager.build_replan_nudge` | ✅（但累积） |
| 13 | 注入探索提醒 | `plan_manager.build_exploration_nudge` | ✅（但累积） |
| 14 | 更新循环检测页面状态 | `loop_detector.record_page` | ✅ |
| 15 | 注入循环检测提醒 | `loop_detector.get_nudge_message` | ✅（但累积） |
| 16 | 强制最后一步 done（替换为 DoneAgentOutput） | `_force_done_on_last_step`（改 schema enum 为 done-only） | ✅（机制不同但等价） |
| 17 | 强制失败后 done（替换为 DoneAgentOutput） | `_force_done_after_failure`（改 schema enum） | ✅（机制不同但等价） |

**缺失集中在：子步骤 8（prepare_step_state 的清理+描述）、子步骤 10（状态消息替换）、子步骤 1 的 recent_events/截图、子步骤 6 的 sensitive_data 告知。**

---

## 4. P0 核心结构性缺失：消息分类管理 + 状态消息替换 + 上下文消息清理

### 4.1 现状与影响

- `step.py:205` 每步 `append` 完整 DOM state 消息 → 第 N 步 messages 里堆积 **N 份完整 DOM**（每份可达数万 token）。直接随步数线性吃掉 context window。
- `step.py:267/278/294` 三类注入每步 `append` → 旧 budget warning / last step / failure 提示**残留累积**，LLM 误判"仍在告警"。
- assistant 消息（`step.py:362-369`）每步 `append` 简化文本，与 `self.history` 不同步。
- `_trim_messages` 只能按尾部条数裁，会把**仍是当前状态的 state 消息裁掉**，或把过时 state 留下——精度差。

### 4.2 browser-use 做法

- `MessageManager` 维护 `state_messages` / `context_messages` / `agent_history_items` 等独立列表（`message_manager/service.py`）。
- `create_state_messages` 末尾 `_set_message_with_type(msg, 'state')` → 每步**替换**唯一 state 消息。
- `prepare_step_state()` 开头 `context_messages.clear()`（`message_manager/service.py:205`）→ 每步**清理**注入消息。

### 4.3 方案权衡：选项 A vs 选项 B

**选项 A：引入完整 `MessageManager` 类**
- ✅ 完全对齐 browser-use 架构，未来扩展（image/file/memory）有清晰插槽
- ❌ 改动面大：重构 `agent.py` 初始化、`step.py` 全部 append 点、`_trim_messages`、`message_compactor`、`client` 脱敏入口、obs 事件
- ❌ 一次性大重构，回滚困难

**选项 B（推荐）：给 `self.messages` 加 `_type` 元数据 + 3 个辅助方法**
- 给 dict 加内部键 `_type`（值：`"state"|"context"|"user"|"assistant"`）
- Anthropic SDK 对 message dict 键校验严格（只认 role/content），`_type` 必须在 `_trim_messages` 返回前**剥除**——唯一边界点，集中可控
- 改动局限在 `step.py` 与 `agent.py:_trim_messages`，compactor/脱敏透明

#### 推荐：选项 B

**理由：**
1. browser-use 的 MessageManager 本质即「带 type 的消息列表 + 增删辅助」，dict + `_type` 元数据即可表达等价语义。
2. **`RerunMixin` 不读 messages** → 向后兼容成本接近零（重放、history 落盘均不受影响）。
3. `_type` 唯一边界点是 `_trim_messages`（送 SDK 前剥除），集中可控，双重保险（单测 + client 入口防御）。
4. **渐进式**：P0 先做替换 + 清理，`_type` 已就位，未来若要 image/file 可平滑升级到选项 A。

### 4.4 涉及文件

- `src/tree_walker/agent/step.py`（主战场：注入点全部改造）
- `src/tree_walker/agent/agent.py`（`_trim_messages` 边界剥 `_type`）
- `src/tree_walker/config.py`（新增 `enable_message_typing: bool = True` flag，回滚用）

### 4.5 改造步骤（有序）

> 示例代码用 4 空格缩进示意；实际落地须遵循目标文件既有缩进（`step.py`/`agent.py` 为 4 空格）。

**Step 1 — 定义消息类型常量**（`step.py` 顶部，紧邻 `_PARAM_VALIDATION_MAX_RETRIES`）

```python
_MSG_TYPE = "_type"          # 内部键，不送 SDK（_trim_messages 边界剥除）
TYPE_STATE = "state"         # 当前页状态（每步替换，全局唯一）
TYPE_CONTEXT = "context"     # 注入提示（每步清理后重灌：budget/last/failure/loop）
TYPE_USER = "user"           # 持久 user 消息（任务说明、conversation summary）
TYPE_ASSISTANT = "assistant"
```

**Step 2 — 新增 3 个辅助方法**（`StepPipeline` 类内，紧邻 `_prepare_context`）

```python
def _set_state_message(self, content) -> None:
    """替换唯一的 state 消息（对齐 browser-use _set_message_with_type('state')）。"""
    self.messages = [m for m in self.messages if m.get(_MSG_TYPE) != TYPE_STATE]
    self.messages.append({"role": "user", "content": content, _MSG_TYPE: TYPE_STATE})

def _clear_context_messages(self) -> None:
    """每步入口清理上一步的注入提示（对齐 prepare_step_state 的 context_messages.clear）。"""
    self.messages = [m for m in self.messages if m.get(_MSG_TYPE) != TYPE_CONTEXT]

def _add_context_message(self, content) -> None:
    """追加一条注入提示（每步先清后灌，不累积）。"""
    self.messages.append({"role": "user", "content": content, _MSG_TYPE: TYPE_CONTEXT})
```

**Step 3 — `_prepare_context` 入口先清理**（`step.py:146-150`）

```python
async def _prepare_context(self) -> tuple[BrowserStateSummary, str]:
    self._clear_context_messages()   # P0：每步入口先清理上一步注入
    browser_state = await self.browser.get_state(include_screenshot=False)
    ...
```

**Step 4 — 状态消息改替换**（`step.py:205`）

```python
# 旧：self.messages.append({"role": "user", "content": state_msg})
self._set_state_message(state_msg)
```

**Step 5 — 三处注入改用 `_add_context_message`**（`step.py:267/278/294`）

```python
# _inject_budget_warning / _force_done_on_last_step / _force_done_after_failure 内
self._add_context_message(msg)   # 替换原 self.messages.append({"role":"user","content":msg})
```

**Step 6 — `_trim_messages` 边界剥 `_type`**（`agent.py:337-351`）

```python
def _trim_messages(self, max_messages: int = 20) -> list[dict[str, Any]]:
    # ... 原 compactor / 条数分支保留 ...
    if self._compactor:
        if len(self.messages) > max_messages * 3:
            out = list(self.messages[-(max_messages * 3):])
        else:
            out = list(self.messages)
    elif len(self.messages) <= max_messages:
        out = list(self.messages)
    else:
        out = list(self.messages[-max_messages:])
    # P0：剥除内部 _type 键（送 SDK 前的最后边界）
    return [{k: v for k, v in m.items() if k != _MSG_TYPE} for m in out]
```

> **副作用（改善，需说明）**：现状 `_trim_messages` 返回的 dict 与 `self.messages` 共享引用，client 的 `_shorten_urls_in_messages` / `_filter_sensitive_in_messages` 会**就地改 `content` 污染 self.messages**。Step 6 的列表推导式重建 dict 副本后，client 改的是副本、不再污染原始 messages——这是更干净的语义，但与现状略有不同（脱敏/缩短只作用于发往 SDK 的那份）。无负面影响。

**Step 7 — assistant 消息补 `_type`**（`step.py:362-369`）

```python
self.messages.append({
    "role": "assistant",
    "content": f"[{eval_}] Goal: {goal} | Action: {name}",
    _MSG_TYPE: TYPE_ASSISTANT,
})
```

### 4.6 兼容性分析（逐项）

| 现有机制 | 影响 | 说明 |
|---|---|---|
| `MessageCompactor.maybe_compact` | ✅ 透明 | 保留的 dict 仍带 `_type`，但 compactor 不送 SDK。建议 `summary_msg` 补 `_MSG_TYPE=TYPE_USER`，使其不被 `_clear_context_messages` 误删。`messages[:] = [first, summary_msg, *tail]` 不变。 |
| client 脱敏 `_filter_sensitive_in_messages` | ✅ 透明 | 入参是 `_trim_messages` 返回值（已剥 `_type`），且只读 `content` 字段，对 `_type` 无感。 |
| client URL 缩短 `_shorten_urls_in_messages` | ✅ 透明 | 同上。 |
| `RerunMixin` 历史重放 | ✅ 零影响 | 全程不读 `self.messages`。 |
| `AgentHistoryList` 落盘 | ✅ 零影响 | 只 dump `self.history`，不 dump messages。 |
| obs 事件（`ModelCallEvent.message_count`） | ✅ 透明 | 只数 `len(trimmed)`，不关心 `_type`。 |
| param 校验重试（`step.py:452-458`） | ⚠️ 注意 | 该处 `retry_messages = list(messages) + [{...}]` 追加的纠错消息无 `_type`——建议补 `_MSG_TYPE=TYPE_CONTEXT`，使其下一步被清理。 |

### 4.7 测试要点

1. 连续 5 步后 `messages` 里 `TYPE_STATE` 消息**恒为 1 条**（最新 DOM），无过时堆积。
2. 第 N 步注入 budget warning，第 N+1 步（未达阈值）`messages` 里**无 `TYPE_CONTEXT` 残留**。
3. `_trim_messages` 返回的每个 dict **不含** `_type` 键（用 `assert all("_type" not in m for m in trimmed)` 守卫）。
4. compactor 触发后（`messages[:] = [first, summary, *tail]`）仍能正确识别 state/context 类型，下一步清理/替换正常。
5. 首步（无旧 state）`_set_state_message` 不报错；`max_steps` 最后一步 force_done 后下一步清理正常。
6. user/assistant 交替序列合法（state 替换后上一步若是 assistant，新 state 是 user，序列仍合法）——用 mock Anthropic SDK 校验 messages schema 不报错。

### 4.8 工作量

**1.5 人天**（含测试 + flag 回滚开关）。

---

## 5. P1 感知增强（每项可独立交付）

### 5.1 page_stats（链接数 / 交互元素 / iframe / SPA 骨架屏检测）

#### 现状
- `DOMCollectionMetrics`（`views.py:802-808`）已采集 `iframe_count` / `element_count` / `degradation_level`，但 `SerializedDOMState`（`views.py:694-708`）**不携带**，也不传 LLM。
- `build_state_message`（`system_prompt.py:113`）入参无 `page_stats`。
- 链接数、骨架屏未采集。

#### browser-use 做法
state 消息含 `<page_stats>` 段：`links / interactive_elements / iframes` + SPA 加载骨架屏提示（`prompts.py:_get_browser_state_description`，约 228-241）。

#### 缺失影响
- LLM 不知道页面还在加载（骨架屏/placeholder），盲目点击占位元素 → 报错或点错。
- iframe 数量等已采集信息被浪费。

#### 涉及文件
- `src/tree_walker/browser/views.py`（`SerializedDOMState` 加 `page_stats` 字段 —— **注意该类是 `@dataclass`，用 `field(default_factory=dict)`**）
- `src/tree_walker/browser/dom.py`（`build_dom_state` 填充 page_stats，复用 `metrics.iframe_count`/`element_count`）
- `src/tree_walker/browser/serializer.py`（遍历时统计 `links`、检测骨架屏）
- `src/tree_walker/prompts/system_prompt.py`（渲染 `[Page Stats]`）
- `src/tree_walker/agent/step.py`（传参）

#### 函数签名变更

```python
# views.py（@dataclass，tab 缩进）
@dataclass
class SerializedDOMState:
    _root: SimplifiedNode | None
    selector_map: DOMSelectorMap
    element_tree_text: str
    file_input_backend_ids: list[int] = field(default_factory=list)
    file_inputs_meta: list[FileInputInfo] = field(default_factory=list)
    page_stats: dict[str, Any] = field(default_factory=dict)   # 新增

# system_prompt.py
def build_state_message(..., page_stats: dict[str, Any] | None = None) -> str:
```

#### 实施步骤
1. **serializer.py**：在构建 `element_tree_text` 的遍历中累加 `links`（`tag=='a'` 的交互节点数）；检测骨架屏启发式（class 含 `skeleton|loading|placeholder|spinner` 且交互元素 < 3 → `skeleton=True`）。
2. **dom.py `build_dom_state`**（约 `dom.py:1043`）：组装 `page_stats = {"links":..., "interactive": metrics.element_count, "iframes": metrics.iframe_count, "skeleton":...}`。
3. **system_prompt.py**：`[Page Title]` 后插入：
   ```
   [Page Stats] links=N, interactive=N, iframes=N, SKELETON/LOADING (page may not be fully rendered)
   ```
   （`page_stats` 为空时不渲染。）
4. **step.py:192-204**：传 `page_stats=browser_state.dom_state.page_stats`。

#### 兼容性
无破坏：新字段默认空 dict/None；老调用方不传 `page_stats` 时 `build_state_message` 不渲染该段。

#### 测试要点
1. 正常页面 stats 准确（links/interactive/iframes 计数）。
2. 骨架屏页面 `skeleton=True`，渲染提示。
3. 空页面（`EMPTY_DOM_STATE`）`page_stats={}` → 不渲染 `[Page Stats]`。
4. `DOMCollectionMetrics` 降级（`MINIMAL`/`FAILED`）时不报错。

#### 工作量
**1.0 人天**。

---

### 5.2 recent_events（最近浏览器事件）

#### 现状
完全未采集浏览器侧事件（`onbeforeunload` 弹窗、`alert`、下载开始、网络 5xx、console error）。LLM 只能通过 URL 变化间接推断。

#### browser-use 做法
维护 `recent_events` 列表，state 消息注入 `[Recent Events]`（`include_recent_events` 控制）。

#### 缺失影响
- LLM 看不到 `onbeforeunload` 弹窗、`alert()`、下载开始提示。
- 网络失败/导航被拦时 LLM 困惑"为什么没跳转"。

#### 涉及文件
- `src/tree_walker/browser/session.py`（CDP 监听 + 采集 + consume）
- `src/tree_walker/browser/views.py`（`BrowserEvent` 模型 + `BrowserStateSummary.recent_events` 字段）
- `src/tree_walker/prompts/system_prompt.py`（渲染）
- `src/tree_walker/agent/step.py`（传参）
- `src/tree_walker/config.py`（`enable_recent_events: bool = False`）

#### 函数签名变更

```python
# views.py（Pydantic，tab 缩进）
class BrowserEvent(BaseModel):
    type: Literal["navigation", "dialog", "download", "network_error", "console_error"]
    message: str
    timestamp: float

class BrowserStateSummary(BaseModel):
    url: str = ""
    title: str = ""
    tabs: list[TabInfo] = Field(default_factory=list)
    dom_state: SerializedDOMState | None = None
    screenshot: bytes | None = None
    recent_events: list[BrowserEvent] = Field(default_factory=list)   # 新增

# session.py
class BrowserSession:
    def record_event(self, event: BrowserEvent) -> None: ...
    def consume_recent_events(self) -> list[BrowserEvent]: ...
```

#### 实施步骤
1. **session.py**：`Page.enable` 后挂 CDP 监听。**首期只接两类最常用**（控制风险）：
   - `Page.javascriptDialogOpening` → `type="dialog"`（alert/confirm/prompt/onbeforeunload）
   - `Browser.downloadWillBegin` → `type="download"`
2. `get_state`（`session.py` 约 1499）：返回前 `recent_events=self.consume_recent_events()`。
3. **system_prompt.py**：`[Page DOM]` 前插入 `[Recent Events]`（最多 5 条，倒序，无事件不渲染）。
4. **step.py**：传 `recent_events=browser_state.recent_events`。
5. **CDP 监听失败要 `try/except` 兜底**——不能因事件采集失败拖垮 `get_state`。

#### 兼容性
默认空列表；feature flag 默认关。`record_event` 仅 `append`（O(1)），不影响 `get_state` 性能。

#### 风险
CDP 监听回调在事件线程触发，须线程安全（`asyncio.run_coroutine_threadsafe` 或加锁）。建议用 `collections.deque(maxlen=20)` 自动丢弃溢出。

#### 测试要点
1. 触发 `alert()`：state 消息出现 `[Recent Events] dialog: ...`。
2. 无事件时不渲染该段。
3. `consume` 后下次不重复出现。
4. CDP 监听异常不影响 `get_state` 正常返回。

#### 工作量
**1.5 人天**。

---

### 5.3 agent_history_description（统一历史格式 + 滑动窗口）

#### 现状
- messages 里 assistant 消息是**简化文本**（`step.py:362-369`），丢失 `memory` / action params 细节。
- `_trim_messages` 按条数裁，早期关键决策（memory/目标）被丢光，LLM 重复探索。

#### browser-use 做法
- `agent_history_description` 属性（`message_manager/service.py:150-186`）格式化为 `<agent_history>` 块。
- 滑动窗口：保留首条 + `[... N previous steps omitted...]` + 最近 N 条（`max_history_items`）。
- 配合 `compacted_memory`（压缩摘要）作前缀。

#### 缺失影响
- LLM 看不到早期 memory，重复尝试已失败路径。
- 多步后 messages 膨胀，且早期 assistant 简化文本信息量低。

#### 涉及文件
- `src/tree_walker/agent/agent.py`（新增 `_build_agent_history_description`）
- `src/tree_walker/agent/step.py`（注入，复用 P0 的 `_set_state_message` 同型机制——每步替换）
- `src/tree_walker/config.py`（`max_history_items: int = 10`）

#### 函数签名

```python
# config.py
max_history_items: int = 10   # compactor 启用时建议自动降到 5

# agent.py
def _build_agent_history_description(self) -> str | None:
    """读 self.history 格式化为 <agent_history> 块（滑动窗口）。"""
    if not self.history.history:
        return None
    max_items = self._max_history_items
    items = self.history.history
    if len(items) <= max_items:
        shown, omitted = items, 0
    else:
        shown = [items[0]] + items[-(max_items - 1):]
        omitted = len(items) - max_items
    lines = ["<agent_history>"]
    if omitted > 0:
        lines.append(f"  [... {omitted} previous steps omitted ...]")
    for h in shown:
        mo = h.model_output or {}
        goal = mo.get("next_goal", "")
        eval_ = mo.get("evaluation_previous_goal", "")
        memory = mo.get("memory", "")
        action = mo.get("action", {})
        action_str = f"{action.get('name','?')}({action.get('params',{})})"
        result = h.result[0] if h.result else None
        result_str = str(result)[:200] if result else ""
        lines.append(f"  Step {h.step_number}: [{eval_}] Goal: {goal} | {action_str} -> {result_str}")
        if memory:
            lines.append(f"    Memory: {memory}")
    lines.append("</agent_history>")
    return "\n".join(lines)
```

#### 实施步骤
1. **依赖 P0**：P0 完成后，注入一条 `TYPE_USER` 的 `<agent_history>` 消息，每步**替换**（同 `_set_state_message` 模式，可抽 `_set_history_message` 或复用）。
2. 保留 messages 里 assistant append（保证 user/assistant 交替合法）。
3. compactor 启用时 `max_history_items` 自动降到 5（避免与 compactor 双重占用 token）。
4. 可选：若 `self._compactor._compacted_memory` 非空，前缀加 `<compacted_memory>...</compacted_memory>` 块（对齐 browser-use）。

#### 兼容性
读 `self.history`（已落盘结构，`AgentHistory` 含 `model_output`/`result`/`state_summary`），与 messages 改造正交。

#### 测试要点
1. 3 步内：含全部历史，无省略提示。
2. 15 步（`max_history_items=10`）：首步 + `[... 5 previous steps omitted ...]` + 最近 9 步。
3. `memory` 字段正确渲染。
4. compactor 启用时 `max_history_items` 降为 5。

#### 工作量
**1.0 人天**（依赖 P0）。

---

## 6. P1 注入增强

### 6.1 sensitive_data_description（告知 LLM 可用 secret，按 URL 过滤）

#### 现状
- `agent.py:88-98`：把 task 里真实 secret 替换成占位符（`_safe_task`），`_sensitive_map = {real_val: placeholder}`。
- client 层 `_filter_sensitive_in_messages`（`client.py:125-142`）在送 SDK 前把 messages 里残留的真实值替换为占位符。
- **但从不告知 LLM 有哪些占位符可用、在哪些页面用** → LLM 遇到登录页不知道该用 `input_text(text="<secret>password</secret>")`。

#### browser-use 做法
`create_state_messages` 注入 `sensitive_data_description`：列出按 URL pattern 过滤后可用的占位符（`message_manager/service.py`，`_get_sensitive_data_description`）。

#### 缺失影响
- LLM 不知道占位符机制，可能编造密码或卡在登录页反复试错。

#### 涉及文件
- `src/tree_walker/config.py`（`sensitive_data` 兼容两种格式）
- `src/tree_walker/agent/agent.py`（保留 `_sensitive_map` 脱敏用 + 新增 `_sensitive_data_raw` 描述用）
- `src/tree_walker/prompts/system_prompt.py`（渲染 `[Available Secrets]`）
- `src/tree_walker/agent/step.py`（传参）

#### 函数签名变更

```python
# 兼容两种 sensitive_data 格式：
#   旧: {"password": "real123"}                                  # 全局（无 URL 过滤）
#   新: {"password": {"value": "real123", "urls": ["*login*"]}}  # URL 过滤

# agent.py
def _build_sensitive_description(self, page_url: str) -> str | None:
    if not self._sensitive_data_raw:
        return None
    available = []
    for placeholder, spec in self._sensitive_data_raw.items():
        urls = spec.get("urls") if isinstance(spec, dict) else None
        if urls is None or any(fnmatch.fnmatch(page_url, p) for p in urls):
            available.append(placeholder)
    if not available:
        return None
    return ("Available secrets (use as <secret>key</secret> in input_text params): "
            + ", ".join(sorted(available)))
```

#### 实施步骤
1. **config.py**：`_load_sensitive_data` 兼容两种格式，产出 `_sensitive_data_raw`（`{placeholder: {value, urls}}`）。
2. **agent.py**：保留 `_sensitive_map`（`{real_val: placeholder}`，client 脱敏用）+ 新增 `_sensitive_data_raw`（description 用）。
3. **step.py**：`sensitive_desc = self._build_sensitive_description(browser_state.url)`。
4. **system_prompt.py**：`[Task]` 后渲染 `[Available Secrets] ...`（无可用时不渲染）。

#### 兼容性
旧 `dict[str,str]` 仍工作（`urls=None` 即全局可用）。

#### 测试要点
1. 全局 secret（旧格式）每页都出现 `[Available Secrets]`。
2. URL 过滤（新格式）只在匹配页出现，其他页不渲染。
3. 旧格式向后兼容。
4. 占位符不含真实值（确认 description 只列 key 不列 value）。

#### 工作量
**0.5 人天**。

---

### 6.2 read_state_images（推迟到 P2，破坏性变更）

#### 现状
`read_file`（已有，见 `docs/tools-optimize/read_file.md`）读图片只能文本返回，LLM 看不到图。

#### 设计要点
state 消息 content 需从 `str` 改为 `list of blocks`（多模态）。**较大破坏性变更**：

```python
{"role":"user","content":[
    {"type":"text","text":"<state text>"},
    {"type":"image","source":{"type":"base64","media_type":"image/png","data":"..."}}
], _MSG_TYPE: TYPE_STATE}
```

#### 破坏性连锁点（已核实）
- **client 脱敏/URL 缩短**：有 `isinstance(content, str)` 守卫 → **不崩，但静默跳过** list content（多模态文本块不被脱敏/缩短 → **安全洞**，须改造为遍历 text block）。
- **compactor `full_text = "\n".join(m.get("content","") for m in messages)`**（`message_compactor.py:55`）→ list content **会崩**（`TypeError: sequence item: expected str, list`）。

#### 推荐处置
归入 **P2**，与 screenshot 视觉通道统一改造多模态 content。建议抽辅助函数统一处理 str/list：

```python
def _extract_text(content) -> str:
    """统一从 str 或 list[block] 提取纯文本（供 compactor join / 脱敏遍历）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text","") for b in content if isinstance(b, dict) and b.get("type")=="text")
    return ""
```

compactor 改 `full_text = "\n".join(_extract_text(m.get("content")) for m in messages)`；client 脱敏/URL 缩短改为遍历 text block。

#### 工作量
**1.5 人天**（与 P2 合并实施）。

---

## 7. P2 视觉通道接入点（引用现有文档）

> ⏸️ **暂缓（本期不实施）** —— 视觉通道 + read_state_images 均涉及 content 从 `str` 改 `list` 的破坏性连锁改造，本期不做。下文保留作为未来接入参考。

> 详细方案见 **`docs/tools-optimize/screenshot.md`**（阶段二）。本节只声明在「准备上下文」阶段的接入点，不重复其内容。

### 现状
`step.py:150` 显式 `include_screenshot=False`（断路止血注释：「LLM 视觉通道尚未打通」）。

### 接入点（准备上下文阶段）
- `src/tree_walker/agent/step.py:150`：受配置 `enable_screenshot` 控制，改 `include_screenshot=True`。
- `src/tree_walker/prompts/system_prompt.py:build_state_message`：返回类型 `str` → `str | list[dict]`（多模态化）；`_set_state_message` 接受 list content。
- `src/tree_walker/llm/client.py`：`_filter_sensitive_in_messages` / `_shorten_urls_in_messages` 改为遍历 text block（当前静默跳过 list，是安全洞）。
- `src/tree_walker/agent/message_compactor.py:55`：`join` 改用 `_extract_text`（当前对 list 崩溃）。
- `src/tree_walker/config.py`：`enable_screenshot: bool = False` + `use_vision: Literal[True, False, "auto"] = False`。

### 破坏性连锁
content 从 `str` 改 `list[dict]` 是跨 `client.py` / `message_compactor.py` / `system_prompt.py` 的连锁改造，**必须三项同步**，否则 compactor 崩溃或脱敏失效。建议先抽 `_extract_text(msg)` 辅助函数统一处理（见 6.2）。

### 工作量
**2.0 人天**（含 client/compactor 兼容改造，与 6.2 read_state_images 合并共 3.5d）。

---

## 8. P3 browser-use 特有项判断结论

> ⏸️ **暂缓（本期不实施）** —— 下表"判断"列保留原始分析结论供参考，但**本期 P3 整体不落地**，包括原先建议对齐的 Today 日期注入（成本仅 0.2d，若后续改变主意可随时单独补上）。

| 项 | 判断 | 理由 |
|---|---|---|
| **CAPTCHA 等待** `wait_if_captcha_solving` | ⏭️ **跳过** | TreeWalker 无 CAPTCHA 集成；browser-use 也是检测后让 LLM 决策。`loop_detector` + LLM 已能处理卡顿，且 system prompt 已含"CAPTCHA 阻塞 → done"规则。投入产出比低。 |
| **Today 日期注入** | ✅ **对齐** | LLM 处理"今天的新闻/最新数据"会编造日期。方案：`build_system_prompt`（`system_prompt.py:92`）末尾追加 `Today's date: {datetime.now().strftime("%Y-%m-%d")}`。**0.2 人天**。风险：rerun 不读 system_prompt，无影响。 |
| **unavailable_skills_info** | ⏭️ **跳过** | browser-use 的 skill 是动态注册 + cookie 校验机制；TreeWalker 用静态注册的 actions，`_update_action_models_for_page` 已通过 `page_url` 过滤处理"当前页不可用动作"。语义已覆盖。 |
| **file_system / todo.md** | ⏭️ **跳过** | TreeWalker 已有 `write_file` / `read_file` + `allowed_write_paths`（见 `docs/tools-optimize/write_file.md`、`read_file.md`），功能等价。再叠一层 `todo.md` 是重复造轮子。 |
| **page_info（视口上下像素）** | ⚠️ **可选** | browser-use 注入"页面上方/下方还有多少屏"。TreeWalker DOM 树已含滚动信息，价值有限，建议观察 P1 落地后再评估。 |

---

## 9. 优先级路线图

| 阶段 | 项目 | 工作量 | 可独立交付 | 依赖 | feature flag | 本期 |
|---|---|---|---|---|---|---|
| **P0** | 4. 消息分类 + state 替换 + context 清理 | 1.5d | ✅ | 无 | `enable_message_typing` | ✅ |
| **P1a** | 5.1 page_stats | 1.0d | ✅ | 无 | `enable_page_stats` | ✅ |
| **P1b** | 5.2 recent_events | 1.5d | ✅ | 无 | `enable_recent_events` | ✅ |
| **P1c** | 5.3 agent_history_description | 1.0d | ✅ | P0（建议） | `max_history_items` | ✅ |
| **P1d** | 6.1 sensitive_data_description | 0.5d | ✅ | 无 | `enable_sensitive_description` | ✅ |
| ~~P1e~~ | ~~Today 日期注入~~ | ~~0.2d~~ | — | — | — | ⏸️ 归入 P3，暂缓 |
| ~~P2~~ | ~~read_state_images + screenshot~~ | ~~3.5d~~ | — | — | — | ⏸️ 暂缓 |
| ~~P3~~ | ~~CAPTCHA / skills / todo.md / Today / page_info~~ | — | — | — | — | ⏸️ 暂缓 |

**本期总计**：P0 + P1（a/b/c/d）≈ **5.5 人天**。（P1e Today、P2、P3 暂缓，未计入）

**本期交付顺序**：
1. **第一批（止血）**：P0（消息累积是当前最严重的 token 浪费源，优先）
2. **第二批（感知）**：P1a page_stats + P1d sensitive_data_description（低成本高收益）
3. **第三批（历史/事件）**：P1c agent_history_description + P1b recent_events

> P2 / P3 的分析与判断仍保留在第 7、8 章作为对齐全景与未来参考，需要时再启动。

---

## 10. 风险与回滚

### 风险点

| # | 风险 | 缓解 |
|---|---|---|
| 1 | **`_type` 键泄漏到 SDK**：Anthropic SDK 报 `Extra inputs are not permitted` | **双重保险**：(a) 单测强制断言 `_trim_messages` 返回的 dict 无 `_type`；(b) `client.py:get_action` 入口再加一道防御性剥除（`[{k:v for k,v in m.items() if k!="_type"} for m in messages]`） |
| 2 | **state 替换破坏 user/assistant 交替**：Anthropic 要求严格交替 | state 替换后若上一步是 assistant，新 state 是 user，序列仍合法；单测用 mock SDK 校验 messages schema |
| 3 | **`_clear_context_messages` 误删持久 user 消息** | 常量分类明确：只删 `_type==context`；持久 user 消息标 `_type==user`；summary_msg 标 `TYPE_USER`；单测覆盖 |
| 4 | **recent_events CDP 监听拖慢/拖垮 get_state** | 监听 fire-and-forget；`record_event` 仅 `append`（O(1)）；`deque(maxlen=20)` 自动溢出；CDP 异常 `try/except` 兜底 |
| 5 | **多模态 content 连锁**：P2 改 list 后 compactor 崩 / 脱敏失效 | 先抽 `_extract_text(msg)` 统一处理 str/list；P2 落地前必须三项（client/compactor/system_prompt）同步；单测覆盖 list content 场景 |
| 6 | **`_trim_messages` 副作用变化**：列表推导式重建 dict 后 client 不再污染 self.messages | 这是改善（更干净），无负面影响；在 P0 PR 描述中说明 |

### 回滚策略
**每阶段独立提交，feature flag 控制**：
- P0：`AgentSettings.enable_message_typing: bool = True`（关则回退到原始 append 行为）
- P1a/b/c/d：各自独立配置（`enable_page_stats` / `enable_recent_events` / `max_history_items` / `enable_sensitive_description`）
- 任一阶段出问题，**关闭对应 flag 即可回滚**，无需 revert 代码。

---

## 附录：Critical Files 速查

| 文件 | P0 | P1a | P1b | P1c | P1d | P1e | P2 |
|---|---|---|---|---|---|---|---|
| `agent/step.py` | ✅主 | 传参 | 传参 | 注入 | 传参 | — | 接入点 |
| `agent/agent.py` | `_trim_messages` | — | — | `_build_agent_history_description` | `_build_sensitive_description` | — | — |
| `prompts/system_prompt.py` | — | 渲染 | 渲染 | — | 渲染 | `build_system_prompt` | 多模态化 |
| `browser/views.py` | — | `SerializedDOMState.page_stats` | `BrowserEvent`/`recent_events` | — | — | — | — |
| `browser/dom.py` | — | `build_dom_state` 填充 | — | — | — | — | — |
| `browser/serializer.py` | — | 统计 links/骨架屏 | — | — | — | — | — |
| `browser/session.py` | — | — | CDP 监听 + consume | — | — | — | — |
| `llm/client.py` | 边界防御 | — | — | — | — | — | 脱敏/缩短遍历 block |
| `agent/message_compactor.py` | summary 标 type | — | — | 协同 | — | — | `_extract_text` |
| `config.py` | flag | flag | flag | `max_history_items` | 格式兼容 | — | `enable_screenshot` |

---

*本文档基于 TreeWalker 当前 master 分支（`_prepare_context` @ `step.py:146-216`）与 browser-use `_prepare_context` + `MessageManager` 的对比。落地实施时须以最新代码为准复核行号。*
