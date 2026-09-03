# form_interaction 类失败分析报告

> 数据基础：新基线（SR 52.2%，反节流 flags + 数据重置）88 个失败任务中 GLM 自述归入 form_interaction 的 18 个任务，
> 每个任务一条 2026-08-21 与评测同条件重跑的完整轨迹（`results/logs/form_interaction/`），
> 对照 `results/shopping_admin_antithrottle.json`（新基线原始判分）与
> `webarena_repo/config_files/<task_id>.json`（WebArena checker 定义）。
> 重跑结果概览：**4 个翻转成功**（196/454/504/542）、**11 个复败**（464/493/503/505/543/546/551/695/700/702/703）、
> **1 个"agent 自称成功+内部 Judge 判 SUCCESS 但 checker 判 0"**（499）、**2 个误归类**（5/712）。

---

## 一、类别校验结论

**有效任务 14 个 / 部分有效 1 个 / 归类存疑 3 个**（18 个中）。

| task_id | 重跑结果 | 校验结论 |
|---|---|---|
| 503/504/505 | 失败/成功/失败 | ✅ 有效：批量库存表单（bulk Update Attributes 流程） |
| 543/546/464 | 失败×3 | ✅ 有效：商品描述编辑（评论引用→description 字段→保存） |
| 700/702/703 | 失败×3 | ✅ 有效：Cart/Catalog Price Rule 新建表单 |
| 551 | 失败 | ✅ 有效：可配置商品变体向导（Create Configurations） |
| 695 | 失败 | ✅ 有效：新建商品表单 |
| 542/454 | 成功（翻转） | ✅ 有效：订单地址表单 / 商品状态表单 |
| 493 | 超时挂死 | ✅ 有效（dialog 阻塞代表样本）；但另有目标订单漂移问题（见发现 11） |
| 499 | checker 判 0 | ✅ 有效（订单出货+追踪号表单）；失败机制是"绕过 UI 提交跳过了 checker 要求的副作用"（见发现 9） |
| 712 | 失败 | ⚠️ 部分有效：报表日期表单确实没把日期持久化到终态页面（checker 要求 `sales_report_from` 值=`5/1/21`），但叠加"报表数据聚合为空"的环境因素 |
| **5** | 失败 | ❌ **名不副实**：查询类任务（"What is the top-1 best-selling product type"）。checker 参考答案是 **"Duffle"**（exact_match），agent 答 "simple"——它把 "product type" 理解成 product_type 属性，而任务要的是商品品类名。失败本质是**语义解读错误**（数据解读类），轨迹中它已找到 Overnight Duffle/Impulse Duffle 销量并列第一，却没认出这就是答案。应归 data_interpretation |
| **196** | 成功（翻转） | ❌ 存疑：比较类查询任务，新基线失败的直接原因确是筛选面板交互（filter 打不开），归 form_interaction 勉强成立，但任务本质是数据聚合查询 |

另有一个**超然于 agent 行为的发现**：546（Lucia Cross-Fit Bra 描述更新）的 checker 检查的是
`http://localhost:7780/admin/../affirm-water-bottle.html`（**保温水瓶**商品页）的描述需包含
"Wide mouth opening makes it easy to clean"——intent 与 checker 指向**不同商品**，属 WebArena 任务定义错位。
该任务按 intent 正确执行反而必然判 0（见发现 10）。

---

## 二、真实失败机制

跨 14 个有效任务，失败在轨迹层面收敛为**四条主链**（按侵蚀步数排序）：

### 链 A：「找不到目标」——KO 网格行渲染冻结 + 残留过滤器（吞掉 ~48% 步数）

Magento admin 列表页 shell 渲染、记录数可见（"308 records found"），但 `tbody tr` 是**空的 Knockout 模板**，
innerText/textContent 全空；同时上一个任务残留的服务器端书签过滤器使新任务的搜索被覆盖。
agent 平均花 10-22 步定位一个商品/订单，有效路径只有三条（agent 靠运气撞见）：
① **截图动作会强制网格行渲染**（7 例铁证，见发现 1）；② `POST /admin/mui/index/render`（带 form_key）直接拿 JSON（464 step 10-11 撞见）；
③ `require('uiRegistry')` 读写 `product_listing_data_source`（505 step 13-20 撞见）。

### 链 B：「提交不进」——原生输入不触发 change 事件，Save 提交空值

TreeWalker `input_text` 打字后只派发 `InputEvent('input')`（`_trigger_framework_events` 有意省略 change，
session.py:2787 注释自证），而 KO 的 `value` 绑定**默认监听 `change`**。后果链：打字 → 字段 DOM 值正确但
KO 视图模型为空 → Save POST 提交空必填字段 → 验证错误页/表单清空 → 此后 Save 处理器静默 → 十几步保存重试。
对照实验在 695 内部自然完成：原生打字+Save=表单清空；JS native setter（带 input+change 事件）重填+Save=保存成功。

### 链 C：「点不动 / 提交死」——未水合页面上的原生点击静默 + 提交通道耗尽

网格未渲染时 Clear all/Search/Filters/Actions 按钮点击全部"no visible effect"（截图强制渲染后同样动作一次生效，
504 step 1-4 vs step 17 对照）；表单页 Save/Submit 原生点击静默（499 Submit Shipment、700/702 Save、542 Save Order Address），
agent 逐级降级 JS click → jQuery trigger → form.submit()/requestSubmit → fetch POST，每级 1-2 步，常在第三级才通。

### 链 D：「差一步」——预算被 A/B/C 吃光，死在最后提交/验证前

11 个复败任务里 6 个（543/551/505/695/700/702）到达了"目标字段已填好/已勾选"状态，因步数耗尽没完成最后的
Save/Apply/验证。翻转组（504/542/454/196）证明同类任务 30 步内本可完成——成败分野就是摩擦步数是否超预算。

---

## 三、类内分化

**近成功组**（差 ≤3 步，6 个）：543（textarea 已填好，差 1 步 Save）、551（向导 30/31 已勾选，差 1-2 步 Apply+Save，
且最后一步被自己的 JS 语法错误烧掉）、505（7 个商品已选中，Actions 菜单打不开，差 ~3 步）、695（商品已保存，
Customizable Options 持久性未验证）、700/702（所有字段已填，Save 通道死）。共同特征：**卡在提交/持久化环节（链 B/C）**。

**远失败组**（中段即死，3 个）：464（Content 区块的 description textarea 不在 DOM，JS 点击区块头也不展开）、
546（同样无 textarea + 该产品无正面评论——但 checker 本身错位，救不救都判 0）、503（不知道
Update attributes→Advanced Inventory→Stock Availability 的批量路线，退到"逐个编辑"死在行提取）。
共同特征：**卡在定位/字段发现环节（链 A），或路线知识缺失**。

**分界线**：不在任务复杂度，而在①是否在预算内到达"目标字段可操作"（定位摩擦），②到达后提交通道是否有效（change 事件/兜底提交）。
翻转组 4 个任务是随机过程把这些变量摇对了的样本：454/196 都是在第 12/22 步靠**截图**解锁网格后直奔目标；
504 靠 Memory 里自带 Update attributes 路线知识；542 第三次重填后走通了 JS 路线。

**步数效率**（11 个复败任务 ~310 步粗分）：定位/过滤纠缠 ~48%、保存/提交重试 ~19%、有效表单操作 ~23%、
观察/等待/截图诊断 ~10%。"一步能做完却用十步"的实锤：
- 543 用 **16 步**清一个残留过滤器（"Diana Tights"）；
- 700 用 **13 步**点一个 Save（step 16-28）；
- 503 用 **9 步**试图读出已筛出的 6 行数据；
- 712 用 **4+ 次**点击同一个 "Show Report"。

---

## 四、发现清单

### 发现 1：截图动作强制 Knockout 网格行渲染——列表页生死开关
- **证据**：
  - 454 step 22-23：`screenshot(full_page)` → step 23 Eval 原文 *"Success — screenshot triggered grid render; found target: 'Ryker LumaTech™ Tee (Crew-neck)', ID 478"*
  - 196 step 12-13：step 12 Memory *"Data rows render as empty tr (ko fastForEach)"* + screenshot → step 13 Eval *"Rows now render with cell text (screenshot step succeeded)"*
  - 493 step 11-12：screenshot（snapshot_entries 5618→35858）→ step 12 Eval *"Rows now render in DOM. Grace Nguyen's most recent Pending order is #000000065"*
  - 543 step 13-14：screenshot → *"Rows now visible but show Diana Tights products"*；546 step 20-21：screenshot → *"Success — rows rendered"*；551 step 13-14：screenshot → *"DOM now shows rendered grid rows"*；504 step 15-17：screenshot 前 Search 点击 4 次无效，截图后同样 input+click 一次生效；695 step 12-13：模态内属性网格同理
  - **反例对照**：464（grid 阶段从未截图，行 3 分钟内始终为空，靠 mui POST 逃出）、503（全程未截图，行提取 9 步全空而死）
- **机制**（2026-08-24 修正版）：**相关性确凿、机制归因中等置信**。窗口不可见（最小化/被完全遮挡）时 Chrome 不为该窗口产帧（rAF/布局/合成停摆），`Page.captureScreenshot` 走强制产一帧的通道，一帧过后 KO 网格行立即可读。`wait_for_page_settle` 只等 requirejs 模块数稳定（日志 `stage: 'requirejs'`），不等网格数据行。三点校准：
  1. ⚠️ 初版报告曾把 *"Element backendNodeId=… is occluded at (x,y)"* 日志当窗口遮挡佐证——**误用**：那是 TreeWalker 元素级 hit-test 遮挡检测（`session.py:2338 _is_element_occluded`，按钮被 sticky 头部等页内元素盖住），与窗口可见性无关，撤回。
  2. **端口错位事实（2026-08-24 核查）**：重跑日志全部连 `ws://localhost:9223`，而带三面反节流 flags（`--disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding`）的 Chrome 由 `run_task.ps1:73-81`/`run_category.ps1:187-193` 启动在 **9222**；9223 的标准启动命令（`docs/p6/03-web-e2e-runbook.md:22-26`）**不带任何反节流 flags**。跑评测的 9223 Chrome 很可能 flags 缺席（回查命令行可证）。另：即便 flags 在场，三面 flags 管的是 timer 节流/进程降级/遮挡致隐藏，**管不住"最小化窗口无表面可画→不产帧"**（合成器结构性行为；Windows 遮挡判定还需 `--disable-features=CalculateNativeWinOcclusion` 配合，见 docs/p6/09:17）。
  3. **同构先例**：`docs/p6/10-livestream-viewport-retrospective.md:32`（2026-08-14）在同一只 9223 Chrome 实测："窗口被最小化/遮挡时 Chrome 不产帧；而 Page.captureScreenshot 无此限制……用户把窗口拉到前台后帧即正常"。
- **待验证**：下次重跑时 ① 查 9223 实例命令行（`Get-CimInstance Win32_Process -Filter "name='chrome.exe'"`）；② 空行现场 evaluate `await new Promise(r=>requestAnimationFrame(()=>r('ok')))`（带 timeout）——挂起即坐实帧停摆；③ 窗口可见 vs 最小化 A/B 对比空行频率。
- **置信度**：相关性=高（7 正例 + 2 反例，454 的 LLM 自己都得出了同样结论）；机制=中（"flags 缺席 / 最小化 / 读取依赖布局"三解释尚未区分，见待验证）

### 发现 2：任务间服务器端书签过滤器污染链
- **证据**（时间戳连续、同一浏览器会话）：
  - 503（12:03-12:08 搜 "Rocco Gym Tank"）→ 504 step 1（12:08 起）Eval：*"keyword filter shows 'Rocco Gym Tank' and 0 records"*
  - 504（搜 "Selene Yoga Hoodie"）→ 505 step 1 Eval：*"grid shows a leftover keyword filter 'Selene Yoga Hoodie'"*
  - 546（12:40-12:46 搜 "Lucia Cross-Fit Bra"）→ 551 step 1（12:46 起）Eval：*"active keyword filter shows 'Lucia Cross-Fit Bra'"*
  - 454（搜 "Ryker Tee"）→ 464 step 1 Eval：*"shows filter 'Name: Ryker', not Antonia"*；454 自己继承 "Name: Cora"
- **机制**：Magento 把 admin 用户每个列表页的"当前视图/过滤器"存为服务端 bookmark，跨任务、跨会话持久；新任务的 URL 过滤参数会被它覆盖（543 step 5：*"URL param approach didn't override the stored bookmark filter"*）。清它的按钮（Clear all）在网格未渲染时又点不动（链 C），形成 5-16 步的泥潭。
- **置信度**：高

### 发现 3：原生 input_text 缺 change 事件 → KO 表单 Save 提交空值
- **证据**：
  - 695 step 25-26：step 25 原生填完全部字段+点 Save → step 26 Eval：*"Save failed — page reloaded as blank New Product form with all fields empty ('This is a required field' errors). All entered data was lost"*；step 26 改 JS native setter（input+change）重填 → step 27 Save 成功 *"You saved the product."*（id 2048）
  - 700 step 16→19：Save 提交后 step 19 Eval：*"Save click actually submitted and reloaded the form with validation errors (name field was empty — values were lost)"*；重打字仍被标 INVALID（step 19 ⚠️ *"field is marked INVALID by the page validator (msg:This is a required field.)"*）
  - 702 step 20-27：Save 无效；step 22 两个字段打字后均 ⚠️ INVALID；step 27 改 `send_keys`+evaluate 回读才见值
  - 542 step 13/15/19 同一地址填了三遍（*"Typed new street/city/zip via native input_text, but after clicking Save the DOM shows old values again"*）
  - **代码事实**：`session.py:2764 _trigger_framework_events` 只派发 `InputEvent('input')`（2787 行注释 *"intentionally omits it [change]"*）；`_force_set_value`（session.py:2479）**派发 input+change**——恰是 JS 路线能持久化的差异。KO `value` 绑定默认监听 `change`。
- **机制**：打字路径（CDP 逐字符 + 仅 input 事件）不更新 KO observable → 保存时序列化出空值 → 验证失败/表单重置 → Save 处理器进入静默状态。
- **置信度**：高（现象 4 任务复现 + 代码级因果链；change 与 KO 绑定的微观细节标注为中高——也不排除部分场景是残留 `mage-error` class 导致的假 INVALID）

### 发现 4：Save/Submit 原生点击静默，兜底降级梯子耗步
- **证据**：499 step 5-8（Submit Shipment 原生 click / JS submit / requestSubmit 全静默，step 9 fetch POST 才建单成功）；700 step 16-22（Save 原生/JQuery click 全静默）；505 step 22-24（Actions 菜单 JS click 打不开）；503 step 3/5/9（Clear all、Search 点了 4 次无效）
- **机制**：两类成因——①页面未水合/未渲染（配合发现 1，截图后同按钮立即有效）；②验证状态已毒化后的 KO 表单（配合发现 3）。agent 的降级梯子（原生→JS click→jQuery→submit/requestSubmit→fetch POST）方向正确但每次降级烧 1-2 步。
- **置信度**：高

### 发现 5：JS dialog 无自动处理 → 整个 agent 循环挂死
- **证据**：493 step 20 点 Submit Comment（Evaluate 返回 "page shows Please wait"）→ step 21 `wait 4s` 完成于 19:46:46 → **此后 3 分 44 秒零日志**（连 step 22 的 DOM snapshot 都没开始）→ 19:50:30 `task 493 超时（600s），跳过`，json 记 n_steps=0
- **机制**：提交触发的 JS 确认框阻塞页面 JS 执行，`dom_snapshot.collector` 的 `Runtime.evaluate` 挂起，循环冻死在步间。代码事实：`session.py:1681 _on_javascript_dialog` 只把事件**记录**进 `_recent_events`，从未调用 `Page.handleJavaScriptDialog`——对话框永远没人关。
- **置信度**：高（用户先验已定位此型；本轨迹把冻结点钉在提交后的 snapshot 阶段）

### 发现 6：LLM 手写 JS 语法错误系统性烧步 + evaluate 工具裸抛异常
- **证据**：`Illegal return statement`（顶层 return 未包 IIFE）：493 step 2、505 step 2、551 step 2、542 step 3、712 step 2、702 step 3、5 step 6；`Missing catch or finally after try`：504 step 13、505 step 14；`Unexpected end of input`（长代码截断）：551 step 27（**该任务差的就是这一步**）、5 step 11。另 703 step 8：`evaluate` 缺 `code` 参数在 `actions.py:2315 params["code"]` 抛 KeyError traceback（工具层 bug）
- **机制**：每任务平均 ~1 步浪费在 LLM 代码质量上；关键收尾步撞上即致命（551）。
- **置信度**：高

### 发现 7：领域路线知识缺失直接决定同款任务成败
- **证据**：
  - 503（败）step 18 Eval 明明列出了菜单选项 *"Actions menu opened showing: Delete, Change status, Update attributes, …"* 却退到逐个编辑死路；504（成）step 8 Memory 一开始就带着 *"bulk update qty to 0 / stock status via Actions → 'Update Attributes' → stock"* 路线
  - 700/702 两轮四个 run 全部把 Cart Price Rule 的固定折扣写成 `by_fixed`——**sales_rule 的合法值是 `cart_fixed`**（checker：700 要求 `simple_action=cart_fixed`，702 要求 `by_percent`），`by_fixed` 是 catalog rule 的值域。错误码值参与提交，Save/POST 失败与此叠加
  - 695 的 checker 要求 `product[size]=179`、`product[color]=60`（**商品属性**下拉的 option id）+ 属性集含 "bottom"；agent 走了 Customizable Options（自定义选项）路线——方向性错位，且 Add Attribute 模态里搜 "size" 得 0 条（又一处过滤器失效）
- **机制**：GLM 对 Magento admin 的功能路线（bulk 操作、规则动作码、属性 vs 自定义选项）没有可靠先验，全靠页面探索；探索又踩在链 A/B/C 的摩擦上。
- **置信度**：高（checker 交叉验证）

### 发现 8：checker 形态是"终态页面 url_match + 字段回读"——保存后跳走 = 白干
- **证据**（`webarena_repo/config_files/*.json`）：700/702/695/712 的 eval 均含 `url_match`（`url: last`）+ 在**最后一页**上 `querySelector('[name=…]').value` 断言。新基线 700 自述 *"You saved the rule"（rule ID 5）*、703 自述 rule id 7 已建——但它们随后导航去规则列表验证，终态页不是编辑页 → url_match 与字段断言全挂。重跑 700 则根本没保存成功。
- **机制**：agent 的"保存后去列表页确认"直觉与 checker 的"终态=编辑页+字段回读"模型相反。
- **置信度**：高（对 700/703 基线失败的重新解释；重跑轨迹内无法直接看到基线终态，此条对基线的归因标注为中高）

### 发现 9：绕过 UI 的提交会跳过 checker 要的副作用（499 两轮皆 0 的真因）
- **证据**：499 checker 要求订单 304 的 commentsHistory 页包含 *"Tracking number 13849373987 for United States Postal Service assigned"*。重跑 step 5-8 原生提交全静默 → step 9 **fetch POST** 建 shipment 成功，shipment 视图页验证 Carrier/Title/Number 全对、内部 Judge 判 SUCCESS → checker 仍 0：fetch POST 只建了 shipment+track，**不生成那条订单评论**。新基线走了 UI 流程有评论，但 carrier 用了 "Custom Value"，评论文案缺 "United States Postal Service" 全称，同样 0。
- **机制**：两条路各缺半边——UI 流程产评论但 carrier 全称没对上；fetch POST carrier 对但不产评论。
- **置信度**：高（checker 定义 + 两轮轨迹对照）

### 发现 10：546 的 WebArena 任务定义错位（intent 与 checker 指向不同商品）
- **证据**：`config_files/546.json` 的 program_html 检查 `localhost:7780/affirm-water-bottle.html` 的描述须含 *"Wide mouth opening makes it easy to clean"*（水瓶评论），而 intent 是 Lucia Cross-Fit Bra。
- **机制**：无论 agent 做对做错都判 0。同类风险提示：464/543 的 checker 是正常对应的（antonia-racer-tank.html / bella-tank.html）。
- **置信度**：高（定义文件直证）

### 发现 11：493 的双层失败——dialog 之外还有目标订单漂移
- **证据**：493 checker 断言 **order_id=307** 的评论首条精确等于指定文案；轨迹里 agent 在全量网格（搜索未生效）里按日期找到的"最新 Pending"是 **#000000065**。重跑环境中 300 段订单已被此前任务改状态（499 给 304 出过货、542 改过 300 地址），307 很可能不再是 Pending，"最新 pending"判定随之漂移。
- **机制**：环境漂移 + 网格搜索失效（链 A）叠加。dialog 修复是必要非充分。
- **置信度**：中（307 在重跑时刻的状态无直接日志证据，为推断）

### 发现 12：LLM API 连续连接失败会终止整个任务（703 的 n_steps=14 之谜）
- **证据**：703 step 14-18 连续 5 次 *"Step N failed: Connection error / Request timed out"*（anthropic 客户端重试日志）→ *"Max consecutive failures (5) reached"*，循环退出，json 记 `n_steps=14, is_done=false, error=null`。
- **机制**：infra 抖动被计入 agent 的 max_failures，任务被误杀。此为"无 dialog 特征的纯挂死"之一型——不是挂死，是被终止。
- **置信度**：高

### 发现 13：步数分配——摩擦占比
- **证据**：11 个复败任务 ~310 步粗分：定位/过滤纠缠 ~48%、保存/提交重试 ~19%、有效表单操作 ~23%、观察/等待/诊断 ~10%（逐任务步数归类见第三节实例）。
- **机制**：链 A 是最大税源；预算警告（step.py `_inject_budget_warning`，75% 起注入）存在但只提醒不改变策略。
- **置信度**：中高（归类基于人工标记，边界步有 ±10% 弹性）

---

## 五、优化建议

> 落点均为当前仓库实际模块。预期影响按"本类 18 个任务（14 有效）中的重跑复败 11 个 + 新基线同型失败"克制估算，
> 各建议间存在重叠（同一任务常被多条链卡住），合并上限见第六节。

### 建议 1：settle 后强制一帧合成，解锁 KO 网格行渲染
- **改动层**：工具层 — `src/tree_walker/browser/session.py` 的 `wait_for_page_settle()`（被 `tools/actions.py:576 _action_navigate` 调用）
- **具体改动**：settle 完成后若页面存在 `.admin__data-grid`（或通用：`table tbody tr` 全空 innerText 且页面声明了 N records），调用一次 `Page.captureScreenshot`（`{clip:{x:0,y:0,width:1,height:1,scale:1}}`，丢弃返回值，开销 <100ms）强制合成，等 300ms 后复查行文本；仍为空再放行。等价于把 7 个任务里 agent 靠运气发现的"screenshot kick"产品化。
- **预期影响**：直接救回 543、551（两者只差最后 1-2 步，省下的 10-16 步绰绰有余）；505 大概率；464/503/546 的定位阶段同步受益（但各有第二短板）。估计 **+2~4**
- **验证方法**：`run_category.ps1 -Category form_interaction -Force` 重收对比；重点看各任务"定位目标耗时步数"前后的分布
- **风险**：每页多一次截图 CDP 调用（毫秒级）；对无网格页面应短路跳过，避免拖慢普通站点

### 建议 2：type_text 补发 change（+blur）事件
- **改动层**：工具层 — `session.py:2414 type_text()` → `session.py:2764 _trigger_framework_events()`
- **具体改动**：`_trigger_framework_events` 在 InputEvent 之后增加 `el.dispatchEvent(new Event('change', {bubbles:true}))` 与 `el.blur()`（或仅 change——blur 可能触发下拉收起等副作用，先只加 change，回归后再评估 blur）。`_action_input_text`（actions.py:1087）在派发后复查 `_read_validation_state`，INVALID 告警随之更新。这与 `_force_set_value` 已有行为（input+change）对齐，消除同一工具内的双标准。
- **预期影响**：700/702 的第一轮 Save 不再空提交（配合建议 4 的 cart_fixed 知识后可救）；695 第一次 Save 不清空（省 2 步且保住 Customizable Options——尽管 695 还需属性路线）；542 少两轮重填。估计 **+2~3**
- **验证方法**：同上重跑；单测在 `tests/` 加一个 KO 风格夹具（监听 change 才更新模型的 input）断言 type_text 后模型值更新
- **风险**：个别页面 change 事件触发提交（如搜索框回车即查）——先在内部 evaluate 通道回归 B 站/抖音用例

### 建议 3：JS dialog 自动处理，终结挂死
- **改动层**：工具层 — `session.py:1681 _on_javascript_dialog`
- **具体改动**：回调内（或队列消费侧）自动 `Page.handleJavaScriptDialog`：默认 `accept=False`（dismiss 最安全），`beforeunload` 用 `accept=true`；把 `{type, message, 处理方式}` 写入 `record_event` 供 LLM 感知。加 config 开关（`browser.auto_dismiss_dialog: true`）保持产品侧可控。注意 CDP 需先 `Page.enable` 且 handle 调用要在事件回调上下文外排队执行（避免 ws 读线程直接 await）。
- **预期影响**：493 不再 600s 挂死（但按发现 11，还需数据不漂移才能得分）；更重要的是消除操作类任务的整类死亡模式。
- **验证方法**：构造 `evaluate('confirm("x")')` 的冒烟用例；重跑 493 观察是否走完
- **风险**：自动 dismiss confirm 可能改变页面语义（如 Magento 删除确认被跳过=误删）——eval 场景可接受，产品默认需配置

### 建议 4：Magento admin 领域手册进 domain-skills（零代码）
- **改动层**：prompt 层 — `domain-skills/localhost:7780/quirks.md + _sop.md`，开 `config.py:87 enable_skill_injection`（P1 机制现成，`system_prompt.py:163` 按 host 注入）
- **具体改动**（quirks.md 骨架，逐条对应本报告发现）：
  ```
  ## 列表页（product_listing / sales_order_grid 等）
  - 首次进入先点 Clear all / Default View 清残留书签过滤器，再搜索；URL 过滤参数会被书签覆盖
  - 若 "N records found" 可见但行文本为空：立即截图一次强制渲染；或 POST /admin/mui/index/render/
    (form_key + namespace=product_listing + search=…) 直接拿 JSON；或 require('uiRegistry')
    读 <ns>_data_source 的 data.items
  ## 表单提交
  - KO 表单（商品/价格规则）填写后必须让字段收到 change 事件：input_text 打字后若字段带 mage-error，
    用 evaluate 走 native setter + input/change 重设再提交；Save 点击无效时降级顺序：
    el.click() → jQuery(el).trigger('click') → form.requestSubmit() → fetch POST（注意 fetch POST 可能跳过订单评论等副作用）
  ## 已知路线
  - 批量改库存：选中全部 → Actions > Update attributes > Advanced Inventory →
    Stock Availability=Out of Stock（勾 Change）→ Save（"Message is added to queue" 即成功）
  - Cart Price Rule 固定金额折扣 simple_action=cart_fixed；百分比=by_percent（by_fixed/by_percent 是 catalog rule 的值）
  - 价格规则表单 Actions 区字段隐藏在 DOM（simple_action/discount_amount 可直接 JS 设值），先存基础信息再二轮补 Actions
  - 商品描述 textarea 在 Content 区块懒加载；找不到时先点开 Content 区块再 querySelector('textarea[name="product[description]"]')
  - 修改类任务完成后：停留在编辑页并回读字段值验证，不要导航去列表页
  ```
- **预期影响**：503（路线）、700/702（cart_fixed+两段式+终态停留）、464（Content 区块+mui recipe 提前）。估计 **+2~3**
- **验证方法**：同上重跑；注意 domain-skills 目前默认关，评测配置需显式开启（评测侧 smoke_test 配置）
- **风险**：host 匹配需覆盖带端口的 localhost:7780（`url_utils` 的 host 归一化需确认）；手册过长会挤占上下文，控制在 ~40 行

### 建议 5：evaluate 代码自愈 + 参数防御
- **改动层**：工具层 — `tools/actions.py:2314 _action_evaluate`
- **具体改动**：① 顶层 `Illegal return statement` 时自动包 `(()=>{ ... })()` 重试一次；② `Missing catch or finally` 时自动补 `catch(e){return 'Err:'+e.message}`；③ `params.get("code")` 缺失返回友好 ActionResult 而非 KeyError traceback（703 step 8 的 bug）；④ LLM 长代码截断（Unexpected end of input）时在报错里提示"代码疑似被截断，请拆短"；⑤ 契约修正（2026-08-23 复盘 batch1 task-505 step 12 补充）：`models.py:397` 的 `code` 描述只把 IIFE 称为 "Best practice"，未告知"代码按脚本体执行、顶层 return 是语法错误"；而同工具 `args`/`elements` 模式（models.py:433-446）的官方示例是裸 `return document.querySelector(a[0]).disabled`——那个模式下代码被包进 `function(...a){...}`，裸 return 合法且必须。**同一工具两种模式对裸 return 的约定相反且 schema 不提示**，是裸 return 滑动的结构性诱因。修法：描述中明说两种模式的差异（Playwright 实测同样拒绝裸 return 字符串，"行业惯例写 return"不成立——惯例是调用方自己包函数）。
- **预期影响**：每任务省 ~1 步；551 的死因正是最后一步语法错误（与建议 1 叠加后 551 高概率回归）。估计 **+0~1**
- **验证方法**：单测覆盖三类畸形代码的重试路径；重跑统计 SyntaxError 步数
- **风险**：自动包裹可能改变语义（极罕见），重试仅一次并注明

### 建议 6：评测基建——任务间重置书签过滤器 + API 断连不计入失败上限
- **改动层**：agent 策略层/评测层 — evals workspace 的 runner（`run_task.ps1`/smoke_test）与 `agent/step.py:174` 的 max_failures 语义
- **具体改动**：① 每个任务开始前，runner 用注入的 admin session 调一次
   `POST /admin/admin/ui_bookmark/` 的清理（或最简单：导航到目标列表页执行一次 JS
   `[...document.querySelectorAll('button')].find(b=>/clear all/i.test(b.textContent))?.click()`）；
   ② `step.py` 把 LLM 客户端 `Connection error/timeout` 归为可重试 infra 错误，不累计 `consecutive_failures`（703 被误杀）。
- **预期影响**：所有列表页起点任务省 5-16 步（对整类乃至全 184 任务都有效——新基线同样受污染）；703 类中断消除。form 类内估计 **+1~2**
- **验证方法**：重跑时对比各任务前 5 步内是否还出现 "stale filter" 字样
- **风险**：①属于评测环境修复，注意与"新基线可比性"——建议在报告口径上单列"含基建修复"的数字

### 建议 7：修改类任务的终态与副作用策略（技能层——按 host 注入，不进通用 prompt）
- **改动层**：技能层 — `domain-skills/localhost_7780/_sop.md`（**不进 `prompts/system_prompt.py`**：TreeWalker 是通用 agent，"电商后台终态/副作用"是站点知识而非通用行为，按 host 注入正合适；进 SYSTEM_PROMPT 会污染所有无关站点）
- **具体改动**（两条规则，2026-08-24 已落地 `_sop.md` 的「完成验证与提交流程」节）：①"完成创建/修改后，留在编辑页回读关键字段值作为完成证据（不要跳去列表页）"（针对发现 8 的 url_match 型 checker，也符合真实用户验证习惯）；②"电商后台的订单/出货类操作优先走页面自身提交流程（会生成订单评论/邮件等副作用），fetch POST 仅用于读数据；若必须绕过提交，事后检查同页面的历史/评论区是否记录了该操作"（针对发现 9 的 499）。
- **预期影响**：700/702 终态正确（与建议 2/4 叠加）；499 若环境未漂移可由 UI 流程+carrier 全称救回（发现 9：重跑已用对 carrier，缺的是评论副作用）。估计 **+1~2**
- **验证方法**：重跑看 499 终态 commentsHistory、700/702 终态 URL
- **风险**：无通用行为风险（只影响 localhost_7780 站点）；手册长度可控（净增 6 行）

---

## 六、优先级

按「可救任务数 × 实现成本」排序（可救数取各建议独立贡献的中位数，重叠已折减）：

| 序 | 建议 | 层 | 改动量 | 预期救回（本类） | 备注 |
|---|---|---|---|---|---|
| P0-1 | 建议 1 强制渲染帧 | 工具层（session.py settle） | 小（~30 行） | 2~4 | 全类最大税源（链 A）的总闸 |
| P0-2 | 建议 2 type_text 补 change | 工具层（session.py 一处） | 极小（~5 行+单测） | 2~3 | 因果最干净的确定性修复 |
| P1-1 | 建议 4 Magento 手册 | 技能层（纯文档+开关） | 小 | 2~3 | 零代码；评测配置需开 injection |
| P1-2 | 建议 3 dialog 自动处理 | 工具层（session.py 回调） | 小 | 0~1（防整类挂死） | 安全性靠配置开关 |
| P2-1 | 建议 7 终态/副作用策略 | 技能层（`_sop.md`） | 极小 | 1~2 | **改为可实施并已落地（2026-08-24 修订）**：载体从通用 SYSTEM_PROMPT 改为按 host 的站点手册——TreeWalker 是通用 agent，电商后台终态/副作用规则属站点知识 |
| P2-2 | 建议 5 evaluate 自愈 | 工具层（actions.py） | 小 | 0~1 | 顺手修 KeyError bug |
| ~~P3~~ | ~~建议 6 评测基建~~ | 评测侧 runner | — | — | **不实施**（2026-08-24 决定；评测侧脚本不动） |

**合并估算**：11 个重跑复败任务中，P0 两项 + P1/P2 落地后可救 **5~7 个**（高置信：543、551、700、702、503；
中置信：505、464、493、703、499）。**基本救不了**：546（checker 定义错位，发现 10）、695（需切属性集+设
size/color 商品属性的深流程，checker 又要求 option id 精确值，改造成本高收益 1）、5/712（非典型 form 任务）。
叠加 4 个已翻转任务，本类上限约 **SR 从 0/18 → 10~12/18**（以重跑口径计）。

### 实施记录（2026-08-24）

除 ~~P3~~（不实施）外，其余六项已落地，全量 2370 测试通过：

| 项 | 落点 | 测试 |
|---|---|---|
| P0-1 强制渲染帧 | `session.py` `wait_for_page_settle`（拆出 `_settle_poll`）+ 新增 `_kick_frozen_data_grid`（检测 `.admin__data-grid`/`table.data-grid` 行全空 → 丢弃式整视口 jpeg 截图强制一帧 → 复查）；`actions.py` `_action_navigate` 回显 `(data-grid render kick applied…)` | `tests/test_p7_form_interaction_fixes.py`、`test_navigate.py` |
| P0-2 type_text 补 change | `session.py` `_trigger_framework_events` 在 InputEvent 后补发 `change`（blur 仍不发）；与 `_force_set_value`/`_clear_text_field` 行为对齐 | `test_input_text_framework.py`（断言翻转） |
| P1-1 Magento 手册 | `domain-skills/localhost_7780/{quirks,_sop}.md`；`url_utils.extract_host_with_port`（端口限定 key，本机不同端口互不误注入）；`config.py` `enable_skill_injection` 默认开（env `AGENT_ENABLE_SKILL_INJECTION=false` 可关）；**补丁（2026-08-24 重跑核查）**：`SkillLoader._resolve_dir` 加仓库根回退——评测 runner 的 CWD 是 `evals/webarena`，相对 `skills_dir="domain-skills"` 在那里不存在（手册在 TreeWalker 仓库根），loader 按 `loader.py` 上级回退到包仓库根同名目录（editable 安装即本仓库；已用 eval venv + eval CWD 端到端验证命中） | `test_agent_skill_injection.py`、`test_skills_loader.py::TestRepoRootFallback` |
| P1-2 dialog 自动处理 | `session.py` `_setup_event_tracking` 由 `_connect` 无条件调用：`javascriptDialogOpening` → 记录（含处理动作，对 LLM 可见）+ 事件循环上 `Page.handleJavaScriptDialog`（beforeunload→accept，其余→dismiss）；`BrowserSettings.auto_handle_js_dialog`（默认开，env `BROWSER_AUTO_HANDLE_JS_DIALOG`） | `test_recent_events.py::TestDialogAutoHandle` |
| P2-2 evaluate 自愈 | `session.py` `_syntax_repair_candidates`（Illegal return→包 IIFE；Missing catch→双候选试跑）+ `evaluate()` 失败重试一次 + 截断报错附提示；`actions.py` `_action_evaluate` code 参数 KeyError 守卫；`models.py` `code` 描述明示"脚本体 vs args 包函数体"两种模式契约 | `test_p7_form_interaction_fixes.py` |
| P2-1 终态/副作用策略（2026-08-24 修订为可实施） | 载体=站点手册非通用 prompt：`domain-skills/localhost_7780/_sop.md` 新增「完成验证与提交流程」节（编辑页终态回读 + UI 流程优先/fetch POST 仅读 + 事后查评论区副作用），价格规则节原重复行并入该节；`SYSTEM_PROMPT` 不动（通用 agent，站点知识按 host 注入） | `test_skills_loader.py`（加载即可，内容无断言） |

验证入口不变：`run_category.ps1 -Category form_interaction -Force` 重收对比。
注意 2026-08-24 核查发现的**端口错位**（评测脚本在 9222 起 flags Chrome，日志却连 9223）——
重收前先统一评测 Chrome 端口，否则强制渲染帧之外的环境变量（窗口最小化等）仍不确定。

---

### 附：证据文件索引
- 轨迹：`evals/webarena/results/logs/form_interaction/<task_id>.log`（本文引用均含 task_id+step，可 grep 复核）
- 重跑判分：同目录 `<task_id>.json`；新基线判分：`evals/webarena/results/shopping_admin_antithrottle.json`
- checker 定义：`evals/webarena/webarena_repo/config_files/<task_id>.json`
- 代码锚点：`session.py:1681`（dialog 只记录）、`session.py:2414/2764`（type_text 不发 change）、
  `session.py:2479`（_force_set_value 发 input+change）、`actions.py:576`（settle 调用点）、
  `actions.py:2314-2315`（evaluate KeyError）、`config.py:87`（enable_skill_injection 默认关）、
  `step.py:174/482`（max_failures / 预算警告）
