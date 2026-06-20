# upload_file 工具完善方案：成功回显 + 目标替换提示 + accept 类型软校验

> 参照 browser-use（`browser_use/tools/service.py:835-961` 的 `upload_file` 动作 + `browser_use/browser/watchdogs/default_action_watchdog.py:2644-2680` 的 `on_UploadFileEvent` + `browser_use/browser/session.py:2540-2594` 的 `find_file_input_near_element`）完善本项目的 `upload_file` 动作。
> 相关文档：`docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.22 upload_file 节（本项目现状，注意其"主要逻辑"行号 `actions.py:359-421` 已过期，实际为 `actions.py:652-714`；CDP 行号 `session.py:861/807` 已过期，实际为 `session.py:1404/1350`）、`browser-use/docs/Tools技术细节/05-动作详解-浏览器交互.md` 的 6. upload_file 节（参考标杆）。

## 背景（为什么改）

当前 `_action_upload_file`（`actions.py:652-714`）已实现完整的文件输入定位（`_pick_nearest_file_input` DOM 树遍历 + 坐标距离 + session 层 shadow DOM 兜底），定位能力实际**比 browser-use 更全**（browser-use 无 shadow DOM 兜底）。但对照刚被完善的 `input_text`/`click`/`navigate`（已统一"回显 + 错误映射 + bool 信号上浮"惯例），存在以下缺口：

| # | 缺口 | browser-use | TreeWalker 现状 | 影响 | 风险 |
|---|------|-------------|-----------------|------|------|
| G1 | **成功回显太薄** | `Successfully uploaded file to index N` + `long_term_memory` | 仅 `Uploaded {file_path}`，无 tag/label/index、无 `long_term_memory` | LLM 无法从 result 确认上传到哪个元素、文件名是什么，与 click/input_text/navigate 回显惯例脱节 | 无 |
| G2 | **目标替换被静默吞掉** | 元素级定位失败 raise/返回 error | 非 file input 时自动跳到最近 file input 上传，回显仍写 `Uploaded ...`，**LLM 不知 index 被替换** | LLM 误以为上传到了它选的元素（多为按钮/容器），后续基于错误假设操作 | 低 |
| G3 | **无 accept 类型软校验** | 不校验（browser-use 也不读 accept） | 不读 accept | LLM 可把 `.txt` 传给只接 `.png` 的 input，CDP `DOM.setFileInputFiles` 静默设置成功，但前端校验/服务器会拒，LLM 全程不知 | 中 |
| G4 | **白名单架构差异** | 三级：available_file_paths / downloaded_files / FileSystem | 单级 `_allowed_upload_paths` 前缀匹配 | 本项目无 FileSystem/downloaded_files 管线，单级白名单已够用；**不强行对齐** | 无（架构差异） |

**好消息——session 层定位机制已就绪（本次复用，不重写）：**

- `set_file_input`（`session.py:1365-1407`）：`backend_node_id` → `file_input_backend_ids[0]` → shadow DOM 搜索 → `DOM.setFileInputFiles`。⇒ browser-use `on_UploadFileEvent` 的 CDP 调用等价物**已实现**，且多了 shadow DOM 兜底。
- `_pick_nearest_file_input`（`actions.py:69-107`）：DOM 树遍历优先 + 坐标欧氏距离兜底。⇒ browser-use `find_file_input_near_element` + scrollY-nearest fallback 等价物**已实现**。
- `find_file_inputs_in_shadow_dom`（`session.py:1344-1357`）：`DOM.getDocument {depth:-1, pierce:True}` + 递归（browser-use 无此能力）。
- `highlight_element`（`session.py:523-525`）：委托 HighlightManager，**内部吞异常**（非阻塞 best-effort），对齐 click。

预期结果：upload_file 成功时回显 `Uploaded 'name.ext' to [TAG] {label} at index N`、非 file input 被替换时给出 `⚠️ Note` 告知实际目标、文件扩展名与 input accept 不符时给出 `⚠️ Note`；全部复用已有 session 工具，分层与 click/input_text 一致。

---

## 已确认的决策（方案采用）

- **范围 = 惯例对齐 + accept 软校验**：P0（action 层回显 + 目标替换提示）+ P1（accept 属性软校验）本次纳入；多文件上传 / 上传后读回校验延后。
- **不改 `models.py`**：`UploadFileParams`（`index: int` / `path: str`）保持不变；accept 校验、回显都是内部鲁棒性细节，不暴露给 LLM。
- **accept 谓词放 action 层**：`_file_matches_accept` 是纯字符串谓词、不感知 CDP、只喂回显 note，放 `actions.py` 模块级（紧邻既有 file-input helper `_pick_nearest_file_input`，与 `_is_file_input_node` 等同区）。
- **目标替换提示用 `⚠️ Note`**：对齐 input_text 值校验的 `⚠️ Note` 风格，追加到同一 `extracted_content`，不改 `ActionResult` 形状。
- **accept 不符不阻断**：只追加 `⚠️ Note`、仍正常上传（软校验；硬阻断会误杀 `mimetypes` 推断不准的边界，且 browser-use/CDP 本就不拦）。
- **不拆 highlight try**：对齐 `_action_click`——highlight（best-effort）+ `set_file_input` 共用一个 try，统一映射 `File upload failed: {e}`；HighlightManager 已吞 highlight 异常，实践中只有 set_file_input 错误会上浮。
- **回显只显 basename**：`Uploaded 'x.png' to ...`，不含完整路径（路径长且含本机绝对路径噪音）；`⚠️ Note` 里 accept 值原样回显便于 LLM 对照。

---

## 改动文件（共 1 个源文件 + 1 个新建测试 + 1 个文档同步）

### 1. `src/tree_walker/tools/models.py` —— 不改

`UploadFileParams`（`models.py:102-105`）与 `ACTION_DEFINITIONS["upload_file"]`维持 `index: int` / `path: str` / `terminates_sequence=False`。多文件上传延后（不引入 `list[str]`）。

### 2. `src/tree_walker/browser/session.py` —— 不改

`set_file_input` / `find_file_inputs_in_shadow_dom` / `_walk_for_file_inputs` / `highlight_element` 全部原样复用。

---

### 3. `src/tree_walker/tools/actions.py`（action 层做策略）

**(a) 新增模块级 `_file_matches_accept`（紧随 `_pick_nearest_file_input`，`:107` 之后）**

纯 Python 谓词，解析 HTML `accept`（逗号分隔的扩展名 / 通配 MIME / 全 MIME），用 stdlib `mimetypes` 推断类型做匹配；空 accept 视为不限制：

```python
import mimetypes


def _file_matches_accept(file_path: str, accept: str | None) -> bool:
	"""True if file_path is acceptable under the input's ``accept`` attribute.

	Parses the HTML accept attribute (comma-separated tokens, each a file
	extension like ``.png``, a wildcard MIME like ``image/*``, or a full MIME
	like ``application/pdf``) and matches the file's extension / guessed MIME.
	Empty/missing accept means "no restriction" (True).

	Uses stdlib mimetypes to map the extension to a MIME, so wildcard and
	full-MIME tokens work without a hard-coded table. Used by
	Tools._action_upload_file to emit a soft ⚠️ Note on mismatch — it never
	blocks the upload (CDP / browser-use do not either).
	"""
	accept = (accept or "").strip()
	if not accept:
		return True
	file_ext = os.path.splitext(file_path)[1].lower()
	guessed_mime, _ = mimetypes.guess_type(file_path)
	for token in accept.split(","):
		token = token.strip().lower()
		if not token:
			continue
		if token.startswith("."):
			if file_ext == token:
				return True
		elif token.endswith("/*"):
			prefix = token[:-1]  # "image/"
			if guessed_mime and guessed_mime.startswith(prefix):
				return True
		else:
			if guessed_mime == token:
				return True
	return False
```

> **放 action 层的理由（D3）**：纯字符串谓词、不碰 CDP、只喂回显 note；与 `_pick_nearest_file_input`/`_is_file_input_node` 同属"file-input 辅助函数区"。`mimetypes` 是 stdlib，零新依赖。

**(b) 新增 `_describe_upload` 静态 helper（紧随 `_describe_input`，约 `:374` 之后）**

镜像 `_describe_click`/`_describe_input` 的属性优先级链与 60 字截断，生成 `Uploaded 'name.ext' to [TAG] {label} at index N`：

```python
@staticmethod
def _describe_upload(entry: Any, index: int, file_path: str) -> str:
	"""Build a human-readable upload echo, mirroring _describe_click /
	_describe_input / navigate / go_back style.

	Shows the uploaded file's basename (not the full path — which is long and
	noisy) plus an identifying attribute the LLM can also see in the DOM tree
	(aria-label/title/name/placeholder), then node_value, then just the tag.
	Skips 'value'/'alt': a file input's value is the browser-faked
	'C:\\fakepath\\<name>' (not useful) and alt is irrelevant here. Bounded to
	~60 chars per field so the echo fits the LLM context.
	"""
	shown = os.path.basename(file_path)
	if len(shown) > 60:
		shown = shown[:60] + "..."
	tag = entry.tag_name.upper()
	attrs = getattr(entry, "attributes", {}) or {}
	for key in ("aria-label", "title", "name", "placeholder"):
		v = attrs.get(key)
		if v:
			v = v.strip()
			if len(v) > 60:
				v = v[:60] + "..."
			return f"Uploaded {shown!r} to [{tag}] {v!r} at index {index}"
	node_value = (getattr(entry, "node_value", "") or "").strip()
	if node_value:
		if len(node_value) > 60:
			node_value = node_value[:60] + "..."
		return f"Uploaded {shown!r} to [{tag}] {node_value!r} at index {index}"
	return f"Uploaded {shown!r} to [{tag}] at index {index}"
```

**(c) 新增 `_find_node_by_backend_id` 静态 helper（紧随 `_describe_upload`）**

在缓存的 selector_map 里按 backend_node_id 反查节点，用于"替换目标"场景读取真正 file input 的 `accept`；查不到（如 file input 隐藏、不在交互 selector_map）优雅返回 None：

```python
@staticmethod
def _find_node_by_backend_id(
	backend_node_id: int | None,
	dom_state: SerializedDOMState | None,
) -> EnhancedDOMTreeNode | None:
	"""Look up the DOM node whose backend_node_id matches in the cached
	selector_map. Returns None if not found (e.g. the file input is hidden and
	excluded from the interactive selector_map). Used by _action_upload_file
	to read the resolved file input's `accept` attribute for soft validation.
	"""
	if not dom_state or backend_node_id is None:
		return None
	for node in dom_state.selector_map.values():
		if getattr(node, "backend_node_id", None) == backend_node_id:
			return node
	return None
```

**(d) 重写 `_action_upload_file`（`:652-714`）**

整合 P0（回显 G1 + 目标替换提示 G2）与 P1（accept 软校验 G3）：

```python
async def _action_upload_file(self, params: dict, browser: BrowserSession) -> ActionResult:
	file_path = params["path"]

	# 1. 路径白名单 + 文件存在/非空校验（保持原逻辑）
	allowed = self._allowed_upload_paths
	if allowed:
		if not any(file_path.startswith(p) for p in allowed):
			return ActionResult(
				error=f"File path not in allowed upload paths: {file_path}",
			)
	if not os.path.isfile(file_path):
		return ActionResult(error=f"File not found: {file_path}")
	if os.path.getsize(file_path) == 0:
		return ActionResult(error=f"File is empty: {file_path}")

	# 2. 元素查找（保持原逻辑）
	entry, error = await self._get_element_by_index(params["index"], browser)
	if error:
		return error

	# 3. 判定目标是否本身 file input；非 file input 时定位最近 file input
	tag = entry.tag_name.upper()
	attrs = entry.attributes
	is_file_input = tag == "INPUT" and attrs.get("type", "").lower() == "file"

	backend_id = entry.backend_node_id
	file_input_ids: list[int] = []
	if not is_file_input:
		if self._cached_browser_state and self._cached_browser_state.dom_state:
			file_input_ids = list(
				self._cached_browser_state.dom_state.file_input_backend_ids,
			)
		if not file_input_ids:
			return ActionResult(
				error="Element is not a file input and no file input found on page",
			)
		backend_id = _pick_nearest_file_input(
			entry, file_input_ids,
			self._cached_browser_state.dom_state if self._cached_browser_state else None,
		)

	logger.info(
		"upload_file: index=%d, tag=%s, type=%s, backend_node_id=%s, "
		"is_file_input=%s, resolved_backend_id=%s, available_file_inputs=%s",
		params["index"], tag, attrs.get("type", ""), entry.backend_node_id,
		is_file_input, backend_id, file_input_ids,
	)

	# 4. 高亮 + 上传（对齐 _action_click：共用 try，统一映射；highlight best-effort）
	try:
		await browser.highlight_element(backend_id)
		await browser.set_file_input(
			backend_node_id=backend_id,
			file_path=file_path,
			file_input_backend_ids=file_input_ids if not is_file_input else None,
		)
	except Exception as e:
		return ActionResult(error=f"File upload failed: {e}")

	# 5. 成功回显（G1）+ 目标替换提示（G2）+ accept 软校验（G3）
	memory = self._describe_upload(entry, params["index"], file_path)
	if not is_file_input:
		memory += (
			f"  ⚠️ Note: index {params['index']} is not an <input type='file'>; "
			f"uploaded to the nearest file input on the page instead."
		)

	file_input_entry = entry if is_file_input else self._find_node_by_backend_id(
		backend_id,
		self._cached_browser_state.dom_state if self._cached_browser_state else None,
	)
	accept_attr = None
	if file_input_entry is not None:
		accept_attr = (getattr(file_input_entry, "attributes", {}) or {}).get("accept")
	if accept_attr and not _file_matches_accept(file_path, accept_attr):
		memory += (
			f"  ⚠️ Note: the file extension does not match this input's "
			f"accept={accept_attr!r} — the site may reject the upload."
		)

	logger.info(memory)
	return ActionResult(extracted_content=memory, long_term_memory=memory)
```

要点：
- **成功回显（G1）**：`_describe_upload` 生成 `Uploaded '...' to [TAG] ...`，写入 `extracted_content` + `long_term_memory`（**不**设 `success=True`——`ActionResult` 校验器 `views.py` 对非 done 动作拒绝 `success=True`）。
- **目标替换提示（G2）**：`is_file_input=False` 时追加 `⚠️ Note`，告知 LLM 实际上传到了"最近的 file input"。回显本身仍描述 LLM 选中的 entry（按钮/容器），符合"LLM 看到什么就回显什么"。
- **accept 软校验（G3）**：解析"真正 file input"的 accept（直接命中时用 entry，替换时用 `_find_node_by_backend_id` 反查），扩展名不符追加 `⚠️ Note`；查不到 accept 或无 accept 属性则静默跳过。
- **异常映射（沿用）**：`highlight + set_file_input` 共用 try → `File upload failed: {e}`，对齐 `_action_click`；前置校验（白名单/不存在/空/无 file input）各自明确 error。

---

### 4. `tests/test_upload_file.py`（新建，行为级测试，闭合覆盖缺口）

当前 upload_file **无专门行为级测试**（仅 `tests/test_highlight.py:459-485` 的 `test_upload_file_triggers_highlight` 断言高亮被调用）。参照 `tests/test_input_text.py` 的 helper 模式（`_make_entry`/`_make_state`/`_make_browser`，端到端 `Tools().execute("upload_file", {...}, browser, browser_state=state)`，mock 边界为 `highlight_element`/`set_file_input`/`get_state`，**不碰 CDP 原语**）。

**用例清单：**

| 组 | 用例 | 断言要点 |
|---|------|---------|
| 元素查找 | index 在 selector_map、entry 是 file input | 调 `highlight_element`+`set_file_input`；`error is None`；回显以 `Uploaded` 开头 |
| 元素查找 | index 不在 selector_map | 返回 `Element {N} not found...`；**不**调 highlight/set_file_input |
| 路径校验 | 文件不存在 | `File not found: ...`；不调 set_file_input |
| 路径校验 | 文件为空（0 字节） | `File is empty: ...` |
| 路径校验 | 白名单不匹配 | `File path not in allowed upload paths: ...`（需 `Tools(allowed_upload_paths=[...])`） |
| 回显 G1 | file input 带 aria-label | `extracted_content == "Uploaded 'a.png' to [INPUT] 'avatar' at index 5"`；== `long_term_memory`；`success is None`；`is_done is False` |
| 回显 G1 | file input 无可识别属性 | `Uploaded 'a.png' to [INPUT] at index 5` |
| 回显 G1 | 路径含目录（`/tmp/x.png`） | 回显**只含 basename** `x.png`，不含目录 |
| 目标替换 G2 | entry=BUTTON、有 file_input 候选 | 回显描述 `[BUTTON]`；含 `⚠️ Note: index N is not an <input type='file'>`；`set_file_input` 用解析后的 backend_id（非 entry.backend_node_id） |
| 目标替换 G2 | entry 非 file input 且无候选 | `Element is not a file input and no file input found on page` |
| accept G3 | `accept="image/png"` + 文件 `.png` | 无 `⚠️ Note` |
| accept G3 | `accept=".pdf"` + 文件 `.txt` | 回显含 `⚠️ Note` 且含 `accept='.pdf'` |
| accept G3 | `accept="image/*"` + 文件 `.jpg` | 无 `⚠️ Note`（通配匹配） |
| accept G3 | 无 accept 属性 | 无 accept 相关 `⚠️ Note` |
| accept G3 | 替换目标场景 + accept 不符 | 两条 `⚠️ Note` 都出现（替换 + accept） |
| 异常映射 | `set_file_input` 抛异常 | `error` 以 `File upload failed:` 开头 |

代表性骨架（仿 `test_input_text.py`，用 tempfile 造真实非空文件满足 `isfile`/`getsize` 校验）：

```python
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from tree_walker.browser.views import (
	BrowserStateSummary, EnhancedDOMTreeNode, NodeType, SerializedDOMState,
)
from tree_walker.tools.actions import Tools


def _make_entry(*, tag="INPUT", backend_node_id=42, attributes=None,
				node_value="", x=0, y=0):
	return EnhancedDOMTreeNode(
		node_id=backend_node_id, backend_node_id=backend_node_id,
		node_type=NodeType.ELEMENT_NODE, node_name=tag.upper(),
		node_value=node_value, attributes=attributes or {},
	)
	# 注意：测试 entry 需带 x/y 供 _pick_nearest_file_input 坐标兜底使用


def _make_state(selector_map, *, file_input_backend_ids=None):
	return BrowserStateSummary(
		url="https://example.com", title="",
		dom_state=SerializedDOMState(
			_root=None, selector_map=selector_map, element_tree_text="",
			file_input_backend_ids=file_input_backend_ids or [],
		),
	)


def _make_browser(*, set_side_effect=None):
	bs = MagicMock()
	bs.current_session_id = "sid"; bs.current_target_id = "tid"
	bs.highlight_element = AsyncMock()
	bs.set_file_input = AsyncMock(side_effect=set_side_effect) if set_side_effect \
		else AsyncMock()
	bs.get_state = AsyncMock(return_value=_make_state({}))
	return bs


@pytest.fixture
def tmp_upload(tmp_path):
	"""A real non-empty temp file that passes os.path.isfile / getsize checks."""
	p = tmp_path / "sample.png"; p.write_bytes(b"png-bytes")
	return str(p)


class TestUploadFileEcho:
	@pytest.mark.asyncio
	async def test_echo_includes_basename_and_label(self, tmp_upload):
		entry = _make_entry(attributes={"aria-label": "avatar"})
		state = _make_state({5: entry})
		browser = _make_browser()
		result = await Tools().execute(
			"upload_file", {"index": 5, "path": tmp_upload}, browser, browser_state=state)
		assert result.error is None
		bn = os.path.basename(tmp_upload)
		assert result.extracted_content == f"Uploaded {bn!r} to [INPUT] 'avatar' at index 5"
		assert result.extracted_content == result.long_term_memory
		assert result.success is None and result.is_done is False


class TestUploadFileSubstitution:
	@pytest.mark.asyncio
	async def test_non_file_input_appends_note(self, tmp_upload):
		btn = _make_entry(tag="BUTTON", backend_node_id=3, x=10, y=10)
		fin = _make_entry(tag="INPUT", backend_node_id=9,
						  attributes={"type": "file"}, x=12, y=12)
		state = _make_state({3: btn}, file_input_backend_ids=[9])
		# selector_map 需含 file input 节点，供 _find_node_by_backend_id 反查 accept
		state.dom_state.selector_map[9] = fin
		browser = _make_browser()
		result = await Tools().execute(
			"upload_file", {"index": 3, "path": tmp_upload}, browser, browser_state=state)
		assert result.error is None
		assert "not an <input type='file'>" in result.extracted_content
		# set_file_input 收到的是解析后的 file input backend_id（9），非按钮（3）
		browser.set_file_input.assert_awaited_once()
		assert browser.set_file_input.call_args.kwargs["backend_node_id"] == 9


class TestUploadFileAccept:
	@pytest.mark.asyncio
	async def test_mismatch_appends_accept_note(self, tmp_path):
		p = tmp_path / "notes.txt"; p.write_bytes(b"x")  # .txt
		entry = _make_entry(attributes={"type": "file", "accept": ".pdf"})
		state = _make_state({1: entry})
		result = await Tools().execute(
			"upload_file", {"index": 1, "path": str(p)}, _make_browser(), browser_state=state)
		assert "accept='.pdf'" in result.extracted_content

	@pytest.mark.asyncio
	async def test_wildcard_match_no_note(self, tmp_upload):  # sample.png vs image/*
		entry = _make_entry(attributes={"type": "file", "accept": "image/*"})
		state = _make_state({1: entry})
		result = await Tools().execute(
			"upload_file", {"index": 1, "path": tmp_upload}, _make_browser(), browser_state=state)
		assert "accept=" not in result.extracted_content


class TestUploadFileErrorMapping:
	@pytest.mark.asyncio
	async def test_set_file_input_raises(self, tmp_upload):
		entry = _make_entry(attributes={"type": "file"})
		state = _make_state({1: entry})
		browser = _make_browser(set_side_effect=RuntimeError("CDP down"))
		result = await Tools().execute(
			"upload_file", {"index": 1, "path": tmp_upload}, browser, browser_state=state)
		assert result.error is not None and result.error.startswith("File upload failed:")
```

> `tests/test_highlight.py:459-485` 的 `test_upload_file_triggers_highlight` 需回归确认：mock 的 `set_file_input` 不抛异常时仍 `result.error is None` 且 `highlight_element.assert_awaited_once_with(7)`——重写后该断言不变（入口签名不变）。

---

### 5. 附带文档同步（建议本次实现一并做）

更新 `docs/Tools技术细节/04_动作清单与CDP映射.md` 的 4.22 upload_file 节：

- **行号纠错**：`actions.py:359-421` → `actions.py:652-714`；CDP `session.py:861` → `session.py:1404`；`session.py:807` → `session.py:1350`。
- **主要逻辑代码块**：替换为重写后的 `_action_upload_file`（保留三级校验 + 非 file input 替换逻辑；新增 `_describe_upload` 回显、`⚠️ Note` 目标替换提示、`_file_matches_accept` accept 软校验）。
- **CDP 清单**：补 `DOM.setFileInputFiles` 的 files 为单文件列表说明；shadow DOM 兜底 `DOM.getDocument {depth:-1, pierce:True}` 标注触发条件。
- **注意事项**：补"成功回显 `Uploaded 'name' to [TAG] ...`"、"非 file input 时回显含 `⚠️ Note` 告知实际目标被替换"、"扩展名与 accept 不符追加 `⚠️ Note`（不阻断）"。

---

## 技术决策说明（要点）

- **accept 软校验放 action 层、不改 session（D3）**：纯字符串谓词，只影响回显 note；放 `actions.py` 与既有 file-input helper 同区。代价：每次成功上传多做一次 `os.path.splitext` + `mimetypes.guess_type`，纯本地计算，成本可忽略。
- **`⚠️ Note` 追加到同一 `extracted_content`（D2）**：对齐 input_text 单消息风格，不改 `ActionResult` 形状；多条 Note 串接，`⚠️` 标记可被 LLM 解析。
- **回显描述"LLM 选中的 entry"而非"真正 file input"（D4）**：LLM 索引的是按钮/容器，回显它选的元素 + `⚠️ Note` 说明被替换，比回显一个 LLM 看不见的隐藏 input 更可解释。
- **accept 不符不阻断（D5）**：硬阻断会误杀 `mimetypes` 推断不准（如 `.csv` 可能被推成 `text/csv` 或 `application/vnd.ms-excel`）的合法上传；browser-use/CDP 本就不拦，保持一致。
- **不拆 highlight try（D6）**：对齐 `_action_click`；HighlightManager 已吞 highlight 异常，实践中只有 set_file_input 错误上浮，`File upload failed:` 前缀已足够。
- **`_find_node_by_backend_id` 优雅降级（D7）**：替换目标场景下 file input 常隐藏、不在交互 selector_map，查不到 accept 时静默跳过校验，不报错。

## 已知限制（本次不处理，留作未来）

> **后续修复（issue #34）已落地**，见 [`upload_file_fix.md`](./upload_file_fix.md)：① `Page.setInterceptFileChooserDialog` 拦截原生文件框（click file input 不再弹 OS 选择器）；② **`_pick_nearest_file_input` 被 4 个实证探针证伪并删除**——抖音隐藏 input 无任何可区分的客户端信号（自身/容器/LCA/坐标全失效，恒选首个=Bug 2 根因），改为 **click 发现 + 诚实回退**（`discover_file_input_via_click` 点 dropzone 捕获 `fileChooserOpened.backendNodeId`=页面真正关联的 input；未命中→诚实 error 引导 agent 驱动弹窗，绝不瞎猜）；③ accept 软校验 note 改为**中性 informational**（`⚠️`→`ℹ️`，明示"已成功/勿重试"），消除诱导 LLM 换 index 重传。下述为本次（#18）未处理的其余限制。

- **多文件上传（multiple）**：browser-use 也不支持；需改 `models.py` 把 `path` 改为 `str | list[str]` + session 层 files 列表 + 回显/accept/读回适配多文件，LLM schema 变化较大，留待明确需求后做。
- **上传后读回校验**：browser-use 也未做；需 `DOM.resolveNode` + `Runtime.callFunctionOn` 读 `input.files.length`，CDP 编排较重、价值边际（`setFileInputFiles` 失败一般直接抛异常），待遥测确认有"静默失败"案例再做。
- **超时机制**：browser-use 有 30s 事件超时；本项目 CDP 调用走 `client.send`，暂无统一超时封装，留待全局 CDP 超时改造时一并加。
- **白名单三级化（G4）**：browser-use 的 available_file_paths/downloaded_files/FileSystem 依赖其下载管线与虚拟文件系统，本项目无对应基础设施，单级 `_allowed_upload_paths` 已满足安全诉求，不强行对齐。

---

## 验证步骤

1. **本方案文档自身核对**：交叉核对所有 `file:line` 引用（browser-use 的 `service.py`/`default_action_watchdog.py`/`session.py`、本项目的 `actions.py`/`session.py`/`models.py`/`views.py`）与现有代码一致；确认被"复用"的函数（`set_file_input`/`find_file_inputs_in_shadow_dom`/`_pick_nearest_file_input`/`highlight_element`）确实存在且行为如述。
2. **（实现阶段，非本次）跑测试**：
   ```powershell
   uv run python -m pytest tests/test_upload_file.py tests/test_highlight.py -x -v
   uv run python -m pytest tests/ -x -v
   uv run python -m pytest tests/ --cov=tree_walker.tools --cov=tree_walker.browser --cov-report=term-missing
   ```
3. **（实现阶段）手动验证**：真实浏览器开一个含 `<input type="file" accept=".png">` 的页面，分别传 `.png`（无 Note）与 `.txt`（回显含 `⚠️ Note`）；再对一个包裹隐藏 input 的上传按钮传文件，确认回显含 `⚠️ Note` 目标替换提示。
