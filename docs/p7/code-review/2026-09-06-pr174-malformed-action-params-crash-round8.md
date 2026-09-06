# Code Review（第八轮）：PR #174（fix/173-malformed-action-params-crash)

- **日期**：2026-09-06
- **目标**：修复提交 `7bbeacc`（针对[第七轮 review](2026-09-06-pr174-malformed-action-params-crash-round7.md) 的修复；前几轮见 [round1](2026-09-06-pr174-malformed-action-params-crash.md) … [round6](2026-09-06-pr174-malformed-action-params-crash-round6.md)）
- **范围**：全分支 diff `master...HEAD`（相对第七轮评审时的增量修复）
- **方法**：8 个 finder 角度并行 → 逐候选验证（多条实证复现，跨文件反驳淘汰弱候选）
- **验证结论**：5 项 CONFIRMED、4 项 PLAUSIBLE、1 项 CLAUDE.md 规范违规
- **处置基调**：按快速收尾路径分级——#1/#2/#10 建议微修后合并；#3-#9 接受留档

## 总评

第七轮主线（单一策略表 `normalize_model_output(live|history)`、`_HonestDone` 带外标记、归一化前移过 truncate、分发收拢）已落地，本轮发现的两个实证问题都是**新机制与既有代码的交互缝隙**：

- **#1**：带外标记被 `_restore_urls/_restore_sensitive` 的 dict 重建剥掉——机制在 variant B + sensitive/URL 场景下静默失效（回到烧重试的修复前行为）。
- **#2**：validator 的拷贝归一化只覆盖 kwargs 构造路径，`model_validate` 路径（恰是 `load_from_dict` 所用）仍改写调用方 dict。

其余为边缘语义与卫生项。**分级建议见文末**：修 #1/#2/#10 后合并，其余接受留档（无崩溃类，影响面窄或仅重放/旧数据）。

## Findings（按严重度排序）

### 1. CONFIRMED（实证）— `_restore_urls_in_output`/`_restore_sensitive_in_output` 以 `{k: _restore(v) for k, v in obj.items()}` 重建每个 dict（行 125-126、165-166），静默剥掉第七轮 #5 依赖的 `_HonestDone` 带外标记——两个 restore 都在 `normalize_actions_list` 安装标记（372 → 385/389）之后、以及在空响应 fallback 构建 `_fb`（321 → 331-335）之后运行

- **位置**：`src/tree_walker/llm/client.py:385`
- **类别**：correctness

**失败场景**：variant B（output_model）+ 配置了 sensitive_data 或 messages 含任一 ≥100 字符 URL 的 run。LLM 输出 `action: null/123/[None]` → client 合成 `_HonestDone` done → restore 把它转回普通 dict（实证：`is_honest_failure_action` 返回 False）→ `_validate_action_params` 拿 `{"text": "Invalid action shape", "success": False}` 对 StructuredDoneParams（extra="forbid"、data 必填）校验 → 报错 → `_validate_params_or_retry` 烧 2 次全上下文重试告诉模型它的"合成 done"非法，然后返回模型最后一次响应（可以是任意非 done 动作）——设计好的一次调用诚实终止被静默禁用。测试全过只因都传空 messages（`url_map == {}`）。

### 2. CONFIRMED（实证）— `AgentHistory._normalize_malformed_actions`（mode='before'）写 `data["model_output"] = mo_copy`，而 model_validate 路径上 pydantic 把**调用方原始 dict** 交给 validator——构造随后字段校验失败时仍改写了调用方输入，违反 validator 自己文档化的不变量"绝不就地改写调用方传入的 dict（构造随后字段校验失败时，不得腐蚀…输入）"

- **位置**：`src/tree_walker/agent/views.py:161`
- **类别**：correctness

**失败场景**：`AgentHistory.model_validate({'step_number': 1, 'model_output': {'actions': [{'name': 'click', 'params': '561257'}]}, 'result': 'not-a-list'})` 对 result 抛 ValidationError，但调用方 dict 已被改写为 params={} 并物化 actions/镜像（实证，含 `load_from_dict` 所用的嵌套 `AgentHistoryList.model_validate` 路径——web /load 与 rerun detect 把 request/json dict 直接传入）。仓库守卫测试 `test_failed_construction_does_not_mutate_caller_dict` 只走 kwargs 构造（pydantic 会新建 data dict），抓不到该路径。

### 3. CONFIRMED — 升级分支的 judge 运行恰在其注释声称保护的场景中是静默空转：降级步是 done 步时该步从未进 history（其 `_finalize` 失败），`_run_judge` 的守卫 `if not self._judge or not self.history.is_done(): return`（`agent.py:402`）在评判前退出

- **位置**：`src/tree_walker/agent/agent.py:317`
- **类别**：correctness

**失败场景**：前 2 步 `_finalize` 失败且 done 步的 `_finalize` 也抛错 → `finalize_degraded_steps` 达 3 → `run()` 带 done=True、`self._judge` 已设进入升级分支 → `await self._run_judge()` → `history.is_done()` 检查 `history[-1].result`，但 done 步不在 history → 未评判即返回。第六轮 #7 的"最需要独立验证的 run 不丢 verdict"恰在注释点名的降级-done 情形下失效（若该步是第一步，history 为空、`final_result()` 也是 None）。

### 4. PLAUSIBLE — `run()` 按**累计不清零**计数器中止（`finalize_degraded_steps >= 3`，第六轮 #4/#5），而 `AgentState` 字段注释（`views.py:84-88`）写"run() **连续**达阈值时升级终止"——3 次分散的瞬时 `_finalize` 失败会杀掉接近完成的 run，注释与代码的矛盾还会诱使未来"修"回洗掉 bug 的老路

- **位置**：`src/tree_walker/agent/agent.py:311`
- **类别**：correctness

**失败场景**：100 步 run 的第 5、50、95 步 `_finalize` 瞬时抛错（如 history append/StepMetadata 的间歇错误）：计数器从不清零，第 95 步 `>= 3` 触发、`run()` 以 "aborting run" break——动作已执行、任务在 95% 完成度被中止，与确定性 `_finalize` bug 不可区分。相信"连续"注释的维护者改为成功时清零，就会重新引入第六轮 #4/#5 刚关闭的交错洗掉问题。

### 5. CONFIRMED — `update_action_params` 假定每个 actions 条目是 dict（`actions[action_index].setdefault("params", {})`），但本 PR 的 history 策略有意保留裸字符串/None 条目（第七轮 #2），validator 还会把旧格式 `{"action": "click"}` 物化为 `actions: ["click"]`——master 抛有文档的优雅 IndexError，新代码抛 AttributeError

- **位置**：`src/tree_walker/agent/views.py:325`
- **类别**：correctness

**失败场景**：经 `AgentHistoryList.load_from_dict` 加载含 `"model_output": {"action": "click"}` 的 master 时代历史（本 PR 自己测试承认的老格式）→ validator 产出 `actions: ["click"]` → P4 编辑 API `update_action_params(step_number=1, action_index=0, field="text", value="x")` → `"click".setdefault("params", {})` 抛 `AttributeError: 'str' object has no attribute 'setdefault'`——公开变更 API 上的未处理崩溃；新测试只覆盖 params-字符串情形，未覆盖裸字符串条目。

### 6. PLAUSIBLE — 新的 done success 默认（仅当 text 或 data 存在时为 True）翻转重放保真：master 时代以空 params 执行 done 并记录 success=True 的历史，现在重放为 success=False

- **位置**：`src/tree_walker/tools/actions.py:2751`
- **类别**：correctness

**失败场景**：master 时代录制含 `model_output.actions = [{"name": "done", "params": {}}]` 且 result 为 success=True（master 上可达：参数校验梯耗尽 → "proceeding anyway" → 旧默认 `params.get("success", True)` = True）。`rerun_history` 重执行 `_action_done({})` → text、data 均 None → success=False → 重放 run 的最终结果/摘要把原本成功的 run 报为 FAILURE——恰在 rerun 模块致力保持的"原始/重放一致"不变量上分叉。

### 7. PLAUSIBLE — `_build_agent_history_description` 现在把 **name key 缺失**的 dict 渲染为 "done(...)"（`name_of` 的缺 key 默认），master 原本渲染 "?"（`a.get('name', '?')`）；`actions_of` 的 `[{}]` 兜底还能渲染出幻影 "done({})" 行——每步发给 LLM 的 `<agent_history>` 块现在会声称中步执行过 done

- **位置**：`src/tree_walker/agent/agent.py:588`
- **类别**：correctness

**失败场景**：LLM 输出 `[{"name":"click","params":{...}}, {"params":{}}]`（中段条目缺 name key）：`_has_invalid_name` 对缺 key 返回 False，归一化不动它；`_is_valid_action` 只查 `actions[0]` 镜像，于是它进入 history。之后每步的滑动窗口 `<agent_history>` 把该步渲染为 "click(…), done({})"，master 原本渲染 "click(…), ?"——模型被告知 done 已执行，可能据此认为任务已终止。

### 8. CONFIRMED — 每个实况步骤对同一 model_output 归一化三次（`client.py:372` choke point、`step.py:665`、`step.py:190`）——`_step:190` 这层是死防御（到达它的 model_output 全部来自 `_get_next_action`，其每条返回路径都已归一化），且每层对有意保留的畸形条目重发同一条 WARNING

- **位置**：`src/tree_walker/agent/step.py:190`
- **类别**：efficiency

**失败场景**：小模型持续输出 `[valid, null, valid]`：每步日志 "action[1] malformed (NoneType, live) — left as-is" 打三遍（每归一化层一次，已执行验证）；200 步 run 在 console/JSONL obs 流产生 ~600 条重复告警，淹没真实信号，外加每层每步一次冗余 O(#actions) 重扫。更简：删掉 `step.py:190` 的调用（或对 left-as-is 日志按形态去重）。

### 9. PLAUSIBLE — `_redact_history_data` 新增的镜像脱敏分支（`actions = actions + [mirror]`）——因 `model_dump` 把共享的 `actions[0]`/`action` 引用复制进两个 dict 而加——没有测试构造"列表条目与镜像是不同拷贝"的 history

- **位置**：`src/tree_walker/agent/views.py:204`
- **类别**：test-coverage

**失败场景**：CLAUDE.md（单元测试要求）："新增功能或修改功能时必须同步增加测试用例，覆盖正常路径和关键边界情况。" 唯一相关测试（`test_save_redacts_only_input_action_params`）用的是无 actions 列表的旧格式 model_output——回退镜像修复会让敏感值（如 input_text 密码）经镜像拷贝重新泄漏进保存的 JSONL 且零测试失败——一个没有任何测试钉住的安全回归，而本 PR 自己的测试注释把"回退该修复零测试失败"定义为不可接受标准。

### 10. CONFIRMED — 全新模块 100% 空格缩进（150 行空格缩进、0 行 tab），违反仓库显式 tab 规则——同一 PR 的另一个新文件 `tests/test_step_malformed_action.py` 全程用 tab

- **位置**：`src/tree_walker/action_shape.py:47`
- **类别**：conventions

**失败场景**：CLAUDE.md（代码风格）："缩进使用制表符（tab），不使用空格。编辑文件时务必确保缩进字符与现有代码一致，否则 Edit 工具会因为字符不匹配而失败。" `action_shape.py` 是新建文件、无可匹配的既有风格，但每个块级行都用 4 空格；代价即规则自述的后果（未来 Edit 工具编辑字符不匹配），加一个 PR 永久分裂两个新文件的缩进约定。

## 处置分级（快速收尾）

**建议微修后合并（3 + 1 顺手）**：

1. **#1**：`_restore_urls_in_output`/`_restore_sensitive_in_output` 的 dict 重建保留 `_HonestDone` 标记（或在 restore 之后重新安装标记）；补一个带非空 messages/敏感数据的 get_action 测试。
2. **#2**：validator 内对 `data["model_output"]` 先做拷贝再改写（覆盖 model_validate 路径）；守卫测试补一条 model_validate 构造路径用例。
3. **#10**：`action_shape.py` 缩进转 tab。
4. 顺手：删 `step.py:190` 的第三次归一化调用（#8）。

**接受留档（7 条，无崩溃类）**：#3（降级 done 步 judge 空转——罕见，仅缺 verdict）、#4（累计 vs 连续权衡——至少把注释改为"累计"消除矛盾）、#5（编辑 API 对旧格式裸字符串条目 AttributeError——仅 P4 编辑 API + 旧数据）、#6（旧录制 done-`{}` 重放 verdict 翻转——仅重放）、#7（`<agent_history>` 幻影 done 渲染——误导但不崩）、#9（镜像脱敏缺测试——后续 PR 补）。

修完 #1/#2/#10 后建议直接合并，不再发起全量评审；如需最终检查用 `/code-review pr#174 low`（低档只报高置信度问题）。

**微修验证**：`unset ALL_PROXY HTTP_PROXY HTTPS_PROXY && uv run python -m pytest tests/test_step_malformed_action.py tests/test_llm_client.py -x -v`（#1/#2 属 client/views 路径）。改动继续落在 `fix/173-malformed-action-params-crash` 分支，PR #174 自动更新。
