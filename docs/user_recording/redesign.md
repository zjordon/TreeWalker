# 录制器重设计：Signal 模型 + 统一翻译层

> **状态**：设计阶段。本文以业界录制回放工具（Playwright Codegen / Selenium IDE / Chrome Recorder / UiPath）的共性设计模式为蓝本，重设计 TreeWalker 用户操作录制的**录制端架构**。
>
> **范围**：仅录制端（扩展 + `recorder/` 后端 + `event_mapper`）。**重放端（`rerun.py` 五级匹配 + 重试 + 菜单重打开）零改动**——录制产物最终 flatten 成现有 `AgentHistory` 格式。
>
> **不做**：atomizer（动作原子化 / LLM 蒸馏）。录制目标是产出可重放序列，不是可复用技能。
>
> **动机**：现有实现（`feat/user-recording` 分支）已跑通抖音上传流程，但暴露的结构性痛点用"补丁"难以根治（见 §1）。

---

## 1. 现有实现的结构性痛点

现有实现经过真实业务联调（抖音上传），踩坑记录见 `troubleshooting.md` / `modal-trigger-capture.md`。痛点分三层，**每一层都是"缺概念"导致的，不是规则不够多**：

### 1.1 采集层：findInteractiveAncestor 与后端 is_interactive 不对齐

`action-recorder.ts` 的 `findInteractiveAncestor` 只用 `INTERACTIVE_SELECTOR`（CSS 选择器 `a/button/input/select/textarea/[role]`），不含 `cursor:pointer` / `onclick` 检测。而后端 `dom.py:is_interactive` 有 14 级规则，规则 14（cursor:pointer）收录了大量 div 触发器。

**后果**：用户点 Semi Select 的 `semi-select-selection`（cursor:pointer div 触发器），扩展向上找不到可交互祖先，返回内部 span/div → 录内部元素指纹 → 回放点内部元素不开下拉。

**这是"对齐"问题**：录制侧和后端用两套"可交互"定义，必然失配。

### 1.2 去噪层：基于成品 steps 事后收口，无法区分意图

现有去噪散落在三处，且都作用于"已经拼好的 `AgentHistory` steps"：

| 函数 | 位置 | 问题 |
|---|---|---|
| `dedupe_uploads` | `event_mapper.py:110` | 吸收 upload_file 前的 click——但无法区分"上传按钮 click"和"打开编辑器 click"。实测把抖音"选择封面"click（打开封面编辑器）误吸收，回放编辑器没开 → 封面 input 找不到 |
| `dedupe_auto_navigates` | `event_mapper.py:162` | 用 gap 时间（3s）猜 navigate 是不是副作用——猜错就丢真实导航或留冗余导航 |
| `denoise_steps` | `event_mapper.py:218` | 合并相邻同 index click/input/scroll——机械规则，不基于意图 |

**根因**：去噪发生在"事件已映射成 action"之后。这时原始上下文（点击后 500ms 出现了 modal？这次导航是 click 触发的还是地址栏？）已丢失，只能靠时间 gap 猜。**`dedupe_uploads` 误吸收封面 click 就是这个问题的典型表现——click + 紧邻 upload_file 既可能是"点上传按钮"，也可能是"先开编辑器再上传"，事后无法区分。**

### 1.3 时序层：SPA modal 连锁失败（最痛）

抖音上传 20 步中 8 成功 12 失败，失败的全是 modal 内步骤（封面编辑器/合集下拉/自主声明/发布）。原因：打开 modal 的触发器动作要么录错元素（1.1），要么被去噪误删（1.2），要么没录到 → 回放时 modal 没打开 → 内部元素不在 DOM → 连锁失败。

**这不是单点 bug，是缺一层"时序语义"**——录制器不知道"这个 click 打开了一个 modal"，自然不知道"后续步骤依赖这个 modal 打开"。

---

## 2. 业界怎么解决：三个核心概念

调研 Playwright Codegen（源码级）、Selenium IDE（源码级）、Chrome Recorder、UiPath 后，提炼出三个共性设计概念，正好对应上面三层痛点：

### 2.1 Signal（信号）—— 解决时序语义

**Playwright 的核心发现**：导航、弹窗、下载不应是独立动作，而应是**附加到最近动作的信号**。

```typescript
// Playwright actions.d.ts
type Signal = NavigationSignal | PopupSignal | DownloadSignal | DialogSignal | ExpectSignal;
type Action = ClickAction | FillAction | NavigateAction | ...;
// 一个 action 可以携带 signals：表示"做完这个动作后，发生了这些副作用"
```

Playwright 的 `RecorderSignalProcessor` 对导航信号做关联判定：
- 若距上一个 click/press/fill 动作 ≤ 5s → **导航作为 signal 附加到该动作**（"这次点击导致了跳转"）
- 否则 → 独立成 navigate 动作

**对 TreeWalker 的意义**：把"打开 modal"建模成 click 动作的 `modal_opened` 信号，而不是独立动作或去噪规则。回放端（即使不动）在 flatten 时也能利用这个信息——带 modal 信号的 click 意味着"后续步骤依赖此 modal"。

### 2.2 状态机字段 —— 解决意图推断

**Selenium IDE 的核心发现**：录制器需要维护一组"最近发生过什么"的状态字段，用来抑制即将到来的冗余事件。

Selenium IDE 的 `RecordingState`：`typeTarget` / `typeLock` / `focusTarget` / `focusValue` / `preventClick` / `preventClickTwice` / `enterValue` / `tabCheck`。

典型用法：用户按 Enter 提交表单 → 记录 `sendKeys ENTER` → 设 `preventClick=true` 持续 500ms → 抑制即将到来的提交按钮 click 事件（因为 Enter 已经提交了，click 是冗余的）。

**对 TreeWalker 的意义**：`dedupe_uploads` 误吸收封面 click 的根因是没有状态——录制器不知道"刚才那个 click 打开了编辑器"。有了状态机字段（如 `pendingModal`），click 后检测到 modal 出现就标记，后续 upload_file 就不会被误关联。

### 2.3 预处理器管线 + 事后回溯 —— 解决去噪层意图缺失

**Selenium IDE 的 `record()` 方法**：每条命令生成后，经过 `recorderPreprocessors` 管线，每个 preprocessor 可返回 `{action:'drop'}`（丢弃）或 `{action:'update', command}`（改写）。这是显式的"事后修正钩子"。

**Playwright 的信号处理器**：导航/弹窗/下载信号到达时，回溯看最近的动作，决定"附加"还是"独立"。这是隐式的"事后回溯修正"。

**对 TreeWalker 的意义**：去噪不应是一个事后函数（`denoise_steps`），而应是**事件流上的管线**。每个事件到达时，经过一组处理器，处理器可看滑动窗口内的上下文做决策。

---

## 3. 定稿架构：统一翻译层 + Signal 模型

### 3.1 总览

```
┌─ 扩展（content script）─────────────────────────────────────┐
│  Raw Event Collector                                        │
│  监听 DOM 事件，归一化为 RecorderEvent（含元素线索）          │
│  findInteractiveAncestor 对齐后端 is_interactive（§4.1）     │
└──────────────────────────┬──────────────────────────────────┘
                           │ RecorderEvent 流（HTTP POST）
                           ▼
┌─ 后端（recorder/）─────────────────────────────────────────┐
│  ┌─ Translation Pipeline（统一翻译层，新）──────────────┐  │
│  │  Stage 1: event → action（映射，1:1 / N:1）          │  │
│  │  Stage 2: signal detection（信号检测，事后回溯）      │  │
│  │  Stage 3: translation rules（五类翻译模式）           │  │
│  │  Stage 4: state machine（状态字段，意图推断）         │  │
│  └────────────────────────┬─────────────────────────────┘  │
│                           ▼                                 │
│  Recording 模型（action + signals，内部表示）               │
│                           │                                 │
│  Flatten：Recording → AgentHistoryList（落盘格式）          │
└──────────────────────────┬──────────────────────────────────┘
                           │ AgentHistory JSON（现有格式）
                           ▼
                    rerun.py（零改动，五级匹配重放）
```

**核心变化**：现有架构是"事件 → 立即拼 AgentHistory → 事后 denoise_steps 收口"。新架构是"事件 → 翻译管线产出 Recording（action+signal 模型）→ 落盘时 flatten 成 AgentHistory"。

中间多了一个 **Recording 内部模型**，它承载 signal 和状态机字段，让去噪在"有完整上下文"的地方做，而不是在"上下文已丢失的成品 steps"上做。

### 3.2 Recording 内部模型（新）

```python
# recorder/models.py（新）

@dataclass
class ActionRecord:
    """一个语义动作（映射自一条或多条 RecorderEvent）。"""
    action_name: str                    # click / input_text / scroll / navigate / ...
    params: dict[str, Any]              # 不含 index（flatten 时后端定位填）
    element_ref: ElementRef | None      # 元素线索（xpath/tag/name/id/aria_label/rect）
    timestamp: float
    signals: list[Signal]               # 本动作触发的副作用信号

@dataclass
class Signal:
    """动作的副作用信号（事后检测，附加到 ActionRecord.signals）。"""
    kind: SignalKind                    # navigation / dialog / modal_opened / download / dom_changed
    timestamp: float
    detail: dict[str, Any]              # navigation: {to_url}; modal: {selector}; dialog: {type, message}
    source_action_ts: float | None      # 关联到的触发动作时间戳（回溯填充）

@dataclass
class Recording:
    """整次录制的内部表示（落盘前）。"""
    actions: list[ActionRecord]
    # 状态机字段（Selenium 式，跨事件保持）
    focus_target_xpath: str | None = None
    focus_value: str | None = None
    pending_modal: str | None = None    # 最近打开的 modal 选择器
    last_action_ts: float | None = None
```

**关键设计**：
- `ActionRecord.signals` 是 signal 模型的核心——副作用不独立成动作，附加到触发动作
- 状态机字段（`focus_target_xpath` 等）跨事件保持，供翻译规则判断意图
- `element_ref` 比 xpath 更丰富（含 tag/name/id/aria_label/rect），支持后端多级定位

### 3.3 翻译管线（四阶段）

现有 `map_event` 是一个查表函数。新设计拆成四阶段管线，每阶段职责单一：

#### Stage 1: event → action（映射）

直通（1:1）和聚合（N:1）两种翻译模式在此处理。这一阶段**不做意图判断**，只做机械映射。

```python
def translate_event(event: RecorderEvent, state: RecordingState) -> ActionRecord | None:
    """Stage 1: 事件 → 动作（1:1 映射或 N:1 聚合）。
    
    返回 None 表示该事件被聚合进前一个动作（如连续 input 的第 N 个字符）。
    """
    t = event.type
    if t == "click":
        return ActionRecord("click", {}, event.element_ref, event.ts, [])
    if t == "input_text":
        # 聚合：同 focus_target 的连续 input，只保留最终值（状态机辅助）
        if state.focus_target_xpath == event.element_ref.xpath:
            return None  # 聚合进前一个 input_text（取最终值）
        return ActionRecord("input_text", {"text": event.params["text"], "clear": True}, ...)
    # ... 其余 10 类映射
```

**与现有 `map_event` 的区别**：聚合判断在这里做（用状态机字段），而不是事后 `denoise_steps` 合并。

#### Stage 2: signal detection（信号检测，事后回溯）

事件到达后，后台并行检测 DOM 副作用信号。**这是解决 modal 时序问题的核心**。

```python
async def detect_signals(action: ActionRecord, browser: BrowserSession) -> list[Signal]:
    """动作执行后 500ms，检测页面副作用，产出信号。"""
    await asyncio.sleep(0.5)  # 等副作用显现
    state = await browser.get_state(include_screenshot=False)
    signals = []
    
    # 导航信号
    if state.url != action.pre_url:
        signals.append(Signal(SignalKind.NAVIGATION, now, {"to_url": state.url}, action.timestamp))
    
    # modal 打开信号（检测新增的 [role=dialog] / [aria-modal=true]）
    new_modals = detect_new_modals(state, action.pre_dom_fingerprint)
    for m in new_modals:
        signals.append(Signal(SignalKind.MODAL_OPENED, now, {"selector": m.selector}, action.timestamp))
    
    # dialog 信号（window.alert/confirm/prompt）
    # download 信号
    return signals
```

**关键**：信号检测是**事后异步**的——动作先记录，500ms 后检测副作用再附加信号。这让录制器知道"这个 click 打开了 modal"，后续步骤就有了时序上下文。

⚠️ 这一步需要后端 `get_state()`，已有现成。`detect_new_modals` 用 MutationObserver 式逻辑对比前后 DOM（参考 BrowserBC 的 `mutation-summary-recorder` 的 `modal_added` 信号检测）。

#### Stage 3: translation rules（五类翻译模式）

这是对"动作空间翻译"的显式建模。每条规则是一个函数，接收动作 + 上下文，决定翻译方式：

```python
# recorder/translation_rules.py（新）

def rule_file_upload(actions: list[ActionRecord], i: int) -> list[ActionRecord]:
    """规则：文件上传翻译。
    
    人类操作：点上传按钮 → OS 弹框 → 选文件
    重放操作：upload_file(path) 直接 setFileInputFiles
    
    翻译策略：
    - 识别 <input type=file> change → upload_file（替代）
    - 前置的"点上传按钮"click 保留（可能是打开编辑器，非上传按钮）
    - 跳过 OS 弹框相关事件（录不到，无需处理）
    
    关键：不再像旧 dedupe_uploads 那样无条件吸收前置 click。
    用 signal 区分——如果前置 click 有 modal_opened 信号，说明它打开了编辑器，绝不是上传按钮。
    """
    action = actions[i]
    if action.action_name != "upload_file":
        return [action]
    prev = actions[i-1] if i > 0 else None
    if prev and prev.action_name == "click":
        # 有 modal_opened 信号的 click 绝不是上传按钮，保留
        if any(s.kind == SignalKind.MODAL_OPENED for s in prev.signals):
            return [action]  # 保留前置 click，不吸收
    return [action]

def rule_navigation_signal(actions: list[ActionRecord], i: int) -> list[ActionRecord]:
    """规则：导航信号关联。
    
    Playwright 式：navigate 动作如果距上一个 click ≤ 5s 且 click 无导航信号，
    把导航作为信号附加到 click，navigate 动作丢弃（它是 click 的副作用）。
    
    替代旧 dedupe_auto_navigates 的 gap 时间猜测——现在用信号明确关联。
    """
    action = actions[i]
    if action.action_name != "navigate" or action.params.get("new_tab"):
        return [action]
    prev = actions[i-1] if i > 0 else None
    if prev and prev.action_name in ("click", "input_text", "send_keys"):
        if abs(action.timestamp - prev.timestamp) <= 5.0:
            # 关联：导航是前置动作的副作用，附加信号，丢弃独立 navigate
            prev.signals.append(Signal(SignalKind.NAVIGATION, action.timestamp, 
                                       {"to_url": action.params["url"]}, prev.timestamp))
            return []  # 丢弃独立 navigate
    return [action]

def rule_redundant_click(actions: list[ActionRecord], i: int) -> list[ActionRecord]:
    """规则：误操作去重。
    
    同元素同 url 间隔 <2s 的 click 合并（打错重打）。
    替代旧 denoise_steps 的 click 折叠——但现在基于 element_ref 而非事后 index。
    """
    # ...
```

**五类翻译模式的归属**：

| 翻译模式 | 处理阶段 | 例子 |
|---|---|---|
| 直通 1:1 | Stage 1 | click → click |
| 聚合 N:1 | Stage 1（状态机辅助） | 逐字符 → input_text |
| 跳过 1:0 | Stage 3 规则 | OS 弹框事件、被信号关联的冗余 navigate |
| 替代 1:1 | Stage 3 规则 | file change → upload_file |
| 补全 0:1 | Stage 3 规则 | 信号附加后补 wait（可选，落盘时不一定生成） |

#### Stage 4: state machine（状态字段更新）

每个事件/动作处理后，更新状态机字段：

```python
def update_state(state: RecordingState, action: ActionRecord):
    if action.action_name == "input_text" and action.element_ref:
        state.focus_target_xpath = action.element_ref.xpath
        state.focus_value = action.params.get("text")
    elif action.action_name == "click":
        # click 后清除 focus（除非点的是 focus 元素本身）
        if state.focus_target_xpath != (action.element_ref.xpath if action.element_ref else None):
            state.focus_target_xpath = None
            state.focus_value = None
    # modal 状态
    modal_signals = [s for s in action.signals if s.kind == SignalKind.MODAL_OPENED]
    if modal_signals:
        state.pending_modal = modal_signals[-1].detail.get("selector")
```

### 3.4 Flatten：Recording → AgentHistoryList（落盘）

录制停止时，把内部 `Recording` 模型 flatten 成重放端能消费的 `AgentHistoryList`：

```python
def flatten(recording: Recording, browser: BrowserSession) -> AgentHistoryList:
    """Recording 内部模型 → AgentHistoryList（重放端格式，零改动消费）。"""
    steps = []
    for action in recording.actions:
        # 后端定位元素（复用现有 locator.locate_by_ref）
        interacted_element = None
        if action.element_ref and action.action_name in INDEX_ACTIONS:
            node = await locate_by_ref(action.element_ref, browser.get_state().dom_state.selector_map)
            if node:
                interacted_element = DOMInteractedElement.load_from_enhanced_dom_tree(node).to_dict()
                action.params["index"] = locate_index(node, selector_map)
        
        step = AgentHistory(
            step_number=len(steps),
            model_output={
                "actions": [{"name": action.action_name, "params": action.params}],
                "next_goal": "",  # 占位，重放不读
                "evaluation_previous_goal": "",
            },
            result=[],
            state_summary={"url": action.page_url, "title": action.page_title},
            interacted_element=[interacted_element],
            metadata=StepMetadata(step_start_time=action.timestamp, step_end_time=action.timestamp, 
                                  step_number=len(steps)),
        )
        steps.append(step)
    return AgentHistoryList(history=steps)
```

**signal 在 flatten 时的处理**：信号主要用于录制期间的意图推断和去噪，flatten 时大部分信号被消费掉（如导航信号已让冗余 navigate 被丢弃）。可选：modal_opened 信号可生成一个隐式 `wait` 步骤（如果重放端将来支持条件等待），但当前重放端不动，所以 modal 信号在 flatten 时只用于确保触发器 click 不被误删。

---

## 4. 采集层改进（扩展端）

### 4.1 findInteractiveAncestor 对齐后端 is_interactive（解决痛点 1.1）

现有 `findInteractiveAncestor` 只用 CSS 选择器。新增两条对齐后端 `dom.py:is_interactive`：

```typescript
function findInteractiveAncestor(el: Element | null): Element | null {
  let cur: Element | null = el;
  while (cur && cur !== document.body) {
    try {
      if (cur.matches(INTERACTIVE_SELECTOR)) return cur;
      // 对齐后端规则 14：cursor:pointer（收录 div 触发器）
      if (window.getComputedStyle(cur).cursor === 'pointer') return cur;
      // 对齐后端规则 10：onclick/onmousedown 属性
      if ((cur as HTMLElement).onclick || cur.getAttribute('onclick') || cur.getAttribute('onmousedown')) return cur;
    } catch { /* 非元素节点 */ }
    cur = cur.parentElement;
  }
  return el;
}
```

**效果**：用户点 `semi-select-selection`（cursor:pointer div）→ 向上找到触发器 → 录触发器指纹 → 回放 selector_map 有该触发器（后端规则 14 收录）→ 指纹匹配 → 点击触发器 → modal 打开。

> JS `addEventListener` 监听器（后端规则 3，React onClick 等）content script 难检测（`getEventListeners` 是 DevTools API），但 cursor:pointer + onclick 覆盖绝大多数 div 触发器。这是"尽量对齐"而非"完全对齐"。

### 4.2 信号采集：DOM 副作用观察器（解决痛点 1.3）

扩展端新增一个轻量 `MutationObserver`，专门检测动作副作用，作为后端 signal detection 的补充（扩展端能更实时地观察到 DOM 变化）：

```typescript
class SideEffectObserver {
  private observer: MutationObserver;
  private lastActionTs: number = 0;
  
  constructor(private sendSignal: (signal: SignalEvent) => void) {
    this.observer = new MutationObserver((mutations) => {
      if (Date.now() - this.lastActionTs > 1000) return;  // 只在动作后 1s 内观察
      const signals = this.detectSignals(mutations);
      signals.forEach(s => this.sendSignal(s));
    });
    this.observer.observe(document, { childList: true, subtree: true, attributes: true });
  }
  
  markAction(ts: number) { this.lastActionTs = ts; }
  
  private detectSignals(mutations: MutationRecord[]): SignalEvent[] {
    const signals: SignalEvent[] = [];
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node instanceof HTMLElement) {
          // modal 打开
          if (node.matches('[role="dialog"], [aria-modal="true"], .modal, .ant-modal'))
            signals.push({ type: 'modal_opened', selector: this.selectorOf(node), ts: Date.now() });
          // 下拉展开
          if (node.matches('[role="listbox"], .ant-select-dropdown, .semi-select-option-list'))
            signals.push({ type: 'dropdown_opened', selector: this.selectorOf(node), ts: Date.now() });
        }
      }
    }
    return signals;
  }
}
```

扩展检测到信号后 POST 后端，后端附加到最近动作的 `signals` 列表。这比后端事后 `get_state()` 对比更实时（DOM 变化瞬间捕获，不用等 500ms）。

### 4.3 采集端保留的能力（已验证有效，不重写）

以下采集能力经抖音上传联调验证有效，**保留不动**：
- contenteditable / Slate 富文本：MutationObserver 观察 characterData + childList（`troubleshooting.md` 问题 6 的修复）
- input 400ms 合并、scroll wheel 累计节流、IME 抑制
- file input change → upload_file
- SPA pushState/replaceState hook（injected.ts MAIN-world）

---

## 5. 解决痛点对照

| 痛点 | 现有解法 | 重设计方案 | 根治？ |
|---|---|---|---|
| findInteractiveAncestor 漏 div 触发器（1.1） | 无（用窄 CSS 选择器） | 加 cursor:pointer + onclick 检测，对齐后端 is_interactive（§4.1） | ✅ |
| dedupe_uploads 误吸收封面 click（1.2） | 一刀切吸收前置 click | signal 模型：有 modal_opened 信号的 click 绝不吸收（§3.3 rule_file_upload） | ✅ |
| dedupe_auto_navigates 用 gap 猜副作用（1.2） | gap 3s 猜测 | navigation signal 明确关联到触发动作（§3.3 rule_navigation_signal） | ✅ |
| SPA modal 连锁失败（1.3） | 无（事后补丁） | modal_opened signal 附加到触发 click + SideEffectObserver 实时检测（§4.2） | ✅ |
| 去噪散落两处无统一模型（1.2） | event_mapper 三个函数 + 扩展端若干 | 统一翻译管线（四阶段）+ Recording 内部模型 | ✅ |
| Slate 富文本拦截事件 | MutationObserver（已修） | 保留 | ✅ 已解决 |

---

## 6. 实施路线

### 阶段 1：内部模型 + 翻译管线骨架

**目标**：建 `recorder/models.py`（ActionRecord/Signal/Recording）+ `recorder/translation.py`（四阶段管线），把现有 `event_mapper.map_event` 迁进 Stage 1。

**验收**：现有测试（`test_recorder_event_mapper.py` ~20 用例）在新管线上通过。

### 阶段 2：signal 机制

**目标**：实现 Stage 2 signal detection + 扩展端 SideEffectObserver（§4.2）。重点测 modal_opened / navigation 两类信号。

**验收**：录制抖音"选择封面"→ click 信号列表含 `modal_opened`（封面编辑器）。录制"合集下拉"→ click 信号含 `dropdown_opened`。

### 阶段 3：翻译规则 + flatten

**目标**：实现 Stage 3 五类翻译规则（§3.3），特别是 `rule_file_upload`（用 signal 区分）和 `rule_navigation_signal`。实现 `flatten()` 落盘。

**验收**：录制抖音完整上传流程 → flatten 成 AgentHistory → `load_and_rerun` 重放，modal 内步骤（封面/合集/自主声明/发布）成功。

### 阶段 4：findInteractiveAncestor 对齐 + 端到端验证

**目标**：扩展端 `findInteractiveAncestor` 加 cursor:pointer + onclick（§4.1）。端到端重录抖音上传流程，对比旧实现的 8/20 成功率。

**验收**：重放成功率显著提升（目标 >15/20）。回归测试全绿。

---

## 7. 文件结构

```
src/tree_walker/recorder/
    __init__.py
    models.py          # 新：ActionRecord / Signal / Recording / RecordingState
    translation.py     # 新：四阶段翻译管线（替代 event_mapper 的去噪职责）
    rules.py           # 新：五类翻译模式规则
    flatten.py         # 新：Recording → AgentHistoryList
    recorder.py        # 改：用新管线，保留 target 修正 / upload 解析
    locator.py         # 不动：xpath/属性 → selector_map 定位
    server.py          # 不动：HTTP 服务
    event_mapper.py    # 废弃：职责拆进 translation.py + rules.py（保留 map_event 纯映射作 Stage 1）
recording_extension/
    capture/
        action-recorder.ts    # 改：findInteractiveAncestor 对齐 + 状态机字段
        side-effect-observer.ts  # 新：MutationObserver 信号采集
        selector.ts           # 不动
        navigation-recorder.ts # 不动
    shared/
        types.ts       # 改：加 SignalEvent 类型
```

---

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| signal detection 延迟（500ms）影响录制流畅度 | SideEffectObserver 在扩展端实时检测，后端 500ms get_state 作 fallback；signal 异步附加不阻塞主流程 |
| flatten 后 AgentHistory 丢失 signal 信息 | 重放端不动的前提下，signal 主要用于录制期去噪；如需保留，可在 `state_summary` 里序列化（重放端忽略） |
| findInteractiveAncestor 的 getComputedStyle 性能 | click 频率不高，向上遍历层数有限（到 body）；必要时缓存或限层深 |
| SideEffectObserver 误报（页面自身 DOM 变化非用户触发） | 只在动作后 1s 内观察（`markAction` 打时间戳），非动作窗口忽略 |
| 翻译规则迭代成本 | 规则函数接口统一（`actions, i -> list[ActionRecord]`），可单元测试每个规则；数据驱动迭代（§9） |

---

## 9. 数据驱动迭代策略

翻译规则不可能一次写全。建议：

1. **第一版只实现最确定的规则**（file_upload / navigation_signal / redundant_click），其他直通
2. **录制一批真实操作**（抖音 / 表单 / 搜索 / 导航混合），存原始事件流
3. **回放看哪些翻译错了**，针对错误案例加规则
4. 每个规则有真实的"录错了"案例支撑，避免过度设计

这比一开始就猜测所有规则靠谱——就像最初也没预料到文件上传和输入法切换是坑，后来也没预料到 Slate 富文本和 cursor:pointer div 是坑。

---

## 附录：业界工具翻译策略对照（调研结论）

| 难点 | Playwright Codegen | Selenium IDE | Chrome Recorder | TreeWalker 重设计 |
|---|---|---|---|---|
| 逐字符 vs 最终值 | `fill` on input（最终值） | `type` on change（最终值） | `change` step（最终值） | Stage 1 聚合 + focus 状态机（最终值） |
| 文件上传 | `setInputFiles`（input.files.name） | change 事件记录 | change step | upload_file + signal 区分前置 click |
| 导航 | NavigationSignal 附加到 click（5s 阈值） | readystatechange + 1500ms settle | navigate step | NavigationSignal 附加（§3.3） |
| 弹窗 | DialogSignal | preventClick 500ms | 手动 step | DialogSignal（Stage 2 检测） |
| 自动等待 | 回放层 actionability 检查 | 显式命令 | waitForElement step | 重放端不动（五级匹配 + retry 兜底） |
| 事后回溯 | RecorderSignalProcessor | recorderPreprocessors 管线 | 无 | Stage 2 signal detection + Stage 3 rules |

**核心共性**：所有成熟工具都用"最终值优先"（非逐键）+ "信号附加"（非独立动作）+ "状态机抑制"（非事后去噪）。TreeWalker 重设计对齐这三个共性。
