# 录制器重设计实施计划：Signal 模型 + 统一翻译层

> 本文件是 [`redesign.md`](./redesign.md) 的**落地实施计划**（已评审通过）。
> 设计动机、痛点分析、业界对照见 `redesign.md`；本文只讲**怎么改哪些文件、关键决策、验证方式**。
>
> **状态**：实施中（`feat/user-recording` 分支）。

---

## Context（为什么做）

`feat/user-recording` 分支的录制器已跑通抖音上传，但去噪逻辑是「事件 → 立即拼 `AgentHistory` → 事后 `denoise_steps` 收口」的补丁堆叠（`dedupe_uploads` / `dedupe_auto_navigates` / `denoise_steps`），三个结构性痛点（详见 [`modal-trigger-capture.md`](./modal-trigger-capture.md)）：采集层 `findInteractiveAncestor` 与后端 `is_interactive` 不对齐、去噪基于成品 steps 靠时间 gap 猜意图、SPA modal 连锁失败。

`redesign.md` 给出重设计：引入 `Recording`/`ActionRecord`/`Signal` 内部模型 + 四阶段翻译管线（event→action / signal 检测 / 翻译规则 / 状态机），落盘时 flatten 成现有 `AgentHistoryList`，**重放端 `rerun.py` 零改动**。

**目标产出**：去噪从「事后成品 steps 上猜」搬到「带 signal 上下文的 `ActionRecord` 管线」上，用 `modal_opened`/`navigation` signal 让规则做意图判断而非 gap 猜测。

**实施范围（已确认）**：全量（后端管线 + 扩展 SideEffectObserver + findInteractiveAncestor 确认 + 尝试抖音 e2e）；旧 `event_mapper` 去噪函数删除，测试迁移到新管线。

---

## 关键设计决策（对 redesign 文档的一处必要修正）

> ⚠️ **`redesign.md` §3.4 的 `flatten()` 伪代码在落盘时才 `locate_by_ref` 定位——这会破坏 modal 元素捕获**：modal 在 `stop` 前已关闭、DOM 里没了，stop 时 locate 必失败。当前 `handle_event` **每事件实时定位**（modal 打开时 DOM 是活的）是 modal 可回放的命脉，必须保留。

**因此**：定位 + 指纹投影（`locate_by_ref` + `DOMInteractedElement.load_from_enhanced_dom_tree`）仍**在 `handle_event` 事件到达时实时做**，结果（resolved `index` + `interacted_element` dict + resolved upload path/tab_id）存进 `ActionRecord`。`flatten()` 退化为**纯 reshape**（`ActionRecord` → `AgentHistory`），不再需要 browser/`get_state`，同步、可无 mock 单测。

signal 模型与翻译规则的价值不变——它们作用在带 signal 的 `ActionRecord` 列表上，替代事后 denoise。**这是对 redesign 文档落地时的一处工程修正，不是设计变更。**

---

## 架构

```
扩展 content script ──RecorderEvent 流──▶ POST /event
                                  ──SignalEvent 流──▶ POST /signal   (新, SideEffectObserver)

后端 Recorder.handle_event:
  Stage1 translate_event (map_event 纯映射 + 状态机 + 连续 input 聚合)
    + 实时 locate/指纹 (命脉, 存 ActionRecord)
    + 导航信号 (pre/post url diff, 廉价)
  → Recording.actions

Recorder.stop:
  Stage3 apply_rules (signal 感知的 file_upload / navigation_signal / redundant_click / merge)
  → flatten() 纯 reshape → AgentHistoryList → 落盘

rerun.py 零改动消费
```

**核心变化**：现有「事件 → 立即拼 AgentHistory → 事后 denoise」改为「事件 → 翻译管线产出 Recording（action+signal）→ 落盘时 flatten」。中间多了 `Recording` 内部模型承载 signal + 状态机字段，让去噪在「有完整上下文」的地方做。

---

## 文件改动

### 新增（后端）

#### `src/tree_walker/recorder/models.py` — 内部模型（dataclass）

- `SignalKind`（Enum: `navigation` / `modal_opened` / `dropdown_opened` / `dialog` / `download`）
- `Signal`（kind, timestamp, detail: dict, source_action_ts）
- `ElementRef`（xpath / tag / id / name / aria_label / role / rect — 录制瞬间线索，定位后不再用）
- `ActionRecord`（action_name, params【含实时 resolved index/path/tab_id】, element_ref, timestamp, signals: list[Signal], **interacted_element: list[dict|None]|None**【实时指纹投影，flatten 直用】, page_url, page_title）
- `RecordingState`（focus_target_xpath, focus_value, pending_modal, last_action_ts）
- `Recording`（actions: list[ActionRecord], state: RecordingState）
- `from_event(event: dict) -> ElementRef`：从扩展事件 dict 抽 ElementRef（复用 locator 的字段名）

#### `src/tree_walker/recorder/translation.py` — Stage 1 + Stage 4

- `translate_event(event, state) -> ActionRecord | None`：调 `event_mapper.map_event` 做纯映射；不可映射返 None；同时 `update_state`。不做 locate（locate 在 recorder 实时做，因为要拿指纹）。
- `update_state(state, action)`：input_text 设 focus_target_xpath/focus_value；click 清 focus（除非点的是 focus 本身）；modal 信号设 pending_modal。
- `aggregates_input(new, last, gap_s=1.5) -> bool`：连续同 xpath input_text 且在 gap 内 → 真（recorder 据此把 last.params 取最终值、不 append）。替代旧 `coalesce_inputs` + `denoise_steps` 的 input 合并。

#### `src/tree_walker/recorder/rules.py` — Stage 3 五类翻译模式

- `Rule = Callable[[list[ActionRecord], int], list[ActionRecord]]`（返替换列表，空=丢弃）
- `apply_rules(actions, rules=[...]) -> list[ActionRecord]`：迭代跑全部规则（导航信号规则先跑，让后续规则看到附加的 signal）
- `rule_navigation_signal`（替代 `dedupe_auto_navigates`，signal 化）：navigate 且非 new_tab，若前一步是 click/input/send_keys 且 `diff < 5.0s`（strict `<`，保 5.0s keep 用例）→ 给前一步附 `NAVIGATION` signal，丢弃此 navigate；连续 navigate / 首步 / new_tab 保留。
- `rule_file_upload`（替代 `dedupe_uploads`，signal 化）：upload_file 前置 click **有 `MODAL_OPENED` signal → 明确保留**（打开编辑器，绝非上传按钮）；吸收前置 fakepath input_text（text 含 `fakepath` 或 basename 与 upload path 一致，gap≤10s）；无 signal 时维持现状（click 也不吸收，等同当前补丁行为）。
- `rule_redundant_click`（替代 `denoise_steps` click 折叠）：相邻同 element_ref.xpath 的 click，`diff < 2s` → 留最后一条。
- `rule_merge_inputs`（安全网）：相邻同 xpath input_text → 留最后值（Stage1 已聚合，防乱序）。
- `rule_merge_scrolls`（替代 `denoise_steps` scroll 合并）：相邻同方向 scroll → amount 求和 clamp 1-10。

#### `src/tree_walker/recorder/flatten.py` — 落盘 reshape

- `flatten(recording: Recording) -> AgentHistoryList`：纯 reshape，无 browser。每条 ActionRecord → AgentHistory（step_number 重排 0..N-1、model_output={actions:[{name,params}]}、interacted_element 直用 ActionRecord.interacted_element、state_summary={url,title}、metadata=StepMetadata(step_start_time=ts, step_end_time=ts, step_number)）。

### 改动（后端）

#### `src/tree_walker/recorder/recorder.py`

- `__init__`：加 `self.recording = Recording(actions=[], state=RecordingState())`；保留 `self.history`（stop 后赋 flatten 结果，`server.py` 的 `/stop` 响应 `len(rec.history.history)` 仍工作）。
- `handle_event`：`translate_event` → 不可映射返 None；可映射时**实时 locate + 指纹投影**（复用现有 `locate_by_ref` + upload_file file-input 兜底 + `DOMInteractedElement.load_from_enhanced_dom_tree`，原样搬）填 `ActionRecord.interacted_element/params`；tab_id/upload path 解析原样搬；导航信号（pre/post `get_state` url diff，click/input/send_keys 触发时附 `NAVIGATION`）；`aggregates_input` 命中则改 last.params 取最终值不 append，否则 append。返回 ActionRecord 或 None。
- 加 `attach_signal(signal_payload: dict)`：把 `/signal` 来的 modal/dropdown 信号附到 `recording.actions[-1]`（timestamp 窗口 ≤2s 内最近一条）。
- `stop`：`recording.actions = apply_rules(recording.actions)` → `self.history = flatten(recording)` → 保留 `_prepend_initial_navigation`（首步非 navigate 才补起始页 navigate）+ 可选 done 追加 → `save_to_file`。

#### `src/tree_walker/recorder/server.py`

- 加 `POST /signal` → `rec.attach_signal(await request.json())`。
- `/event` 响应 step 改用 `len(rec.recording.actions)`（ActionRecord 无 step_number）。

#### `src/tree_walker/recorder/event_mapper.py`

**删除** `coalesce_inputs` / `dedupe_uploads` / `dedupe_auto_navigates` / `denoise_steps` 及 `_within_gap`；**保留** `map_event` + `needs_target`（Stage 1 纯映射，translation.py 复用）。

#### `src/tree_walker/recorder/__init__.py`

导出更新（加 models/translation/rules/flatten 公开符号，去 `coalesce_inputs`）。

### 新增/改动（扩展端）

#### `recording_extension/capture/side-effect-observer.ts`（新）

`installSideEffectObserver({ sendSignal }) -> { uninstall, markAction }`。`MutationObserver(document, childList+subtree)`，仅 `Date.now()-lastActionTs < 1000` 窗口内观察；addedNodes 匹配 `[role=dialog],[aria-modal=true],.modal,.ant-modal` → `{type:'modal_opened',selector,ts}`；匹配 `[role=listbox],.ant-select-dropdown,.semi-select-option-list` → `{type:'dropdown_opened',...}`。

#### `recording_extension/capture/action-recorder.ts`

`InstallOptions` 加可选 `onAction?: (ts:number)=>void`；`emit()` 末尾调 `onAction?.(ts)`（供 observer.markAction 打时间戳）。`findInteractiveAncestor` 已含 cursor:pointer+onclick（`redesign.md` §4.1 已完成），确认即可不动。

#### `recording_extension/entrypoints/content.ts`

先建 observer，把 `observer.markAction` 作 `onAction` 传给 `installActionRecorder`，`install()` 里 `installSideEffectObserver({ sendSignal })` 与 action/navigation recorder 一起装/卸；`sendSignal` 走 `chrome.runtime.sendMessage({kind:'signal', signal:{...signal,url:location.href}})`。

#### `recording_extension/entrypoints/background.ts`

`Message` union 加 `{kind:'signal', signal}` 分支 → `postSignal(signal)`（仅 recording 时转发）。

#### `recording_extension/shared/backend.ts`

加 `postSignal(signal)` → `POST /signal`。

#### `recording_extension/shared/types.ts`

加 `SignalEvent`（type:'modal_opened'|'dropdown_opened', selector, ts）+ `ContentMessage` 加 `{kind:'signal',signal}`。

---

## 测试迁移

**`tests/test_recorder_event_mapper.py`**：删除 `coalesce_inputs`/`denoise_steps`/`dedupe_*` 相关用例（~16 个），**保留** `map_event`/`needs_target` 用例（~14 个，Stage 1 不变）。

**新增**：

- `tests/test_recorder_models.py`：ElementRef/Signal/ActionRecord 构造 + `from_event`。
- `tests/test_recorder_translation.py`：`translate_event` 各类型映射、`update_state`（focus/pending_modal）、`aggregates_input`（连续同 xpath 合并 / 不同 xpath / 超 gap 不合 / 跨动作不合）。
- `tests/test_recorder_rules.py`：把旧 denoise 场景全部迁移，**行为等价**——
  - `rule_navigation_signal`：upload/click 后 ≤gap 的 navigate 丢弃、5.0s 保留、连续 navigate 保留、new_tab 保留、首步保留。
  - `rule_file_upload`：fakepath input_text 吸收、普通 input_text 不吸收、前置 click 有 modal_opened signal 保留、前置 click 无 signal 也保留（=当前补丁）、两 upload 各自处理、超 gap 不吸收。
  - `rule_redundant_click`：短时同元素折叠、超时不折。
  - `rule_merge_scrolls`：同方向求和 + clamp10、反方向保留。
- `tests/test_recorder_flatten.py`：ActionRecord 列表 → AgentHistoryList（step_number 连续、interacted_element 透传、不可映射事件不入）。

**`tests/test_recorder.py`**：保留，走新管线的端到端（FakeBrowser + patch_projection 不变）；`test_stop_applies_denoise_to_history` 改验证新管线输出（合并 input 取最终值）；其余定位/落盘/初始 navigate/done 连号用例行为不变应自然通过。

---

## 验证

1. **单测** ✅：`uv run python -m pytest tests/test_recorder_models.py tests/test_recorder_translation.py tests/test_recorder_rules.py tests/test_recorder_flatten.py tests/test_recorder.py tests/test_recorder_event_mapper.py tests/test_recorder_server.py tests/test_recorder_locator.py -q` → **105 passed**。
2. **覆盖率** ✅：recorder 包 **92%**（>85% 目标）。未覆盖行主要是 `recorder.py` 的 CDP client 错误分支（需真 browser）。
3. **全量回归** ✅：`uv run python -m pytest tests/ -q` → **1940 passed**，无 regression（rerun.py 未动）。
4. **扩展构建** ✅：`cd recording_extension; npm run build` → `.output/chrome-mv3/` 产出（content.js 含新 SideEffectObserver）。
5. **CDP 只读冒烟** ✅：`uv run python examples/smoke_pipeline_cdp.py` 连真实抖音页（creator.douyin.com，608 节点 selector_map），跑通 translate_event → locate_by_ref → DOMInteractedElement 指纹 → apply_rules → flatten，产出带 element_hash 的 AgentHistory。
6. **抖音扩展 e2e（待用户手动）**：需用户加载新扩展 build、点「开始录制」手动走抖音上传、停止、`load_and_rerun` 重放，对比旧 8/20 成功率。运行手册见下。

### 抖音 e2e 运行手册（用户执行）

```powershell
# 1. Chrome 已以 --remote-debugging-port=9222 启动 + 抖音已登录（creator.douyin.com）
# 2. chrome://extensions 加载 recording_extension/.output/chrome-mv3（重新加载以拿新 build）
# 3. 起录制后端
uv run python examples/record_user_actions.py --out douyin_redesign.json

# 4. 点扩展弹窗「开始录制」→ 走完整上传（标题/描述/选择封面/合集/自主声明/发布）→「停止录制」
#    产物：rerun-history/douyin_redesign.json
# 5. （可选）落盘后看「选择封面」click 是否带 modal_opened signal：
uv run python -c "import json;h=json.load(open('rerun-history/douyin_redesign.json',encoding='utf-8'));print([s for s in h['history'] if s['model_output']['actions'][0]['name']=='click'][:3])"
#    注：signal 不进 flatten 产物（重放端不读），但录制期日志会打「附信号 modal_opened 到步 N」

# 6. 重放，对比成功率
uv run python examples/replay.py rerun-history/douyin_redesign.json   # 或现有 load_and_rerun 入口
```

**预期收益**：「选择封面」click 不再被误吸收（`rule_file_upload` 只吸收 fakepath input_text）；
合集 select / 自主声明 click 录 cursor:pointer 触发器（`findInteractiveAncestor` 已对齐，§4.1）→
modal 打开 → modal 内步骤可定位。目标成功率 >15/20（旧 8/20）。


---

## 实施顺序

1. 后端：`models.py` → `translation.py` → `rules.py` → `flatten.py`（纯新文件，互不破坏现有）
2. 后端接线：`recorder.py` 迁移 → `server.py`/`event_mapper.py`/`__init__.py`
3. 测试：迁移 + 新增，跑绿 + 覆盖率 + 全量回归
4. 扩展端：`side-effect-observer.ts` + 接线 + `npm run build`
5. e2e 验证
