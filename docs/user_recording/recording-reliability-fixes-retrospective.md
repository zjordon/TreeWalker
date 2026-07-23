# 录制器可靠性修复总结（issue #136 + 末步录不全竞态）

> 2026-07-23，分支 `fix/136-cover-switch-click`。本次 e2e 调试中逐层暴露并解决了三个独立但
> 相关的录制/重放可靠性问题：
>
> 1. **抖音封面切换 click 回放点错**（#136 主体）——指纹撞车 + 文字没进指纹。
> 2. **末步偶发录不全**——`/event` 与 `/stop` 并发竞态（**非异常**，故历次"扩大异常捕获"均无效）。
> 3. **文字归一化对不齐**——抖音每字独立 span，`get_all_children_text` 与扩展 `textContent` 取文字方式不一致。
>
> 配套：issue #135（"录制端去指纹化、定位全推迟到重放"的整体重构）经评估不可行已关闭 NOT_PLANNED；
> 本次走的是**窄而准**的路线——只在指纹撞车处补一个 text 维度，不动整体架构。

---

## 一、问题 1：封面切换 click 回放点错（#136）

### 现象

重放用户录制的抖音上传流程（`douyin_redesign11/12.json`），从横封面切竖封面的 click（设置竖封面）
回放点错——点到"设置横封面"，横→竖切换失败，竖封面上传落到错的区。

### 关键事实：封面编辑器有**两处**切换横/竖封面

`rerun-history/conver-dialog.html`：

- **顶部 step tab**（line 12-13）：`<div class="step-dXVbPX [step-active]"><span>设置横/竖封面</span></div>`
- **底部 toggle 按钮**（line 1400-1402）：`<button class="semi-button ...primary-RstHX_"><span>设置竖封面</span></button>`

用户录制时点的是**顶部 tab**（DIV），agent 录制点的是**底部按钮**（BUTTON，有 ax_name）——两者不同元素，
但都能切换。问题出在顶部 tab 这条路。

### 根因（三层指纹撞车）

录到的设置竖封面 tab：`node_name=DIV`、`class=step-dXVbPX step-active-AWDV7U`（**点后状态**）、
**`ax_name=null`**。两个 tab 指纹为什么分不开（`src/tree_walker/browser/views.py`）：

1. **ax_name=null**：普通 `<div>`（无 role/aria-label），文字在子 `<span>`，CDP 可访问性树不给 generic div
   算 accessible name（`load_from_enhanced_dom_tree`）。→ 唯一稳定区分信号（文字）没进指纹。
2. **element_hash 用原始 class**（`__hash__` 不过滤）：含会翻转的 `step-active`。录制是点击**后**抓的，
   `step-active` 在设置竖封面这边；重放到这步时（横封面激活）`step-active` 在**设置横封面**那边 →
   **EXACT 级精准匹配到设置横封面**（`rerun._match_element_index`），且 `len(matches)==1` 直接返回，
   走不到 `_nearest_idx` 的 xpath tie-break。
3. **stable_hash 过滤后相同**：`filter_dynamic_classes` 把含 `active` 的类 strip（`views.py`），两 tab
   都剩 `step-dXVbPX` → stable_hash 完全相同。`_get_parent_branch_path` 只取 tag 不取下标，path 也相同。

**反证**：agent 点的底部 `<button>` 设置竖封面，`<button>` 的 accessible name = 其文字 →
ax_name="设置竖封面" → 指纹唯一 → 重放正确。证明"文字进指纹就能区分"。

### 修复：用扩展捕获的文字做主身份

核心 reframing：录制的时序劣势在**重放端不存在**——重放是主动"先看再做"，到这步时元素还在、页面稳定。
而扩展在点击瞬间已握住 `textContent`（ground truth），只是之前没送后端。

- **扩展**（`recording_extension/`）：`buildElementRef` 早就算好了 `ref.text`（`capture/selector.ts`），
  但 click emit 把它丢了。修复：`action-recorder.ts` 的 onClick/onSelect emit 加 `text: ref.text`；
  `shared/types.ts` 的 `RecorderEvent` 加 `text?` 字段。
- **录制端**（`recorder.py`）：`handle_event` 指纹路径给 `interacted_element[0]` 注入 `event["text"]`；
  `_store_semantic_clue` 的 base 也带 text。**text 为主、ax_name 兜底**。
- **重放端**：`rerun.py` `MatchLevel` 加 `TEXT=0`，`_match_element_index` 顶部加 TEXT 级（`node_name +
  get_all_children_text` 归一化匹配），**优先于 EXACT**；`locator.py` `locate_by_ref` 顶部同样加 TEXT 级。

详见 `semantic-clue-replay.md` 的"录制失败 ≠ 重放必败"思路（本次是把"文字"也作为重放端可用的线索）。

---

## 二、问题 2：末步偶发录不全（并发竞态）

### 现象

`douyin_redesign12.json` step20：`click {}`（params 空、无 index）+ `interacted_element: null` +
**`url/title` 全空**。重放最后这步失败。**偶发**——有时录到有时录不到。之前修过好几次，每次都是"增加捕获异常的范围"。

### 根因：aiohttp 并发，**不是异常**

`recorder/server.py` 用 aiohttp，`/event`（→`handle_event`）和 `/stop`（→`stop`）**并发处理、互不等待**。
而 `handle_event` 一进来就由 `translate_event` 把 action append 进 `recording.actions`，**然后**才做慢操作
（`get_state` + `_LOCATE_RETRY_DELAYS` 重试 sleep，可达数秒）。若用户在最后一个事件处理中途点停止，
`/stop` 并发跑 `flatten` 落盘——落的是**半成品 action**：interacted 还是默认 None、page_url 还没赋（那行在
方法末尾）、index 没填。偶发取决于 handle_event 是否抢在 stop 前 跑完。

### 为什么"扩大异常捕获"反复无效

**根本没有异常。** 三重兜底（issue #129）保证 click **只要走完**就一定是 `interacted=[语义线索]`、
绝不可能是 null。出现 null = handle_event **没走完就被落盘了**。所以历次把 try/except 越扩越大（抓
BaseException、加末尾保底）都治不了——方向错了。`url/title` 全空也是同一证据（`page_url` 在方法末尾才赋）。

### 修复：`asyncio.Lock` 串行化状态变更

`Recorder` 加 `self._lock = asyncio.Lock()`，把四个改共享状态（`recording.actions` / `history`）的方法串行：

- `handle_event` 拆成**加锁 wrapper** + `_handle_event_impl`（实际逻辑）。
- `stop` 先置 `_recording=False`，再 `asyncio.wait_for(lock.acquire(), timeout=_STOP_LOCK_TIMEOUT_S=15s)`
  **等 in-flight 事件跑完**才 flatten/落盘；15s 超时兜底（`get_state` 真卡死时不让 stop 永久挂起，强制落盘）。
- `start` / `attach_signal` 也加锁。`attach_signal` 因此改 async（`server.py` 与测试加 `await`）。

**How to apply**（已存 memory `recorder-stop-event-concurrency-race`）：以后录制器再出 flaky/偶发问题，
**先查并发**（/event、/stop、/signal 是否并发改共享状态），别急着扩异常捕获。

---

## 三、问题 3：文字归一化对不齐（每字独立 span）

### 现象

问题 1 的 TEXT 级对设置竖封面 tab **没命中**（掉到 EXACT）。加诊断日志后看到 live 节点文字是
`"设 置 竖 封 面"`（每字之间有空格），而录制存的 text 是 `"设置竖封面"`（无空格）。

### 根因：取文字方式不一致

抖音封面 tab 把**每个字拆成独立 `<span>`**（排版需要）：

- **扩展** `element.textContent`：拼接后代文本节点**不加分隔符** → `"设置竖封面"`
- **后端** `EnhancedDOMTreeNode.get_all_children_text`：用 `\n` 连接文本节点 → `"设\n置\n竖\n封\n面"`
  → 原 `normalize_text` 只折叠空白 → `"设 置 竖 封 面"` ≠ `"设置竖封面"`

### 修复：`normalize_text` 移除**全部**空白

`locator.normalize_text` 从"折叠空白"改成 `re.sub(r"\s+", "", ...)`。这样 `"设 置 竖 封 面"` →
`"设置竖封面"` == 录制值，TEXT 命中正确的设置竖封面 tab（DIV，`node_name` 约束排除底部按钮）。
UI 文字标签几乎不会仅靠空白区分，strip 全部空白安全。

诊断日志（`[TEXT诊断]`）定位后已移除。

---

## 四、关键技术决策

### text 优先、ax_name 兜底（而非反过来）

录制侧身份用**扩展 text 为主、后端 ax_name 兜底**（text 为空时才用 ax_name）。理由：后端 `get_state`
在动作**之后**跑，对状态依赖元素（如底部 toggle 按钮，同一按钮横/竖态读不同文字）会拿到动作后的错误名称；
扩展 text 是点击**瞬间**的真值。这与整个录制架构方向一致——扩展捕获事件瞬间真相，后端 `get_state` 才是
不可靠那一环。

### TEXT 级放在 EXACT **之前**

因为 bug 正是 EXACT 唯一匹配到错的"设置横封面"（`step-active` 时序），`len(matches)==1` 直接返回、
绕过 `_nearest_idx` 的 xpath tie-break。TEXT 必须更早才能救回。TEXT 要求 `node_name` 匹配，所以顶部
tab（DIV）不会误匹配底部按钮（BUTTON）。

### 向后兼容

agent 自录 / 旧扩展录制的 `interacted_element` **没有 text 字段** → TEXT 级 `if h_text:` 为假直接跳过，
走原 EXACT/STABLE/... 六级，行为**逐字节不变**（有专门测试 `test_match_text_skipped_when_absent` 守住）。

---

## 五、改动清单（9 文件）

**扩展**：`recording_extension/capture/action-recorder.ts`（click/select emit text）、
`shared/types.ts`（RecorderEvent.text）。

**后端**：
- `src/tree_walker/agent/rerun.py`：`MatchLevel.TEXT=0` + `_match_element_index` TEXT 级；import `normalize_text`。
- `src/tree_walker/recorder/locator.py`：`normalize_text`（strip 全部空白）+ `locate_by_ref` TEXT 级。
- `src/tree_walker/recorder/recorder.py`：interacted_element 注入 text；**`asyncio.Lock` 串行化**
  start/handle_event/stop/attach_signal；`handle_event` 拆 wrapper + impl；stop 带 15s 超时等锁；
  `_STOP_LOCK_TIMEOUT_S`；attach_signal 改 async。
- `src/tree_walker/recorder/server.py`：`/signal` 加 `await`。

**测试**：`tests/test_recorder.py`、`tests/test_recorder_locator.py`、`tests/test_rerun_history.py`
（normalize_text、TEXT 级命中/兜底/per-char-span/无 text 兼容、text 注入、语义线索带 text、
**stop 等待 in-flight 事件的竞态回归测试**）。

---

## 六、验证

- **2055 测试全过**（+8 新测试），recorder 包覆盖率 89%（>85%）。
- 单元层面三个机制均已覆盖：TEXT 级覆盖指纹撞车、normalize 覆盖 per-char span、锁覆盖 stop 等待。
- **e2e 待用户验证**：重录抖音上传流程 → 确认①设置竖封面 click 命中 TEXT（非 EXACT）点对 tab；
  ②录制结尾步骤完整（不再出现 `click {} + null`）。

---

## 七、教训

- **"偶发 + 历次靠扩大异常捕获修"= 大概率不是异常**。先找时序/并发根因。`click {} + null + 空 url`
  这种"全空"特征 = action 没处理完就被落盘，指向竞态而非抛异常。
- **指纹方案对 generic div（无 role/aria、文字在子节点）天然区分力弱**：ax_name=null + path 不含下标 +
  类被过滤 → 同款兄弟元素撞车。文字是这类元素唯一稳定区分信号，要单独送、单独匹配。
- **扩展 `textContent` 与后端 `get_all_children_text` 取文字方式不一致**（前者不加分隔符，后者加 `\n`），
  遇到每字独立 span 的排版会失配——比对前要 strip 全部空白。
- **诊断日志要敢加敢删**：不确定根因时加一条 INFO 诊断（打印同 tag 节点文字样本），定位后立刻移除。
