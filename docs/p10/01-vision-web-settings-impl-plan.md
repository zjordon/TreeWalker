# tw-web 设置面接入视觉通道参数（issue #179）实施方案

> P10 配套收尾。P10 主线（LLM 视觉通道，issue #175 / PR #177）已全链路交付，本方案补
> tw-web 设置面注册表缺的两项：`AGENT_USE_VISION` / `AGENT_LLM_SCREENSHOT_SIZE`。
> 跟踪：issue #179，分支 `feat/179-web-vision-settings`。日期：2026-09-08。

## 0. 背景与目标

P10 交付后，视觉通道的开关与降采样尺寸只能靠**启动 tw-web 前设 env / 写 .env**——设置面
注册表 `_SETTINGS_FIELDS` 成形于 T2 C（PR #166），早于 P10。目标：视觉通道在 web 端
**可配、可校验、默认不变**（`use_vision` 默认 off = 评测红线）。

修复面极小：后端注册表加两行 + SET 校验加一个新 type + 测试；**前端零改动**（§2.3 已核实）。

## 1. 现状与代码事实（已核实）

| 事实 | 位置 |
|---|---|
| 视觉配置定义：`use_vision: bool = False` / `llm_screenshot_size: tuple \| None = None` | `src/tree_walker/config.py:150`、`:153` |
| env 读取：`AGENT_USE_VISION`（默认 false）；`AGENT_LLM_SCREENSHOT_SIZE`（空 = 模型自适应：视觉模型 `(1400, 850)`，文本模型不缩放） | `src/tree_walker/config.py:443-449` |
| 尺寸解析：`_parse_screenshot_size`——空/空白 → None；格式非法 → **warning + None（静默忽略，不炸 load_settings）**；`(\d+)[x×](\d+)` 且 <100px 地板 → 同样忽略 | `src/tree_walker/config.py:46-60` |
| 设置面注册表：16 项 `SettingField`（key/env/type/default/section/choices/sensitive），注释明言「按用户会想调的精选；**加项 = 加一行**」 | `src/tree_walker/web/server.py:303-326` |
| SET 白名单 + 校验：`_validate_setting_value` 现支持 bool/enum/int/float 四 type，非法 raise → 400；**type=str 不做任何校验** | `src/tree_walker/web/server.py:329-343` |
| SET 写入语义：非敏感字段**空串照写** `os.environ`（仅敏感字段跳过空值）→ 空串 = 显式清空 | `src/tree_walker/web/server.py:399-406` |
| GET 数据源：注册表逐项 `os.environ.get(f.env, f.default)` → 前端动态渲染 | `src/tree_walker/web/server.py:351-372` |
| 前端控件映射：bool→checkbox / enum→select / int·float→number / **其余（含未知 type）→ text** | `web_ui/src/components/SettingsShell.tsx:112-134` |
| 前端 DTO：`type: string`，无枚举收窄；SettingsShell 测试用 mock 数据，不枚举后端注册表 | `web_ui/src/api.ts:223-231` |
| 既有 settings 测试：get 默认值/脱敏、set 白名单/校验/原子性、set 影响新 agent | `tests/test_web_server.py:340-438` |

**静默失效坑**（issue #179 已记录）：`AGENT_LLM_SCREENSHOT_SIZE` 若按 type=str 直接入表，
UI 填错（如 `abc`、`50x50`）会被 `load_settings` 静默忽略——用户毫无反馈。故 SET 侧校验
是本方案的必要项，非可选项。

## 2. 方案设计

### 2.1 注册表新增两行（`server.py` `_SETTINGS_FIELDS`，TAB 缩进）

```python
# Agent 区（「Skill 注入」之后）：
	SettingField("视觉通道", "AGENT_USE_VISION", "bool", "false", "agent"),
	# 评测红线：默认 false；开启属视觉增强口径，SR 不与主口径对比（P10 原则）
# 高级区（末尾）：
	SettingField("截图降采样", "AGENT_LLM_SCREENSHOT_SIZE", "size", "", "advanced"),
	# 空 = 模型自适应（视觉模型 (1400,850)，文本模型不缩放）
```

归区决策：

- **视觉通道 → agent 区**：`AgentSettings` 字段 + `AGENT_` 前缀，与「计划模式」「Skill 注入」
  同区；且性质相同——能力开关 + 评测红线默认 off（与 Skill 注入并列最贴切）
- **截图降采样 → advanced 区**：调优参数，默认空即最优（模型自适应），普通用户无需触达

默认值约定（`SettingField` docstring）：**须与 `config.load_settings` fallback 一致**——
`"false"` 与 `""`（空）。否则前端「默认展示 / 重置」与实际生效值错位。

### 2.2 SET 校验：新 type `"size"`

语义：

- **空串放行**（= 清空回模型自适应，与 `load_settings` 的空 → None 一致）
- 非空须 `_parse_screenshot_size(raw)` 解析成功（`WxH` 格式 + ≥100px 地板）

实现（`_validate_setting_value` 加分支，TAB 缩进）：

```python
	if field.type == "size":
		# 空 = 模型自适应；非空须 WxH（≥100px，复用 config 的解析器：单一事实源，正则/地板不漂移）
		if raw and _parse_screenshot_size(raw) is None:
			raise ValueError(f"{field.env} 须为 WxH（如 1400x850，宽高 ≥100px）或留空: {raw!r}")
		return raw
```

import：`from tree_walker.config import load_settings` 已有，追加
`_parse_screenshot_size`（同包 import 私有 helper，与既有 load_settings 同源，可接受；
`_parse_screenshot_size` 对非法值自身会 `logger.warning` 一次，SET 报 400 时多一条日志，无害）。

**备选（不采用）**：在 server.py 复制 4 行正则 + 地板——两处漂移风险；或将
`_parse_screenshot_size` 转 public——改 config 公开面，超出本 issue 最小修复面。

### 2.3 前端：零改动（已核实，非假设）

`SettingsShell.tsx:112-134` 的控件映射对未知 type 兜底为 text 输入框（`"size"` → text）；
`api.ts` 的 `type` 是裸 string。设置面分栏从 GET 动态取。无需改 `web_ui/`，npm 产物
（`src/tree_walker/web/static/`）无需重打包。跑一遍 web_ui 测试确认即可（§4.4）。

### 2.4 明确不做（出界）

- **帮助文案机制**（「视觉模式建议调大 LLM 超时」「文本模型收图静默致盲」提示）：
  `SettingField` 无 description 字段，加文案 = 扩结构 + 前端渲染，超出最小面 → 后置
- **不动 `use_vision` 默认值**（评测红线）；**不**把 `model_supports_vision` 名单硬编码进前端
- **不**动 config.py / agent 侧任何代码（P10 已交付，纯补 web 配置面）

## 3. 实施步骤

1. `server.py`：`_SETTINGS_FIELDS` 加两行（§2.1）
2. `server.py`：import `_parse_screenshot_size` + `_validate_setting_value` 加 size 分支（§2.2）
3. `tests/test_web_server.py` 扩测试（§4，4 空格缩进）
4. `uv run python -m pytest tests/ -x -v` 全量绿（当前基线 2571 测）
5. web_ui `npm test` 确认零改动假设（SettingsShell mock 驱动，预期全绿）
6. 真机验收（用户手动，runbook §5）

## 4. 测试计划

### 4.1 get：新字段就位（`tests/test_web_server.py`，扩 `test_settings_get_defaults_and_masking`）

- `AGENT_USE_VISION`：section=agent、type=bool、default="false"、value="false"
- `AGENT_LLM_SCREENSHOT_SIZE`：section=advanced、type=size、default=""、value=""

### 4.2 set：合法路径

- `{"AGENT_USE_VISION": "true"}` → 200，`os.environ` 写入（复用既有 monkeypatch 清理模式）
- `{"AGENT_LLM_SCREENSHOT_SIZE": "1400x850"}` → 200；`""`（清空）→ 200
- bool JSON 字面量 `true` → 规范化为 `"true"`（既有行为，顺带覆盖）

### 4.3 set：校验拒绝

- `"abc"` / `"1400"` / `"1400x"` → 400（格式）
- `"50x50"` / `"0x0"` → 400（<100px 地板——config 语义对齐）
- `{"AGENT_USE_VISION": "yes"}` → 400（bool 分支，回归）

### 4.4 语义端到端（仿 `test_settings_set_affects_new_agent`）

set `AGENT_USE_VISION=true` + `LLM_MODEL=glm-5.3-flash` → `load_settings()`：
`agent.use_vision is True` 且 `agent.llm_screenshot_size == (1400, 850)`（模型自适应默认生效）；
再 set `AGENT_LLM_SCREENSHOT_SIZE=1200x700` → `(1200, 700)`（显式覆盖优先）；
清空 `""` → 回 `(1400, 850)`。

### 4.5 回归与前端

- 既有 settings 测试（白名单 400 / 原子性 / 敏感跳过 / new-agent 生效）不破
- `cd web_ui && npm test`（预期零改动全绿；若快照枚举字段则同步——非预期）

## 5. 真机验收 runbook（用户手动）

1. Chrome 9223（tw-web 专用端口，勿碰 9222）+ `uv run tw-web`
2. 设置面：Agent 区出现「视觉通道」（默认关）、高级区「截图降采样」（默认空）
3. 开「视觉通道」+ LLM 模型切 `glm-5.3-flash` → 应用 → 新任务问视觉问题
   （如「页面顶部导航栏的背景色是什么」）
4. 证据：live 控制台回流消息含图，或任务历史 `screenshot_path` 落盘（`_finalize` 存原图）
5. 关「视觉通道」→ 新任务消息不含 image block（与现状零差异）
6. 降采样填 `abc` / `50x50` → 表单报 400（`须为 WxH（如 1400x850，宽高 ≥100px）或留空`）
7. 视觉模式建议同步调大「LLM 超时(秒)」（P10 验收：视觉调用偶发 >120s）

## 6. 验收标准（对齐 issue #179）

1. 设置面出现「视觉通道」开关（Agent 区）与「截图降采样」输入（高级区），默认 false / 空
2. UI 开启视觉 + 模型切视觉模型 → 下一任务每步 state 消息带图（回流契约：
   `agent.messages[-1]["content"]` 为 list 且含 `type=="image"`）；关闭后零差异
3. `GET /settings/get` 返回两新字段；SET 白名单生效；非法尺寸（abc / 50x50）报 400
4. 全量测试绿；注册表默认值与 `config.load_settings` fallback 一致（测试断言锁定）

## 7. 风险与注意

| 风险 | 缓解 |
|---|---|
| 评测口径污染 | `use_vision` 默认 off 不变；视觉口径分列报告（P10 红线沿用） |
| 注册表默认值与 config fallback 漂移 | 默认值 `"false"` / `""` 由 §4.1 测试断言锁定 |
| `_parse_screenshot_size` 私有 import 耦合 | 同包内已有 `load_settings` 先例；签名稳定（P10 刚交付，有 58 例测试护着） |
| 空串 SET 语义歧义（用户以为「没设」） | 空串本就 = 模型自适应默认，GET 显示与 default 一致，无错位 |
| 文本模型 + 视觉开 → 静默致盲 | P0 已知发现；本方案不加 UI 提示（§2.4 后置），验收 runbook §5.3 用视觉模型规避 |

## 8. 参考

- issue #179（本方案实施跟踪）；issue #175 / PR #177（P10 交付，commit 1738987）
- 配置定义：`docs/tools-optimize/screenshot.md` §2.4；验收记录：同文档「阶段二真机验收记录」
- 注册表与约定：`src/tree_walker/web/server.py` `SettingField` docstring
- ROADMAP P10（✅ 已完成，2026-09-07）
