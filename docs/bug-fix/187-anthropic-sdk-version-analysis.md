# issue #187 分析：anthropic SDK 版本与 temperature 透传——叙事反转与正确的修复方向

- 日期：2026-09-15；分支基线 master `ef625bd`
- 修订：2026-09-15 v2——eval 仓侧核验（issue 评论）确认核心反转全部属实；纠正本文一处事实错误（§6）
- 方法：三环境实测签名（本仓 venv 0.109.0 / 临时环境 0.122.0、0.125.0、1.5.0）+ PyPI 版本时间线 + eval 仓证据复核（`docs/skills/shop-admin/harness-optimize.md` 2026-09-12 09:04 条目、`.venv` 实况）

## 0. 结论速览——issue 的两个前提都不成立，但底层诉求有效

| issue 的说法 | 实测事实 |
|---|---|
| "TreeWalker 依赖锁定的 anthropic SDK 为 1.4.0（2023 年版本）" | ❌ 本仓 pyproject `anthropic>=0.104.0`、uv.lock **0.109.0**，`Messages.create` **已支持** temperature。1.4.0 是 **eval 仓 venv 当时自行解析出的版本**——且它是 **2026 年新 1.x 线**，不是 2023 老版 |
| "升级 SDK 至当前版本使 temperature 可透传" | ❌ **方向反了**：当前最新版 **1.5.0 的 `Messages.create` 已移除 `temperature`/`top_p`**（参数面换成 `output_config`（effort/format）、container、workspace_id 等）——升级到 1.x 恰恰会**重新引入** eval 仓踩过的那个 TypeError |
| （隐含诉求）LLM 调用可显式控温、judge 确定性 | ✅ 有效——但正确修法是**给依赖封顶 `<1.0`**，不是升级 |

**真正的根因**：`anthropic>=0.104.0` 是**无上界**的宽松约束——本仓 uv.lock 锁在 0.109.0 没事，但任何下游环境（eval 仓以路径依赖装 tree_walker）**重新解析**时求解器可以选到 1.x 线；1.x 移除了 temperature → eval judge 的 `temperature=0` → TypeError → 66 次评测静默计 0。eval 仓 9/12 的"兼容修复"实际是把自己的 venv **降级**到 0.122.0（0.x 线）——症状消除，但 TreeWalker 的 spec 仍然放行 1.x，下一个全新解析的环境会再次踩中。

## 1. 版本考古（三环境实测 + PyPI 时间线）

`Messages.create` 是否含 `temperature`：

| 版本 | 出处 | temperature | 备注 |
|---|---|---|---|
| 0.109.0 | 本仓 uv.lock / venv | ✅ | 当前全量测试（2684 测）在此版本跑 |
| 0.122.0 | 本地 Windows checkout 的 eval 仓 `.venv`（**非 runner 环境**，见 §6 修订） | ✅ | 0.x 线较新版本（2026-08-13）；eval 评测**未在此版本跑过** |
| 0.125.0 | PyPI 最后一个 0.x（2026-08-19） | ✅ | 0.x 线全程支持 |
| **1.4.0** | **eval runner venv（事发至今未变）** | ❌ | **1.x 线**（1.4.0 发布于 2026-09-04）——移除 temperature/top_p；22×3 离线重判即在此版本 + 代码层降级上跑 |
| 1.5.0 | PyPI 当前最新 | ❌ | 参数面：`max_tokens, messages, model, cache_control, container, inference_geo, metadata, output_config, service_tier, stop_sequences, stream, system, thinking, tool_choice, tools, user_profile_id, workspace_id` |

1.x 里采样控制的去处：`output_config: OutputConfigParam`，仅 `effort`（low/medium/high/xhigh/max）与 `format`（structured outputs）——**没有 temperature 等价物**。

兼容面对照（1.5.0 实测）：`RateLimitError`/`APIError` 异常层级 ✅、`Message`/`ToolUseBlock`/`TextBlock` 核心类型 ✅——本仓 LLMClient 的常规用法在 1.x 下多数仍可用，**唯独 sampling 参数断裂**；且 1.x 新增的平台参数（container/workspace_id/output_config）在 GLM 兼容端点（open.bigmodel.cn/api/anthropic，实现的是 0.x Messages 形状）上大概率不存在——本项目升 1.x 双重不利。

## 2. 事故链复盘（修正版）

1. eval 仓以路径依赖安装 tree_walker，其 venv 重新解析 `anthropic>=0.104.0` → 求解器选当时最新 **1.4.0**（无上界，1.x 合法满足）；
2. 1.x 的 `Messages.create` 无 temperature → judge `_glm_chat_completion` 传 `temperature=0` → 每次调用 `TypeError: unexpected keyword argument 'temperature'`；
3. 评测器"异常计 0"吞掉 → 22 个 fuzzy/ua 任务 × 三口径 = **66 次评测全部判 0**，运行期间零发现；
4. eval 仓 9/12 修复：**纯代码层** try/TypeError 降级（去掉 temperature 用端点默认温度重试），**依赖版本未动**（runner venv 仍是 1.4.0）→ 22×3 离线重判全部跑在 1.4.0 + 降级上，翻绿（A +14 / B +19 / C +17，含追加的 task 790）。**代价：eval judge 当前实际是默认采样温度，确定性尚未恢复**——待 TW cap `<1.0` 合入、eval 重装依赖落 0.x 后才成立（eval 仓随后二选一：降级依赖恢复确定性，或接受默认温度并入档）；
5. 遗留：**本仓 spec 仍无上界**——`>=0.104.0` 今天依然允许任何下游环境解析出 1.5.0 并复现事故。

## 3. 对 issue 两请求项的重新解读

- **"升级 SDK 使 temperature 可透传"** → 实际动作是**收紧约束**：`anthropic>=0.109,<1.0`。`_extract_call` 本就是 `**create_kwargs` 透传封装（client.py:443-455），temperature 早已可用——缺的不是代码路径，是**版本契约**。
- **"注意 _extract_call / fallback 兼容性"** → fallback client 构造（`Anthropic(api_key=..., base_url=...)`）与主 client 同构，0.109→0.122 无签名变化（同 0.x 线），无兼容工作量的实际需求；需要做的是升级后的全量回归 + 一条锁契约的单测。

## 4. 修复方案

**P0（spec 修正 + 契约锁定）**：
1. pyproject：`anthropic>=0.109.0,<1.0`，注释说明上界理由（1.x 移除 temperature/top_p，采样参数断裂；GLM 兼容端点为 0.x 形状）；
2. `uv lock` 刷新至 **0.122.0**——理由（v2 修订）：0.x 线内较新版本（2026-08-13），非"与 eval 实证环境对齐"（eval 从未在 0.122 跑过，见 §6）；不追 0.125 仅取保守；
3. 单测：`_extract_call(temperature=0)` 透传断言（mock client 断言 kwargs 收到 temperature）——把"本仓契约支持控温"钉进测试；
4. 可选加一条 spec 守护测试：解析 pyproject 的依赖声明断言含 `<1` 上界（防未来"顺手升级"把 cap 抹掉）；
5. **cap 合入后的回归步骤（时序关键，v2 修订）**：eval 仓此时再新起干净 venv 重解析 tree_walker → anthropic 落 0.x——spec 未 cap 前做该检查会合法解析到 1.5.0，反而误判验收失败；eval judge 确定性的恢复也以此为前提。

**不做（附理由）**：
- 升级 1.x：与 issue 目标（temperature 可透传）**直接冲突**（1.x 无此参数）；GLM 兼容端点不支持 1.x 平台参数；无本生态验证。
- 迁移 `output_config.effort`：语义不同（effort 分档 ≠ 温度控采样），且 GLM 端点不认——等真实需求与端点支持再议。
- 在本仓调用点全面加 temperature 形参：本仓自身调用（agent 主循环/judge/extract）当前无控温需求记录在案；需求方是 eval 仓 judge（外部调用 `_extract_call` 已可用）。先不动接口。

**风险**：无——0.109→0.122 同 0.x 线小版本差，全部测试 mock SDK 层，回归成本一次全量跑。

## 5. 验收清单

- [ ] `uv run python -c "import anthropic; print(anthropic.__version__)"` → 0.122.0
- [ ] `Messages.create` 签名含 temperature（0.122 实测 ✅）
- [ ] 新增透传单测 + spec 守护测试通过
- [ ] 全量 `uv run python -m pytest tests/ -x -v` 绿、覆盖率 >85%
- [ ] （**cap 合入之后**才做，v2 修订）eval 仓新起干净 venv 重解析 tree_walker → anthropic 落在 0.x（<1.0 生效的直接证据；cap 前重解析会合法选到 1.5.0）

## 6. 修订记录（2026-09-15 v2，依据 eval 仓侧核验）

eval 侧对本文逐条实证复核（issue #187 评论）：**核心反转全部属实**（无上界 spec、0.109 含 temperature、1.4/1.5 不含、PyPI 时间线、1.5 参数面一字不差、`_extract_call` 透传），P0 方案确认可执行无保留意见。**纠正本文一处事实错误及其连带论据**：

- ❌ 原文称"eval 仓 venv（9/12 修复后）0.122.0 / 9/12 修复实为降级 / task 119 重判在 0.122 验证"——**均不实**：eval runner venv 至今仍为 **1.4.0**，9/12 修复是纯代码层 TypeError 降级，66 次重判全部跑在 1.4.0+降级上。
- 错误来源：分析时 `ls` 的是**本地 Windows checkout** 的 eval 仓 `.venv`（恰装 0.122.0），与 **Linux runner** 的 venv（1.4.0）是两台机器两套环境——同名仓多环境，检证必须指认运行侧环境。
- 连带修正：(1) eval judge 当前为默认采样温度、确定性未恢复，恢复以 TW cap 合入 + eval 重装依赖为前提（§2 步骤 4）；(2) lock 0.122 的论据改为"0.x 线内较新版本"（§4.2）；(3) "干净 venv 重解析落 0.x"的验收挪为 cap 合入后的回归步骤（§4.5/§5）。

## 附：证据索引

- 本仓：`pyproject.toml:8`（`anthropic>=0.104.0` 无上界）、`uv.lock`（0.109.0）、`src/tree_walker/llm/client.py:443-455`（`_extract_call` 透传封装）、`:67/:80`（主/fallback client 构造）
- 实测：0.109/0.122/0.125 temperature ✅，1.4(事发)/1.5 ❌；1.5.0 参数面与 `OutputConfigParam` 定义（effort/format，无 temperature 等价物）
- eval 仓：`docs/skills/shop-admin/harness-optimize.md` L133-157（三级证据 + TypeError 降级修复 + task 119 验证 1.0）、`.venv` 现装 0.122.0、追加受害任务 790 的分段日志教训
- PyPI：0.125.0（2026-08-19，最后 0.x）→ 1.x 线（最新 1.5.0）
