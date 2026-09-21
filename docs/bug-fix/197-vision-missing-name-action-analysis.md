# issue #197 分析：视觉模式"?"伪动作二连判死——真相是缺 name 键的畸形动作 + 梯子无形状定向

- 日期：2026-09-21（V 轮证据 2026-09-17~19）
- 证据：`evals 仓 results/self_overestimate/V_20260918/task_{108,109,200,492,495,696,782}.log` 等 12 份（本仓可得切片）；issue 引 `docs/evaluation/v-fail-analysis-2026-09-19.md` 批次 B（四段日志全量 50 次）
- 关联：#173/#176（畸形动作归一化与未注册名连坐——本 issue 是其策略边界外的形态）、#175（视觉接入，PR #177）、#186（内梯预算 _PARAM_VALIDATION_MAX_RETRIES 的来源）
- 模型口径：glm-5.3-flash + use_vision=true（smoke_test `_validate_vision_settings` 名单内）

## 0. 结论速览

| 项 | 结论 | 置信度 |
|---|---|---|
| 现象定性 | **issue 的"模型吐字面 `?` 当动作"是误读**：日志里的 `'?'` 是 `client.py:553` `a.get("name", "?")` 的**缺键占位符**——模型实际吐的是**缺 `name` 键的 action dict**（如 `{"params": {...}}`）。真吐字面 `"?"` 会被 `_is_valid_action` 判真、走参数校验报 `Unknown action '?'`，与本批日志的 "empty action" 链矛盾 | 确定（代码逻辑互斥判别） |
| 直接根因 | glm-5.3-flash 在**巨型页**（Magento Sales 订单网格，snapshot≈24.7k 条目/ax≈9.7k）上间歇输出缺 name 键的 agent_response 动作；重试仍带同款巨型上下文（含图），原样再吐 | 确定（日志+代码） |
| 死刑机制 | 双梯均二连即 fallback done：外梯（`_get_action_with_retry`，1 次澄清重试）+ 内梯（`_validate_params_or_retry`，预算 2）；澄清文案泛化（"忘了返回动作"），不含"缺 name 键"的定向纠错信息，也不降级去图 | 确定 |
| 触发聚集 | 本仓切片 7 死中 **4 个死在 step 1 的同一张订单网格页**（108/200/492/495，entries 24671~24757）；其中 108/200 死前还有 `captureScreenshot` 10s 超时（视觉模型收到纯文本巨页） | 确定（日志） |
| 视觉特有性 | 切片计数：V 轮 19 行 `?` 动作 / 12 任务；C 轮 2 / B 轮 1——**高一个数量级**，与 issue 判断一致 | 确定（grep 全量） |
| 切片死亡账 | 7 任务死于 `?`（外梯 5：108/200/492/495/782；内梯 2：109/696），消耗 14 行；另 5 任务（1/3/65/699/769）单次 `?` 被重试救回（任务仍因他因判 0） | 确定（本仓切片；issue 的 544/545/549 日志不在本仓，未核） |

---

## 1. 证据勘误：`'?'` 是日志占位符，不是模型输出

issue 的机制段写"模型吐字面 `?` 当动作"——**这不对，且误导修复方向**（针对"问号"做黑名单没有意义，明天模型吐的是别的缺键形态）。

判别链（三者互斥，日志可区分）：

1. **显示层**：`client.py:553` `names = [a.get("name", "?") for a in raw_action if isinstance(a, dict)]`
   —— `'?'` 只在 **dict 存在但 `name` 键缺失**时出现（`.get` 默认值）；
2. **若是字面 `"?"` 名字**（`{"name": "?"}`）：`_is_valid_action`（step.py:1274 `bool(name and isinstance(name, str))`）判**真**，走 `_validate_action_params` 报 `Invalid params for '?': Unknown action '?'`——日志形态完全不同，本批没有；
3. **若是 `name: null` / 空串**：显示为 `[None]` / `['']`，不是 `['?']`——本批也没有。

本批 19 行全部是形态 1：**动作 dict 缺 `name` 键**。佐证：所有死亡任务的 `['?']` 后紧跟 "LLM returned empty action"（外梯）或 "invalid action during param validation retry"（内梯）——两者都以 `_is_valid_action == False` 为前提，与"缺键"互洽。

> 日志歧义本身是次生问题：`a.get("name", "?")` 让"缺键"与"字面问号"同屏显示，issue 作者因此误判。修复应顺手让畸形条目 log 原始形状（§4.1）。

## 2. 双梯死法全链

### 2.1 外梯（step.py:992 `_get_action_with_retry`）——5 死

```
get_action → 归一化（缺键 dict 不被处置，见 §2.3）→ _is_valid_action=False
  → WARNING "LLM returned empty action, retrying with clarification..."
  → 原 messages + 泛化澄清文案 → 再吐同款缺键 dict
  → WARNING "still returned empty action after retry, using fallback done"
  → done(text="No action returned by LLM", success=False) → 任务死（2-3 步）
```

- task_108：step1 = 截图 10s 超时 → text-not-tool_use（directive 重试）→ `?` ×2 → 死。
- task_495：step1 首调即 `?`（9s 后）→ 澄清 → `?` → 死。
- task_782：step1 `?` 后澄清**救回**（吐 2 动作继续跑），step2 再 `?` ×2 → 死——同一任务内可恢复性与不可恢复性并存，纯看模型当次采样。

### 2.2 内梯（step.py:1152 `_validate_params_or_retry`）——2 死

- task_109：`read_grid` 参数多带 `read` 键 → 参数重试(1/2) → 重试响应退化成缺键 dict（"invalid action during param validation retry — clarifying (2/2)"）→ 再 `?` → 预算耗尽 fallback done。
- task_696：参数重试中 text-not-tool_use（get_action 内层 directive 重试）+ `?` 混合，同样预算耗尽死。

内梯预算 `_PARAM_VALIDATION_MAX_RETRIES = 2`（#176 P0-B 引入：一次 Invalid-params + 一次 Invalid-action 封顶）——**总 LLM 调用有界**的设计目标保留，但对视觉模型的间歇性形状退化，2 次太脆。

### 2.3 缺键 dict 为何裸奔到梯子（#173/#176 的策略边界）

`action_shape.py:71` `_has_invalid_name`：**`"name" not in action` → return False**（只管"键存在但无效"，注释明说缺键不算它管的类别）。于是单元素 live 列表里的缺键 dict：

- 不进 `honest_done_action()` 分支（那是 `name: null`/空串的归宿——**即时一次调用死**，比缺键死得还快，且无重试机会）；
- 只被无害化 params 后**原样保留** → 镜像过不了 `_is_valid_action` → 落澄清梯。

即：#173 把"键存在但无效"做成诚实终止、#176 把"未注册名"做成丢弃+澄清，**唯独"整键缺失"没有独立处置策略**——在文本时代它稀有（B/C 轮各 1-2 次）无妨；视觉模型把它放大一个数量级后，成了最大单项损失。

## 3. 触发条件与量化

### 3.1 页面聚集（本仓切片 7 死）

| 任务 | 死步 | 死前页面 snapshot_entries | 截图超时 | 梯 |
|---|---|---|---|---|
| 108 | 1 | 24671（sales/order 网格） | ✅ 10s | 外 |
| 200 | 1 | 24671（同上） | ✅ | 外 |
| 492 | 1 | 24757（同上） | — | 外 |
| 495 | 1 | 24757（同上） | — | 外 |
| 782 | 2 | 6925~7745 | — | 外 |
| 109 | 1 | （网格页） | — | 内 |
| 696 | 多步后 | （购物站） | — | 内 |

4/7 死在同一张**全场最大页**（同 URL，entries 恒 24671~24757）；C 轮文本口径同任务 108=1.0。触发画像 = 视觉模型 + 巨型 DOM 文本（±截图缺失/降采样图）→ 工具调用形状崩坏。这是**模型侧退化**（glm-5.3-flash flash 档在超长输入下 tool-call 格式不稳），仓库侧无法修模型，只能修梯子的韧性。

### 3.2 计数（本仓切片 vs issue 全量）

- 本仓切片（V_20260918 目录）：**19 行 `?` 动作 / 12 任务**；7 死（14 行）+ 5 救回（各 1 行）。
- issue 四段全量：50 行、47 进澄清、9-10 死。切片只覆盖一段，比例结构一致（单次重试多数可救、二连即死）。
- 时间横跨 09-17 11:04 → 09-19 00:53，全程间歇，非单点故障——与 issue 判断一致。

## 4. 修复方向评估

issue 提的三方向逐一评（编号沿用 issue）：

### 4.0 前置（新发现，issue 未列）：畸形条目日志改形状直出

`client.py:553` 对缺键/非 dict 条目改 log 原始形状（如 `repr(a)[:120]`，注意脱敏——值可能含敏感真值，name 缺失时 repr params 有泄漏风险，可只 log 键集合 + 类型）。本次"字面问号"误诊即源于此；不修则下次同类事故还会误读。低成本，建议随修复带上。

### 4.1 方向 1「识别无效 + 换参/降级重试」——**取降级，不取换参**

- **换采样参数（温度/种子）**：client 全程不设 temperature（#187 后连传都不传，服务端默认）。加"重试时升温"是在给 glm 端点的采样行为打盲钉——不可控且引入新方差，**不建议**。
- **澄清文案定向化**（低成本高杠杆）：`_INVALID_ACTION_CLARIFICATION`（step.py:72）现文案"You forgot to return an action"与实际错误（动作对象缺 `name` 键）**不匹配**——模型自认已返回动作，纠错信息为零，原样再吐是自然结果。改为形状定向（明示 `{"name": ..., "params": ...}` 要求 + 一个最小示例），内外梯共用。
- **降级去图重试**（对齐既有先例）：client.py:414 已有 fallback 切文本模型时 `_strip_image_blocks` 的降级先例。视觉梯子第二次重试前滤掉 image block（保留文本状态）——直接对症"巨型图+巨型 DOM 压垮工具调用格式"，且 C 轮证据表明纯文本口径同任务可通过。**建议作为第二次重试的固定动作**（不额外加开关）。

### 4.2 方向 2「死刑门槛 N≥4」——**采纳，按预算计而非固定次数**

现状：预算 30 步只用 2 步就死，fallback done 是"整任务级"死刑，而单次澄清救回率过半。建议：

- 外梯澄清重试 1 次 → **2 次**（第二次自动带去图+定向文案，即 4.1）；
- 内梯 `_PARAM_VALIDATION_MAX_RETRIES` 2 → 3（同理第二次起带降级）；
- 死刑仍保留（有界调用是 #176 的正确设计），但触发条件从"二连无效"变为"**定向+降级重试后仍无效**"。按 V 轮数据，二连 `?` 里相当部分在换文案/去图后会分化（782 的 step1 救回证明模型并非锁死）。
- 注意成本上界：每次重试是全上下文调用（视觉 ~10-50s），+1 次外梯 +1 次内梯的最坏增量 ≈ 100s/事故、19 行事故/轮——可接受。

### 4.3 方向 3「评测口径按事故定向重试入账」——evals 仓侧动作，不在本仓范围

同 #180 惯例处理即可；但注意：**修复（4.1+4.2）落地后 V 轮重跑应先于口径豁免**——否则无法验证修复效果。顺序：先修、真机重跑这 10 任务、剩余再谈口径。

### 4.4 不建议的方向

- **缺键 dict 改判 honest-done**（与 `name: null` 对齐）：等于把"间歇可救"降级成"必死"——本批 5 个救回案例全部走的是梯子，堵死梯子与数据相悖。
- **黑名单"?"等单字符**：建立在误诊上（§1），且下一个畸形形态不是问号。

## 5. 建议的修复组合（待确认后出 impl-plan）

1. `client.py` 畸形动作日志形状直出（脱敏）；
2. `_INVALID_ACTION_CLARIFICATION` 形状定向化（内外梯共用）；
3. 外梯/内梯各 +1 次重试预算，**第二次重试自动去图**（复用 `_strip_image_blocks`）；
4. 单测：注入"缺 name 键 dict"序列——单次救回 / 二连后第三次救回 / 三连仍死 fallback done 三态；去图重试路径断言（messages 无 image block）；
5. 真机验收：V 轮 10 任务定向重跑（evals 仓）。

以上 1-4 在本仓，5 在 evals 仓。
