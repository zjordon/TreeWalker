# screenshot 工具优化方案（分阶段）

> 参照 browser-use（`browser_use/browser/session.py:3898-3955` take_screenshot、`browser_use/agent/prompts.py:360-386` _resize_screenshot、`prompts.py:428-458` image block 构造）完善本项目 screenshot 工具。
> 相关现状文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.15 节；参考标杆：`browser-use/docs/Tools技术细节/05-动作详解-浏览器交互.md` 的 14. screenshot 节。

---

## Context（为什么做这个改动）

当前 TreeWalker 的 screenshot 子系统存在一个**根本性断路**与若干能力缺口：

1. **断路（最严重）**：`step.py:129` 每步 `get_state(include_screenshot=True)` 触发一次 `Page.captureScreenshot` + 解码几百 KB bytes，但 `build_state_message`（`prompts/system_prompt.py:113-201`）**只产纯文本 `str`、从不读 `browser_state.screenshot`**；`LLMClient` 整条消息通道只认 `str` content（`client.py:82-84`、`:134-135` 均 `if not isinstance(content, str): continue`）。**截图字节从未回流给 LLM —— 每步白白付出 CDP 成本**。`grep "\.screenshot\b"` 在 `src/` 下除赋值外零读取。
2. **工具能力贫乏**：`take_screenshot`（`session.py:711-717`）写死 `{"format":"png"}`、零参数；`_action_screenshot`（`actions.py:823-830`）不透传任何参数、不传 `save_path` 时返回空 `ActionResult()`；`ScreenshotParams`（`models.py:104-106`）只有 `save_path` 且 `extra="forbid"`。
3. **无降采样**：即便将来打通视觉，原始设备像素比 PNG 会直接灌进 messages，token 爆炸。
4. **截图前无等待**：`_wait_for_page_settle`（`session.py:776-807`）存在但截图路径不调用，首屏 lazy load/动画会拍空。
5. **错误静默**：`get_state` 内 `except Exception: pass`（`session.py:693`）吞掉所有截图失败，无任何日志。

**已确认的决策（用户已选）：**
- 方案深度 = **分阶段**：阶段一聚焦「工具完善 + 断路止血」（不碰视觉通道），阶段二独立规划「打通 LLM 视觉通道」。两阶段都在本文档内、标注优先级。
- 降采样 = **PNG + 尺寸降采样**（PIL LANCZOS，失败回退原图），不引入 JPEG/quality 路径。
- 工具参数 = **完整扩展**（format / quality / clip / full_page）。

预期结果：阶段一让 screenshot 工具全参数化、降采样能力就位、断路止血（每步不再白拍）、错误可观测、测试覆盖 ≥85%；阶段二在配置开启时让 LLM 真正「看见」每步降采样截图。

---

## 工程约束（实施时务必遵守）

- Windows + PowerShell；包用 `uv`，跑脚本/测试用 `uv run python ...`。测试命令 `uv run python -m pytest tests/ -x -v`。
- **缩进按文件**（已复核）：`src/tree_walker/dom.py`、`views.py`、`config.py` 的 `HighlightSettings`/`TUISettings` 段 = **TAB**；`session.py`、`actions.py`、`models.py`、`system_prompt.py`、`agent/*.py`、`llm/client.py`、`config.py` 的 `AgentSettings`/`LLMSettings`/`BrowserSettings` 段 = **4 空格**；tests 大多 TAB（`test_system_prompt.py` 除外）。下文代码片段均按目标文件缩进给出。
- 改完跑相关单测 + 全量回归；覆盖率目标 >85%。
- 不主动 `git commit` / `git push`。

---

## 阶段一：screenshot 工具完善 + 断路止血（优先做，风险低）

### 1.1 `take_screenshot` 参数化（`session.py:711-717`，4 空格）

```python
async def take_screenshot(
    self,
    format: str = "png",
    quality: int | None = None,
    clip: dict | None = None,
    full_page: bool = False,
    wait_settle: bool = False,
) -> bytes:
    """Capture a screenshot of the current viewport.

    Args:
        format: 'png' | 'jpeg' | 'webp'.
        quality: 0-100, only effective when format == 'jpeg' (CDP constraint).
        clip: optional rect {'x','y','width','height'} in CSS px (scale forced to 1).
        full_page: capture the full scrollable page (captureBeyondViewport=True).
        wait_settle: poll document.readyState to 'complete' before capturing.

    Raises:
        RuntimeError: if CDP returns no 'data' field.
    """
    if wait_settle:
        try:
            await self._wait_for_page_settle()
        except Exception as e:
            logger.warning("Pre-screenshot wait_settle failed: %s", e)

    params: dict = {"format": format}
    if full_page:
        params["captureBeyondViewport"] = True
    if quality is not None and format == "jpeg":
        params["quality"] = int(quality)
    if clip is not None:
        params["clip"] = {
            "x": clip.get("x", 0.0),
            "y": clip.get("y", 0.0),
            "width": clip.get("width", 0.0),
            "height": clip.get("height", 0.0),
            "scale": 1,
        }

    try:
        result = await self.client.send.Page.captureScreenshot(
            params,
            session_id=self.current_session_id,
        )
    except Exception as e:
        logger.warning("Page.captureScreenshot failed: %s", e)
        raise

    if not isinstance(result, dict) or "data" not in result:
        raise RuntimeError("Screenshot failed - no data returned")

    return base64.b64decode(result["data"])
```

**关键决策：** `quality` 仅 `format=="jpeg"` 进 params（CDP 硬约束，对齐 browser-use）；`clip` 强制 `scale=1`；`captureBeyondViewport` 仅 `full_page=True` 时加；`wait_settle` 默认 False（避免每步自动截图变慢），工具层 full_page 截图时传 True；session 层抛异常不吞。**回归兼容**：默认实参等价旧行为，`session.py:692` `get_state` 内 `await self.take_screenshot()` 无需改签名，`test_highlight.py:277` 的 mock 仍兼容。

### 1.2 `ScreenshotParams` 完整 schema（`models.py:104-106`，4 空格；`Literal` 已在 `models.py:1` import）

```python
class ScreenshotClipParams(BaseModel):
    """Viewport rectangle for a clipped screenshot, in CSS pixels."""
    model_config = ConfigDict(extra="forbid")
    x: float = Field(default=0.0, ge=0.0, description="Left offset in CSS pixels")
    y: float = Field(default=0.0, ge=0.0, description="Top offset in CSS pixels")
    width: float = Field(gt=0.0, description="Rectangle width in CSS pixels")
    height: float = Field(gt=0.0, description="Rectangle height in CSS pixels")


class ScreenshotParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["png", "jpeg", "webp"] = Field(
        default="png",
        description="Image format. 'jpeg' supports quality; 'png' is lossless.",
    )
    quality: int | None = Field(default=None, ge=0, le=100, description="0-100, only effective when format='jpeg'.")
    clip: ScreenshotClipParams | None = Field(default=None, description="Optional viewport rect {x,y,width,height} (CSS px).")
    full_page: bool = Field(default=False, description="Capture the full scrollable page instead of the viewport.")
    save_path: str = Field(default="", description="Optional file path to save the screenshot bytes to disk.")
```

**ACTION_DEFINITIONS 描述同步**（`models.py:203-207`）：

```python
"screenshot": (
    ScreenshotParams,
    "Take a screenshot with optional format, quality (jpeg), clip region, "
    "or full page. Saves to save_path if given.",
    False,
),
```

### 1.3 `_action_screenshot` 完善（`actions.py:823-830`，4 空格）

```python
async def _action_screenshot(self, params: dict, browser: BrowserSession) -> ActionResult:
    fmt: str = params.get("format", "png")
    quality = params.get("quality")
    clip = params.get("clip")
    full_page: bool = params.get("full_page", False)
    save_path: str = params.get("save_path", "")

    try:
        screenshot_bytes = await browser.take_screenshot(
            format=fmt, quality=quality, clip=clip,
            full_page=full_page, wait_settle=full_page,
        )
    except Exception as e:
        logger.warning("screenshot action failed: %s", e)
        return ActionResult(error=f"Screenshot failed: {e}")

    if save_path:
        try:
            with open(save_path, "wb") as f:
                f.write(screenshot_bytes)
        except OSError as e:
            return ActionResult(error=f"Failed to save screenshot to {save_path}: {e}")
        return ActionResult(extracted_content=f"Screenshot saved to {save_path} ({len(screenshot_bytes)} bytes)")

    meta = f"format={fmt}, {len(screenshot_bytes)} bytes"
    if full_page:
        meta += ", full_page"
    if clip:
        meta += f", clip={clip.get('width')}x{clip.get('height')}"
    return ActionResult(extracted_content=f"Screenshot captured ({meta}) but not saved (no save_path).")
```

**决策：** 无 `save_path` 时返回可读 meta（诚实告知「拍了但未存盘」）；`wait_settle=full_page`（全页 lazy 图片更需等待，快照保持零延迟）；文件 IO 错误单独捕获；阶段一**不**把图塞进 messages（留阶段二）。

### 1.4 降采样 helper（**新建** `src/tree_walker/browser/image_utils.py`，4 空格）

独立文件的理由：session.py 已 800+ 行；降采样是纯函数无 session 状态；便于单测、便于阶段二多入口复用。

```python
"""Image resizing helpers for screenshots (LLM-bound and on-disk).

Pillow is an OPTIONAL dependency: if unavailable, all helpers degrade
gracefully (return the original bytes). Keeps the base install slim.
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

try:
    from PIL import Image
    _PILLOW_AVAILABLE = True
except ImportError:
    Image = None  # type: ignore[assignment]
    _PILLOW_AVAILABLE = False
    logger.info("Pillow not installed; screenshot resizing is a no-op")


def is_resize_available() -> bool:
    return _PILLOW_AVAILABLE


def resize_screenshot_bytes(data: bytes, target: tuple[int, int] | None) -> bytes:
    """Downscale to fit within ``target`` (w, h), preserving aspect.

    Mirrors browser-use agent/prompts.py:360-386:
      - target is None / Pillow missing / empty data -> return data unchanged.
      - image already <= target in both dims -> unchanged (idempotent).
      - else LANCZOS-resample to fit-within, re-encode as PNG.
      - ANY exception -> log warning, return original (never raises).
    Output is always PNG (no JPEG/quality path — project decision).
    """
    if target is None or not _PILLOW_AVAILABLE or not data:
        return data

    tw, th = target
    if tw < 100 or th < 100:
        logger.warning("resize target %s below 100px floor, skipping", target)
        return data

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        logger.warning("resize: cannot decode image (%s), returning original", e)
        return data

    w, h = img.size
    if w <= tw and h <= th:
        return data

    scale = min(tw / w, th / h)
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))

    try:
        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.warning("resize failed (%s), returning original", e)
        return data
```

**依赖策略：** Pillow 作为**可选依赖**，加到 `pyproject.toml`（当前无 `[project.optional-dependencies]` 段，hatchling 支持 PEP 621）：

```toml
[project.optional-dependencies]
vision = ["pillow>=10.0.0"]
```

安装：`uv sync --extra vision`（或 `uv pip install pillow`）。阶段一工具层**不强制**调用 resize（只提供能力）；缺失时 `is_resize_available()==False`，所有 resize no-op。

### 1.5 断路止血：阶段一把每步截图改为不取（**推荐策略 a**）

| | (a) 默认关截图 | (b) 保持截 + 降采样但不回流 |
|---|---|---|
| 每步 CDP 成本 | **消失** | 不变（+ 额外 PIL 解码） |
| 阶段二迁移 | 切回 True 接入 image block | 只加 image block |
| 风险 | 极低（零功能损失，本来就没回流） | 中（改链路无收益） |

**采用 (a)**，仅改 `step.py:129` 单点（**不改** `BrowserSession.get_state` 默认参数，避免破坏其它调用方/测试）：

```python
# 断路止血：每步截图暂不取（LLM 视觉通道尚未打通，见 docs/tools-optimize/screenshot.md 阶段二）
browser_state = await self.browser.get_state(include_screenshot=False)
```

`test_highlight.py` 不受影响（它显式 `get_state(include_screenshot=True)`）。实施前 `Grep "\.screenshot"` 全量确认无其它读取方（已确认仅赋值 + `build_state_message` 不读 + `views.py` 类型声明）。

### 1.6 `get_state` 错误日志（`session.py:689-694`，4 空格）

```python
screenshot: bytes | None = None
if include_screenshot:
    try:
        screenshot = await self.take_screenshot()
    except Exception as e:
        logger.warning("get_state: take_screenshot failed: %s", e)
        screenshot = None
```

### 1.7 CDP 超时 —— 不新增专用条目

`cdp_timeout.py` 是 batch 并行 + 两阶段重试（服务 DOM 多源采集），`Page.captureScreenshot` 是单次串行，塞进去不自然；单次超时由 cdp-use 底层 WebSocket 控制（`cdp-use>=1.4.5`）。阶段一仅在 session 层 `try/except + logger.warning + RuntimeError on missing data`。若阶段二发现偶发挂死，再用 `asyncio.wait_for` 包一层，不污染 `cdp_timeout.py`。

### 1.8 阶段一文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/browser/session.py` | `take_screenshot` 参数化 + `get_state` 错误日志 | `:711-717` / `:689-694` |
| `src/tree_walker/tools/models.py` | `ScreenshotParams` 扩展 + 新 `ScreenshotClipParams` + ACTION_DEFINITIONS 描述 | `:104-106` / `:203-207` |
| `src/tree_walker/tools/actions.py` | `_action_screenshot` 完善 | `:823-830` |
| `src/tree_walker/browser/image_utils.py` | **新建**降采样 helper | 全新 |
| `src/tree_walker/agent/step.py` | 断路止血 `include_screenshot=False` + TODO | `:129` |
| `pyproject.toml` | `[project.optional-dependencies] vision` | `:14` 后 |
| `tests/test_screenshot.py` | **新建**工具层 + CDP params + 降采样单测 | 全新 |

### 1.9 阶段一测试计划（新建 `tests/test_screenshot.py`，TAB 缩进对齐 test_highlight）

**`take_screenshot` CDP params 构造**：默认只发 `{"format":"png"}`；`full_page=True` → 含 `captureBeyondViewport`；`format=jpeg,quality=80` → 含 `quality`；`format=png,quality=80` → 不含 `quality`；`clip` → `params["clip"]["scale"]==1`；CDP 返回 `{}` → 抛 `RuntimeError`；CDP 抛异常 → 透传（有 warning）。
**`_action_screenshot`**：`save_path` 落盘 + extracted 含 "saved"；无 `save_path` → extracted 含 "captured"；`take_screenshot` 抛错 → `ActionResult.error` 且不冒泡；`clip+full_page` 参数透传。
**`resize_screenshot_bytes`**：`target=None` no-op（`is` 同一对象）；图已 ≤ target 幂等；2000×1000 → target(1400,850) 输出 ~1400×700 PNG；非法字节返回原数据不抛；`target<(100,100)` no-op；Pillow 缺失分支（`pytest.importorskip` + monkeypatch `_PILLOW_AVAILABLE=False`）。
**回归**：`test_highlight.py` debug 三测 + `test_dom_views.py:536` 仍绿。

---

## 阶段二：打通 LLM 视觉通道（优先级次之，独立大改动）

### 2.1 核心约束：Anthropic 官方 SDK image block 格式

已确认 `client.py:10` `from anthropic import Anthropic, APIError, RateLimitError`，`get_action:180-187` 调 `self.client.messages.create(...)`。Anthropic 原生 image block 格式为：

```python
{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "<b64>"}}
```

**不要照搬 browser-use `prompts.py:428-458` 的 `ContentPartImageParam(image_url=ImageURL(url='data:...'))`**（那是 OpenAI/兼容层格式）。

> **⚠ 关键现实（已复核 `config.py:92-94`）：默认模型是 `glm-5.1`（智谱，`base_url=https://open.bigmodel.cn/api/anthropic`），是文本模型，不支持视觉输入。** 因此：
> - `use_vision` 默认 **False**；用户需显式配置支持多模态的模型（如 `glm-4v` / `glm-4.6`，或将 `LLM_MODEL` 切到 `claude-*` + 真 Anthropic base_url）。
> - 智谱 anthropic 兼容接口对标准 Anthropic image block（`source.type=base64`）的接受度需**端到端验证**（列入阶段二验证项与风险）。若不兼容，阶段二需调整格式或走智谱原生多模态接口。
> - 按模型自适应尺寸：仅对已知视觉模型家族给默认值，其余 None。

### 2.2 `build_state_blocks` 新增（`prompts/system_prompt.py:113-201` 后，4 空格）

保留 `build_state_message(...) -> str` 向后兼容，新增 `build_state_blocks(...) -> list[dict]`：

```python
def build_state_blocks(
    browser_state: BrowserStateSummary,
    screenshot_b64: str | None = None,
    **kwargs,
) -> list[dict]:
    """User content as Anthropic content blocks: text + optional image."""
    text = build_state_message(browser_state=browser_state, **kwargs)
    blocks: list[dict] = [{"type": "text", "text": text}]
    if screenshot_b64:
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64},
        })
    return blocks
```

`step.py:171-184` 改为调 `build_state_blocks`，content 从 `str` 变 `list[dict]`；前置：切回 `include_screenshot=True`，按 `self._use_vision and browser_state.screenshot and not _is_new_tab_step_zero(...)` 决定是否降采样并转 b64。

### 2.3 `LLMClient` 过滤器适配（`client.py:71-103` / `:124-141`，4 空格）

`_shorten_urls_in_messages` / `_filter_sensitive_in_messages` 当前 `if not isinstance(content, str): continue` 会**跳过** block list。改为：str 分支照旧；list 分支遍历 block，仅对 `block["type"]=="text"` 做替换，image block 透传。把现有 `_replace` 闭包抽成独立 `_make_replacer` 函数，避免循环内 `nonlocal counter` 陷阱。`get_action` 无需改（已透传 messages 给 SDK）。

### 2.4 配置（`config.py` `AgentSettings`，4 空格段；`load_settings`）

```python
# AgentSettings 新增字段
use_vision: bool = False
llm_screenshot_size: tuple[int, int] | None = None   # None = no resize
```

`load_settings()` 读 `AGENT_USE_VISION` / `AGENT_LLM_SCREENSHOT_SIZE`（`_parse_size("1400x850")`）。按模型自适应：`claude-*`/`glm-*v*` → 默认 `(1400, 850)`，其余 None。**默认 False / None**（对应默认 glm-5.1 文本模型）。

### 2.5 边界场景

1. 新标签页 step 0 不带图（`_is_new_tab_step_zero`：`n_steps==0` 且 url 空/about:blank/DOM 空）。
2. Fallback 模型无视觉：`_try_switch_to_fallback` 切换后，重试分支 filter 掉 image block（标注「配套增强」）。
3. 截图为 None：`build_state_blocks` 只发 text block。
4. Pillow 缺失：返回原图（可能很大但可用）+ warning。
5. 消息历史膨胀：与 `MessageCompactionSettings`（`config.py:32-38`）联动，压缩时丢图留文（配套优化，可后置）。
6. 坐标反映射（`_convert_llm_coordinates_to_viewport`，分数比例）仅当未来引入「LLM 输出 (x,y) 视觉点击」时需要，阶段二不实现，仅标注入口。

### 2.6 阶段二文件清单

| 文件 | 改动 | 锚点 |
|---|---|---|
| `src/tree_walker/prompts/system_prompt.py` | 新增 `build_state_blocks` | `:201` 后 |
| `src/tree_walker/agent/step.py` | 切回 True + blocks + resize + `_is_new_tab_step_zero` | `:129` / `:171-184` |
| `src/tree_walker/llm/client.py` | 两过滤器适配 block list | `:71-103` / `:124-141` |
| `src/tree_walker/config.py` | `AgentSettings` 加 `use_vision`/`llm_screenshot_size` + load_settings | `:60-79` / `:221-250` |
| `src/tree_walker/browser/image_utils.py` | 阶段一已建，复用 | — |
| `tests/test_state_message.py` 或并入 test_screenshot | 回流契约测试 | 全新 |

### 2.7 阶段二测试

`build_state_blocks`：无 screenshot → 单 text block；有 screenshot → `blocks[1]` 为 Anthropic image block（`source.type=="base64"`，**不含** `image_url`）；`_shorten_urls`/`_filter_sensitive` 对 block list 的 text 替换 + image 透传；`use_vision=False` → 无 image block；`use_vision=True` + 2000×1000 → 输出 ~1400×700；step 0 新标签页跳过。**回流契约**：断言 `agent.messages[-1]["content"]` 为 list 且含 `type=="image"`。

---

## 风险与回归点

| 风险 | 缓解 |
|---|---|
| `test_highlight.py:277` mock 兼容 | 默认参数 = 旧行为，单测断言默认 `args[0]=={"format":"png"}` 守护 |
| 缩进一致性（文件混合） | 代码片段已按目标文件缩进给出；实施时 IDE 显示空白复核 |
| Pillow 引入体积 | 可选依赖，缺失降级 no-op |
| 阶段一切 `include_screenshot=False` | 实施前 `Grep` 全量确认无其它读取方（已确认） |
| **智谱兼容接口 image block 格式**（阶段二） | 默认 `use_vision=False`；阶段二端到端验证格式接受度，不兼容则调整 |
| `_shorten_urls` 闭包 `nonlocal` 陷阱 | 抽 `_make_replacer` 独立函数 |

---

## 验证方法

**单元测试（阶段一验收）：**
```powershell
uv run python -m pytest tests/test_screenshot.py tests/test_highlight.py tests/test_dom_views.py -x -v --cov=tree_walker.browser.session --cov=tree_walker.tools.actions --cov=tree_walker.browser.image_utils --cov-report=term-missing
uv run python -m pytest tests/ -x -v
```
**阶段一端到端（可选 `examples/screenshot_demo.py`，`uv run`）：** 驱动真实 Chrome，分别测 `take_screenshot()` 默认、`full_page=True, wait_settle=True`、`clip={...}`、`resize_screenshot_bytes(png,(800,600))`、`is_resize_available()`。
**阶段二端到端：** `AGENT_USE_VISION=true` + 多模态模型，在 `get_action` 加临时日志确认 image block 进了请求；用真实模型跑视觉任务（如「页面顶部 logo 是什么颜色」）确认 LLM 能回答 —— 阶段二最终验收信号。

## 验收 checklist

**阶段一：** `take_screenshot` 全参数 + 正确 CDP params 组装；缺 data 抛 RuntimeError + action 返回 error；`ScreenshotParams` 完整且 `extra="forbid"`；无 save_path 返回可读 meta；`image_utils` 幂等+异常回退+Pillow 缺失降级；`step.py:129` 切 False + TODO；新测试全绿覆盖率 >85%；`test_highlight` 仍绿；pyproject 加 vision optional dep。
**阶段二：** image block 为 Anthropic 格式（非 image_url）；两过滤器正确处理 block list；`use_vision=False` 无图（阶段一止血仍生效）；回流契约测试通过；step 0 跳过；降采样尺寸在 target 内；真实模型能回答视觉问题。
