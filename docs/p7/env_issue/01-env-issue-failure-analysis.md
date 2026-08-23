# env_issue 类失败分析报告

> P7 失败归因 · env_issue 类。分析日期：2026-08-21。
>
> 分析对象：新基线（SR 52.2%，`evals\webarena\results\shopping_admin_antithrottle.json`）88 个失败任务中 GLM 自述归类为 env_issue 的 21 个任务（日志目录 `results\logs\env_issue\` 含 21 对文件）。基线全 score=0；重跑（同条件收日志：反节流 flags、数据重置后 Magento、30 步/600s 上限）6 个翻盘通过（0/492/496/497/500/711）、15 个仍失败。
>
> 方法：重跑轨迹逐条深读 + WebArena 官方判分器配置（`webarena_repo\config_files\*.json`）+ DB 地面真值（docker exec mysql）三方交叉验证。旧先验分析（`docs/../evals/webarena/docs/failure-analysis-2026-08-14.md`）仅作假设清单参考。
>
> 注意：本批轨迹采集时，工作区在途修复（R7-1 点击无效果检测、B3-1 requirejs settle、R7-2 验证标记回读等）已生效——即本报告分析的是"已加检测后仍失败"的残余。

## 一、类别校验结论

**有效 4 个 / 混合 5 个 / 归类存疑或错误 12 个**。自述把「我搞不定」大量上报成了「环境有问题」。

| 判定 | 任务 | 实际情况 |
|---|---|---|
| **真 env_issue** | 374 | Themes grid 永久 0 行 + preview 路由 404/伪 200（mui JSON 证明服务端有数据，纯 UI 渲染层死） |
| | 759, 760 | map 容器（localhost:3000）未启动，ERR_CONNECTION_REFUSED，agent 干净快速失败 |
| | 184 | **数据漂移致不可通过**：DB 实查 qty=0 产品有 164 个，官方 exact_match 参考答案是唯一的 "Sinbad Fitness Tank"——任何 agent 走 UI 都不可能给出该答案 |
| **混合**（环境诱因，agent 手段决定成败） | 696, 698 | Add Attribute modal 用合成事件打开后只有骨架、永不加载数据（见发现 5）；真实点击被遮挡/无反应把 agent 逼进合成路径 |
| | 699 | Actions/Conditions 折叠区 Knockout 组件永不实例化、`edit_form` 元素缺失——页面区块对人类也不可用，但基础表单区正常 |
| | 0, 704, 711 | Show Report 按钮 jQuery 监听缺失（会话级 flaky）+ 日期值被迟到部件清除；重跑 0/711 翻盘证明非持久损坏 |
| **归类错误**（应为其他类） | 110 | 数据解读：11 个月里只 4 月差 1（把 1 笔 closed 算进 successful；DB 证实参考口径 complete-only）。基线 n_steps=0 是 runner/LLM 层挂死，非环境 |
| | 185 | agent 表达 + 判分管线：找对了 qty=3 的两个产品、说出了参考答案 "Eos"/"Minerva"，败在 done success=False 的对冲措辞（重跑 eval_answer 含两词却判 0，判分管线亦有异常，见发现 8） |
| | 492, 496, 497, 500 | agent 自救能力（基线 3-20 步早退 vs 重跑全过，差在 JS click/直拼 URL/读回校验等手段） |
| | 548 | agent 自残：向导 modal 打开成功后，被自己的 evaluate JS 点击序列点到 Confirm/Cancel 整体关掉（L300/L323） |
| | 697 | LLM 输出退化（预算压力下参数缺失→空动作）+ 页面重载清空未保存表单，应为 LLM 稳定性类 |
| | 706, 708 | 日期语义错误（见发现 2），环境数据其实与判分无关 |
| | 713 | 走错页面（Ordered Products ≠ 判分器要求的 bestsellers）+ 证据链断裂 |

## 二、真实失败机制

真 env_issue 子集（374/759/760/184/699）的共同点是**失败点在浏览器外侧**：容器未起、DB 数据漂移、RequireJS/Knockout 组件初始化死亡。证据：

- **374** Step 2-17：等 3s（L148）、reload、Default View/Filters 点击（L193/L220）全部无效，grid 恒 0 行；Step 18 的 mui JSON POST 却返回 `theme_id=1 is "Magento Blank"`（L237）——服务端完好，渲染层死。
- **699** Step 20：原生 CDP click 点中带 `click:toggleOpened` 绑定的真实开关，无 "no visible effect" 警告（点击已送达），`data-state-collapsible` 仍 'closed'（L280）；Step 27 连 `edit_form` 都为 null（L362-363）。
- **759** Step 0-3：三次 navigate + 一次 reload 点击全部 `ERR_CONNECTION_REFUSED`（L24/L48），4 步干净收尾。

混合子集（696/698/0/704/711）的共同机制是**提交/触发链的时序与路径依赖**：同一页面同一会话内，一条打开路径出数据、另一条只出骨架；一次 UI 填值丢失、JS 设值却恒生效（详见发现 3/5/6）。

## 三、类内分化

**分界线不在任务复杂度，在"失败点在哪一层"**：

| | 近成功（差一个动作/一个语义） | 远失败（agent 不可救） |
|---|---|---|
| 任务 | 0（重跑过）、711（重跑过）、704（终态只差停在正确表单状态）、706/708（只差 To 日期）、713（只差去对页面）、185（答案已说出）、110（一个月差 1） | 374（渲染层死）、699（组件死）、184（数据漂移）、759/760（容器死）、696/698（需真实点击打开 modal——待探针验证） |
| 特征 | 页面/数据/工具都正常可用，败在提交方式、终态纪律、语义解读或收尾表达 | 页面区块对任何用户不可操作，或正确答案在环境中不存在 |
| 页面 | 报表页、订单页（1-2 页表单） | 大型 Knockout listing/向导页 |
| 策略 | 已找到正确页面/数据，最后一步走错 | 从未获得可用入口 |

步数与分化无关：近成功组里 708 只用 14 步、713 只用 7 步照样 0 分；远失败组 374 用满 29 步。

## 四、发现清单

### 发现 1：报表类任务判分只看「终态 URL + From/To 输入框精确值」，agent 却把预算烧在取数/导出上
- **证据**：
  - 判分器配置（`config_files\704.json`）：`eval_types: [url_match, program_html]`，`reference_url: .../reports/report_sales/sales`，program_html 仅检查 `sales_report_from` = **"2/1/23"**、`sales_report_to` = **"2/28/23"**——**没有任何数据/导出检查**。706/708/711/713 同构。
  - **704** Step 20-27（L265-355）：已通过 fetch POST 确认数据存在（"response 81059 chars … no longer contained 'We couldn't find any records'"，L272），随后 8 步全烧在解析响应/触发 CSV 下载，3 步死于代码截断；done 时输入框是空的 → program_html 失败。
  - **711**（对照组，score=1.0）：Step 5 evaluate 设值+提交（L85-88）→ Step 7 "inputs now show value=7/5/21 and value=5/31/23"（L104）→ 直接 done。**grid 是空的照样满分**——因为终态输入值精确等于参考值。
  - **708**：内部 Judge 判 SUCCESS（报表如实生成、存了 PDF），score=0——判分器要的输入值它没满足。
- **机制**：WebArena 对 "generate report" 任务的程序化判分 = done 后读当前页 DOM 的两个 input.value + 当前 URL。agent 把任务理解为"拿到数据/导出"，在判分器根本不检查的环节烧掉预算，且最后的 evaluate/导航把已填好的表单状态破坏掉。
- **置信度**：高（判分器代码 + 711 通过路径实证）。

### 发现 2：日期范围语义错误——Q1 / this year 被填成"到今天为止"
- **证据**：
  - **706** Step 25（L349-354）：填 From=01/01/2023、**To=03/15/2023**；参考要求 exact_match **"3/31/23"**（Q1 终点）。
  - **708** Step 2（L48-52）：填 **To=03/15/2023**；参考要求 **"12/31/2023"**（this year = 整个日历年）。
- **机制**：任务文本 "Today is 3/15/2023" 是背景锚点，agent 把它当成了区间终点。仅此一项，即使提交链完美也判 0。
- **置信度**：高。

### 发现 3：input_text 输入的日期值被迟到初始化的页面部件清除（间歇性时机问题）；JS 设值+change 事件恒有效
- **证据**：
  - **708**（最干净的证据链，id 正确）：Step 2 `Typed '01/01/2023' into [INPUT] 'From'`（L48）+ Show Report → Step 4 evaluate 读回（L78）→ Step 5 Eval："the form was reset: report_type=created_at_order, **from/to empty**, so prior inputs were not committed"（L85）。
  - **713**：Step 1 填两日期+点 Refresh（L37-41）→ Step 2 读回（L50）→ Step 3 Eval："**Inputs were empty after refresh** — Magento reset them"（L58）。
  - **711**：Step 1 填+点击（L35-41）→ Step 4 JS 读回为空 → Step 5 改用 evaluate 设值+提交后值持久且页面成功导航（L88-104）。
  - **反例（间歇性证明）**：**706** Step 25 后期同样的纯 UI 填值路径，"Filter values confirmed persisted"（L374）——早填丢、晚填留 → 与部件初始化时机相关（B3-2 注释"迟到部件重置表单"所针即此机制，工作区已有检测、尚无修复）。
- **机制**：input_text 逐键输入只派发 input 事件（`session.py type_text` 刻意不发 change/blur），Magento 报表日期框的日历部件初始化晚于输入时，其内部状态为空并在 blur/提交时回写清空字段；JS 原生 setter 赋值 + `dispatchEvent(change)` 恒生效。
- **置信度**：高（丢失与持久双态均有日志实证；部件清值的确切触发器为推断，`examples/p7_probe_datefield.py` 的 watch 相位即为此设计，未跑）。

### 发现 4：evaluate 管线三缺陷系统性烧步——裸 return 报 Illegal、长代码被截断、引号修复反毁代码
- **证据**（跨 10+ 任务，合计 ≥20 步）：
  - **Illegal return statement**（工具无自动 IIFE 包裹，全靠 description 劝模型自己写）：711 Step 3（L61-67）、704 Step 26（L340-343）、496 Step 4（L73-77）、548 Step 2/3（L54/L69）、696 Step 5（L96-99）、698 三次（L45/L119/L160）、706 Step 3/16（L61/L199）。
  - **Unexpected end of input（代码被截断，执行必败）**：704 Step 14/24/26（L194-197/L314-317/L340-343，**同一任务连中三次**，翻盘路径直接被杀）、548 Step 25（L328-331，60s 大驱动脚本报废）、698 Step 21/22（L292/L307）、184 Step 27（L336-339）。
  - **引号修复破坏代码**：374 Step 26 正则 flags 被改坏（L327-330）、110 Step 16 `paging[pageSize]` 引号被吃（L201-204）、706 Step 14 括号错位（L187-190）。
- **机制**：`session.py` 无-args 路径直接 `Runtime.evaluate(validated_code)` 执行（session.py:3188-3198），预检只有正则修复；长代码截断的源头在 LLM 输出侧（管线内无长度限制、max_tokens 已是 16384，排除输出上限；错误展示的 `[:500]` 截断只是展示层）——即 GLM 在长 JSON 参数里偶发自闭合的半截代码，管线无平衡性检查，执行必然 SyntaxError 并烧掉整步。
- **置信度**：现象与成本=高；截断源自模型侧=中高（标注：源头归因未直接抓到原始响应，属"未验证推测"的可证伪部分）。

### 发现 5：合成事件打开的 modal 只有骨架、不触发数据加载；真实 el.click() 才会
- **证据**：
  - **697（决定性对照组）**：Step 4-13 jQuery `trigger('click')`/`openModal` 等 8 次尝试，modal 开着但网格空（L187 "Modal grid never surfaced via evaluate attempts"）；**Step 14** occluded→JS fallback 的真实 `el.click()` 后——"Add Attribute modal is now open with the attribute grid; **Size (id 144) and Color (id 93) are listed**"（L204）。
  - **696**：Step 9 jQuery trigger 打开（L151 visible:true）→ Step 13 `wait 3s` 后重读仍空（L193）→ 之后 8 次重读、112 秒内单元格文本始终为空（L161-L282）。
  - **698**：Step 12 手动初始化 widget 打开（L191）→ Step 13-22 全空；Step 23 保存后整页重载，新页面真实点击+wait 4s+trigger 读，**仍空**（L346-370）。
- **机制**：jQuery `.trigger('click')` 只执行 jQuery 绑定的 handler，不触发原生 addEventListener；"打开 modal"与"触发网格 XHR 加载"分属两条事件线，合成触发恰好只跑了前者——等待救不了"加载从未被发起"。
- **置信度**：中高（697 同会话对照组极强，但缺对 696/698 同页直接用真实事件点击的复现——建议探针，见建议 4）。

### 发现 6：jQuery 绑定按钮对 CDP 合成点击系统性无反应（每堵墙烧 3-10 步）
- **证据**：496 Ship 按钮（L37）、Add Tracking（L155）；497 L52/L137/L180；548 Search（L40）、Edit Configurations（L259）；696 Add Attribute 原生 click 两次 no-effect（L181）；704 Show Report（L158/L235）；184 Filters 按钮（L128）。R7-1 的 "no visible effect" 检测（已在采集版本生效）让 agent 能感知，多数靠 JS 手段翻盘（496/497/500 重跑全过），但每次翻墙 3-10 步。
- **机制**：Magento admin 按钮普遍 jQuery 绑定 + 部分被遮挡/位于 sticky 头，CDP 坐标点击投递不可靠；agent 逐步试错找出路。
- **置信度**：高。

### 发现 7：跨任务网格筛选状态泄漏（残留 filter 每任务烧 4-6 步）
- **证据**：497 Step 22 "A **'Jane Doe' keyword filter is active**"（L283，来自 492）；185 Step 0 进入网格即带 "Frankie" 筛选（L32）；184 Step 4 残留 "Cronus Yoga Pant" 筛选（L82）。
- **机制**：同浏览器 profile 连续跑任务，Magento 网格筛选状态跨任务存活；agent 开局先花 4-6 步清筛选。
- **置信度**：高。

### 发现 8：数据集知识缺失 + 判分管线两处异常
- **证据**：
  - **185**：brand/manufacturer 属性在数据集中确实不存在（产品编辑页全字段 dump L169、storefront More Information L215/L301 均无）；参考答案是产品名首词（`must_include: ["Eos","Minerva"]`）。agent 找对两个产品、答案里说出了 "Eos"/"Minerva"，却以 success=False + "could not find … no dedicated brand field confirmed" 收尾；**重跑 eval_answer 全文含两个必含词却 score=0**——与 `evaluators.py` must_include 逻辑矛盾（管线异常，成因未定，需离线重放判分排查）。
  - **110**：11 个月 fuzzy_match 连乘，仅 April 差 1（agent 04:8 vs 参考 April: 7；DB 证实 2022-04 complete=7 + closed=1，参考口径 complete-only）→ 一票否决整题 0 分。
  - **184**：docker exec MySQL 实查 qty=0 共 **164 个**产品且参考 exact_match 为唯一 "Sinbad Fitness Tank" → 环境数据态下任务不可通过。
- **置信度**：高（184 有 DB 真值；185 判分异常是观察事实，成因未定）。

### 发现 9（附带）：374 的判分器只要求终态 URL 含 theme edit 路由，agent 从未试过
- **证据**：`config_files\374.json`：url_match，`reference_url: http://localhost:7780/admin/admin/system_design_theme/edit/id/1`。轨迹里 agent 试遍 `preview/id/X`、`index/preview/id/X`、`system_design_theme/preview/id/1`（Step 7-27），**从未构造 `/edit/id/1/` 直连 URL**（订单任务证明该模式在本环境可行：496 Step 0 直拼 view/order_id/299 一次命中）。
- **机制**：若 edit 路由直连可达，判分即可通过——但这是判分器宽松，不是能力修复；**未验证推测**（路由可达性未探针）。
- **置信度**：判分器事实=高；可翻盘=低-中。

## 五、优化建议

### 建议 1：报表/筛选类任务的「终态纪律」提示
- **改动层**：prompt 层（`src\tree_walker\prompts\system_prompt.py`，SYSTEM_PROMPT 的 Rules 段追加一条）
- **具体改动**：
  ```
  Rule N (report/filter tasks): For tasks like "generate/create a report" or grid filtering,
  success = ending ON the report page with the correct filter values VISIBLE IN THE FORM
  inputs. After submitting filters, verify the From/To inputs still hold the intended
  values (read them back); if they were reset, set them via evaluate
  (native setter + change event) and resubmit ONCE, then verify again. Do NOT spend the
  remaining budget extracting grid data or exporting unless the task explicitly asks for
  numbers/files — leave the page in the filtered state and finish.
  ```
- **预期影响**：本类 21 个中估救回 **2-3**（704 最稳：日期已填对、只差保住终态；706/708 需与建议 2 叠加；713 需另去对页面）。
- **验证方法**：`run_category.ps1 -Category env_issue -Force` 重收对比；重点看 704/706/708/713 的 done 前 3 步与 program_html 输入值。
- **风险**：查询型报表任务（如 0 要读出产品名）可能过早收尾——用"任务明确要求数字/文件时除外"限定；非报表任务误触发——按 "report/filter" 词面触发，影响面小。

### 建议 2：日期范围语义规则
- **改动层**：prompt 层（同上位置，或作为建议 1 的子句）
- **具体改动**：
  ```
  Date ranges: "Q1" = 01/01–03/31 of that year; "last month" = the FULL previous calendar
  month; "this year" = 01/01–12/31 of the stated year. "Today is X" is only an anchor for
  relative wording — do NOT use it as the range end unless the task says "to date/as of".
  ```
- **预期影响**：与建议 1 叠加救 **706/708**（+1-2）。704 的 "last month" agent 已算对（2/1–2/28），说明规则可学会。
- **验证方法**：同上，看 706/708 的 To 值是否变为 3/31、12/31。
- **风险**：极低（纯语义约定）。

### 建议 3：evaluate 预检——自动 IIFE 包裹 + 截断/不平衡拒绝执行 + 修复器保守化
- **改动层**：工具层（`src\tree_walker\browser\session.py` `_validate_and_fix_javascript`（:437-479）与 `evaluate` 入口（:3139）；参数描述 `tools\models.py` EvaluateParams.code）
- **具体改动**：
  ```python
  def _validate_and_fix_javascript(code: str) -> str:
      # 新增 0a: 顶层裸 return 且未被函数包裹（无 "function" 包裹层）→ 自动包
      #   (function(){ <code> })()
      # 新增 0b: 括号/引号平衡静态检查（字符串与模板字面量外计数）；
      #   不平衡 → 抛 ValueError("code looks truncated (unbalanced brackets) —
      #   resend the complete code, keep it under ~400 chars, or split into
      #   multiple evaluate calls")，由 _action_evaluate 转为明确 error，
      #   不再送 CDP 执行必败代码
      # 现有 1-7 步骤不变；新增 0c: 代码含反斜杠（regex/转义）时跳过 1-2 的
      #   反转义修复并原样执行（374/110 两类被修复器毁坏的形态）
  ```
  同步在 `EvaluateParams.code` description 追加 "Keep code short (<400 chars); long scripts get truncated in transport — split into multiple evaluate calls."
- **预期影响**：不单独翻盘任务，但全类回收 ≥15 步预算；704（3 步死于截断）与 548/698（大脚本报废）的翻盘概率显著上升，估 +0-1 个任务。
- **验证方法**：单测覆盖三种形态（裸 return/不平衡/含反斜杠）+ 重收日志统计 evaluate SyntaxError 次数（本轮 10+ 任务 ≥20 次）。
- **风险**：平衡检查对模板字符串内括号的误判——实现时先剥字符串再计数；自动包裹幂等（已含 IIFE 的不再包）。

### 建议 4：真实点击优先的按钮/modal 自救梯子（先探针后改）
- **改动层**：agent 策略层 + prompt 层（`system_prompt.py` Rules；`tools\actions.py` click 的 JS fallback 顺序注释性提示已有 R7-1 文案）
- **具体改动**：
  1. 先跑探针验证发现 5：新增 `examples/p7_probe_attribute_modal.py`——干净会话用 `Input.dispatchMouseEvent` 真实点击 Add Attribute，等 3s 读 `.product_form_product_form_add_attribute_modal tbody tr` 文本；有数据 → 机制成立。
  2. 机制成立后在 prompt 加：
  ```
  When a button click reports no visible effect, escalate IN ORDER: (1) el.click() via
  evaluate, (2) full pointer/mouse event sequence, (3) jQuery .trigger('click'). For
  dialogs/modals containing data grids, prefer real-event paths — synthetic jQuery
  triggers can open the modal shell WITHOUT triggering the grid's data load, leaving an
  empty grid that waiting will never fix.
  ```
- **预期影响**：若机制成立，696/698 估救回 **1-2**；同时压缩全类每堵"点击墙"的 3-10 步试错。
- **验证方法**：探针 → 改后重收，看 696/698 是否在第 1 次打开后网格即有数据。
- **风险**：jQuery trigger 在部分场景是唯一有效路径（548 曾靠它打开）——梯子是排序不是禁用。

### 建议 5：评测基建三修（不动 agent）
- **改动层**：评测脚本层（`evals\runner.py`、`run_category.ps1`）
- **具体改动**：
  1. **任务间清网格状态**：`run_one_task` 起始页加载后执行一次"清筛选"JS（存在 Clear all 则点），消除 Jane Doe/Frankie/Cronus 泄漏（发现 7，每任务省 4-6 步）；
  2. **185 离线复评**：用已存 trajectory 重放 `StringEvaluator.must_include`，定位 eval_answer 含必含词却 0 分的管线环节（可能是抽取层或 adapter 吞错）——5 分钟工作，可能白捡 +1；
  3. **759/760 前置探活**：run_category 前检查 localhost:3000 可达，不可达标记 infra-fail 直接跳过，不再烧 agent（并把 map 容器启动写进采集 runbook）。
- **预期影响**：不直接改判分；185 复评潜在 +1；步数收益传导到边缘任务。
- **验证方法**：重跑后看 497/184/185 的前 5 步是否还有清筛选动作。
- **风险**：清筛选 JS 对无网格页面需空操作守卫。

### 建议 6：明确「不可救」清单，避免继续投入
- 374（UI 渲染层死；edit 直连是判分器漏洞不是能力项，是否利用由人决策——建议先探针 `/admin/admin/system_design_theme/edit/id/1/` 可达性再议）、699（组件死）、184（数据漂移，判分参考与 DB 永远对不上）、759/760（容器）。这 5 个在 agent 侧的合理预期收益为 **0**。

## 六、优先级

按「可救任务数 × 实现成本」排序：

| 序 | 建议 | 层 | 成本 | 预期救回（/21） | 备注 |
|---|---|---|---|---|---|
| 1 | 建议 1+2（报表终态纪律 + 日期语义） | prompt | 半天 | 2-3 | 704 最稳；与在途 B3-2 检测互补（检测只告知值被清，纪律告知怎么办） |
| 2 | 建议 3（evaluate 预检+自动 IIFE） | 工具 | 1 天+单测 | 0-1 直接，全类省 ≥15 步 | 独立无依赖，测试好写（三种形态都有现成日志样本） |
| 3 | 建议 5（评测三修） | 评测脚本 | 半天 | 185 潜在 +1 | 185 复评最先做，成本 5 分钟 |
| 4 | 建议 4（真实点击梯子） | 策略+prompt | 探针 0.5 天 + 改动 0.5 天 | 1-2（机制成立前提下） | 探针先行，机制不成立则降级为纯 prompt 顺序建议 |

**合计克制预期**：agent+prompt 侧 3-5 个，评测侧 1 个，环境侧 0 个——即本类 21 个失败中约 1/4 可回收，其余半数是归类水分（12 个应归其他类），少数（4-5 个）是环境真故障。

另注：0/711/492/496/497/500 重跑翻盘本身说明本类基线失败中相当一部分是随机方差，单条轨迹结论均为概率性判断。
