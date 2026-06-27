"""Tests for browser.html_source: CDP DOM.getDocument tree → clean HTML."""
from __future__ import annotations

from tree_walker.browser.html_source import document_body_to_html, node_to_html


def _text(value: str) -> dict:
	return {"nodeType": 3, "nodeValue": value}


def _el(tag: str, children: list | None = None, attrs: list | None = None, node_type: int = 1) -> dict:
	return {
		"nodeType": node_type,
		"nodeName": tag,
		"attributes": attrs or [],
		"children": children or [],
	}


def _doc(*children: dict) -> dict:
	return {"nodeType": 9, "nodeName": "#document", "children": list(children)}


class TestNodeToHtml:
	def test_text_node_escaped(self):
		assert node_to_html(_text("<b> & co")) == "&lt;b&gt; &amp; co"

	def test_empty_text_node(self):
		assert node_to_html(_text("")) == ""

	def test_non_dict_returns_empty(self):
		assert node_to_html(None) == ""  # type: ignore[arg-type]
		assert node_to_html("nope") == ""  # type: ignore[arg-type]

	def test_comment_node_dropped(self):
		assert node_to_html({"nodeType": 8, "nodeValue": "c"}) == ""

	def test_simple_element(self):
		assert node_to_html(_el("P", [_text("hi")])) == "<p>hi</p>"

	def test_nested_structure(self):
		ul = _el("UL", [_el("LI", [_text("one")]), _el("LI", [_text("two")])])
		assert node_to_html(ul) == "<ul><li>one</li><li>two</li></ul>"

	def test_attributes_serialized(self):
		a = _el("A", [_text("link")], attrs=["href", "https://x", "class", "c"])
		out = node_to_html(a)
		assert out.startswith("<a ")
		assert 'href="https://x"' in out
		assert 'class="c"' in out
		assert out.endswith(">link</a>")

	def test_void_tag_no_close(self):
		img = _el("IMG", attrs=["src", "a.png", "alt", "x"])
		assert node_to_html(img) == '<img src="a.png" alt="x">'

	def test_skip_script_style_drops_content(self):
		div = _el("DIV", [
			_el("SCRIPT", [_text("evil()")]),
			_el("STYLE", [_text("{color:red}")]),
			_el("P", [_text("kept")]),
		])
		out = node_to_html(div).lower()
		assert "script" not in out
		assert "style" not in out
		assert "evil" not in out
		assert "kept" in out

	def test_skip_head_meta_link(self):
		out = node_to_html(_el("HEAD", [
			_el("META", attrs=["charset", "utf-8"]),
			_el("LINK", attrs=["rel", "x"]),
		])).lower()
		assert "meta" not in out
		assert "link" not in out

	def test_shadow_roots_walked(self):
		host = _el("DIV", [_el("P", [_text("light")])])
		host["shadowRoots"] = [_el("SPAN", [_text("shadow")])]
		out = node_to_html(host)
		assert "light" in out
		assert "<span>shadow</span>" in out

	def test_iframe_drops_tag_but_keeps_content_document(self):
		iframe = _el("IFRAME")
		iframe["contentDocument"] = _el("DIV", [_text("iframe text")])
		out = node_to_html(iframe)
		assert "<iframe" not in out.lower()
		assert "iframe text" in out

	def test_iframe_crossorigin_no_content_document(self):
		# contentDocument None → nothing reachable
		assert node_to_html(_el("IFRAME")) == ""

	def test_extract_links_false_drops_href(self):
		a = _el("A", [_text("link")], attrs=["href", "https://x", "class", "c"])
		out = node_to_html(a, extract_links=False)
		assert "href" not in out
		assert "link" in out
		assert 'class="c"' in out

	def test_extract_images_false_drops_src(self):
		img = _el("IMG", attrs=["src", "a.png", "alt", "x"])
		out = node_to_html(img, extract_images=False)
		assert "src" not in out
		assert 'alt="x"' in out


class TestDocumentBodyToHtml:
	def test_none_returns_empty(self):
		assert document_body_to_html(None) == ""

	def test_finds_body_and_excludes_head(self):
		body = _el("BODY", [_el("P", [_text("body text")])])
		head = _el("HEAD", [_el("TITLE", [_text("secret-title")])])
		root = _doc(_el("HTML", [head, body]))
		out = document_body_to_html(root)
		assert "body text" in out
		assert "secret-title" not in out

	def test_no_body_falls_back_to_root(self):
		# root with no <body> element — renders the root subtree itself
		div = _el("DIV", [_text("fallback")])
		assert "fallback" in document_body_to_html(div)

	def test_extract_links_propagated(self):
		body = _el("BODY", [_el("A", [_text("x")], attrs=["href", "u"])])
		root = _doc(_el("HTML", [body]))
		out = document_body_to_html(root, extract_links=False)
		assert "href" not in out
		assert ">x<" in out
