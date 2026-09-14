# issue #186 分析：行为纪律两缺口——连败不止损 / 数据不完整仍 done

- 日期：2026-09-13（B 轮证据 2026-09-09/10，v0.17.0 @ 8ddd92b）
- 证据：`results/self_overestimate/B/task_374.log`（steps 6-13）、`task_64.log`（steps 14-19）；汇总 `eval 仓 docs/self-overestimate-analysis-2026-09-10.md`（v2）
- 关联：#185（工具层五修复，PR #189）已消除本轮多数触发器；本 issue 是**策略层**缺口——触发器修了，行为纪律的兜底仍在
- DB 真值（本轮实测）：`sales_order_grid` 按 `billing_name` 计数 =2 的客户 **Emma Davis 与 Veronica Costello 各 2 单**

## 0. 结论速览

| 现象 | 直接根因 | 置信度 | 修复锚点 |
|---|---|---|---|
| ① 连败不止损、原地发明 workaround | 现有三层机制**形状全不匹配**：`consecutive_failures` 多动作步失败不计且被重置、达 `max_failures=5` 是**终止任务**不是转向；loop detector 成功无关（阈值 ≥5 防正当重复误报），374 只有 4 次重复+页面指纹在变；无任何"失败感知"通道 | 确定（日志+代码） | 新增 failure-streak 跟踪 + context nudge（§3.1） |
| ② 数据不完整仍 done(success=True) | 防线只有 prompt 六条检查（**无数据完整性维度**）+ DoneParams 描述，无机械门禁；且证据链比 issue 陈述更深：**问号自然蒸发**（step16 `Emma Davis=1?` → step17 `=1` 无新证据）+ **循环验证**（只复核自家短名单，短名单来自残缺 tally）+ post-hoc Judge 同样被骗（task_64 判 SUCCESS） | 确定（日志+DB 真值） | prompt 补完整性检查 + done(success=True) 不确定标记门禁（§3.2） |

波及面：B 轮 46 任务中 **11 个（24%）** 出现同工具跨步连败 ≥2（evaluate ×10 + screenshot ×1）；自评偏高全域 A 42 / B 46 / C 34（剔除基建误判后）。

---

## 1. 现象①：连败不止损（task_374，steps 6-13）

### 1.1 时间线

| step | 动作 | 结果 | 关键行为 |
|---|---|---|---|
| 6 | navigate 裸 jpeg URL | OK（Empty DOM 警告×2） | 离开得分页（该任务判分只看最终 URL） |
| 7 | [close_tab, **screenshot**] | 1/2 失败 | **多动作步失败不计入 consecutive_failures** |
| 8 | [screenshot] | 失败 | 计 1——仅此一次被计数 |
| 9 | [**evaluate**(document.write 包 HTML), wait, screenshot] | evaluate 成功即截断序列 | **多动作步成功把 consecutive_failures 重置为 0**；发明 workaround 升级投入 |
| 10 | [screenshot] | 失败 | 又计 1（对 wrapper 页仍 10s 超时） |
| 11-12 | [evaluate(fetch base64 "验证"), screenshot] | evaluate OK / screenshot 失败 | 再重置；为判分不看的目标继续烧步 |
| 13 | done(success=True) | — | 全程停留在错误页面 |

净效果：6 步 ~2 分钟，4 次相同参数 screenshot 失败 + 2 次发明性 evaluate，无任何机制介入。

### 1.2 三层现有防护为何全部空转

1. **`consecutive_failures` / `max_failures=5`**（step.py `_post_process` Phase-4 语义，对齐 browser-use）：
   - 单动作步失败才计数；多动作步任何失败**不计**（"deferred to loop detection"）；
   - 更糟：非计数步会把已计的清零（step 9）——374 的计数在 0/1 间振荡，永不达 5；
   - 即使达到，语义是 `run()` **终止任务**（agent.py:296）——而 374 此刻完全可救（回得分页即可），终止与"放弃该手段、回归目标"是两个物种。
2. **loop detector**（loop_detector.py）：repetition ≥5/8/12 分级 + 页面停滞 ≥5。设计上**成功无关**（为容忍翻页等正当重复，阈值必须高）——374 的 4 次相同 screenshot（同 hash）< 5，且页面指纹随 document.write 在变，两维全不触发。失败才是强得多的信号，但它不感知。
3. **#185 的修复边界**：screenshot 空消息是 374 的直接触发器，已修（满信息文案含成因与出路，真机冒烟 374 转成功）；evaluate 语法链的根因也已自愈。**但策略层缺口独立存在**：任何未来的新错误形态（消息再清楚也连败）仍会裸奔——task_112 的 evaluate 连败 ×3 当时消息里就带完整代码回显，agent 照样连踩。

### 1.3 波及面（B 轮全量复扫）

同工具跨步连败 ≥2：**11/46 任务**——task 2/64/112/374/543/544×2/695/697/698/707/775（evaluate ×10、screenshot ×1）。止损 nudge 若在 streak=2 触发，这些任务全部会在第 2 次失败后收到干预。（其中 evaluate 链多数已被 #185 从根上修掉，此数字说明的是机制覆盖面而非当前残量。）

---

## 2. 现象②：数据不完整仍 done（task_64，steps 14-19）

### 2.1 证据链（比 issue 陈述深两层）

1. **step 16（诚实时刻）**：memory 明确 `Emma Davis=1?`、eval 自知 "A ~60-row gap in the middle remains unverified"——不确定性的自我记录是存在的；
2. **问号蒸发**：step 17 eval 变成 "Emma Davis and David Lee **seen only once**"——`?` 在**无任何新证据**下消失（LLM 把"待验证"漂白成"事实"）；此后 agent 只复核短名单（Lisa Green=3 排除、Veronica Costello=2 确认）；
3. **循环验证**：短名单本身产自残缺 tally（#185 现象③：块边界+不可见尾把 Emma Davis 数成 1）——复核短名单成员永远发现不了**被 tally 丢掉的名字**；
4. **step 19 假完整性断言**：done 文本称 "Full 308-row tally showed **no other customer** with exactly 2"——事实为假（DB 真值 Emma Davis 也有 2 单）；答案 Veronica Costello **不完整**（正确答案含两个名字），判 0；
5. **Judge 同样被骗**：post-hoc Judge verdict=SUCCESS——它读的是同一条自信轨迹，循环验证对它同样不可见。Judge 不是此问题的解。

### 2.2 现有防线

- system prompt "Before calling done(success=true)" 六条检查（system_prompt.py:61-72）：覆盖需求复述/动作完成/数据来源/阻塞错误，**无数据完整性维度**（计数是否来自全量读取、自评中是否有未消解标记）；
- `DoneParams.success` 描述："Leave True only when every requirement was directly confirmed"——纯 prompt，task_64 证明 GLM 会违反；
- **无机械门禁**：done 校验只有 pydantic 形状（text 非空）；
- 背景：自认成功但判分失败 A 42 / B 46 / C 34（v2，剔除基建误判后仍约七成真实失败伴随自认成功）；反向案例（判分成功但自认失败，N/A 型 183/201/247/491）说明是双向校准问题——本 issue 只取可执行的两条。

---

## 3. 修复方案

### 3.1 连败止损（请求项 1）——failure-streak nudge

**跟踪器**（新增，独立于 loop detector——后者保持成功无关性不动）：
- per-action-name 跨步失败 streak：`record_failure(name)` / `record_success(name)`（该动作成功即清零）；**多动作步内的失败也计**（374 的形态），在 `_execute_actions` 每动作产 result 处挂钩；
- 豁免：`done`（失败自会进澄清梯）；`wait/go_back` 无失败形态不特判。

**干预**（注入点 = `_prepare_context` 与 loop nudge 同位，state message 每步重建天然自清）：
- streak ≥2：注入 "You have failed `<tool>` N times in a row. Stop retrying or inventing workarounds for it. Re-read the original task and switch to a different approach that directly advances the task's final goal — ask whether the failing sub-goal is required by the task at all."；
- streak ≥4：升级措辞（"strongly consider declaring this sub-goal unreachable; complete/verify the task's actual deliverable, or finish with an honest partial result"）。
- 不走 `consecutive_failures`（保留其 browser-use 对齐的终止语义）；不因 nudge 强制拦截动作（软干预，与 loop detector 哲学一致）。

**预期**：374 形态第 2 次失败即被拉回任务目标（判分只看 URL——nudge 的 "re-read the task / final goal" 直接对应该缺口）；112/64 的 evaluate 链同样在 streak=2 收到转向提示。

### 3.2 done 数据完整性门禁（请求项 2）——prompt + 机械门禁双层

**Prompt 层**（system_prompt.py 六条检查后追加第 7 条 + `DoneParams.success`/`text` 描述同步）：

> 7. **Data completeness** — answers/counts must be derived from COMPLETE data. If your evaluation or memory contains unresolved markers (a value with `?`, an unread gap, a partial tally, an unverified assumption), either resolve them with tools first, or call done(success=false) stating exactly what is missing. Verifying only a shortlist derived from partial data does not establish completeness.

**机械门禁**（步内一次性澄清重试，复用 `_validate_params_or_retry` 梯子的既有机制）：
- 触发：`done(success=True)` 到达时，扫描本步 `evaluation_previous_goal` + `memory`（+ done.text），先剥 URL（`https?://\S+`），再匹配不确定标记——词尾 `?`（如 `Emma Davis=1?`、`=2?`）与关键词 `unknown / unverified / unread / gap / missing / pending / partial / uncertain / unclear / not sure`；
- 命中 → 步内重试一次，消息引用命中的标记："your self-evaluation contains unresolved uncertainty (matched: `Emma Davis=1?`). Either verify the missing pieces with tools, or call done(success=false) describing what is incomplete."；
- 二次 done 仍带标记 → 放行（防死循环、防误报卡死）；`success=False` 的 done 不拦（诚实收题必须畅通）；honest-done 带外标记跳过。
- 误报控制：只扫自评/memory 不扫工具输出；URL 先剥；词尾 `?` 需贴数字/标识符（`=1?` `Davis?`），孤立句末问号酌情放宽。

**为什么值得机械层**：task_64 的问号是 agent 自己写下的——门禁只是把 agent 的自我记录当真，不引入任何模型判断。

### 3.3 后置不做（另案）

- 请求项 3（自评校准整体审计、反向案例 183/201/247/491）：涉及 Judge 语义与任务完成定义对齐，独立成案；
- "手段升级"识别（失败后转 evaluate 发明 workaround 的模式检测）：语义过糊，streak nudge 的措辞已覆盖其行为后果，先不做。

## 4. 验收建议

- 单测：streak 计数（多动作步失败计入/成功清零/豁免）、nudge 分级文案、门禁标记匹配（词尾?/关键词/URL 剥离/二次放行/honest-done 跳过）、`success=False` 不拦；
- 回放口径（非门槛）：task_374 在第 2 次 screenshot 失败后应收到止损 nudge；task_64 若重放旧轨迹，step 19 的 done 应被门禁打回一次；
- 真机：B 轮任务子集复跑无回归（nudge 注入不改变成功任务的行为）。

## 附：证据索引

- task_374.log：step 6 离开得分页（L100）、step 7 多动作失败不计（L124 明示 "not incrementing"）、step 9 发明 document.write（L147）、step 12 fetch base64 "验证"（L172）、step 13 done 全程在错误页；
- task_64.log：step 16 `Emma Davis=1?` + 60 行缺口（L232）、step 17 问号蒸发（L243-244）、step 19 假完整性断言（L265-268）、Judge SUCCESS（L279）、score=0（L282）；
- DB 真值：`sales_order_grid` GROUP BY billing_name HAVING c=2 → `Emma Davis 2 / Veronica Costello 2`（2026-09-13 docker exec 实测）；
- 波及面：B 轮 46 任务同工具连败≥2 共 11 个（本文 §1.3 复扫脚本口径：步内动作序 × WARNING 失败行归属）。
