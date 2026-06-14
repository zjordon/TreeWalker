# Tools 子系统技术细节文档：browser-use vs TreeWalker 工业级评估

> 评估时间：2026-06-14
> 评估对象：
> - **A 方案**：`D:\dev\git\z_jordon\browser-use\docs\Tools技术细节`（browser-use 主线）
> - **B 方案**：`D:\dev\git\z_jordon\TreeWalker\docs\Tools技术细节`（本项目 TreeWalker）
> 判据：**工业级可用性**（非 demo）

---

## Context

用户拥有两套"Tools 技术细节"文档：
- **A 方案**：browser-use 项目（成熟开源，star 数万，PyPI `browser-use`）
- **B 方案**：TreeWalker 项目（v0.1.0，自称"基于 browser-use 架构的简化重写"，去除 Playwright 依赖，改用 `cdp-use` 直连 CDP）

用户明确表示：**需要一个工业级可用的实现，而不是 demo**。因此本评估的判据不是"哪个文档更漂亮"，而是：

1. 文档与代码一致性（可审计、可追溯）
2. 错误处理与容错机制的完整度
3. 边界情况覆盖（iframe / shadow DOM / 框架受控组件 / CJK）
4. 可观测性、可配置性、可扩展性的工程化深度
5. 技术栈选型对未来生产环境的契合度
6. 长期维护与社区生态风险

---

## 一、客观对比矩阵

### 1.1 文档体量与结构

| 维度 | browser-use (A) | TreeWalker (B) |
|---|---|---|
| 文档文件数 | 8 个 | 6 个 |
| 总行数 | ~1,800 行 / 80KB | ~2,400 行 / 80KB |
| 核心文档 | `05-动作详解`（17KB）+ `06-动作详解`（10KB）+ `08-long_term_memory`（18KB） | `04_动作清单与CDP映射`（1,274 行）单一巨文档 |
| 视觉辅助 | Mermaid 类图、序列图、流程图 | Mermaid 类图、序列图、CDP 调用矩阵表 |
| 行号引用 | ✅ 大量，部分偏移需校验 | ✅ 大量，经 Grep 验证准确 |

**判定**：B 文档密度更高，单文件 1,274 行覆盖 24 个动作的完整代码 + CDP 映射，可读性略弱但信息密度更高；A 拆分更细，查找更便利。

### 1.2 Tool 覆盖范围

两边都注册 **24 个 action**，清单几乎一一对应（navigate / click / input_text(input) / scroll / search / extract / send_keys / switch_tab / close_tab / wait / go_back / find_elements / find_text / screenshot / save_as_pdf / dropdown_options / select_dropdown / upload_file / write_file / read_file / replace_file / evaluate / search_page / done）。

| 差异点 | browser-use (A) | TreeWalker (B) |
|---|---|---|
| 文档完整度 | 23 个有详解，`wait` 未在 05/06 详述 | 24 个全部独立小节 |
| Schema 暴露方式 | LLM 输出 `AgentOutput.action: list[ActionModel]` Union | LLM 输出**单一** `agent_response` tool，24 个 action 作为 `action.name` enum 值 |

**判定**：B 的覆盖更彻底，无遗漏；A 在 Schema 设计上沿用 browser-use 主线（多 action 并行），B 改用 Anthropic 原生 tool_use（单 tool + enum 字段）。后者更贴近 Anthropic SDK 最佳实践，但放弃了 multi-act 并行的延迟优势。

### 1.3 错误处理与容错

| 机制 | browser-use (A) | TreeWalker (B) |
|---|---|---|
| 网络错误分类（navigate） | ✅ 4 类（`ERR_NAME_NOT_RESOLVED`/`ERR_CONNECTION_REFUSED`/`ERR_TIMED_OUT`/`ERR_TUNNEL_CONNECTION_FAILED`），源码含 `ERR_INTERNET_DISCONNECTED` 与 `net::` 通配 | 部分实现 |
| 重试链（navigate） | ✅ 空 DOM → 等 3s → reload → 等 5s（service.py:498-523） | ✅ 类似但更简化 |
| LLM 参数校验失败 | 未单独文档化重试链 | ✅ 3 层重试 + `_FALLBACK_DONE_OUTPUT` 兜底（step.py:33-38, 410-451） |
| 连接错误识别 | 未单独文档化 | ✅ `_is_connection_error` 单独识别后**触发重连而非吞错** |
| 熔断器（Circuit Breaker） | ❌ 无 | ✅ `browser/circuit_breaker.py`（81 行，closed/open/half_open 三态） |
| CDP 批处理超时 | 单命令超时 | ✅ `browser/cdp_timeout.py`（196 行）两阶段超时 + 重试 |
| 循环检测 | ✅ 文档提及"循环检测器"（03.md:79-80） | ✅ 独立模块 `loop_detector.py` |
| 消息压缩 | 未单独文档化 | ✅ 独立模块 `message_compactor.py` |

**判定**：B 在容错机制的**模块化与可观测**上明显胜出（独立的熔断器、CDP 批处理、循环检测、消息压缩均为独立模块）；A 的网络错误分类更细但散落在单文件中。

### 1.4 边界情况覆盖

| 场景 | browser-use (A) | TreeWalker (B) |
|---|---|---|
| iframe 穿透 | ✅ `DOM.getDocument(depth=-1, pierce=True)` | ✅ 同上 + `max_iframes` 上限 + 自动发现 iframe target |
| shadow DOM | ✅ `pierce=True` | ✅ `pierce=True` + `find_file_inputs_in_shadow_dom` 专门方法 |
| **Vue/React 控制的输入框** | ⚠️ 通用 input 实现，未专门处理框架受控组件 | ✅ **完整 `_trigger_framework_events` JS 注入**（session.py:604-669），处理 `InputEvent`、Vue `__vue__/_vnode/__vueParentComponent__` 双版本、`setTimeout(0)` 异步队列、React `onChange` 兼容 |
| CJK 字符输入 | ⚠️ 文档未单独说明 | ✅ 非 ASCII 只发 `char` 事件，跳过 keyDown/keyUp（session.py:572-590） |
| 文件上传 | ✅ 找就近 `<input type="file">` | ✅ `_pick_nearest_file_input` 双策略（DOM 树遍历 + 坐标距离，actions.py:69-107）+ `_allowed_upload_paths` 白名单 |
| `<select>` 自动转换 | ✅ click 检测 `<select>` 自动转 dropdown | ✅ 同上 |
| 新标签页检测 | ✅ click 后检测自动切换 | ✅ 同上 |

**判定**：B 在**框架受控组件兼容性（Vue/React）和 CJK 输入**上有决定性优势——这正是当前 git 分支 `feat/fix-bilili-tag-input_text` 正在修复的真实生产问题；A 在这两项上几乎空白。

### 1.5 可观测性 / 可配置性 / 可扩展性

| 工业级特征 | browser-use (A) | TreeWalker (B) |
|---|---|---|
| 可观测性 | `ActionResult.metadata` + `ProductTelemetry` 字段 | ✅ **完整 Observability 子系统**：`observability/` 目录含 `event_bus.py` / `events.py` / `metrics.py` / `anomaly_detector.py` / `session_evaluator.py` / `jsonl_recorder.py` / `decision_prompt.py`，`StepPipeline` 发射 `ToolCallEvent` / `ToolResultEvent` / `StepEndEvent` |
| 可配置性 | `exclude_actions` / `domains` 域名过滤 / `output_model` 双变体 / `set_coordinate_clicking` 模型自适应 | ✅ `AgentSettings` / `LLMSettings` / `BrowserSettings` / `JudgeSettings` / `TruncationSettings` / `action_page_filters`（URL glob 过滤）/ `max_steps/max_failures/action_timeout/llm_timeout` |
| 可扩展性 | ✅ `@registry.action()` 装饰器，Type 1/Type 2 注册 | ✅ 声明-绑定双轨制：加 `_action_*` 方法 + 一条 `ACTION_DEFINITIONS` 记录即可 |
| TOTP 2FA | ✅ `pyotp.TOTP(..., digits=6).now()`（registry/service.py:463-466） | ❌ 无 |
| `<secret>` 占位符 | ✅ Registry 执行前替换 + 日志脱敏 | ✅ `sensitive_data` 占位符替换（client.py:124-163），实现不同 |
| 域名级敏感数据隔离 | ✅ `sensitive_data` 按域名 | ❌ 未单独实现 |
| `long_term_memory` 信道 | ✅ **专门的 `08-long_term_memory` 文档（18KB）**，逐 action 列出设置位置 | ⚠️ 通过 `ActionResult.extracted_content` 隐式进入上下文，无显式信道 |
| Plan Manager | ❌ 无 | ✅ `plan_manager.py` 独立模块 |
| Judge 评估器 | ❌ 无 | ✅ `judge.py` 独立模块 + `JudgeSettings` |

**判定**：
- A 在**与业务无关的通用机制**（TOTP、域名级敏感数据隔离、`long_term_memory` 显式信道）上更完整。
- B 在**生产运维侧**（Observability 全套、Plan、Judge）上明显胜出——这些是工业级 Agent 在生产环境长期运行必需的组件。

### 1.6 技术栈与依赖风险

| 维度 | browser-use (A) | TreeWalker (B) |
|---|---|---|
| 浏览器层 | **Playwright**（含 driver，~150MB） | **`cdp-use`** 库（直连 CDP WebSocket，<5MB） |
| LLM SDK | OpenAI / Anthropic / 通用 | Anthropic 原生 + 智谱 GLM 兼容网关 |
| TUI | 无 | `textual`（`tw-tui` CLI） |
| 包管理 | pip / poetry | **uv** |
| Python | ≥3.11 | ≥3.12 |

**风险分析**：
- A 依赖 Playwright，浏览器 driver 由 Playwright 团队维护，**升级 Chrome 大版本时 Playwright 通常滞后数天到数周**；但社区生态成熟，故障可 google。
- B 直接用 CDP，**Chrome 升级当天即可用**，无中间层；但 `cdp-use` 是小众库，维护风险高，**B 实际上等于自维护一个 CDP 客户端**。

---

## 二、专业结论

### 2.1 直接结论

> **就工业级生产可用性而言，TreeWalker (B) 在工程结构、可观测性、容错模块化、以及对真实生产边界（Vue/React 受控组件、CJK）的覆盖上明显领先于 browser-use (A)；但 browser-use 在生态成熟度、通用机制完整度（TOTP / 域名级敏感数据 / long_term_memory 显式信道）和长期维护风险上更稳。**
>
> **二者并非"哪个更好"的对立选择，而是处于工业成熟度曲线的不同位置**：A 是"广度优先的成熟基础设施"，B 是"深度优先的现代化重写"。

### 2.2 各自的工业级定位

**browser-use (A) 的工业级定位**：
- ✅ 适合**通用浏览器自动化平台**：TOTP 2FA、域名级敏感数据、long_term_memory 等机制面向的是 SaaS 化部署场景
- ✅ 适合**对生态依赖度高的团队**：Playwright 社区、Stack Overflow 答案、第三方教程丰富
- ⚠️ 不适合**需要精细控制 CDP 时序和性能**的场景：Playwright 中间层增加延迟
- ⚠️ 不适合**Vue/React 单页应用密集**的国内业务（如 bilibili、淘宝等）：受控组件兼容性问题真实存在

**TreeWalker (B) 的工业级定位**：
- ✅ 适合**国内业务场景**：Vue/React 受控组件兼容、CJK 输入、智谱 GLM 网关原生支持
- ✅ 适合**需要长期生产运维**的 Agent：Observability / Circuit Breaker / Plan / Judge 均为生产环境必备
- ✅ 适合**性能敏感**场景：直连 CDP，无 Playwright 中间层
- ⚠️ 不适合**当下立即上线**：v0.1.0、依赖 `cdp-use` 小众库、社区生态空白
- ⚠️ **TOTP / 域名级敏感数据隔离 / long_term_memory 显式信道**是真实缺口

### 2.3 针对"工业级可用"的最终建议

**如果你的诉求是"今天就要上生产、稳定压倒一切"** → 选 **browser-use (A)** + 借鉴 B 的 Vue/React 兼容性补丁（`_trigger_framework_events` 思路可移植）。

**如果你的诉求是"自研可控、为长期演进打基础、且核心场景是受控组件密集的国内 Web 应用"** → 选 **TreeWalker (B)**，但需补齐以下三个工业级缺口：

1. **TOTP 2FA 支持**（移植 browser-use 的 `pyotp` 集成，约 30 行代码）
2. **域名级 `sensitive_data` 隔离**（在 `LLMClient` 占位符替换逻辑中加入按 URL host 的过滤规则）
3. **`long_term_memory` 显式信道**（在 `ActionResult` 中加入布尔字段，明确区分"持久记忆"vs"一次性结果"，避免上下文膨胀）

### 2.4 文档质量的次要结论

**作为"技术细节文档"**：
- B 文档（2,400 行 / 1 个核心 1,274 行的 04_动作清单）**信息密度更高、源码-文档对应更精确**，更适合作为二次开发蓝本
- A 文档（8 个文件 / 含独立的 `08-long_term_memory` 18KB 专题）**专题深度更高、面向不同读者分层更清晰**，更适合作为教学/培训材料

**判定**：B 的文档已经达到工业级"内部技术白皮书"水准；A 的文档达到工业级"对外 API/扩展指南"水准。两者文档质量都属于上乘，**文档本身不构成决策瓶颈**。

---

## 三、关键证据索引（供后续核查）

### browser-use (A) 关键证据
- `docs/Tools技术细节/01-架构概览.md:42-67` —— 目录结构 + 行数自述
- `docs/Tools技术细节/05-动作详解-浏览器交互.md:69-74` —— navigate 网络错误分类
- `docs/Tools技术细节/06-动作详解-数据处理与文件.md` —— extract/search_page/find_elements 详解
- `docs/Tools技术细节/07-CDP操作汇总.md:130-263` —— 9 个 CDP 域方法表
- `docs/Tools技术细节/08-long_term_memory字段详解.md` —— 18KB 专题文档
- `browser_use/tools/service.py:498-523` —— navigate 重试链源码
- `browser_use/tools/service.py:544-553` —— 网络错误字符串源码
- `browser_use/tools/registry/service.py:463-466, 480-482` —— TOTP 源码

### TreeWalker (B) 关键证据
- `docs/Tools技术细节/README.md:82-114` —— 术语速查 + 源码索引
- `docs/Tools技术细节/04_动作清单与CDP映射.md` —— 1,274 行核心文档
- `src/tree_walker/browser/session.py:572-590` —— CJK 字符处理
- `src/tree_walker/browser/session.py:604-669` —— Vue/React 框架事件触发
- `src/tree_walker/browser/circuit_breaker.py` —— 81 行熔断器
- `src/tree_walker/browser/cdp_timeout.py` —— 196 行两阶段超时
- `src/tree_walker/tools/actions.py:69-107` —— 双策略文件输入查找
- `src/tree_walker/observability/` —— 7 个文件的可观测性子系统
- `pyproject.toml:6-13` —— 依赖声明（`cdp-use>=1.4.5`，无 Playwright）
