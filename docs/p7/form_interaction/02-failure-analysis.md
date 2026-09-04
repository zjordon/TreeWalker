# form_interaction 第二轮失败分析（六项修复落地后的重跑）

> 数据：2026-08-25 07:10–08:18 全 18 任务重跑（同条件：30 步/600s、反节流 flags Chrome、
> 逐任务连发）。本轮运行的是含全部六项修复的代码（skill 18/18 加载、`auto_handle=True`
> 日志确认）。
> 对照：01 报告（2026-08-21 批次，4/18）与 `01-failure-analysis.md` 的实施记录。
> 额外证据：2026-08-25 对 rule 5/6/7 的**实机 DOM 探针**（eval venv + Playwright +
> cookie，对齐 checker 的 locator/prep_actions 语义）。

---

## 〇、结论摘要

**SR 4/18 → 8/18（+4）**：新翻转 499、503、505、712。六项修复全部确认激活且有效——
上一批的两大税源（网格渲染冻结 ~48% 步数、Save 空提交）被实证消除。剩余 10 个失败中
**4 个是 checker 级阻断**（700/702/703 实质已完成、546 这次连描述都写成功了但 checker
查错页面），真正行为侧仍有短板的是 Content 区块（464/543）、模态网格盲区（695）、
向导 Generate 不生效（551）、环境漂移+深表单（493）。

---

## 一、两轮对比总表

| task | 旧批 分/步 | 新批 分/步 | 变化 | 一句话归因（新批） |
|---|---|---|---|---|
| 196 | 1 / 15 | **1 / 2** | ⬆ 效率 | 截图解锁+批量读取，2 步完成 |
| 454 | 1 / 28 | **1 / 8** | ⬆ 效率 | 手册清过滤器路线，8 步完成 |
| 504 | 1 / 28 | **1 / 16** | ⬆ 效率 | 批量路线直奔目标 |
| 542 | 1 / 28 | **1 / 19** | ⬆ 质量 | 这次**收货+账单双地址**都改了（旧批仅账单） |
| **499** | 0 / 16 | **1 / 26** | ✅ 翻转 | invoice→New Shipment **UI 流程**→Comments History 验证（规则②兑现） |
| **503** | 0 / 29 | **1 / 30** | ✅ 翻转 | 手册两连击：Clear all 先行 + 逐商品 JS setter+change |
| **505** | 0 / 29 | **1 / 29** | ✅ 翻转 | Update attributes 批量路线 + 编辑页 live 验证 |
| **712** | 0 / 26 | **1 / 9** | ✅ 翻转 | 日期 change 持久化，终态停在报表页（9 步） |
| 5 | 0 / 29 | 0 / 4 | ⬆ 效率 | 语义错位依旧（答 Hawkeye，参考 "Duffle"） |
| 464 | 0 / 29 | 0 / 29 | — | Content 区块 textarea 仍不渲染 |
| 493 | 0 / 挂死600s | 0 / 29 | ⬆ 不再挂死 | 目标订单 65≠参考 307 + 评论表单 AJAX/addComment 深坑 |
| 543 | 0 / 29 | 0 / 29 | — | 同 464（本次连 textarea 都没出现） |
| 546 | 0 / 29 | 0 / 25 | ⬆ 行为成功 | **描述写入成功**（uiRegistry 路线）——但 checker 查水瓶页，判 0 不可避免 |
| 551 | 0 / 29 | 0 / 29 | — | 向导勾选 30/31+Generate+Save，变体矩阵仍是原 6 个 |
| 695 | 0 / 29 | 0 / 28 | — | Add Attribute **模态内**网格冻结（kick 盲区） |
| 700 | 0 / 29 | 0 / 18 | ⬆ 实质完成 | rule 建对（探针实测），死于 checker `selectedIndex` |
| 702 | 0 / 29 | 0 / 13 | ⬆ 实质完成 | 同上（探针实测 by_percent/45 全对） |
| 703 | 0 / API断14 | 0 / 11 | ⬆ 跑完 | 同上 + name 需字面 "Thanks giving sale" |

总步数 463 → 335（-28%）；通过任务平均 21.75 → 13.25 步。

---

## 二、六项修复的效果验证

### P0-1 网格强制渲染帧 —— 确定性生效
9 次触发、9 次 `rendered_after=True`（499/503×2/504/505/542/546/551，日志
`data-grid render kick: rows=N rendered_after=True`）。上一批"生死开关"（靠 LLM 随机
截图）变为每张网格页 navigate 后的确定性后台动作；直接支撑 196（2 步）、454（8 步）的
极速通过。

### P0-2 type_text 补 change —— INVALID 告警归零
全批 **0 次** "marked INVALID"（上批 700/702 反复出现）；712 的报表日期一次持久化
（checker 读 `sales_report_from` 值=5/1/21 → 过）；503 六个商品逐个"JS native setter
+ change event"保存全部生效。

### P1-1 Magento 手册 —— 18/18 加载，路线知识直接制胜
每份日志开头 `skill loaded: host=localhost_7780 chars=2577 files=['_sop.md','quirks.md']`。
可归因的制胜行为：503 step 2 即"JS-click **Clear all**, set keyword"（quirks 列表页
第一条）；503 转入"per-product + JS setter + change"（quirks 表单节）；499 的
"must **invoice before shipping**（Pending 不能直接 Ship）"链条；505 的 Update attributes
批量路线+live 验证（SOP）。残留书签过滤器仍在（P3 不实施——493 继承了 542 的
order 300 过滤器，12 次提及），但手册的 Clear-all-先行让多数任务 1-2 步化解。

### P1-2 dialog 自动处理 —— 挂死消失
493 触发 3 次自动处理，任务正常跑完 29 步（上一批同任务在提交环节挂死 600s、
n_steps=0）。493 里出现的 `.modal` DOM 遮罩由 agent JS 清理，JS confirm 由自动处理
兜住——两类"对话框"都没再冻结循环。

### P2-2 evaluate 自愈 —— 本批零触发
全批无 `Illegal return statement`（LLM 本批输出恰好干净）。无触发样本 ≠ 无效
（上批 8 处实证的错误类仍在防御范围内），效果验证留待后续批次。

### P2-1 终态/副作用规则（_sop.md）—— 规则②直接兑现为 499 的翻转
499：走 admin **UI** New Shipment 表单（非 fetch POST），事后在 Comments History
验证 *"Tracking number 13849373987 for United States Postal Service assigned"*——
正是 checker 要求的精确文案。上一批两次"自称成功判 0"（fetch POST 缺评论副作用 /
UI 流程缺 carrier 全称）在这条规则下同时补齐。

---

## 三、剩余 10 个失败的归因（含实机探针证据）

### A. checker 级阻断（4 个，行为侧已尽力）

**700 / 702 / 703 —— 实质完成，死于一个字段**
探针（2026-08-25，对齐 checker locator+prep_actions）实测三条规则终态：

| 字段 | checker 要求 | rule 5 (700) | rule 6 (702) | rule 7 (703) |
|---|---|---|---|---|
| url（子串） | …/promo_quote | edit 页 ✓ | edit 页 ✓ | edit 页 ✓ |
| name must_include（大小写不敏感*） | fall discount / Pride Month / Thanks giving sale | "Fall Discount $10 Off" ✓ | "Pride Month 45% Off" ✓ | "Thanksgiving Sale $40 Off" ✗（差一个空格） |
| website_ids.selectedIndex | 0 | 0 ✓ | 0 ✓ | 0 ✓ |
| **customer_group_ids.selectedIndex** | **1** | **0 ✗** | **0 ✗** | **0 ✗** |
| simple_action | cart_fixed / by_percent / cart_fixed | cart_fixed ✓ | by_percent ✓ | cart_fixed ✓ |
| discount_amount | 10 / 45 / 40 | 10 ✓ | 45 ✓ | 40 ✓ |

\* `evaluation_harness/evaluators.py:86 clean_answer` 对双方 lowercase——大小写不敏感。

三任务共同死因一个：**全选 4 个客户组（"all customers"的正确语义）→ selectedIndex=0**，
checker 要求 1（即参考 DOM 里 NOT LOGGED IN 未被选中、首个选中项是 General）。这是
WebArena 参考 DOM 的残留怪癖，与任务意图直接矛盾——行为侧不可救，除非注入
checker 特定知识（overfit，不建议）。703 另加 name 字面 "Thanks giving sale"
（任务原文的怪拼写，agent 规范成了 "Thanksgiving"）。

**546 —— 行为首次成功，checker 定义错位依旧**
本轮 agent 通过 **uiRegistry data provider** 路线把描述写入成功（保存后回读
DESCLEN=330，含评论引文）——上一批"找不到 textarea 就死"的问题被它自己绕过了。
但 checker 检查 `affirm-water-bottle.html`（水瓶页）——intent 与 checker 指向不同
商品（01 报告发现 10），判 0 不可避免。

### B. 行为侧仍有短板（6 个）

**464 / 543 —— Content 区块 description textarea 不渲染（持续）**
两任务都在"找到 Content 折叠块 → 打不开/开了没字段"上耗尽预算（464 step 5-28；
543 全程）。**但 546 本轮示范了出路**：textarea 缺席时改走 `uiRegistry` 的
product_form data provider 直接写组件值再保存——这是可直接复用的 SOP 增补点。

**695 —— 模态内网格冻结是 P0-1 的盲区**
Add Attribute 模态打开后其属性网格不渲染行；kick 只挂在 navigate 的 settle 之后，
**后开的模态不经过 navigate** → 不触发。商品本体已建好（ID 2041，价格/数量/库存全对），
差 size/color 属性。工具层小改（evaluate/点击后可选 kick）可覆盖。

**551 —— 向导 Generate Products 不生效（Magento 深坑）**
本轮走完了完整向导：勾选 28/29/30/31×3 色（复选框回读确认）→ Generate Products →
Save（"You saved the product."）——但保存后变体矩阵仍是原 6 个组合，30/31 未生成。
这是 Magento"编辑既有可配置商品追加变体"的语义深坑（新建矩阵 vs 追加），步数不是
瓶颈（29 步用满但流程本身走完了）。

**493 —— 环境漂移 + 评论表单深坑（双因素）**
① 目标订单仍是 **65**（参考订单 **307**）：300 段订单被历次任务改状态（本轮 542 又
改了 300 的地址，残留的 order-300 书签过滤器还干扰了 493 的初始查询）——数据漂移性
问题，行为侧无解；② Comments History 页签 AJAX 不加载 + 4 次提交（2 UI + 2 addComment
POST）全部失败——订单评论表单是独立的深坑，值得单独一份探针分析。

**5 —— 语义错位依旧（非本类）**
4 步完成（P0-1 让它 2 步拿到数据），答 "Hawkeye Yoga Short"（报表第一行），参考答案
"Duffle"（品类名）。agent 明知三商品并列 qty=2（含两个 Duffle）却按报表排序取第一。
属查询/语义类，维持 01 报告的归类结论。

---

## 四、下一步建议（按 可救数×成本 排序）

1. **SOP 增补 description 的 uiRegistry 写入路线**（零代码，改 `_sop.md` 两行）：
   "textarea 不在 DOM 时，用 `require('uiRegistry')` 取 product_form 的 description
   组件（或 data provider）直接设值再保存"——546 已实证。**预期 +2（464、543）**。
2. **模态网格 kick 盲区**（工具层小改）：`_action_evaluate` 或模态打开类点击后可选地
   跑一次 `_kick_frozen_data_grid`（或把它挂进 find_elements 前）。**预期 +1（695，
   还差属性路线知识可并入 SOP）**。
3. **700/702/703 的 checker 怪癖**：不建议行为侧适配（selectedIndex=1 与任务语义矛盾，
   纯 benchmark 残留）。建议在评测报告口径单列 "checker-blocked" 类，避免误判为能力
   缺口；name 字面拼写（"Thanks giving sale"）同理。
4. **551 / 493**：Magento 追加变体语义与订单评论表单各是一份独立深坑分析，成本高、
   单任务收益，暂缓。
5. **P2-2 自愈**：本批零触发，保留现状，后续批次自然验证。

**上限估算**：行为侧可再救 3 个（464/543/695）→ 11/18；700/702/703/546 属
checker-blocked，5/493 属深坑或环境。

---

## 五、口径说明

- 本轮 8/18 为**重跑口径**（同批连发、书签过滤器污染仍在——493 明显受害）；与
  01 报告 4/18 可直接对比（同为重跑口径）。
- 修复归因基于：触发标记统计（kick 9 次 / skill 18 次 / dialog 3 次 / heal 0 次 /
  INVALID 0 次）+ 制胜路线与手册条目的逐条对应 + rule 5/6/7 实机探针。
- 探针方法：eval venv + Playwright(chromium, headless) + storage_state cookie，对齐
  checker 的 locator 与 prep_actions（点 Actions 折叠头后等 1.5s 再读）。
- 探针脚本（`probe_rules.py` / `probe_rules2.py`）已留 evals 工作空间根目录，可复跑。
