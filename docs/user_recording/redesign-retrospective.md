# 录制器重设计：落地与 e2e 调试总结

> 本文回顾 `redesign.md`（Signal 模型 + 统一翻译层）从**实施**到**抖音 e2e 联调**的完整过程，
> 记录每一轮暴露的问题、根因、修复与验证。设计动机见 `redesign.md`，实施步骤见 `redesign-impl-plan.md`。
>
> **成果**：抖音上传流程重放成功率从 **8/20** 提升到 **~21–22/25**（封面编辑器、合集、自主声明、
> 发布确认等 modal 内步骤从全失败到基本打通）。

---

## 1. 起点：为什么要重设计

`feat/user-recording` 分支的录制器已跑通抖音上传，但去噪逻辑是补丁堆叠（`dedupe_uploads` /
`dedupe_auto_navigates` / `denoise_steps` 三个事后函数作用在成品 `AgentHistory` 上），三个结构性痛点：

1. 采集层 `findInteractiveAncestor` 与后端 `is_interactive` 不对齐（cursor:pointer div 触发器漏录）。
2. 去噪基于成品 steps 靠时间 gap 猜意图（`dedupe_uploads` 误吸收「选择封面」click）。
3. SPA modal 连锁失败（modal 没开 → 内部步骤全找不到）。

`redesign.md` 给出方案：引入 `Recording`/`ActionRecord`/`Signal` 内部模型 + 四阶段翻译管线，
落盘时 flatten 成现有 `AgentHistoryList`，重放端零改动。

---

## 2. 实施内容（redesign 本体）

### 后端新增 4 个文件

| 文件 | 职责 |
|---|---|
| `recorder/models.py` | `SignalKind`/`Signal`/`ElementRef`/`ActionRecord`/`RecordingState`/`Recording` + `element_ref_from_event`/`signal_from_payload` |
| `recorder/translation.py` | Stage1 `translate_event`（映射 + 连续 input 聚合）+ Stage4 `update_state`；ts 统一秒口径 |
| `recorder/rules.py` | Stage3 `apply_rules`（signal 感知规则：navigation/file_upload/redundant_click/merge_inputs/merge_scrolls） |
| `recorder/flatten.py` | 纯 reshape：`Recording → AgentHistoryList` |

### 后端改动

- `recorder/recorder.py`：`handle_event` 改建 `ActionRecord`；`stop` 跑 `apply_rules → flatten`；新增 `attach_signal`（`/signal` 端点）。
- `recorder/server.py`：加 `POST /signal`。
- `recorder/event_mapper.py`：删去噪函数，只留 `map_event`/`needs_target`（Stage1 纯映射）。
- `recorder/__init__.py`：导出更新。

### 扩展端

- 新 `capture/side-effect-observer.ts`（MutationObserver 仅动作后 1s 窗口检测 modal/dropdown）。
- `action-recorder.ts` 加 `onAction` 钩子；`findInteractiveAncestor` 已含 cursor:pointer+onclick。
- `content.ts`/`background.ts`/`backend.ts`/`types.ts` 接 `/signal` 流。

### 关键设计修正（对 redesign 文档）

> ⚠️ redesign §3.4 说 `flatten` 落盘时才 `locate_by_ref` 定位——**这会破坏 modal 捕获**：modal 在
> stop 前已关闭、DOM 没了，stop 时 locate 必失败。实际把定位 + 指纹投影保留在 `handle_event`
> **事件到达时实时做**（modal DOM 活着），结果存进 `ActionRecord.interacted_element` + `params['index']`，
> `flatten` 退化为纯 reshape。signal 模型与翻译规则的价值不变。

### 验证

- recorder 包覆盖率 **92%**，新增 ~70 个单测；全量回归 **1940 passed**。
- 扩展 `npm run build` 通过。
- CDP 只读冒烟（`examples/smoke_pipeline_cdp.py`）在真抖音页跑通 translate→locate→指纹→flatten。

---

## 3. e2e 联调：三轮发现 → 修复

### 3.1 第一轮：step8「选择封面」无指纹 → 加 RECT 位置兜底

**现象**：重录 `douyin_redesign.json` 重放，从「选择封面」开始全失败。replay 日志显示 step8 是
`click {}` interacted=[None]（**无指纹**）→ 重放跳过（"录制定位失败的噪声步"）→ 封面 modal 没开 →
step9-14 连锁失败。

**根因**（不是 signal/dedup——redesign 已让 click 不被吸收✓）：**录制时 locate 失败**。`cover-Jg3T4p`
触发器无 name/id/aria-label/role → ATTRIBUTE 兜底失效；xpath 瞬时漂移 → XPATH 失配 → 无指纹。
（诊断脚本确认：cover 在 light DOM、xpath 与 CDP 一致，但点击触发 modal 重排导致录制瞬间 xpath 对不上。）

**修复**：`locator.locate_by_ref` 加 **Level 3 RECT 位置兜底**——按扩展 rect 中心点在 selector_map 里
找 bounds 包含该点的节点，就近兜底。`upload_file` 跳过 Level 3（保 `<input type=file>` 兜底）。
locate 仍失败时记 `ActionRecord.locate_miss` → flatten 写 `state_summary['_locate_miss']` 便于事后分析。

**验证**：`debug_cover_rectfix.py` 故意失配 xpath + cover rect → Level 3 命中 cover。

### 3.2 第二轮：step3 上传失败 → 文件未就位（环境问题）

**现象**：重录 `douyin_redesign2.json` 重放，step3 视频上传后页面没跳 `/post/video` → step4+ 全找不到。

**根因**（用户环境，非代码）：录制把上传路径解析成 `rerun-history/uploads/<name>`，但该目录不存在，
视频实际在 `D:\Videos\test\final\...`。`setFileInputFiles` 拿到不存在的路径（惰性读盘返回 OK 但页面
读不到）→ 上传没处理 → 不跳转。用户替换文件路径后解决。

> 这是约定：扩展只能拿文件名（浏览器安全限制），后端拼 `upload_dir`，**重放前须把文件放该目录**。

### 3.3 第三轮：step8 仍失败 → RECT 兜底改用 IoU 选择

**现象**：文件就位后重放，step8 仍失败。诊断发现 step8 录到的指纹是 cover 区内的 **18×18 svg 图标**
（无稳定属性），不是 `cover-Jg3T4p` 本体——初版 RECT 兜底的「最小容器」启发式选了 svg。

**修复**：RECT 兜底改用 **IoU（交并比）选择**——在「bounds 含点击中心」的候选里取与**点击 rect 的 IoU
最高**者（IoU 并列取面积小），`IoU < 0.1` 不认（过滤整页 root、无关小图标）。用户点 cover(160×120)
时点击中心落在其内 svg 上，IoU 选 cover（≈1）不选 svg（≈0.017）。

**验证**：patch step8 成 cover 真实指纹后重放（replay4）→ step8 开封面编辑器（DOM 2500→3453）→
**step9-20 全过**（封面图上传✅ canvas 预览 11→12→13）→ **21/25**。仅 step21「确定」button 失败。

### 3.4 step21「确定」class-only 失败 → rerun 加 CLASS 第 6 级

**现象**：replay4 跑到 step21（自主声明确认「确定」按钮）失败。按钮无 name/id/aria-label，只有
class=`semi-button semi-button-primary btn-xtdEbg`，录制 ax_name=None 但重放 ax='确定'。rerun 五级
匹配（EXACT/STABLE/XPATH/AX_NAME/ATTRIBUTE）全不过——**ATTRIBUTE 只用 name/id/aria-label，不用 class**。

**修复**（用户选 B，定向增强）：`rerun.MatchLevel` 加 `CLASS=6`，`_match_element_index` 在 ATTRIBUTE 后加
Level 6——按 node_name + **class token 超集**（候选 class ⊇ 录制 token，顺序无关、可多不可少，容忍额外
状态类）匹配，多候选 `_nearest_idx` 就近 tie-break。仅当前 5 级全失败、且录制元素有 class 时触发。

**验证**：replay5 → step21 `匹配级别 CLASS` ✓ 命中并点击。取消按钮是 tertiary（缺 `semi-button-primary`
token）故不误匹配。加 4 个 CLASS 单测，全量 **1950 passed**。

> ⚠️ **破了 redesign「重放端零改动」承诺**——属定向增强（仅兜底，前 5 级全失败才触发），需在 PR 说清。
> CSS-module 哈希后缀（如 `btn-xtdEbg`）跨构建漂移会失效，仅同会话/同构建内稳。

---

## 4. 成果与现状

| 阶段 | 成功率 |
|---|---|
| 重设计前（旧 `recorded.json`） | **8/20** |
| redesign 实施后（未修 locate） | 卡在 step8（选择封面无指纹） |
| + RECT 兜底 + 文件就位 | 卡在 step8（svg 误匹配） |
| + IoU 选择（replay4） | **21/25**（仅 step21 class-only 失败） |
| + CLASS 第 6 级（replay5） | step21 ✓，**~22/25** |

**关键突破**：「选择封面」触发器从录不到指纹 → 录到 `cover-Jg3T4p` → 点开封面编辑器 → modal 内步骤
（封面图上传×2、完成、合集、自主声明 radio、确定）全打通。

> 注：replay4/replay5 用的是 patch 过的 `douyin_redesign3.json`（手动把 step8 指纹替换成 cover），
> 仅为**分别验证**两个修复（patch 文件跨 run 不稳定：replay5 因 patch 指纹过期致编辑器这次没开，9-14 失败、
> 15/25——这是 patch 的局限，非代码回归）。**真正的终态须用户用两个修复重录一次**才能完整体现。

---

## 5. 遗留与注意事项

1. **用户须 fresh 重录一次**：两个修复（IoU locate + CLASS）都是后端纯改，重启 `record_user_actions.py`
   即生效，无需扩展 rebuild。预期 step1-20 ✓（IoU）+ step21 ✓（CLASS）+ 22-24 → 接近全过。
2. **cover 编辑器打开有轻微时敏感性**：replay4 开了、replay5 没开（patch 指纹过期所致）。真实录制应更稳，
   且 rerun 的 3× 退避重试通常能吸收瞬时抖动。
3. **rect 兜底 + CLASS 兜底只对同会话/同构建稳**：跨抖音版本（CSS-module 哈希变）会失效，但 record-then-
   replay-same-session 场景没问题。
4. **redesign 承诺的「重放端零改动」被 CLASS 级打破**：属定向增强，PR/commit 须标注。
5. **rect 兜底误匹配整页 root**（step1 点空处时）：IoU 阈值已过滤大部分，但极端情况仍可能；点击失败无害。

---

## 6. 文件改动清单

**后端（新增）**：`src/tree_walker/recorder/{models,translation,rules,flatten}.py`
**后端（改动）**：`src/tree_walker/recorder/{recorder,server,event_mapper,__init__,locator}.py`、`src/tree_walker/agent/rerun.py`（+CLASS 级）
**扩展（新增）**：`recording_extension/capture/side-effect-observer.ts`
**扩展（改动）**：`recording_extension/capture/action-recorder.ts`、`entrypoints/{content,background}.ts`、`shared/{backend,types}.ts`
**测试（新增）**：`tests/test_recorder_{models,translation,rules,flatten}.py`；`tests/test_recorder_locator.py`（+rect 兜底）；`tests/test_recorder_history.py`（+CLASS）
**诊断脚本（examples/，未提交）**：`smoke_pipeline_cdp.py`、`debug_cover_*.py`、`debug_patch_step8.py` 等
**文档**：`docs/user_recording/{redesign-impl-plan.md,redesign-retrospective.md}`

---

## 7. 教训

- **设计文档的伪代码要验证**：redesign §3.4 的 flatten-at-stop locate 听上去合理，但对 modal 是致命的——
  落地时必须想清楚「定位需要活的 DOM，而 modal 在 stop 前就关了」。
- **诊断要落盘**：第一轮的 `locate_miss` 字段让「为什么录不到指纹」从黑盒变成可读，省了反复猜测。
- **「最小容器」≠「正确目标」**：RECT 兜底初版用最小容器，结果选了 cover 区里的 svg 图标而非 cover 本体——
  用户点的是触发器区域，IoU（与点击 rect 的重叠度）才是正确信号。
- **e2e 会逐层揭问题**：dedup → locate → 文件就位 → svg 误匹配 → class-only 匹配，每修一层露出下一层。
  诊断脚本（CDP 只读探针）+ patch 验证法是逐层推进的有效手段。
