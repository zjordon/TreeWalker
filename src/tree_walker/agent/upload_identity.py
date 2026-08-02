"""上传 file input 的站点无关身份——重放匹配与 agent 端采集的单一真相源。

本模块由 ``rerun.py:_match_file_upload_by_clue``（重放精筛）与 ``tools/actions.py:
_action_upload_file``（agent 执行 upload_file 时采集线索，#151）共用，避免两处复制同一份
微妙的 JS+对齐逻辑。

核心资产 ``UPLOAD_INPUT_CONTEXTS_JS``：单次 ``execute_js`` 扫页面上所有 ``input[type=file]``
（DOM 文档序），用标准信号算每个 input 的身份——原生 ``label`` / ``aria-labelledby`` / 就近可见
文本祖先 / ARIA ``dialog`` / 可点 affordance，外加 ``container_rect``（最近非零祖先 rect）。

为什么需要 ``container_rect``：隐藏 ``<input type=file>`` 自身 rect 是 ``{0,0,0,0}``，重放匹配器
尾部 rect 就近对它双向失效（线索 rect 与每个候选的 ``snapshot_node.bounds`` 都是零，退回
``candidates[0]``）。抖音封面横/竖槽位的真实几何在祖先容器上，``container_rect`` 给出位置信号，
让 rect 就近即便在 xpath 漂移、region/in_dialog 撞车时也能区分横/竖（issue #151）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_UPLOAD_VIDEO_EXTS = frozenset({"mp4", "mov", "avi", "mkv", "webm", "flv", "wmv", "m4v", "ts", "3gp", "mpeg", "mpg"})
_UPLOAD_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff", "svg", "heic"})


# 单次 execute_js 扫所有 input[type=file]（DOM 文档序），返回每个 input 的站点无关身份。
# 源自 rerun.py 旧 _upload_input_contexts；#151 新增 affordance_rect / container_rect：
# 隐藏 input 自身 rect={0,0,0,0}，取最近非零祖先 rect 作位置信号，重放 rect 就近才能区分横/竖封面。
# 不变量：仍按 DOM 文档序一条对应一个 input（调用方按 kind 过滤后与 candidates 按下标对齐）。
UPLOAD_INPUT_CONTEXTS_JS = (
    "(()=>{"
    "const norm=s=>(s||'').replace(/\\s+/g,' ').trim();"
    "const out=[];"
    "document.querySelectorAll('input[type=file]').forEach(inp=>{"
    # 1. 原生 label（W3C input.labels：含 <label for> 指向与包裹 <label>）
    "const labelText=norm(Array.from(inp.labels||[]).map(l=>l.textContent||'').join(' '));"
    # 2. aria-labelledby IDREF → 目标 textContent（不走 accname——浏览器实现差异大）
    "const ariaText=norm((inp.getAttribute('aria-labelledby')||'').split(/\\s+/).filter(Boolean)"
    ".map(id=>document.getElementById(id)).filter(Boolean).map(el=>el.textContent||'').join(' '));"
    # 3. 就近可见文本祖先（≤5 层，首个 textContent 非空且 <200 字）——泛化旧 area_text
    "let region='',p=inp.parentElement,depth=0;"
    "while(p&&depth<5&&!region){const t=norm(p.textContent||'');if(t&&t.length<200)region=t;p=p.parentElement;depth++;}"
    # 4. ARIA dialog（泛化旧 in_modal 的 [class*="modal"]——无障碍标准更稳）
    "const inDialog=!!(inp.closest('[role=dialog]')||inp.closest('[aria-modal=true]'));"
    # 5. Layer 2：可点 affordance 文案/role/tag/rect（≤6 层，首个 button/[role=button]/a/label/
    #    cursor:pointer 且文案非空）。affordance_rect=该可点祖先真实几何（用户实点元素的位置）。
    "let a=inp.parentElement,affText='',affRole='',affTag='',affRect=null,d2=0;"
    "while(a&&d2<6&&!affText){const role=a.getAttribute('role');"
    "const click=a.tagName==='BUTTON'||role==='button'||a.tagName==='A'||a.tagName==='LABEL'||role==='link'"
    "||(window.getComputedStyle(a).cursor==='pointer');"
    "if(click){affText=norm(a.textContent||'');affRole=role||a.tagName.toLowerCase();affTag=a.tagName.toLowerCase();"
    "const ar=a.getBoundingClientRect();if(ar.width>0&&ar.height>0)affRect={x:ar.x,y:ar.y,width:ar.width,height:ar.height};}"
    "a=a.parentElement;d2++;}"
    # 6. container_rect：最近（≤6 层）非零祖先 rect——隐藏 input 的位置信号（横/竖封面槽几何不同）。
    "let cRect=null,c=inp.parentElement,d3=0;"
    "while(c&&d3<6){const r=c.getBoundingClientRect();"
    "if(r.width>0&&r.height>0){cRect={x:r.x,y:r.y,width:r.width,height:r.height};break;}"
    "c=c.parentElement;d3++;}"
    "out.push({accept:(inp.getAttribute('accept')||'').toLowerCase(),"
    "label_text:labelText,aria_text:ariaText,region_text:region,in_dialog:inDialog,"
    "affordance_text:affText,affordance_role:affRole,affordance_tag:affTag,affordance_rect:affRect,"
    "container_rect:cRect});"
    "});"
    "return out;"
    "})()"
)


# 单元素版：在【目标 file input 自身】（``this`` = 该 input）提取身份上下文，供 capture_upload_clue。
# 与 UPLOAD_INPUT_CONTEXTS_JS 同一份信号逻辑，但作 callFunctionOn 的 functionDeclaration（无入参、
# ``this``=input），避开 file_input_candidates 与 document.querySelectorAll 的计数对齐（坑③）。
UPLOAD_INPUT_CONTEXT_ON_ELEMENT_JS = (
	"function(){"
	"const inp=this;"
	"if(!inp||inp.tagName!=='INPUT'||(inp.type||'').toLowerCase()!=='file')return null;"
	"const norm=s=>(s||'').replace(/\\s+/g,' ').trim();"
	"const labelText=norm(Array.from(inp.labels||[]).map(l=>l.textContent||'').join(' '));"
	"const ariaText=norm((inp.getAttribute('aria-labelledby')||'').split(/\\s+/).filter(Boolean)"
	".map(id=>document.getElementById(id)).filter(Boolean).map(el=>el.textContent||'').join(' '));"
	"let region='',p=inp.parentElement,depth=0;"
	"while(p&&depth<5&&!region){const t=norm(p.textContent||'');if(t&&t.length<200)region=t;p=p.parentElement;depth++;}"
	"const inDialog=!!(inp.closest('[role=dialog]')||inp.closest('[aria-modal=true]'));"
	"let a=inp.parentElement,affText='',affRole='',affTag='',affRect=null,d2=0;"
	"while(a&&d2<6&&!affText){const role=a.getAttribute('role');"
	"const click=a.tagName==='BUTTON'||role==='button'||a.tagName==='A'||a.tagName==='LABEL'||role==='link'"
	"||(window.getComputedStyle(a).cursor==='pointer');"
	"if(click){affText=norm(a.textContent||'');affRole=role||a.tagName.toLowerCase();affTag=a.tagName.toLowerCase();"
	"const ar=a.getBoundingClientRect();if(ar.width>0&&ar.height>0)affRect={x:ar.x,y:ar.y,width:ar.width,height:ar.height};}"
	"a=a.parentElement;d2++;}"
	"let cRect=null,c=inp.parentElement,d3=0;"
	"while(c&&d3<6){const r=c.getBoundingClientRect();"
	"if(r.width>0&&r.height>0){cRect={x:r.x,y:r.y,width:r.width,height:r.height};break;}"
	"c=c.parentElement;d3++;}"
	"return {accept:(inp.getAttribute('accept')||'').toLowerCase(),"
	"label_text:labelText,aria_text:ariaText,region_text:region,in_dialog:inDialog,"
	"affordance_text:affText,affordance_role:affRole,affordance_tag:affTag,affordance_rect:affRect,"
	"container_rect:cRect};"
	"}"
)


def file_input_candidates(
    selector_map: dict[int, Any], *, accept_hint: str = "", path: str = "",
) -> list[tuple[int, Any]]:
    """收集 accept(文件类型 kind) 匹配的 file input 候选（selector_map 迭代顺序）。

    kind 优先取自 ``accept_hint``（扩展 change 瞬间捕获的真实 accept），否则按 path 扩展名
    （mp4→video、png→image）推断。供 ``_resolve_file_input_by_accept``（老 accept 兜底）与
    ``_match_file_upload_by_clue``（issue #139 语义线索精筛）共用，避免重复。
    """
    if accept_hint:
        ah = accept_hint.lower()
        kind = "video" if "video" in ah else ("image" if "image" in ah else None)
    else:
        ext = Path(path or "").suffix.lower().lstrip(".")
        kind = "video" if ext in _UPLOAD_VIDEO_EXTS else ("image" if ext in _UPLOAD_IMAGE_EXTS else None)
    candidates: list[tuple[int, Any]] = []
    for idx, node in selector_map.items():
        attrs = getattr(node, "attributes", None) or {}
        if (getattr(node, "node_name", "") or "").upper() != "INPUT" \
                or attrs.get("type", "").lower() != "file":
            continue
        if kind is None or kind in (attrs.get("accept", "") or "").lower():
            candidates.append((idx, node))
    return candidates


async def upload_input_contexts(
    browser: Any, candidates: list[tuple[int, Any]], *, kind: str = "",
) -> dict[int, dict[str, Any]]:
    """单次 ``execute_js``（``UPLOAD_INPUT_CONTEXTS_JS``）返回每个 file input 的站点无关身份上下文。

    返回 ``{selector_map_index: ctx}``，按 DOM 序下标与 ``candidates``（``file_input_candidates``
    同款 kind 过滤）一一对齐。``ctx`` 字段：``accept / label_text / aria_text / region_text /
    in_dialog / affordance_text / affordance_role / affordance_tag / affordance_rect / container_rect``。

    失败不崩：execute_js 异常 / 返回非 list / kind 过滤后数量 ≠ 候选数（坑③：下标对不上）→ 返回
    ``{}``，让匹配器降级到可见性 / rect 就近。详见 ``docs/user_recording/upload-general-identity-impl-plan.md``。
    """
    if not candidates:
        return {}
    try:
        arr = await browser.execute_js(UPLOAD_INPUT_CONTEXTS_JS)
    except Exception as e:
        logger.warning("upload_input_contexts execute_js 失败: %s", e)
        return {}
    if not isinstance(arr, list):
        logger.info("upload_input_contexts: execute_js 返回 %r（非 list），放弃上下文精筛", type(arr).__name__)
        return {}
    # 与 file_input_candidates 同款 accept(kind) 过滤 → DOM 序下标与 candidates 一一对齐
    entries = [
        e for e in arr
        if isinstance(e, dict) and (not kind or kind in (e.get("accept") or ""))
    ]
    if len(entries) != len(candidates):
        logger.warning(
            "upload_input_contexts: kind=%r 过滤后 file input 数 %d ≠ 候选数 %d"
            "（DOM 序对应不可靠，放弃上下文精筛）",
            kind or "(none)", len(entries), len(candidates),
        )
        return {}
    return {idx: entries[i] for i, (idx, _) in enumerate(candidates)}


def nonzero_rect(r: Any) -> bool:
    """rect（dict 或 DOMRect-like）是否 width/height 之一 > 0。None / 非法 → False。"""
    if r is None:
        return False
    try:
        if isinstance(r, dict):
            w = float(r.get("width", 0) or 0)
            h = float(r.get("height", 0) or 0)
        else:
            w = float(getattr(r, "width", 0) or 0)
            h = float(getattr(r, "height", 0) or 0)
    except (TypeError, ValueError):
        return False
    return w > 0 or h > 0


def effective_clue_rect(clue: dict[str, Any]) -> Any:
    """线索里首个非零 rect，供重放匹配器尾部 rect 就近用。

    优先级：``rect``（input 自身，隐藏 input 常为 {0,0,0,0}）→ ``container_rect``（#151 新增，最近
    非零祖先，隐藏 input 的位置信号）→ ``trigger_affordance.rect``（用户实点 affordance 几何）。
    全无则返回原 ``rect``（可能 None/零——保留 legacy，让匹配器退回 ``_nearest_idx`` 旧行为）。
    """
    rect = clue.get("rect")
    if nonzero_rect(rect):
        return rect
    cr = clue.get("container_rect")
    if nonzero_rect(cr):
        return cr
    aff = clue.get("trigger_affordance")
    if isinstance(aff, dict) and nonzero_rect(aff.get("rect")):
        return aff.get("rect")
    return rect


def _bounds_to_dict(bounds: Any) -> dict[str, Any] | None:
    """DOMRect-like / dict → ``{x,y,width,height}`` dict；失败返回 None。"""
    if bounds is None:
        return None
    if isinstance(bounds, dict):
        return bounds
    try:
        if hasattr(bounds, "to_dict"):
            d = bounds.to_dict()
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    try:
        return {
            "x": float(getattr(bounds, "x", 0) or 0),
            "y": float(getattr(bounds, "y", 0) or 0),
            "width": float(getattr(bounds, "width", 0) or 0),
            "height": float(getattr(bounds, "height", 0) or 0),
        }
    except (TypeError, ValueError):
        return None


def build_upload_clue(node: Any, ctx_entry: dict[str, Any]) -> dict[str, Any]:
    """从选中 input 的 node + 身份上下文，构建与 ``recorder.py:_store_upload_clue`` 同形的线索。

    返回 base 字段（**不含** ``_semantic_clue`` / ``kind``——由 ``step.py:_project_interacted_elements``
    包裹时补，与 recorder 一致）：``xpath / tag / rect(input 快照) / accept / label_text / aria_text /
    region_text / in_dialog / container_rect``，``affordance_text`` 非空时附 ``trigger_affordance``
    （``text/role/tag/rect``，rect 用 ``affordance_rect``，缺则退 ``container_rect``）。
    """
    attrs = getattr(node, "attributes", None) or {}
    accept = attrs.get("accept", "") or ""
    snap = getattr(node, "snapshot_node", None)
    bounds = getattr(snap, "bounds", None) if snap else None
    clue: dict[str, Any] = {
        "xpath": getattr(node, "xpath", None),
        "tag": (getattr(node, "node_name", "") or "input").lower(),
        "rect": _bounds_to_dict(bounds),
        "accept": accept,
        "label_text": ctx_entry.get("label_text", ""),
        "aria_text": ctx_entry.get("aria_text", ""),
        "region_text": ctx_entry.get("region_text", ""),
        "in_dialog": bool(ctx_entry.get("in_dialog", False)),
        "container_rect": ctx_entry.get("container_rect"),
    }
    aff_text = (ctx_entry.get("affordance_text") or "").strip()
    if aff_text:
        clue["trigger_affordance"] = {
            "text": aff_text,
            "role": ctx_entry.get("affordance_role") or "",
            "tag": ctx_entry.get("affordance_tag") or "",
            "rect": ctx_entry.get("affordance_rect") or ctx_entry.get("container_rect"),
        }
    return clue


async def capture_upload_clue(
    browser: Any, selector_map: dict[int, Any], backend_id: int,
) -> dict[str, Any] | None:
    """agent 执行 upload_file 后，为【实际命中】的 input（``backend_id``）采集语义线索（#151）。

    best-effort：节点不在 selector_map / 探针失败 / 任何异常 → 返回 None（调用方落回无线索的 legacy
    重放路径，绝不阻塞上传）。成功则返回 ``build_upload_clue`` 产物，形状与手工录制一致 → 重放自动走
    ``_match_file_upload_by_clue`` 稳健路径。
    """
    try:
        node: Any = None
        for n in selector_map.values():
            if getattr(n, "backend_node_id", None) == backend_id:
                node = n
                break
        if node is None:
            return None
        # 在目标元素自身提取身份上下文（resolveNode+callFunctionOn, this=该 input），不依赖候选计数对齐
        ctx = await browser.eval_function_on_node(backend_id, UPLOAD_INPUT_CONTEXT_ON_ELEMENT_JS)
        if not isinstance(ctx, dict):
            return None
        return build_upload_clue(node, ctx)
    except Exception as exc:
        logger.debug("capture_upload_clue 失败（非阻塞）: %s", exc)
        return None
