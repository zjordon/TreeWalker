# 02-阶段2：等目标元素 + actionability 阶段一

> **关联**：#125（本期实现 issue）｜ #123（等待机制完善总览）｜ 前置 #124（阶段1，已落地）｜ 后续 #126（阶段3 networkidle）/ #127（阶段4）
> **状态**：待实施
> **体裁**：带代码的实施计划（基于现状代码，含"现状 → 改后"代码块、逐文件改动清单、测试与验证）
> **对照源**：已逐行核验真实代码（2026-07-19，master @ `a5953ae`）
> **范围**：缺口 5（`wait_for_elements` 从"数数量"升级为"等目标元素匹配"）+ actionability 阶段一（visible + enabled，默认关 + 超时降级）。两者同属重放编排层、语义正交、可独立开关、可并行推进。
> **总原则**：复用现有匹配逻辑（`_match_element_index` / `locate_by_ref`），不新造轮子；新开关默认关 → 零行为变更；超时永远降级，永不引入新失败。

---

## Context（为什么做）

阶段 1（#124）让重放获得了"步间延迟可配置 + readyState 等待"。但 `readyState` 在 SPA 常年 `complete`（SPA 导航不改 readyState），对 SPA 时序收益小（阶段1 文档"边界与风险"已明示）。**SPA 场景的真正解药是阶段 2 的两件事**：

1. **`wait_for_elements` 只数数量，不验目标**：`_wait_for_minimum_elements`（`rerun.py:917-929`）只看 `len(selector_map) >= min_elements`。selector_map 塞了 100 个元素 ≠ 本步要点的那个按钮已在页面上。用户开 `rerun_wait_for_elements=True` 等到的可能仍是空等。
2. **没有 actionability 检查**：动作定位到 index → 立刻执行（`rerun.py:533`）。若元素已挂 DOM 但还没 visible（CSS transition 中、骨架屏未替换）或 `disabled`，click/input 会失败。

参考 `knowledge-garden/ai/agent/browser-wait-and-timing.md`：Playwright actionability 五级检查（Attached/Visible/Stable/Receives events/Enabled），重试直到通过或超时（§1）；所有条件等待的本质是"乐观重试 + 悲观超时"——`deadline + poll + 早退 + 降级`（§8），`_wait_for_minimum_elements` 已是此模式。本期把"数数量"条件升级为"等目标元素定位成功"（§5 方案 B），并加 actionability 轻量版（§5 方案 C 的 visible+enabled 子集，默认关）。

---

## 现状精确锚点（已核验真实代码）

### 锚点 A：`wait_for_elements` 现状（"数数量"） `rerun.py:465-468 / 903-929`

```python
# rerun.py:465-468（_execute_history_step 开头第三段等待）
if wait_for_elements:
    min_elements = self._count_expected_elements(item)        # 取本步所有 action 的最大 index+1
    if min_elements > 0:
        state = await self._wait_for_minimum_elements(state, min_elements, timeout=15.0)
```

```python
# rerun.py:917-929（唯一的"轮询+超时降级"模板，阶段2 两个新等待复用此骨架）
async def _wait_for_minimum_elements(self, state, min_elements, timeout=15.0, poll=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if state and state.dom_state and len(state.dom_state.selector_map) >= min_elements:
            return state
        await asyncio.sleep(poll)
        try:
            state = await self.browser.get_state(include_screenshot=False)
        except Exception:
            pass
    return state                                   # 超时降级，不抛错
```

### 锚点 B：动作循环内定位路径（3 条） `rerun.py:484-533`

每步多 action 逐个定位，**定位成功后立即 `_exec_one`，无 actionability 检查**：

```python
# rerun.py:484-530（精简）
if hist_elem and hist_elem.get("_semantic_clue"):                       # 路径1 语义线索 → locate_by_ref
    matched = locate_by_ref(hist_elem, selector_map)                    # 返回 (idx, node) | None
    if matched is not None: params["index"] = matched[0]
    else: raise ValueError(...)
elif hist_elem and has_index:                                           # 路径2 指纹 → _match_element_index 六级
    updated = self._update_action_indices(hist_elem, action, selector_map)
    ...
else:                                                                   # 路径3 upload_file accept 兜底
    ...
# rerun.py:533
result = await self._exec_one(name, params, state)                      # ← 定位后直接执行，无 visible/enabled 检查
```

**关键事实**：动作循环只把 `index` 写进 `params`，**不保留 node 对象**。actionability 要查 `node.is_visible` 必须重取 `selector_map[idx]`，或在等待循环里重定位时一并拿到 node。

### 锚点 C：两条定位原语签名不对称

| 原语 | 位置 | 输入 | 输出 |
|---|---|---|---|
| `_match_element_index(hist, selector_map)` | `rerun.py:607-682` | 指纹 dict | `tuple[int, MatchLevel] \| None`（无 node） |
| `locate_by_ref(ref, selector_map)` | `recorder/locator.py:104-141` | 语义线索 dict | `tuple[int, Node] \| None`（带 node） |

→ 抽 `_locate_target` 统一为"能否定位 + 返回 `(idx, node)`"。

### 锚点 D：actionability 数据来源（数据免费，已抓）

- **`node.is_visible`**：`bool | None` 字段（`browser/views.py:271`），`browser/dom.py:990` 由 `_is_element_visible_according_to_all_parents`（`dom.py:721-796`，CSS `display:none`/`visibility:hidden`/`opacity<=0` + 视口交叉）填。走完 `build_dom_state` 的节点必有 bool 值。
- **`disabled`**（非字段，两来源）：AX——遍历 `node.ax_node.properties`（`name=='disabled'` 且 value 真，`dom.py:146`）；HTML——`'disabled' in (node.attributes or {})`。
- **file input 隐藏陷阱**（`browser/serializer.py:264-271`）：`INPUT[type=file]` 因 `opacity:0`/`display:none`/1×1 → `node.is_visible=False`；序列化器仅对 **LLM 简化树**强制可见，**不改 `node.is_visible`**。→ actionability 必须对 upload_file 白名单排除 + `INPUT[type=file]` 防御短路双保险。

### 锚点 E：配置链路 5 处（阶段1 范本，`rerun_wait_for_page_settle`）

`config.py:129`（AgentSettings 字段）→ `config.py:347`（env 注入）→ `agent.py:83`（`__init__` 拆字段）→ `rerun.py:202`（RerunMixin 注解）→ `rerun.py:287`（None 哨兵归一化）；沿 `_rerun_step_with_retries` / `_execute_history_step` 管线回传。`rerun_actionability_check` 照搬同构。**当前仓库无任何 actionability 字段**（全仓库 grep `actionability` 仅命中一处文档）。

---

## 方案

### 共用基础设施 1：`_wait_until` 通用轮询原语

抽 deadline+poll+降级骨架，缺口 5 / actionability / 既有 `_wait_for_minimum_elements` 共用；语义独立的开关不合并（避免耦合）。

```python
# rerun.py 新增（紧邻 _wait_for_minimum_elements，L917 前）
async def _wait_until(self, state, predicate, timeout, poll=1.0, refresh=True):
    """通用轮询：谓词命中即返回；超时降级返回当前 state（不抛错）。
    复用 _wait_for_minimum_elements 骨架；predicate(state)->bool，每轮先判再 sleep。"""
    deadline = time.time() + timeout
    while True:
        if predicate(state):
            return state
        if time.time() >= deadline:
            return state
        await asyncio.sleep(poll)
        if refresh:
            try:
                state = await self.browser.get_state(include_screenshot=False)
            except Exception:
                pass

# _wait_for_minimum_elements 改为薄封装（签名/返回不变，L468 调用点不破）
async def _wait_for_minimum_elements(self, state, min_elements, timeout=15.0, poll=1.0):
    return await self._wait_until(
        state,
        lambda s: bool(s and s.dom_state and len(s.dom_state.selector_map) >= min_elements),
        timeout, poll,
    )
```

### 共用基础设施 2：`_locate_target` 统一只读谓词

统一语义线索/指纹两路径的"能否在当前 selector_map 定位目标 + 返回 (idx, node)"。**只读谓词，不参与动作循环的写回**（`_update_action_indices` 的深拷贝写回不动，避免回归）。

```python
# rerun.py 新增（紧邻 _match_element_index 后）
def _locate_target(self, hist_elem, selector_map):
    """只读判定：能否定位目标，返回 (idx, node) | None。
    语义线索→locate_by_ref（已返回 idx+node）；指纹→_match_element_index 得 idx 再取 node。
    hist_elem 为 None（extract/wait/navigate/无指纹 upload_file）→ None。"""
    if not hist_elem:
        return None
    if hist_elem.get("_semantic_clue"):
        return locate_by_ref(hist_elem, selector_map) or None
    match = self._match_element_index(hist_elem, selector_map)
    if match is None:
        return None
    node = selector_map.get(match[0])
    return (match[0], node) if node is not None else None
```

### 缺口 5：等目标元素（替换"数数量"）

**新增 `_wait_for_target_elements` + `_collect_target_hists`**，保留 `_wait_for_minimum_elements` 为薄封装（语义清晰 + 老测试不破）。`rerun.py:465-468` 改调用新函数。

```python
# rerun.py 新增
def _collect_target_hists(self, item):
    """枚举本步所有需定位 action 的 hist_elem（剔除 upload_file / 无指纹 action）。"""
    actions = item.model_output.get("actions") or [item.model_output.get("action", {})]
    interacted = item.interacted_element or []
    out = []
    for i, action in enumerate(actions):
        if not isinstance(action, dict) or action.get("name") == "upload_file":
            continue                                   # accept 路径兜底，无预判价值
        h = interacted[i] if i < len(interacted) else None
        if h and (h.get("_semantic_clue") or h.get("element_hash") or h.get("stable_hash")
                  or h.get("x_path") or h.get("ax_name") or h.get("attributes")):
            out.append(h)
    return out

async def _wait_for_target_elements(self, state, item, timeout=15.0, poll=1.0):
    """等本步所有需定位 action 的目标在 selector_map 能定位（all-or-nothing）。超时降级。"""
    targets = self._collect_target_hists(item)
    if not targets:
        return state                                   # 本步无目标（纯 extract/wait）→ 不等
    def _all_located(s):
        sm = s.dom_state.selector_map if s and s.dom_state else {}
        return all(self._locate_target(h, sm) is not None for h in targets)
    return await self._wait_until(state, _all_located, timeout, poll)
```

```python
# rerun.py:465-468（改后）
if wait_for_elements:
    # 语义升级（阶段2）：从"数数量"改为"等目标元素匹配成功"。默认关=零行为变更。
    state = await self._wait_for_target_elements(state, item, timeout=15.0)
```

> **语义变更说明**：`rerun_wait_for_elements` 含义从"等 selector_map 元素总数 ≥ N"升级为"等本步所有需定位 action 的目标元素能定位到"。默认 `False` → 存量用户零影响；开启者得到更准确的等待。
> **all-or-nothing**：单步多 action 缺一即失败，与其半步执行后失败不如多等；代价是最慢目标决定整步等待（可接受，多 action 步少数且通常相关）。

### actionability 阶段一：visible + enabled（默认关 + 超时降级）

**插入点**：动作循环内、定位成功（`params["index"]` 已写）后、`_exec_one`（`rerun.py:533`）前。**乐观短路**：第一帧已 actionable 就跳过轮询（多数情况零额外等待）。

```python
# rerun.py 模块级辅助（纯函数，便于单测）
_ACTIONABILITY_ACTIONS = frozenset({"click", "input_text", "select_dropdown"})

def _is_file_input(node):          # 防御短路：仿 serializer.py:264-271
    return ((getattr(node, "node_name", "") or "").upper() == "INPUT"
            and (getattr(node, "attributes", None) or {}).get("type", "").lower() == "file")

def _is_actionable(node):          # visible + enabled；None 字段保守放过（不引入新失败）
    if getattr(node, "is_visible", None) is False:     # 明确不可见→阻断；None→放过
        return False
    ax = getattr(node, "ax_node", None)
    if ax:
        for p in (ax.properties or []):
            if getattr(p, "name", "") == "disabled" and p.value:
                return False
    if "disabled" in (getattr(node, "attributes", None) or {}):
        return False
    return True
```

```python
# rerun.py:531 后插入（动作循环内，每个 action）
if (rerun_actionability_check                        # 配置开关
        and name in _ACTIONABILITY_ACTIONS           # 白名单
        and hist_elem):                              # 需指纹用于 poll 期间重解析漂移 index
    node_now = selector_map.get(params.get("index"))
    if node_now is not None and not _is_file_input(node_now) and not _is_actionable(node_now):
        state, fresh_idx, _ = await self._wait_for_actionability(
            state, hist_elem, params["index"],
            timeout=self.rerun_actionability_timeout, poll=self.rerun_actionability_poll)
        if fresh_idx is not None and fresh_idx != params.get("index"):
            logger.info("actionability 等待后 index 漂移 %s→%s（%s）", params.get("index"), fresh_idx, name)
            params["index"] = fresh_idx              # poll 刷新 state 致 index 漂移，用最新值
```

```python
# rerun.py 新增（紧邻 _wait_until）
async def _wait_for_actionability(self, state, hist_elem, initial_idx, timeout, poll):
    """轮询直到目标 visible+enabled；每轮用 hist_elem 重 _locate_target 拿最新 (idx,node)
    （poll 刷新 state 后 index 漂移）。超时降级返回最新 (state, idx, node)，不抛错。"""
    fresh = [
        initial_idx,
        state.dom_state.selector_map.get(initial_idx) if state and state.dom_state else None,
    ]
    def _ok(s):
        sm = s.dom_state.selector_map if s and s.dom_state else {}
        loc = self._locate_target(hist_elem, sm)
        if loc is None:
            return False                             # 目标暂时丢失→继续等
        fresh[0], fresh[1] = loc
        return _is_actionable(fresh[1])
    state = await self._wait_until(state, _ok, timeout, poll)
    return state, fresh[0], fresh[1]
```

**关键点**：①乐观短路避免每步每 action 都 poll；②`is_visible=None` 保守放过（永不引入新失败）；③`hist_elem=None` 跳过（无法重解析漂移 index；这种步通常已被 `_skip_reason` 跳过）；④`_is_file_input` 双保险；⑤超时降级不抛错，让既有 `_rerun_step_with_retries` 兜底。

### 配置：`rerun_actionability_check` + 超时（照 `rerun_wait_for_page_settle` 模式 + 2 超时项）

```python
# config.py:129 后（AgentSettings 新增 3 字段）
rerun_actionability_check: bool = False        # visible+enabled 检查；默认关=零行为变更
rerun_actionability_timeout: float = 2.0       # 单 action 等元素 actionable 超时（秒）；元素已定位只等 visible，2s 够
rerun_actionability_poll: float = 0.3          # poll 间隔（秒）；每次 poll 一次 get_state，0.3s 折中

# config.py:347 后（load_settings 加 3 行 env）
rerun_actionability_check=os.environ.get("AGENT_RERUN_ACTIONABILITY_CHECK", "").lower() == "true",
rerun_actionability_timeout=float(os.environ.get("AGENT_RERUN_ACTIONABILITY_TIMEOUT", "2.0")),
rerun_actionability_poll=float(os.environ.get("AGENT_RERUN_ACTIONABILITY_POLL", "0.3")),

# agent.py:83 后（__init__ 拆 3 字段）
self.rerun_actionability_check = _settings.rerun_actionability_check
self.rerun_actionability_timeout = _settings.rerun_actionability_timeout
self.rerun_actionability_poll = _settings.rerun_actionability_poll

# rerun.py:202 后（RerunMixin 注解 3 个）；rerun.py:287 后（None 哨兵，仅 check 走哨兵）
# rerun_history 签名加 rerun_actionability_check: bool | None = None；沿 _rerun_step_with_retries /
# _execute_history_step 回传（同 wait_for_page_settle 管线）。timeout/poll 走 self（非用户 kwargs）。
```

> CLI/TUI 不动（None 哨兵接管，与阶段1 同构）。缺口 5 超时复用既有 15.0s（语义=等元素出现），不新增配置项。

---

## 关键决策

| # | 决策 | 理由 |
|---|---|---|
| D-1 | 抽 `_wait_until`，缺口5/actionability/既有 `_wait_for_minimum_elements` 共用 | 三者结构同构；减重复；独立开关不合并 |
| D-2 | `_locate_target` 只读谓词，不动动作循环写回（`_update_action_indices`） | 复用匹配逻辑；避免回归；判定与写回职责分离 |
| D-3 | 新增 `_wait_for_target_elements`，保留老函数为薄封装 | 语义清晰（target vs minimum）；老测试不破 |
| D-4 | actionability 插动作循环内、定位后、`_exec_one` 前；乐观短路 | 元素刚定位通常已 actionable；少数 transition/disabled 才需等 |
| D-5 | actionability 单独 `timeout=2.0`/`poll=0.3`；缺口5 复用 15.0/1.0 | 语义不同（actionability 元素已在 map 只等 visible，2s 够） |
| D-6 | `is_visible=None` 保守放过 | "永不引入新失败"；None=无数据不阻断 |
| D-7 | 白名单 + `_is_file_input` 防御短路双保险 | 仿 serializer.py:264-271；file input 隐藏必被误杀 |
| D-8 | 缺口5 等所有目标（all-or-nothing） | 单步多 action 缺一即失败 |
| D-9 | actionability 超时降级不抛错，照原样执行 | 与 `_wait_for_minimum_elements` 一致；让 `_rerun_step_with_retries` 兜底 |
| D-10 | actionability 跳过：白名单外 / `hist_elem=None` / `INPUT[type=file]` | `hist_elem=None` 无法重解析漂移 index |

---

## 改动清单（逐文件）

| 文件 | 改动 |
|---|---|
| `src/tree_walker/agent/rerun.py` | ① 模块级 `_ACTIONABILITY_ACTIONS`/`_is_file_input`/`_is_actionable`；② 新增 `_wait_until`/`_locate_target`/`_collect_target_hists`/`_wait_for_target_elements`/`_wait_for_actionability`；③ `_wait_for_minimum_elements` 改薄封装；④ L465-468 改调 `_wait_for_target_elements`；⑤ L531 后插 actionability 块；⑥ RerunMixin 3 注解 + `rerun_history` 签名加 `rerun_actionability_check` None 哨兵 + 沿管线回传 |
| `src/tree_walker/config.py` | `AgentSettings` 加 3 字段（False/2.0/0.3）；`load_settings` 加 3 行 env |
| `src/tree_walker/agent/agent.py` | `__init__` 拆 3 个 `self.rerun_actionability_*` |
| `src/tree_walker/recorder/locator.py` | **不改**（`locate_by_ref` 复用；L140-141 死代码是既有 bug，不在本期） |
| `src/tree_walker/tools/actions.py` | **不改**（issue 明确"不动 tools"） |
| `src/tree_walker/cli.py` / `tui/app.py` | **不改**（None 哨兵接管） |

**复用**：`_match_element_index`（六级）、`locate_by_ref`（语义线索）、`_wait_for_minimum_elements` 骨架、阶段1 的 5 处配置接线范式、`serializer.py:264-271` file input 判定（移植为 `_is_file_input`）、`_rerun_step_with_retries` 失败兜底。

---

## 测试策略

**`tests/test_rerun_history.py` 新增**（按类分组，~17 个代表用例）：

- **`_wait_until` / 缺口5**：①目标全命中早退（首帧缺 1 个，第二帧全到→poll 1 次）；②超时降级返回末 state；③无目标（纯 extract）立即返回不 poll；④跳过 upload_file；⑤语义线索路径命中；⑥`wait_for_elements=True` 集成（确认等待循环运行≥2 次 get_state）。
- **actionability 纯函数**：⑦`_is_actionable` 四组合（visible×disabled）+ `is_visible=None` 放过；⑧`_is_file_input` 短路；⑨AX disabled property；⑩HTML disabled 属性。
- **actionability 编排**：⑪默认关零行为变更；⑫白名单外动作（wait/navigate/upload_file）跳过；⑬等至 visible 命中；⑭超时降级不抛错且 `_exec_one` 仍被调；⑮index 漂移重解析（idx 5→7，`params["index"]` 改写）；⑯`hist_elem=None` 跳过。
- **配置**：⑰`AgentSettings` 默认（False/2.0/0.3）+ env 注入 + `Agent.__init__` 拷贝 + kwargs 省略回落 self。

---

## 验证

1. **文档体裁**：与 `01-阶段1` 一致（关联块/Context/现状锚点/方案/决策表/改动清单/测试/风险表/核对清单）。
2. **行号锚点**：全部对照 master `a5953ae` 核验（实施时若 master 前进需重核）。
3. **纯文档零代码**：本 plan 不改任何 `.py`；代码改动留给 #125 实现 PR。
4. 后续实现 PR 的验证（文档里写明）：`uv run python -m pytest tests/ -x -v` 全过、覆盖率 ≥85%、默认配置（两开关皆 False）重放零回归。

---

## 边界与风险

| 项 | 说明 |
|---|---|
| **SPA 重渲染指纹漂移** | `_match_element_index` 六级降级有 AX_NAME/ATTRIBUTE/CLASS 兜底；语义线索路径有 RECT 位置兜底，对 SPA cursor:pointer div 更鲁棒；极端全失配→超时降级→既有重试兜底 |
| **actionability→`_exec_one` 间 index 再漂移** | 窗口极短（仅 Python 调用开销）；`tools.execute` 内部重解析，过期则抛错→`_rerun_step_with_retries` 重试。**不做原子 check+execute**（架构无此能力，强行加锁反复杂化） |
| **多 action 步部分就绪** | all-or-nothing，最慢目标决定整步等待；可接受；actionability 已是 per-action 模型 |
| **`is_visible=None`** | 理论 `build_dom_state` 走过都赋 bool；保守放过（D-6） |
| **`disabled` 双路径** | HTML `attributes["disabled"]` + AX `properties` name==disabled；`aria-disabled` 不在本期（需额外属性解析，留给后续） |
| **与重试交互** | actionability 降级后 `_exec_one` 可能失败→重试整步→又超时；2.0s×3=最多 6s 额外开销，可接受；可关开关回退 |
| **性能** | 缺口5 15s×1s=最多 15 次 get_state；actionability 2s×0.3s×N≈6N 次。乐观短路 + 默认关→用户不开零成本 |
| **upload_file 误杀** | 三层保护（白名单 / `hist_elem=None` 跳过 / `_is_file_input` 短路），双单测验证 |
| **缺口5 语义变更** | 默认 False 零影响；开启=升级（更准）；老 API `_wait_for_minimum_elements` 保留 |
| **`locator.py:140-141` 死代码** | 既有 bug，不在本期；记 TODO |

---

## 落地核对清单（实现 #125 时逐项核对）

- [ ] **rerun.py 基础设施**：模块级 `_ACTIONABILITY_ACTIONS` / `_is_file_input` / `_is_actionable` 三个辅助
- [ ] **rerun.py `_wait_until`**：通用原语；`_wait_for_minimum_elements` 改为薄封装
- [ ] **rerun.py `_locate_target`**：统一两条定位路径的只读谓词
- [ ] **rerun.py 缺口 5**：`_collect_target_hists` + `_wait_for_target_elements`；L465-468 改调用
- [ ] **rerun.py actionability**：`_wait_for_actionability`；L531 后插入乐观短路 + 调用块；index 漂移重写 `params["index"]`
- [ ] **rerun.py 配置管线**：RerunMixin 3 注解；`rerun_history` 签名加 `rerun_actionability_check: bool | None = None` + None 哨兵；`_rerun_step_with_retries` / `_execute_history_step` 沿管线回传
- [ ] **config.py**：`AgentSettings` 3 字段（False / 2.0 / 0.3）+ `load_settings` 3 行 env
- [ ] **agent.py**：`__init__` 拆 3 个 `self.rerun_actionability_*`
- [ ] **测试**：缺口 5（6 个）+ actionability（10 个）+ 配置（4 个）
- [ ] **回归**：`uv run python -m pytest tests/ -x -v` 全过；默认配置下重放零回归（`rerun_actionability_check=False` + `rerun_wait_for_elements=False`）
