"""Visual highlight manager for browser interaction feedback."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

from tree_walker.config import HighlightSettings

logger = logging.getLogger(__name__)


class HighlightManager:
	"""Manages visual highlights using CDP Overlay and JS injection."""

	def __init__(
		self,
		settings: HighlightSettings,
		execute_js: Callable[[str], Coroutine[Any, Any, Any]],
		client: Any,
		session_id: str | None = None,
	) -> None:
		self._settings = settings
		self._execute_js = execute_js
		self._client = client
		self._session_id = session_id

	async def highlight_element(self, backend_node_id: int) -> None:
		"""Highlight an element via CDP Overlay.highlightNode."""
		if not self._settings.enabled or not self._settings.interaction_enabled:
			return
		try:
			color = self._settings.interaction_color or {'r': 255, 'g': 165, 'b': 0, 'a': 0.8}
			await self._client.send.Overlay.highlightNode(
				{
					"highlightConfig": {
						"borderColor": color,
						"contentColor": {
							"r": color['r'],
							"g": color['g'],
							"b": color['b'],
							"a": round(color['a'] * 0.125, 3),
						},
					},
					"backendNodeId": backend_node_id,
				},
				session_id=self._session_id,
			)
			asyncio.get_event_loop().call_later(
				self._settings.interaction_duration,
				lambda: asyncio.ensure_future(self._safe_hide_highlight()),
			)
		except Exception as e:
			logger.debug("Highlight failed (non-critical): %s", e)

	async def highlight_click_point(self, x: float, y: float) -> None:
		"""Show crosshair + expanding ring animation at click coordinates."""
		if not self._settings.enabled or not self._settings.click_feedback_enabled:
			return
		duration_ms = int(self._settings.click_feedback_duration * 1000)
		js_code = f"""
(function() {{
	const x = {x} + window.pageXOffset;
	const y = {y} + window.pageYOffset;
	const ring = document.createElement('div');
	ring.setAttribute('data-sba-highlight', 'click');
	ring.style.cssText =
		'position:absolute; left:' + x + 'px; top:' + y + 'px; ' +
		'width:30px; height:30px; border:3px solid rgba(255,165,0,0.8); ' +
		'border-radius:50%; pointer-events:none; z-index:2147483647; ' +
		'transform:translate(-50%,-50%) scale(0.3); ' +
		'transition:all 0.2s ease-out;';
	document.body.appendChild(ring);
	requestAnimationFrame(function() {{
		ring.style.transform = 'translate(-50%,-50%) scale(1)';
		ring.style.opacity = '0.8';
	}});
	setTimeout(function() {{
		ring.style.opacity = '0';
		ring.style.transform = 'translate(-50%,-50%) scale(1.5)';
		setTimeout(function() {{ ring.remove(); }}, 300);
	}}, {duration_ms});
}})();
"""
		try:
			await self._execute_js(js_code)
		except Exception as e:
			logger.debug("Click highlight failed (non-critical): %s", e)

	async def remove_highlights(self) -> None:
		"""Remove all JS-injected highlight elements from the page."""
		js_code = """
(function() {
	document.querySelectorAll('[data-sba-highlight]').forEach(function(el) {
		el.remove();
	});
})();
"""
		try:
			await self._execute_js(js_code)
		except Exception as e:
			logger.debug("Remove highlights failed (non-critical): %s", e)

	async def add_debug_highlights(self, selector_map: dict) -> None:
		"""Inject index labels on all interactive elements for debugging."""
		if not selector_map:
			return

		elements_js = []
		for index, node in selector_map.items():
			pos = getattr(node, 'absolute_position', None)
			if pos is None:
				continue
			if pos.width <= 0 or pos.height <= 0:
				continue
			elements_js.append(
				f"{{idx:{index}, x:{pos.x:.1f}, y:{pos.y:.1f}, "
				f"w:{pos.width:.1f}, h:{pos.height:.1f}}}"
			)

		if not elements_js:
			return

		color = self._settings.debug_highlight_color
		js_code = f"""
(function() {{
	const items = [{','.join(elements_js)}];
	const container = document.createElement('div');
	container.id = 'sba-debug-highlights';
	container.setAttribute('data-sba-highlight', 'debug');
	items.forEach(function(item) {{
		const box = document.createElement('div');
		box.setAttribute('data-sba-highlight', 'debug');
		box.style.cssText =
			'position:absolute; left:' + item.x + 'px; top:' + item.y + 'px; ' +
			'width:' + item.w + 'px; height:' + item.h + 'px; ' +
			'border:2px dashed {color}; box-sizing:border-box; ' +
			'pointer-events:none; z-index:2147483647;';
		container.appendChild(box);
		const label = document.createElement('span');
		label.setAttribute('data-sba-highlight', 'debug');
		label.style.cssText =
			'position:absolute; left:' + item.x + 'px; top:' + (item.y - 18) + 'px; ' +
			'background:{color}; color:white; font-size:10px; ' +
			'padding:1px 4px; border-radius:2px; font-family:monospace; ' +
			'pointer-events:none; z-index:2147483647;';
		label.textContent = '[' + item.idx + ']';
		container.appendChild(label);
	}});
	document.body.appendChild(container);
}})();
"""
		try:
			await self._execute_js(js_code)
		except Exception as e:
			logger.debug("Debug highlights failed (non-critical): %s", e)

	async def _safe_hide_highlight(self) -> None:
		"""Hide CDP Overlay highlight, suppressing errors."""
		try:
			await self._client.send.Overlay.hideHighlight(
				{}, session_id=self._session_id,
			)
		except Exception:
			pass
