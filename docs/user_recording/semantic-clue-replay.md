# 语义线索回放：救回"动作触发页面变化"的录制失败步

> 本文承接 `recorder-timing-solutions.md`（架构方向）+ `cover-upload-fix-plan-v2.md`（file upload 探索）。
> 针对"点 submit/链接触发跳转，录制 interacted_element 为空、回放失败"这类问题，给出一个
> **比重放端 accept 兜底通用、比半主动 get_state 轻**的方案：语义线索回放。**待 review 后实施。**

---

## 一、问题（已确认）

录制时点 submit/链接等触发跳转的按钮：

```
用户点 submit → click 事件 → 扩展发 click → 后端 get_state（此时页面已跳转，原按钮消失）
  → locate_by_ref 失败 → interacted_element = [None] → 重放该步被 skip → 跳转/提交丢失
```

httpbin 表单的 submit 即此（`rerun-history/httpbin.json` step16）。`recorder-timing-solutions.md` 把
它归为"被动观察时序劣势"——凡是"动作触发页面/DOM 变化"（submit/链接/file upload/modal/动态列表），
录制端 `get_state` 抓到的是**变化后**的状态，元素找不到，算指纹结构性不可达。

**两个层面**：
1. click 步骤 `interacted_element` 为空（必然，时序劣势）。
2. 回放丢失程度看跳转类型：**整页跳转**（submit/location.href/a href）连 navigate 都录不到
   （`navigation-recorder` 只捕获 pushState/popstate/hashchange，整页跳转 content script 卸载）→ 回放
   彻底丢失；**SPA 跳转**（pushState）录到 navigate，跳转能重放，但 click 的提交副作用丢失。

---

## 二、关键 reframing：录制失败 ≠ 重放必败

之前一直在"录制端算指纹"上打转（cover_index/D1/半主动），都撞时序劣势。换个角度想**重放**：

```
录制（被动）：用户点 submit → 跳转 → 扩展发 click → get_state（抓跳转后页，button 没了）
重放（主动）：按步骤执行 → 到 click submit 这步时，重放还没点 → 页面稳定，button 完好！
```

**重放到 click 步骤时，submit button 还在**（重放主动按步骤走，没点就没跳转）。重放和 agent 一样有
"先看再做"的优势。**录制端的时序劣势，在重放端不存在。**

问题不是"录制没算出指纹就完蛋"，而是——**录制把定位线索丢成了空**，重放没有线索重新定位。扩展在 click
瞬间其实握住了 e.target 的特征（xpath/tag/name/aria/rect），这些就是 `locate_by_ref` 的输入。**把它们
存为"语义线索"，重放时复用 `locate_by_ref` 在稳定页面重新定位。**

这是**绕过**录制时序劣势（承认录制算不出指纹），**利用**重放主动优势（重放时元素完好）。

---

## 三、设计

### 1. 线索存什么——复用扩展已有的 ref，不改扩展

扩展在 click/input 事件瞬间已握住 e.target 的特征（`buildElementRef` + `refAttrs`，发给后端）：
`xpath / tag / id / name / ariaLabel / role / rect`。这正好是 `locate_by_ref`（`recorder/locator.py` 三道
防线：xpath→属性→RECT）的输入。**线索本身有效**（是 e.target 在 click 瞬间的真实特征），录制失败只是
因为 get_state 抓错了页——线索没丢，是录制丢成了 `[None]`。

### 2. 录制端：locate 失败 → 存线索，不丢空

`recorder.handle_event` 的 locate 失败分支（现有 `locate_miss`），现在 `interacted_element = [None]`。
改为存语义线索（click/input/select）：

```python
action.interacted_element = [{
    "_semantic_clue": True,       # 标记：语义线索（重放重新定位），非指纹
    "xpath": event.get("xpath"), "tag": event.get("tag"),
    "name": event.get("name"), "id": event.get("id"),
    "ariaLabel": event.get("ariaLabel"), "role": event.get("role"),
    "rect": event.get("rect"),
}]
```

`locate_miss` 诊断保留。**upload_file 保持 `[None]`+accept**（file input 隐藏无属性，三道防线弱，另论）。

### 3. 重放端：检测线索 → 复用 `locate_by_ref` 重定位

`rerun._execute_history_step` 加一条分支（在现有指纹路径前）：

```python
if hist_elem and hist_elem.get("_semantic_clue"):
    matched = locate_by_ref(hist_elem, selector_map)   # 复用！三道防线在稳定页面重定位
    if matched:
        params = dict(raw_params); params["index"] = matched[0]
        logger.info("语义线索重定位 idx=%s", matched[0])
    else:
        raise ValueError(self._format_semantic_clue_failure(hist_elem, selector_map))
elif hist_elem and has_index:
    <现有指纹路径 _update_action_indices，不动>
```

**关键**：`locate_by_ref` 从 `recorder.locator` import；线索字段名（xpath/tag/name/id/ariaLabel/role/rect）
与 `locate_by_ref` 期望完全一致，**零适配**。`locate_by_ref` 的三道防线（XPATH→ATTRIBUTE→RECT）在重放
的稳定页面上工作，比录制抓跳转后页可靠得多。

### 4. skip 逻辑：无需改

`_skip_reason` 现判 "click 无 index 且 `interacted[0] is None`" → skip。语义线索后 `interacted[0]` 是
`{_semantic_clue:...}`（非 None）→ **自然不 skip**，rerun 会尝试重定位。

### 5. 三条路径共存（降级链，互不干扰）

| 录制结果 | interacted_element | 重放路径 |
|---|---|---|
| 成功算指纹（稳定元素） | `{element_hash,...}` | 指纹匹配 `_match_element_index`（现有，最优，保留） |
| 失败存线索（跳转/modal） | `{_semantic_clue,...}` | **新增**：`locate_by_ref` 重定位 |
| upload_file | `[None]` | accept 兜底 `_resolve_file_input_by_accept`（现有，保留） |

指纹路径完全保留——稳定可见元素（input/textarea/label/普通按钮）的录制仍走指纹（最优）。语义线索只补
"录制失败"的步骤。三者按 interacted_element 内容分发，互不干扰。

---

## 四、各场景效果

| 场景 | 录制 | 重放（语义线索） |
|---|---|---|
| **submit button**（httpbin） | click 失败 → 存 submit 的 xpath/tag/rect 线索 | 重放到 click submit（页面稳定）→ locate_by_ref xpath 命中 button → 点击 → 跳转 ✓ |
| **链接整页跳转** | 同上 | 重放定位 a → 点击 → 整页跳转 → 重放继续读新页 ✓（不需 navigate 步骤） |
| **modal 触发器** | click 后 modal 重排，录制时 selector_map 过时 → 失败 | 重放定位触发器（页面稳定）→ 点击 → modal 开 ✓ |
| **file upload** | 已 `[None]`+accept | 仍 accept 兜底（本次不动） |

**整页跳转的关键**：重放定位到 submit 并点击后，浏览器整页跳转，**重放继续读新页**即可——不需要 navigate
步骤（click submit 触发跳转，重放自然进入新页）。所以整页跳转的 navigate 缺失不影响重放，只要 click 能定位。

---

## 五、改动清单

- `src/tree_walker/recorder/recorder.py`：`handle_event` locate 失败分支，click/input/select 存语义线索（非 `[None]`）。
- `src/tree_walker/agent/rerun.py`：`_execute_history_step` 加 `_semantic_clue` 检测分支（复用 `locate_by_ref`）；
  import `locate_by_ref`；可选 `_format_semantic_clue_failure`。
- 测试：`test_recorder.py`（locate 失败存语义线索字段）；`test_rerun_history.py`（语义线索重定位命中/失败）。
- **不改扩展**（事件已有 ref）；**不改 locator.py**（复用）。

---

## 六、验证（端到端）

1. `uv run python -m pytest tests/ -x` 全量绿；recorder 包覆盖率 ≥ 85%。
2. 重录 httpbin 表单 → submit 步录制失败但存语义线索（`interacted_element` 非 None、含 `_semantic_clue`）。
3. 重放 `httpbin.json` → submit 步走"语义线索重定位"（日志 `语义线索重定位 idx=...`）→ 点击 submit →
   跳转 /post → 成功（而非被 skip）。
4. 回归：稳定元素（input/textarea/label）仍走指纹路径（`匹配级别 EXACT`），不受影响。

---

## 七、边界 / 风险

- **xpath 跨会话漂移**：有 ATTRIBUTE（name/aria）+ RECT 兜底（三道防线）；重放页面稳定，比录制抓跳转后页
  可靠。httpbin submit 的 xpath 重放时实测完全匹配（live 确认 button 在 selector_map，idx=1477）。
- **重定位也失败**（三道全 miss，元素真无特征，罕见）：raise + 记日志，不静默 skip。
- **upload_file 不在本次**：保持 accept 兜底（file input 隐藏无属性，三道防线弱；后续若要覆盖，可给
  upload_file 也存语义线索 accept+rect+xpath）。

---

## 八、与其它方向的关系

- **比 accept 兜底通用**：accept 只解 file upload（按文件类型），语义线索解所有"录制失败步"（submit/链接/
  modal/upload），且复用已有 `locate_by_ref`。
- **比半主动 get_state 轻**：半主动要"前奏识别 + pending 认领 + 抢读性能"，语义线索不抢读、不 pending，
  只存已有线索 + 重放加一条检测分支。
- **不冲突、可叠加**：语义线索（本次）解决"能存线索"的失败步；半主动（长期）解决"录制端算指纹"的根本
  时序劣势。两者可共存（降级链：指纹 > 语义线索 > accept）。
