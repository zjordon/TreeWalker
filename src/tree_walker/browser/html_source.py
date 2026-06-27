"""CDP ``DOM.getDocument`` 树 → 干净 HTML 重建（供 extract 工具走 markdown 路径）。

纯函数：调用方把已取到的 CDP 节点 dict（``depth=-1, pierce=True``，天然含
shadow DOM 与同源 iframe 的 ``contentDocument``）传进来，本模块**不再发 CDP**，
递归 ``children`` / ``shadowRoots`` / ``contentDocument`` 重建一份 markdownify
友好的干净 HTML（剥 script/style/template/HEAD 等噪声，门控 ``<a href>`` /
``<img src>``）。
"""
from __future__ import annotations

import html as _html

# 整棵子树丢弃的标签（噪声 / 非内容）。iframe 不在此列——它单独特判：丢掉
# ``<iframe>`` 标签本身，但其内容经 ``contentDocument``（pierce=True 已带出）递归带出。
_SKIP_TAGS = frozenset({
    "script", "style", "template", "noscript", "svg", "canvas",
    "link", "meta", "head",
})
# 自闭合标签（不发闭合标签）。
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


def _parse_attrs(raw: list | None) -> dict[str, str]:
    """CDP ``attributes`` 交错数组 ``[name, val, ...]`` → dict。

    复制自 ``dom.py`` 的同名解析器，保持本模块零依赖（避免 ``import dom`` 的循环风险）。
    值截断到 200 字符（对齐 ``dom._parse_attrs``）。
    """
    attrs: dict[str, str] = {}
    if not raw:
        return attrs
    for i in range(0, len(raw) - 1, 2):
        attrs[raw[i]] = raw[i + 1][:200]
    return attrs


def _find_body(node: dict | None) -> dict | None:
    """在主文档树（不进 ``contentDocument``）里找首个 ``<body>``，找不到返回 None。"""
    if not isinstance(node, dict):
        return None
    if (node.get("nodeName") or "").lower() == "body":
        return node
    for key in ("children", "shadowRoots"):
        for child in node.get(key, []):
            found = _find_body(child)
            if found is not None:
                return found
    return None


def node_to_html(
    node: dict,
    *,
    extract_links: bool = True,
    extract_images: bool = True,
) -> str:
    """递归 ``children`` / ``shadowRoots`` / ``contentDocument`` 重建干净 HTML。

    - ``nodeType`` 3 = 文本节点（``html.escape`` 转义）；
    - ``nodeType`` 1 = 元素节点（``_SKIP_TAGS`` 过滤、``_VOID_TAGS`` 决定是否闭合）；
    - 其余节点类型丢弃。
    - ``extract_links=False`` → ``<a>`` 去掉 ``href``；``extract_images=False`` → ``<img>`` 去掉 ``src``。
    - 空或被跳过的子树返回 ``""``。
    """
    if not isinstance(node, dict):
        return ""
    node_type = node.get("nodeType", 0)
    if node_type == 3:  # TEXT_NODE
        val = node.get("nodeValue") or ""
        return _html.escape(val) if val else ""
    if node_type != 1:  # 只保留 ELEMENT_NODE(1) 与 TEXT_NODE(3)
        return ""

    tag = (node.get("nodeName") or "").lower()
    # iframe：丢掉标签本身，但经 contentDocument 递归带出同源 iframe 内容
    # （跨源 iframe 的 contentDocument 为 None → 返回 ""，内容不可达，out-of-scope）。
    if tag == "iframe":
        content_doc = node.get("contentDocument")
        return node_to_html(content_doc, extract_links=extract_links, extract_images=extract_images) if content_doc else ""
    if tag in _SKIP_TAGS:
        return ""

    attrs = _parse_attrs(node.get("attributes"))
    if tag == "a" and not extract_links:
        attrs.pop("href", None)
    if tag == "img" and not extract_images:
        attrs.pop("src", None)

    attr_str = "".join(
        f' {k}="{_html.escape(str(v), quote=True)}"' for k, v in attrs.items() if v
    )
    out = [f"<{tag}{attr_str}>"]
    if tag not in _VOID_TAGS:
        for child in node.get("children", []):
            out.append(node_to_html(child, extract_links=extract_links, extract_images=extract_images))
        for shadow in node.get("shadowRoots", []):
            out.append(node_to_html(shadow, extract_links=extract_links, extract_images=extract_images))
        content_doc = node.get("contentDocument")
        if content_doc:
            out.append(node_to_html(content_doc, extract_links=extract_links, extract_images=extract_images))
        out.append(f"</{tag}>")
    return "".join(out)


def document_body_to_html(
    root: dict | None,
    *,
    extract_links: bool = True,
    extract_images: bool = True,
) -> str:
    """从 ``DOM.getDocument`` 根定位 ``<body>``（缺失则从根）返回其 HTML。

    ``root`` 为空返回 ``""``。跨源 iframe 的 ``contentDocument`` 为 None，其内容不可达
    （out-of-scope，与 browser-use 同立场）。
    """
    if not root:
        return ""
    body = _find_body(root)
    target = body if body is not None else root
    return node_to_html(target, extract_links=extract_links, extract_images=extract_images)
