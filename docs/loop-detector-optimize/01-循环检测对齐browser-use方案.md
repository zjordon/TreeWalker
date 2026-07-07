# 循环检测（Loop Detection）对齐 browser-use 方案

> 阶段：agent step 的**软循环检测子模块**（`ActionLoopDetector`）—— 既负责 post 阶段的动作记录（`record_action`），也负责 prepare 阶段的页面状态记录（`record_page`）与 nudge 注入（`get_nudge_message`）
> 对照源：`D:\dev\git\learn_agent\browse-use\docs\agent-core\类的职责\ActionLoopDetector类职责.md`；`D:\dev\git\z_jordon\browser-use\browser_use\agent\views.py`（`PageFingerprint` L95-107、`_normalize_action_for_hash` L110-148、`compute_action_hash` L151-154、`ActionLoopDetector` L157-248、`AgentSettings.loop_detection_*` L90-91）+ `service.py`（`_inject_loop_detection_nudge` L1484-1494、`_update_loop_detector_actions` L1496-1513、`_update_loop_detector_page_state` L1515-1528）+ `tests/ci/test_action_loop_detection.py`（405 行）
> 本文档只交付**方案**，不含代码落地；落地为后续独立任务。
>
> **结论先行**：TreeWalker 的 `ActionLoopDetector`（`src/tree_walker/agent/loop_detector.py`）**骨架与 browser-use 已对齐**——都是「滑动窗口动作哈希 + 软 nudge（不阻塞）」，且调用点的**动作记录 + 豁免集合**（`_LOOP_EXEMPT_ACTIONS = {wait, done, go_back}`）也已对齐（`step.py:1290`）。缺口集中在**模块内部的三处能力缺失**：① 哈希归一化不区分动作类型（P1-1）；② 页面状态维度完全缺失——只记 URL 且从未读取（`recent_urls` 是死代码）（P1-2）；③ nudge 只读动作重复、不读页面停滞，文案用阈值桶而非实际计数（P1-3）。另加窗口 15→20（P1-4）。**无 P0 级结构缺失**。可配置开关（`loop_detection_enabled` / `loop_detection_window`）为 P2 暂缓；nudge 注入机制（`[System Notice]` 内联 vs `_message_manager._add_context_message`）为 P3 架构差异不改。**本期建议落地 P1-1/2/3/4，P2/P3 暂缓。**

---

## 1. 背景与范围

本文档由 `04-后处理对齐browser-use方案.md` §7（P3-2）+ §9 的暂缓项独立成文。04 §7 原文：

> post_process 的**调用点**（`record_action` + 豁免集合）已对齐。上述差异落在：**phase 01**（`record_page` 调用 + nudge 注入）+ **loop_detector 模块本身**。本文档仅交叉引用，建议另开独立文档，不在本期展开。

同时 04 §4.5 指出：04 的 P1-1（多动作失败改由循环检测处理）落地后，loop_detector 的页面停滞维度缺失会成为**已知短板**，优先级上升——多动作失败现在更依赖循环检测兜底介入。本文档即落实该建议。

### 范围

| 模块 / 调用点 | 位置 | 本文档 |
|---|---|---|
| `ActionLoopDetector` 模块 | `src/tree_walker/agent/loop_detector.py`（全文 53 行） | ✅ 核心范围 |
| 动作记录调用点 | `step.py:954-958`（Post 阶段逐动作 `record_action`） | ✅（P1-1 归一化影响此处哈希） |
| 页面状态记录调用点 | `step.py:189`（Prepare 阶段 `record_page`） | ✅（P1-2 必须改此调用点以传三维指纹） |
| nudge 取用调用点 | `step.py:209`（`get_nudge_message`）→ `prompts/system_prompt.py:222-224`（`[System Notice]` 内联） | ✅ 取用 / ⏸ 注入机制（P3） |
| 豁免集合 | `step.py:1290` `_LOOP_EXEMPT_ACTIONS` | ✅ 核查（已对齐） |
| 实例化 | `agent.py:83` `self.loop_detector = ActionLoopDetector()` | ✅（P1-4 窗口默认值） |

### 不覆盖

- **nudge 注入机制**：TreeWalker 把 nudge 拼进 `build_state_message` 的 `[System Notice]` 段（`system_prompt.py:222-224`），browser-use 走 `_message_manager._add_context_message(UserMessage)`（`service.py:1491`）。这是消息管理架构差异，**超出模块范围**，归 P3，本文不改。
- **5 阶段流水线本身**（01-04）、**异常处理**（06）、**终结化**（05）。
- **`_FALLBACK_DONE_OUTPUT` / 解析兜底**：那是 LLM 层错误恢复，与循环检测无关。

---

## 2. 现状精确锚点（已核对真实代码）

### 2.1 TreeWalker `loop_detector.py` 全文（`src/tree_walker/agent/loop_detector.py`）

```python
"""Soft loop detection — nudges the LLM when repeated actions are detected."""

from __future__ import annotations

import json
from collections import deque


class ActionLoopDetector:
    """Tracks recent actions and page states to detect stuck loops.

    Does not block actions. Instead, returns nudge messages that get injected
    into the LLM context for the next step.
    """

    def __init__(self, window_size: int = 15) -> None:
        self.recent_actions: deque[str] = deque(maxlen=window_size)
        self.recent_urls: deque[str] = deque(maxlen=window_size)

    def record_action(self, name: str, params: dict) -> None:
        key_params = {k: v for k, v in params.items() if k not in ("text", "clear")}
        action_hash = f"{name}:{json.dumps(key_params, sort_keys=True, default=str)}"
        self.recent_actions.append(action_hash)

    def record_page(self, url: str) -> None:
        self.recent_urls.append(url)

    def get_nudge_message(self) -> str | None:
        if len(self.recent_actions) < 3:
            return None

        counts: dict[str, int] = {}
        for h in self.recent_actions:
            counts[h] = counts.get(h, 0) + 1

        max_count = max(counts.values())
        if max_count >= 12:
            return (
                "CRITICAL: You have repeated the same action 12+ times. "
                "You must immediately try a completely different approach or call done. "
                "Continuing the same action will not succeed."
            )
        if max_count >= 8:
            return (
                "WARNING: You have repeated the same action 8+ times. "
                "You are likely stuck in a loop. Try a completely different approach."
            )
        if max_count >= 5:
            return (
                "WARNING: You have repeated the same action 5+ times. "
                "Consider whether you are making progress or need a different strategy."
            )
        return None
```

### 2.2 browser-use `ActionLoopDetector` 全文（`browser_use/agent/views.py` L95-248）

```python
class PageFingerprint(BaseModel):
    """Lightweight fingerprint of the browser page state."""

    model_config = ConfigDict(frozen=True)

    url: str
    element_count: int
    text_hash: str  # First 16 chars of SHA-256 of the DOM text representation

    @staticmethod
    def from_browser_state(url: str, dom_text: str, element_count: int) -> PageFingerprint:
        text_hash = hashlib.sha256(dom_text.encode('utf-8', errors='replace')).hexdigest()[:16]
        return PageFingerprint(url=url, element_count=element_count, text_hash=text_hash)


def _normalize_action_for_hash(action_name: str, params: dict[str, Any]) -> str:
    if action_name == 'search':
        query = str(params.get('query', ''))
        tokens = sorted(set(re.sub(r'[^\w\s]', ' ', query.lower()).split()))
        engine = params.get('engine', 'google')
        return f'search|{engine}|{"|".join(tokens)}'

    if action_name in ('click', 'input'):
        index = params.get('index')
        if action_name == 'input':
            text = str(params.get('text', ''))
            return f'input|{index}|{text.strip().lower()}'
        return f'click|{index}'

    if action_name == 'navigate':
        url = str(params.get('url', ''))
        return f'navigate|{url}'

    if action_name == 'scroll':
        direction = 'down' if params.get('down', True) else 'up'
        index = params.get('index')
        return f'scroll|{direction}|{index}'

    filtered = {k: v for k, v in sorted(params.items()) if v is not None}
    return f'{action_name}|{json.dumps(filtered, sort_keys=True, default=str)}'


def compute_action_hash(action_name: str, params: dict[str, Any]) -> str:
    """Compute a stable hash string for an action based on type + normalized parameters."""
    normalized = _normalize_action_for_hash(action_name, params)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]


class ActionLoopDetector(BaseModel):
    """Tracks action repetition and page stagnation to detect behavioral loops.

    This is a soft detection system — it generates context messages for the LLM
    but never blocks actions. The agent can still repeat if it wants to.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    window_size: int = 20
    recent_action_hashes: list[str] = Field(default_factory=list)
    recent_page_fingerprints: list[PageFingerprint] = Field(default_factory=list)
    max_repetition_count: int = 0
    most_repeated_hash: str | None = None
    consecutive_stagnant_pages: int = 0

    def record_action(self, action_name: str, params: dict[str, Any]) -> None:
        h = compute_action_hash(action_name, params)
        self.recent_action_hashes.append(h)
        if len(self.recent_action_hashes) > self.window_size:
            self.recent_action_hashes = self.recent_action_hashes[-self.window_size:]
        self._update_repetition_stats()

    def record_page_state(self, url: str, dom_text: str, element_count: int) -> None:
        fp = PageFingerprint.from_browser_state(url, dom_text, element_count)
        if self.recent_page_fingerprints and self.recent_page_fingerprints[-1] == fp:
            self.consecutive_stagnant_pages += 1
        else:
            self.consecutive_stagnant_pages = 0
        self.recent_page_fingerprints.append(fp)
        if len(self.recent_page_fingerprints) > 5:
            self.recent_page_fingerprints = self.recent_page_fingerprints[-5:]

    def _update_repetition_stats(self) -> None:
        if not self.recent_action_hashes:
            self.max_repetition_count = 0
            self.most_repeated_hash = None
            return
        counts: dict[str, int] = {}
        for h in self.recent_action_hashes:
            counts[h] = counts.get(h, 0) + 1
        self.most_repeated_hash = max(counts, key=lambda g: counts[g])
        self.max_repetition_count = counts[self.most_repeated_hash]

    def get_nudge_message(self) -> str | None:
        messages: list[str] = []
        if self.max_repetition_count >= 12:
            messages.append(
                f'Heads up: you have repeated a similar action {self.max_repetition_count} times '
                f'in the last {len(self.recent_action_hashes)} actions. '
                'If you are making progress with each repetition, keep going. '
                'If not, a different approach might get you there faster.'
            )
        elif self.max_repetition_count >= 8:
            messages.append(
                f'Heads up: you have repeated a similar action {self.max_repetition_count} times '
                f'in the last {len(self.recent_action_hashes)} actions. '
                'Are you still making progress with each attempt? '
                'If so, carry on. Otherwise, it might be worth trying a different approach.'
            )
        elif self.max_repetition_count >= 5:
            messages.append(
                f'Heads up: you have repeated a similar action {self.max_repetition_count} times '
                f'in the last {len(self.recent_action_hashes)} actions. '
                'If this is intentional and making progress, carry on. '
                'If not, it might be worth reconsidering your approach.'
            )
        if self.consecutive_stagnant_pages >= 5:
            messages.append(
                f'The page content has not changed across {self.consecutive_stagnant_pages} consecutive actions. '
                'Your actions might not be having the intended effect. '
                'It could be worth trying a different element or approach.'
            )
        if messages:
            return '\n\n'.join(messages)
        return None
```

### 2.3 调用点 / 配置 / 测试锚点

| 锚点 | TreeWalker 位置 | browser-use 位置 | 状态 |
|---|---|---|---|
| 模块实例化 | `agent.py:83` `ActionLoopDetector()`（window=15） | `AgentState.loop_detector`（window=20） | ⚠️ window 不同（P1-4） |
| 动作记录（Post） | `step.py:954-958` 逐动作 `record_action(name, params)` | `service.py:1496-1513` `_update_loop_detector_actions` | ✅ 调用点对齐 |
| 豁免集合 | `step.py:1290` `_LOOP_EXEMPT_ACTIONS = frozenset({"wait","done","go_back"})` | `service.py:1504` `{'wait','done','go_back'}` | ✅ **已对齐** |
| 页面状态记录（Prepare） | `step.py:189` `record_page(browser_state.url)`（仅 URL） | `service.py:1515-1528` `record_page_state(url, dom_text, element_count)`（三维） | ❌ **P1-2** |
| nudge 取用 | `step.py:209` `get_nudge_message()` | `service.py:1486` `get_nudge_message()` | ✅ 取用对齐（返回值内容差异见 P1-3） |
| nudge 注入 | `prompts/system_prompt.py:222-224` 拼进 `[System Notice]` 段（`build_state_message`） | `service.py:1491` `_message_manager._add_context_message(UserMessage)` | 📄 P3（架构差异，不改） |
| 调用点三维数据可达性 | `browser_state.dom_state.element_tree_text` + `len(browser_state.dom_state.selector_map)`（`browser/views.py:694-710` `SerializedDOMState` / `:789-798` `BrowserStateSummary`） | `dom_state.selector_map` + `dom_state.llm_representation()` | ✅ 三维指纹**可行**（P1-2 前提） |
| 可配置开关 | 无 | `AgentSettings.loop_detection_enabled=True` / `loop_detection_window=20`（`views.py:90-91`） | ⏸ P2-1（暂缓） |
| 单测 | `tests/test_loop_detector.py`（6 用例，仅阈值 + min-3 守卫） | `tests/ci/test_action_loop_detection.py`（405 行，归一化/停滞/窗口/集成全覆盖） | ❌ 测试覆盖差距大 |

### 2.4 七个决定性结论

1. **骨架对齐**。两者都是「滑动窗口动作哈希 + 软 nudge（不阻塞动作，仅注入上下文）」，`record_action` / `get_nudge_message` 接口形态一致。
2. **调用点已对齐**。动作记录（`step.py:954-958`）+ 豁免集合 `{wait, done, go_back}`（`step.py:1290`）与 browser-use（`service.py:1496-1513` / `:1504`）一致——04 后处理本期已确认。
3. **哈希归一化是核心缺口（P1-1）**。browser-use `_normalize_action_for_hash` 按动作类型语义归一化（search 词序无关 / click 按 index / input 按 index+text / navigate 按 url / scroll 按 direction）；TreeWalker 无差别剥 `text`/`clear` 后整体 JSON——对 `input_text` **过度合并**（不同文本被判同动作→漏报「在前进」），对 `scroll` **过度切分**（不同 `amount` 判不同动作→漏报滚动循环）。
4. **页面状态维度完全缺失（P1-2）**。browser-use 三维指纹 `PageFingerprint(url, element_count, text_hash)` + `consecutive_stagnant_pages` 计数；TreeWalker `record_page(url)` 只记 URL，且 `recent_urls` **从未被 `get_nudge_message` 读取 = 死代码**。调用点（`step.py:189`）三维数据可达（`element_tree_text` + `len(selector_map)`），补强可行。
5. **nudge 单维度 vs 双维度（P1-3）**。TreeWalker `get_nudge_message` 只读动作重复计数；browser-use 同时读 `consecutive_stagnant_pages`——页面卡住（URL/DOM 不变）时 TreeWalker **无法触发 nudge**。
6. **文案差异（随 P1-3）**。TreeWalker 用阈值桶写法「repeated the same action 5+/8+/12+ times」+「same action」；browser-use 用**实际计数** + 「**similar** action」（因归一化后同哈希是「相似」非「相同」）+ 独立的页面停滞文案。
7. **窗口与可配置性**。browser-use `window=20` + `loop_detection_enabled` 开关（`AgentSettings`）；TreeWalker `window=15` 硬编码、无开关。窗口对齐归 P1-4（零成本），开关归 P2-1（需 config plumbing，暂缓）。

---

## 3. browser-use 子步骤 vs TreeWalker 全景对照

### 子步骤 1：动作归一化与哈希（`compute_action_hash` / `_normalize_action_for_hash`）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 1a | 按动作类型分发归一化 | `_normalize_action_for_hash` 五分支（search/click+input/navigate/scroll/default） | ❌ 单一公式 `name:{json(去 text/clear)}` | ❌ **P1-1** |
| 1b | search 词序/大小写/标点无关 | `sorted(set(tokenize(query.lower())))` | ❌ 整体 JSON，`"a b"`≠`"b a"` | ❌ P1-1 |
| 1c | click 按元素身份（index） | `click\|{index}` | ⚠️ `click:{"index":1}` 巧合对齐 index 分支；但 `element_id` 分支未处理 | ⚠️ P1-1 |
| 1d | input 保留 text（不同文本=不同动作） | `input\|{index}\|{text.strip().lower()}` | ❌ **剥 text** → 不同文本判同动作 | ❌ P1-1 |
| 1e | navigate 按 url | `navigate\|{url}` | ⚠️ 含 `new_tab`，同 url 不同 new_tab 判不同 | ⚠️ P1-1 |
| 1f | scroll 按 direction（不含 amount） | `scroll\|{direction}\|{index}` | ❌ 含 `amount`，同方向不同 amount 判不同 | ❌ P1-1 |
| 1g | 哈希用 sha256[:12] | `compute_action_hash` | ❌ 直接用归一化字符串当「哈希」（功能等价，但无抗碰撞固定长度） | ⚠️ P1-1 |
| 1h | 豁免 wait/done/go_back | `_LOOP_EXEMPT_ACTIONS`（`service.py:1504`） | ✅ `step.py:1290` 同集合 | ✅ 对齐 |

### 子步骤 2：页面状态指纹（`record_page_state` / `PageFingerprint`）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 2a | 三维指纹（url + 元素数 + dom 文本哈希） | `PageFingerprint.from_browser_state` | ❌ `record_page(url)` 仅 URL | ❌ **P1-2** |
| 2b | dom 文本哈希（sha256[:16]） | `text_hash` | ❌ 无 | ❌ P1-2 |
| 2c | 连续停滞计数 `consecutive_stagnant_pages` | 有（页变则归零） | ❌ 无 | ❌ P1-2 |
| 2d | 调用点传三维 | `_update_loop_detector_page_state`（`service.py:1515-1528`） | ❌ `step.py:189` 只传 `url` | ❌ P1-2 |
| 2e | 指纹队列（保留最近 5 个） | `recent_page_fingerprints[-5:]` | ⚠️ `recent_urls` maxlen=15 但**未读**（死代码） | ⚠️ P1-2 |

### 子步骤 3：nudge 生成（`get_nudge_message` 双维度）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 3a | 动作重复三档（5/8/12） | ✅ | ✅ 阈值相同 | ✅ 对齐 |
| 3b | **同时读页面停滞**（≥5） | ✅ 拼接第二条 | ❌ 只读动作重复 | ❌ **P1-3** |
| 3c | 文案用**实际计数** | `repeated ... {max_repetition_count} times` | ❌ 阈值桶 `5+/8+/12+` | ❌ P1-3 |
| 3d | 「similar action」表述（归一化语义） | ✅ | ❌「same action」 | ❌ P1-3 |
| 3e | 含窗口上下文 `in the last {n} actions` | ✅ | ❌ 无 | ❌ P1-3 |
| 3f | 两条 nudge 用 `\n\n` 拼接 | ✅ | N/A（只有一条） | 随 P1-3 |
| 3g | min-3-actions 守卫 | ❌ 无（直接判计数 ≥5） | ✅ 有（`len < 3 return None`） | ✅ TreeWalker 更保守（无害，保留） |

### 子步骤 4：调用点集成（record / inject）

| # | 子步骤 | browser-use | TreeWalker 现状 | 状态 |
|---|---|---|---|---|
| 4a | 动作记录调用点 | `_update_loop_detector_actions`（Post） | ✅ `step.py:954-958` | ✅ 对齐 |
| 4b | 页面状态记录调用点 | `_update_loop_detector_page_state`（Prepare） | ⚠️ `step.py:189`（待 P1-2 改造） | ⚠️ P1-2 |
| 4c | nudge 注入位置 | `_message_manager._add_context_message(UserMessage)` | `build_state_message` 的 `[System Notice]` 段（`system_prompt.py:222-224`） | 📄 P3（架构差异） |
| 4d | 注入日志（🔁 计数+停滞） | `logger.info('🔁 ... repetition=... stagnation=...')` | ❌ 无 | ⚠️ 可选（P1 落地时顺带） |

> **覆盖率**：4 子步骤共 25 项，其中 **5 项 ✅ 对齐**（骨架/阈值/动作记录/豁免/min-3 守卫）；**14 项 ❌ 真 gap** 集中在 P1-1（7 项）/ P1-2（5 项）/ P1-3（5 项，部分重叠）；**4 项 ⚠️ 差异**（window / 调用点待改 / 注入日志 / element_id 分支）；**1 项 📄 P3**（注入机制）；**1 项 ⏸ P2**（开关）。

---

## 4. P1 方案（建议落地）

### 4.1 P1-1：按动作类型语义归一化（`_normalize_action_for_hash` 适配 TreeWalker 动作词表）

#### 4.1.1 现状

`record_action`（`loop_detector.py:20-23`）对**所有**动作套同一公式：

```python
key_params = {k: v for k, v in params.items() if k not in ("text", "clear")}
action_hash = f"{name}:{json.dumps(key_params, sort_keys=True, default=str)}"
```

无差别剥 `text`/`clear`，不区分动作类型。

#### 4.1.2 影响

- **`input_text` 过度合并（漏报前进）**：`input_text(index=1, text="hello")` 与 `input_text(index=1, text="world")` 哈希相同（text 被剥）→ agent 在同一输入框逐步修正文本（**在前进**）却被记为「重复同一动作」→ 误触 nudge。browser-use 保留 `input|index|text` 正是为区分。
- **`scroll` 过度切分（漏报循环）**：`scroll(direction="down", amount=3)` 与 `scroll(direction="down", amount=5)` 哈希不同（amount 入哈希）→ agent 反复向下滚（真循环）却因 amount 微变被判「不同动作」→ 漏报。browser-use 只按 `direction|index`。
- **`search` 词序敏感**：`search(query="a b")` 与 `search(query="b a")` 哈希不同 → 同一意图的两次搜索被判不同。
- **`navigate` 受 `new_tab` 干扰**：同 URL 新开标签 vs 原位导航被判不同。
- **哈希无固定长度**：直接用归一化字符串，长 params 导致窗口内存放大；且无抗碰撞压缩。

#### 4.1.3 browser-use 做法

`_normalize_action_for_hash`（`views.py:110-148`）按动作类型分五分支归一化，再经 `compute_action_hash`（`views.py:151-154`）取 `sha256[:12]`。详见 §2.2 全文。

#### 4.1.4 方案（适配 TreeWalker 24 个动作，**不照搬 browser-use 动作名/参数**）

TreeWalker 动作词表（`src/tree_walker/tools/models.py`）与 browser-use **名字/参数均不同**，须按下表适配：

| browser-use | TreeWalker 动作 | TreeWalker 参数（`models.py`） | 归一化哈希键 | 备注 |
|---|---|---|---|---|
| `search` | `search` | `query: str, engine` | `search\|{engine}\|{sorted(set(re.sub(r'[^\w\s]',' ',query.lower()).split()))}` | 词序/大小写/标点无关 |
| `click` | `click` | `index \| element_id`（二选一） | `click\|{index ?? element_id}` | 同一元素=同一动作；`element_id` 兜底 |
| `input` | **`input_text`** | `index\|element_id, text, clear` | `input_text\|{idx}\|{text.strip().lower()}` | **保留 text**（不同文本=在前进）；纠正当前剥 text 的过度合并 |
| `navigate` | `navigate` | `url, new_tab` | `navigate\|{url}` | 忽略 `new_tab`（同 URL 新开标签仍是循环信号） |
| `scroll` | `scroll` | `direction: "up"\|"down", amount` | `scroll\|{direction}` | **TreeWalker 无 index**；按 `direction` 字符串（browser-use 用 `down` bool→方向）；纠正当前按 amount 过度切分 |
| — | `extract` / `search_page` / `find_elements` / `find_text` / `send_keys` / `switch_tab` / `close_tab` / `select_dropdown` / `dropdown_options` / `upload_file` / `write_file` / `read_file` / `replace_file` / `evaluate` / `screenshot` / `save_as_pdf` | 各自参数 | 默认：`{name}\|{json.dumps({k:v for k,v in sorted(params.items()) if v is not None}, sort_keys=True, default=str)}` | 落入默认分支 |
| — | `wait` / `done` / `go_back` | — | **豁免不记录** | ✅ 已对齐（`step.py:1290`） |

新增模块级函数（`loop_detector.py`）：

```python
import hashlib
import re

def _normalize_action_for_hash(name: str, params: dict) -> str:
    """Normalize action params for similarity hashing (adapted to TreeWalker action vocabulary)."""
    # 元素身份：click / input_text 用 index，缺则回退 element_id
    def _idx() -> str:
        return str(params.get("index") if params.get("index") is not None else params.get("element_id"))

    if name == "search":
        query = str(params.get("query", ""))
        tokens = sorted(set(re.sub(r"[^\w\s]", " ", query.lower()).split()))
        engine = params.get("engine", "baidu")
        return f"search|{engine}|{'|'.join(tokens)}"
    if name == "click":
        return f"click|{_idx()}"
    if name == "input_text":
        text = str(params.get("text", "")).strip().lower()
        return f"input_text|{_idx()}|{text}"
    if name == "navigate":
        return f"navigate|{params.get('url', '')}"
    if name == "scroll":
        return f"scroll|{params.get('direction', 'down')}"
    # 默认：动作名 + 排序后非 None 参数
    filtered = {k: v for k, v in sorted(params.items()) if v is not None}
    return f"{name}|{json.dumps(filtered, sort_keys=True, default=str)}"


def _compute_action_hash(name: str, params: dict) -> str:
    """Stable 12-char hash (sha256[:12], 48-bit) — mirrors browser-use compute_action_hash."""
    return hashlib.sha256(_normalize_action_for_hash(name, params).encode("utf-8")).hexdigest()[:12]
```

`record_action` 改为 `self.recent_actions.append(_compute_action_hash(name, params))`（其余滑动窗口逻辑不变）。

#### 4.1.5 关键决策

- **不照搬 browser-use 的 `_normalize_action_for_hash` docstring**。其 docstring 写「For click actions: hash by element type + rough text content, ignoring index」，但**代码实际是 `click|{index}`**——docstring 过时。本文以**代码**为准（click 按 index），与 `learn_agent/.../ActionLoopDetector类职责.md` 的归纳一致。
- **动作名/参数映射到 TreeWalker 实际词表**：`input`→`input_text`；`scroll` 用 `direction` 字符串（非 browser-use 的 `down` bool）；`click` 有 `element_id` 备选（browser-use 无）。
- **哈希用 `sha256[:12]`**（48-bit，抗碰撞，固定长度），与 browser-use 一致；替换当前「归一化字符串直接当哈希」。
- **保留默认分支**：`extract`/`find_*`/`send_keys` 等无专用归一化的动作走默认 JSON，确保前向兼容（新增动作不会崩）。
- **纠正两处归一化错误**（§4.1.2）：`input_text` 保留 text、`scroll` 去 amount——这是与 browser-use 对齐的**功能修正**，非纯字符串对齐。

#### 4.1.6 边界与风险

- **`click` 的 `element_id` 回退**：TreeWalker 允许 `index` 或 `element_id` 二选一。同一元素在不同 step 可能一次给 `index`、一次给 `element_id`（值还不同）→ 哈希不同 → 漏报。**接受此限制**：browser-use 也只认 `index`，且实践中 LLM 对同一元素倾向稳定给 `index`；若未来出现 `element_id` 频繁抖动，再评估「index↔element_id 统一映射」（需 DOM selector_map 反查，成本高）。
- **`navigate` 忽略 `new_tab`**：同 URL 新开标签也判同动作。这是有意的——同 URL 反复导航本身就是循环信号；若新开标签后页面确实不同，页面停滞维度（P1-2）不会误触（指纹含 url + dom）。
- **`search` 默认 engine 差异**：browser-use 默认 `'google'`，TreeWalker 默认 `'baidu'`（`models.py`）。归一化取 `params.get('engine', 'baidu')`——**用 TreeWalker 自己的默认值**，不照搬 google。
- **测试隔离**：`_normalize_action_for_hash` / `_compute_action_hash` 是纯函数，单测无需起 LLM/浏览器。

---

### 4.2 P1-2：三维页面状态指纹（`PageFingerprint` + `record_page_state` + 调用点改造）

#### 4.2.1 现状

`record_page`（`loop_detector.py:25-26`）只记 URL：

```python
def record_page(self, url: str) -> None:
    self.recent_urls.append(url)
```

`recent_urls` 从未被 `get_nudge_message` 读取（死代码）。调用点 `step.py:189` 也只传 URL：

```python
# 3. Record page for loop detection
self.loop_detector.record_page(browser_state.url)
```

#### 4.2.2 影响

页面停滞（URL 不变但 DOM 不变 / 或 SPA 内 URL 不变 DOM 微变）**完全无法被检测**。典型场景：agent 反复 click 一个无响应按钮（URL 不变、DOM 不变）——动作归一化后若 click 目标元素 index 稳定会触发 P1-1 的动作 nudge，但若 index 抖动则动作维度漏报，此时页面停滞维度是**唯一兜底信号**，当前缺失。

#### 4.2.3 browser-use 做法

`PageFingerprint`（`views.py:95-107`，`frozen`）三维：`url + element_count + text_hash(sha256(dom_text)[:16])`；`record_page_state`（`views.py:187-197`）比最后一条指纹，相同则 `consecutive_stagnant_pages += 1`，否则归零；保留最近 5 条指纹。调用点 `_update_loop_detector_page_state`（`service.py:1515-1528`）从 `browser_state_summary` 取 `url` / `len(dom_state.selector_map)` / `dom_state.llm_representation()`。

#### 4.2.4 方案

**模块侧**（`loop_detector.py`）新增 `PageFingerprint` + 改造记录方法：

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class PageFingerprint:
    url: str
    element_count: int
    text_hash: str  # sha256(dom_text)[:16]

    @staticmethod
    def from_state(url: str, dom_text: str, element_count: int) -> "PageFingerprint":
        text_hash = hashlib.sha256(dom_text.encode("utf-8", errors="replace")).hexdigest()[:16]
        return PageFingerprint(url=url, element_count=element_count, text_hash=text_hash)
```

`ActionLoopDetector` 改造：

```python
def __init__(self, window_size: int = 20) -> None:  # P1-4：15→20
    self.recent_actions: deque[str] = deque(maxlen=window_size)
    self.recent_page_fingerprints: deque[PageFingerprint] = deque(maxlen=5)
    self.consecutive_stagnant_pages: int = 0
    self.max_repetition_count: int = 0
    self.most_repeated_hash: str | None = None

def record_page_state(self, url: str, dom_text: str, element_count: int) -> None:
    fp = PageFingerprint.from_state(url, dom_text, element_count)
    if self.recent_page_fingerprints and self.recent_page_fingerprints[-1] == fp:
        self.consecutive_stagnant_pages += 1
    else:
        self.consecutive_stagnant_pages = 0
    self.recent_page_fingerprints.append(fp)
```

> 删除 `record_page(url)` + `recent_urls`（死代码清理）。`record_action` 顺带在末尾调 `self._update_repetition_stats()`（见 P1-3）。

**调用点改造**（`step.py:188-189`）：

```python
# 3. Record page state for loop detection (3-dim fingerprint)
dom = browser_state.dom_state
self.loop_detector.record_page_state(
    browser_state.url,
    dom.element_tree_text if dom else "",
    len(dom.selector_map) if dom else 0,
)
```

#### 4.2.5 关键决策

- **dom 文本用 `element_tree_text`，不用 browser-use 的 `llm_representation()`**。browser-use 的 `llm_representation()` 是按 LLM 视口动态裁剪的文本（带高亮标记、随视口变），TreeWalker 的 `SerializedDOMState.element_tree_text`（`browser/views.py:702`）是稳定的 DOM 树文本序列化——**更适合做指纹**（稳定性 > 还原 LLM 视角）。这是**适配而非照搬**。
- **`element_count = len(dom.selector_map)`**：与 browser-use 一致（`selector_map` 是元素 index→节点的映射，长度即可交互元素数）。
- **`dom` 为 None 兜底**：`BrowserStateSummary.dom_state` 可为 `None`（`browser/views.py:793`），传空文本 + 0 元素数 → 指纹仍可计算（text_hash 为空串的哈希），不崩。
- **指纹队列 `maxlen=5`**：browser-use 只比最后一条判停滞，保留 5 条供调试/观测；用 `deque(maxlen=5)` 自动淘汰。

#### 4.2.6 边界与风险

- **DOM 文本稳定性**：`element_tree_text` 若包含动态内容（时间戳、随机 token、广告位），同页也会判「变化」→ 停滞计数永不累加 → 漏报。**接受此限制**：browser-use 同样依赖 dom 文本稳定性；若线上发现某类页面 DOM 噪声大，再评估「指纹剔除动态区域」（成本高，暂不做）。
- **`record_page` 删除的兼容性**：当前 `record_page` 仅 `step.py:189` 一处调用（已确认全局唯一），删除安全。
- **`PageFingerprint` 用 dataclass 而非 Pydantic**：`loop_detector.py` 当前是纯 stdlib 模块（无 Pydantic 依赖），保持轻量；browser-use 用 Pydantic 是因其 `AgentState` 全 Pydantic。TreeWalker 用 `@dataclass(frozen=True)` 即可满足「可哈希 + 不可变」。

---

### 4.3 P1-3：`get_nudge_message` 双维度 + 文案对齐

#### 4.3.1 现状

`get_nudge_message`（`loop_detector.py:28-53`）只读 `recent_actions` 计数，文案用阈值桶 `5+/8+/12+ times` + `same action`。

#### 4.3.2 影响

- **页面停滞无法触发 nudge**（P1-2 的消费端）。
- **文案不够informative**：实际重复 9 次时显示「8+ times」（阈值桶），LLM 不知道确切次数；「same action」与 P1-1 归一化语义不符（归一化后同哈希是「相似动作」）。
- **缺窗口上下文**：不告知 LLM「在过去 N 个动作里重复」，LLM 难以判断是否真在循环。

#### 4.3.3 方案

`get_nudge_message` 改造为双维度（动作重复 + 页面停滞），文案对齐 browser-use（实际计数 + similar + 窗口上下文 + 停滞文案）：

```python
def get_nudge_message(self) -> str | None:
    # 维持 min-3 守卫（比 browser-use 更保守，无害）
    self._update_repetition_stats()  # 复用 P1-1 记录时已算；此处幂等兜底
    if len(self.recent_actions) < 3 and self.consecutive_stagnant_pages < 5:
        return None

    messages: list[str] = []
    n = len(self.recent_actions)
    if self.max_repetition_count >= 12:
        messages.append(
            f"Heads up: you have repeated a similar action {self.max_repetition_count} times "
            f"in the last {n} actions. "
            "If you are making progress with each repetition, keep going. "
            "If not, a different approach might get you there faster."
        )
    elif self.max_repetition_count >= 8:
        messages.append(
            f"Heads up: you have repeated a similar action {self.max_repetition_count} times "
            f"in the last {n} actions. "
            "Are you still making progress with each attempt? "
            "If so, carry on. Otherwise, it might be worth trying a different approach."
        )
    elif self.max_repetition_count >= 5:
        messages.append(
            f"Heads up: you have repeated a similar action {self.max_repetition_count} times "
            f"in the last {n} actions. "
            "If this is intentional and making progress, carry on. "
            "If not, it might be worth reconsidering your approach."
        )

    if self.consecutive_stagnant_pages >= 5:
        messages.append(
            f"The page content has not changed across {self.consecutive_stagnant_pages} consecutive actions. "
            "Your actions might not be having the intended effect. "
            "It could be worth trying a different element or approach."
        )

    if messages:
        return "\n\n".join(messages)
    return None
```

> 阈值 **5/8/12（动作）+ 5（停滞）与 browser-use 完全一致**，TreeWalker 现有 5/8/12 无需改。

#### 4.3.4 关键决策

- **`_update_repetition_stats` 抽方法**：browser-use 在 `record_action` 末尾算（`views.py:178-185`）；TreeWalker 当前在 `get_nudge_message` 内联算。改造为：`record_action` 末尾调 `self._update_repetition_stats()`，`get_nudge_message` 直接读 `self.max_repetition_count`（与 browser-use 一致），消除重复计算。
- **保留 min-3 守卫**：browser-use 无此守卫（直接判 ≥5），TreeWalker 多一道更保守的闸门，无害（5 ≥ 3 恒成立），保留以减极端短序列的误报。
- **文案对齐 browser-use 原文**（`Heads up...`），不另造中文文案——nudge 是给 LLM 读的英文上下文，与 browser-use 同语料便于对照验证。
- **「similar action」表述**：因 P1-1 归一化，同哈希语义是「相似」（如 search 词序不同）而非「相同」，文案随之纠正。

#### 4.3.5 边界与风险

- **两条 nudge 同时触发**：动作重复 ≥5 且页面停滞 ≥5 时，两条都注入（`\n\n` 拼）。这是有意的——两个独立信号叠加增强 LLM 警觉；browser-use 同行为（`views.py:211-248`）。
- **min-3 守卫与停滞维度交互**：若 `recent_actions < 3` 但 `consecutive_stagnant_pages >= 5`（极端：前几步全是 wait/豁免，但页面停滞），守卫条件已用 `and` 放宽（见上），停滞仍可触发。

---

### 4.4 P1-4：窗口大小 15→20

#### 4.4.1 现状 / 影响

`__init__(window_size=15)`（`loop_detector.py:16`），browser-use `window=20`。窗口短 → 慢循环（间隔 >15 步的重复）滑出窗口漏报。

#### 4.4.2 方案

`__init__(window_size: int = 20)`，默认值对齐 browser-use。模块重写时顺带，零额外成本。`agent.py:83` `ActionLoopDetector()` 无参调用自动取新默认。

#### 4.4.3 边界

仅改默认值，不强制 20——保留构造参数可注入（为 P2-1 可配置预留）。

---

## 5. P2：差异 / 暂缓（记录取舍，本期不落地）

### 5.1 P2-1：`loop_detection_enabled` 开关 + `loop_detection_window` 可配置 — 暂缓

**分歧**：browser-use `AgentSettings` 暴露 `loop_detection_enabled: bool = True`（`views.py:91`）+ `loop_detection_window: int = 20`（`views.py:90`），`_inject_loop_detection_nudge` / `_update_loop_detector_actions` / `_update_loop_detector_page_state` 三处入口均 `if not self.settings.loop_detection_enabled: return` 短路（`service.py:1486/1498/1517`）。TreeWalker 无此开关，循环检测恒开。

**取舍 / 暂缓理由**：
- 引入需联动 `config.py`（新增 `loop_detection_enabled` + `loop_detection_window` 字段 + 对应 env，仿 `config.py:76/80` 的 `max_failures`/`reconnect_timeout` 模式）+ `agent.py:83` 实例化读 config + `step.py` 三处调用点（189/209/954-958）加 guard。
- 当前无「需关闭循环检测」的明确需求（nudge 是软提示，不阻塞，恒开无害）；可配置窗口的边际价值低（P1-4 已对齐默认 20）。
- **下一步**：若线上出现「nudge 干扰特定任务（如合法的批量重复操作被频繁提醒）」需求，再引入开关（含 config + env + 3 调用点 guard + 单测）。

---

## 6. P3：架构差异 / 不适用 / 已对齐核查（仅文档说明，不动代码）

### 6.1 P3-1：nudge 注入机制（`[System Notice]` 内联 vs `_add_context_message`）— 架构差异，不改

**分歧**：browser-use `_inject_loop_detection_nudge`（`service.py:1484-1494`）把 nudge 作为独立 `UserMessage` 经 `_message_manager._add_context_message` 注入消息历史；TreeWalker 把 nudge 拼进 `build_state_message` 的 `[System Notice]` 段（`prompts/system_prompt.py:221-224`），随每步 user message 内联。

**不改理由**：
- 这是**消息管理架构差异**：browser-use 有独立 `MessageManager`（管理 context 消息的生命周期/裁剪），TreeWalker 用 `build_state_message` 统一拼装。两者都能让 LLM 在下一轮看到 nudge，**功能等价**。
- 01 文档（`01-准备上下文对齐browser-use方案.md`）已标注 TreeWalker 的 nudge「✅（但累积）」——即 nudge 每步重新计算拼装，不跨步累积成独立消息。这是已知整合层差异，**超 loop_detector 模块范围**。
- 改注入机制需重构 `MessageManager`，与 05（终结化）/01（准备上下文）的 message 管理强耦合，不在本模块对齐范围。

### 6.2 P3-2：注入日志 — 可选增强（P1 落地时顺带）

browser-use 注入 nudge 时 `logger.info('🔁 Loop detection nudge injected (repetition=..., stagnation=...)')`（`service.py:1488-1490`）。TreeWalker `step.py:209` 取 nudge 后无此日志。**建议 P1 落地时在 `step.py` 取用 nudge 后顺带加一行 `logger.debug`**（DEBUG 级，避免噪音），便于线上观测循环检测触发频率。成本低，不单列优先级。

### 6.3 ✅ 已对齐核查：`_LOOP_EXEMPT_ACTIONS`

| 维度 | browser-use | TreeWalker | 状态 |
|---|---|---|---|
| 豁免集合 | `{'wait', 'done', 'go_back'}`（`service.py:1504`） | `frozenset({"wait", "done", "go_back"})`（`step.py:1290`） | ✅ 对齐 |
| 豁免理由 | wait 恒同哈希（瞬即误报）/ done 终态 / go_back 导航恢复 | 同（`step.py:1289` 注释） | ✅ 对齐 |
| 应用位置 | `_update_loop_detector_actions` 循环内（`service.py:1508-1510`） | `step.py:957` 循环内 `if action_name not in _LOOP_EXEMPT_ACTIONS` | ✅ 对齐 |

**结论**：豁免集合已完全对齐，**不是缺口**。P1-1 归一化改造不影响豁免逻辑（豁免在调用点 `step.py:957` 先于 `record_action` 判定）。

---

## 7. 测试策略（落地时）

本期**纯文档，无代码改动 → 无新增测试**。未来落地 P1 时需扩展 `tests/test_loop_detector.py`（当前仅 6 用例）：

| P1 项 | 操作 | 用例 |
|---|---|---|
| **P1-1** | 新增 `_normalize_action_for_hash` / `_compute_action_hash` 纯函数测试 | (1) search 词序无关：`search(query="a b")` ≡ `search(query="b a")` ≡ `search(query="a, b")`；(2) search 大小写/标点无关；(3) click 同 index 同哈希；click `index` vs `element_id` 不同；（4）**input_text 不同 text 不同哈希**（回归当前剥 text 的 bug）；(5) **scroll 同 direction 不同 amount 同哈希**（回归当前按 amount 切分的 bug）；(6) navigate 同 url 不同 new_tab 同哈希；(7) 默认分支参数排序无关；(8) 哈希定长 12 字符 |
| **P1-2** | 新增 `PageFingerprint` + `record_page_state` 测试 | (1) 三维相同→停滞 +1；(2) url 同 dom 变→停滞归零；(3) dom 同 url 变→停滞归零；(4) element_count 变→停滞归零；(5) dom_state=None 不崩；(6) 指纹队列 maxlen=5 |
| **P1-3** | 扩展 `get_nudge_message` 测试 | (1) 动作重复 5/8/12 三档文案含实际计数 +「similar」+「in the last N actions」；(2) 实际计数 9 显示「9」非「8+」；(3) 页面停滞 ≥5 触发停滞文案；(4) 两维度同时触发→两条 `\n\n` 拼；(5) min-3 守卫保留 |
| **P1-4** | 窗口测试 | window=20 默认；window 滑出后旧动作不计数 |
| 调用点 | `tests/test_step_*.py` 扩展 | `step.py:189` 传三维（mock `browser_state.dom_state`）；`record_page` 已删→确认无引用残留 |
| 回归 | 全量 | `uv run python -m pytest tests/ -x -v` 全过，覆盖率 >85%（CLAUDE.md 要求） |

**与既有测试的关系**：`test_loop_detector.py` 现有 6 用例（阈值 + min-3）需随 P1-3 文案改动更新断言（`"5+"`→实际计数 / `"CRITICAL"`→`"Heads up"`）。`test_step_error_handling.py`（06 的异常计数）与循环检测是**两通道**，互不影响。

---

## 8. 暂缓 / 剔除项与理由

| 项 | 决定 | 理由 |
|---|---|---|
| P1-1 按动作类型语义归一化 | ✅ **建议落地** | 核心缺口；纠正 input_text 过度合并 + scroll 过度切分两处 bug |
| P1-2 三维页面状态指纹 | ✅ **建议落地** | 页面停滞维度完全缺失；调用点三维数据可达 |
| P1-3 双维度 nudge + 文案 | ✅ **建议落地** | P1-2 的消费端；文案对齐（实际计数 + similar） |
| P1-4 窗口 15→20 | ✅ **建议落地** | 零成本顺带；对齐 browser-use 默认 |
| P2-1 `loop_detection_enabled` + 可配置窗口 | ⏸ 暂缓 | 需 config.py + env + 3 调用点 guard；当前无关闭需求 |
| P3-1 nudge 注入机制 | 📄 N/A | `[System Notice]` 内联 vs `_add_context_message`；架构差异，超模块范围（同 01「但累积」结论） |
| P3-2 注入日志 | 📄 可选 | P1 落地时顺带加 `logger.debug`，不单列 |
| ✅ `_LOOP_EXEMPT_ACTIONS` | ✅ 已对齐 | wait/done/go_back 与 browser-use 一致（`step.py:1290`），核查项非缺口 |

---

## 9. 实施路线图与本期范围

| 优先级 | 项 | 本期 | 复杂度 | 价值 | 依赖 |
|---|---|---|---|---|---|
| **P1-1** | 按动作类型语义归一化 | ✅ 落地 | 中 | 高（修正漏报 + 误报） | 无 |
| **P1-2** | 三维页面状态指纹 + 调用点 | ✅ 落地 | 中 | 高（补唯一兜底维度） | 无 |
| **P1-3** | 双维度 nudge + 文案 | ✅ 落地 | 低 | 中-高（消费 P1-2 + 文案） | P1-1/P1-2 |
| **P1-4** | 窗口 15→20 | ✅ 落地 | 极低 | 低 | 无 |
| P2-1 | 可配置开关 + 窗口 | ⏸ 暂缓 | 中 | 低 | 线上「nudge 干扰」需求 |
| P3-1 | nudge 注入机制 | 📄 N/A | 高 | 低 | 重构 MessageManager |
| P3-2 | 注入日志 | 📄 可选 | 极低 | 低 | 无 |

> **本期建议落地 P1-1/2/3/4，P2/P3 暂缓。无 P0**（骨架 + 调用点已对齐）。本期为**纯方案文档**，代码落地为后续独立任务。

**后续触发条件**：
1. P1 落地任务启动 → 按 §4 方案 + §7 测试执行，全量回归 `uv run python -m pytest tests/ -x -v`。
2. 线上出现「合法批量重复操作被 nudge 频繁干扰」→ 触发 P2-1 评估（引入开关 + 可配置窗口）。
3. 线上出现「DOM 噪声大→停滞维度漏报」→ 评估指纹剔除动态区域（本文未展开）。

---

## 附：落地核对清单（P1 触发时）

- [ ] `loop_detector.py` 新增 `_normalize_action_for_hash(name, params)` + `_compute_action_hash(name, params)`，覆盖 search / click / input_text / navigate / scroll / 默认 六分支（**TreeWalker 动作词表**，不照搬 browser-use 名字）；`input_text` 保留 text、`scroll` 去 amount
- [ ] `loop_detector.py` 新增 `@dataclass(frozen=True) PageFingerprint`（url + element_count + text_hash）+ `from_state`
- [ ] `loop_detector.py` 新增 `record_page_state(url, dom_text, element_count)` + `consecutive_stagnant_pages` 计数 + `recent_page_fingerprints: deque(maxlen=5)`；**删除** `record_page` + `recent_urls`（死代码）
- [ ] `loop_detector.py` 抽 `_update_repetition_stats`；`record_action` 末尾调之；`get_nudge_message` 改双维度（动作 5/8/12 + 停滞 5）+ 实际计数 +「similar」+ 窗口上下文文案；保留 min-3 守卫
- [ ] `loop_detector.py` `__init__(window_size=20)`（P1-4）
- [ ] `step.py:188-189` 调用点改造：`record_page` → `record_page_state(url, dom.element_tree_text, len(dom.selector_map))`，`dom` 为 None 兜底
- [ ] `step.py:209` 取 nudge 后顺带加 `logger.debug`（P3-2，可选）
- [ ] `tests/test_loop_detector.py` 扩展 P1-1/2/3/4 用例（见 §7）；更新现有 6 用例的文案断言（`"5+"`→实际计数 / `"CRITICAL"`→`"Heads up"`）
- [ ] 回归：`uv run python -m pytest tests/ -x -v` 全过，覆盖率 >85%
- [ ] **缩进**：`loop_detector.py` / `test_loop_detector.py` 保持现有 **4 空格缩进**（CLAUDE.md 要求 tab，但此二文件历史为 4 空格——以现有文件为准，否则 Edit 失败）
- [ ] 代码注释如实说明「DOM 噪声可能致停滞漏报」「click 的 element_id 抖动致漏报」两项已知限制

---

*本文档基于 TreeWalker 当前 master 分支（`loop_detector.py` 全文 53 行、`step.py:189/209/954-958/1290`、`agent.py:13/83`、`prompts/system_prompt.py:222-224`、`browser/views.py:694-710/789-798` `SerializedDOMState`/`BrowserStateSummary`、动作词表 `tools/models.py`）与 browser-use `PageFingerprint`（`views.py:95-107`）、`_normalize_action_for_hash`（`110-148`）、`compute_action_hash`（`151-154`）、`ActionLoopDetector`（`157-248`）、`AgentSettings.loop_detection_*`（`90-91`）、`_inject_loop_detection_nudge`/`_update_loop_detector_actions`/`_update_loop_detector_page_state`（`service.py:1484-1528`）的逐行对比，并核对 `learn_agent/browse-use/docs/agent-core/类的职责/ActionLoopDetector类职责.md`。其中 browser-use 行号为子代理核对结果，落地实施时须以最新代码为准复核。本文档承接 04 §7/§9 暂缓项，与 01（nudge「但累积」→本文 P3-1 不冲突）/ 04（后处理失败计数，与循环检测是两通道）/ 06（异常计数，与循环检测是两通道）结论一致。*
