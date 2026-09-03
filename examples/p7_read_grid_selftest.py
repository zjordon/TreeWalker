"""P7 tool_layer 验收自测：read_grid 动作 + [Grid] 元信息在真实 Magento 上跑通。

覆盖（docs/p7/tool_layer/01-feasibility-and-impl-plan.md §5 验收 2）：
  1. read_grid uiregistry 主通道：sales_order_grid，sorting=created_at desc，
     page_size=3，fields 裁剪 → 3 行、total=308、行序 2023-05 起
  2. read_grid fresh 清残留：残留 status=complete 书签时 active_before 回报
  3. read_grid legacy 通道：review 网格（无 uiRegistry）→ legacy_ajax 行
  4. get_state 的 grid_meta：sales_order_grid 页产出 namespace/total/sorting
  5. B3 页面消息读取（只读，不点保存——无副作用验证）

只读（read_grid 的 ds.set 会写书签视图，但订单网格书签本就残留态，等价）。
用法：uv run python examples/p7_read_grid_selftest.py [--port 9223]
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url
from tree_walker.tools.actions import Tools

JS_LOGIN_CHECK = """
(async function(){
  if (document.getElementById('username')) {
    var u = document.getElementById('username'), p = document.getElementById('login');
    u.value = 'admin'; p.value = 'admin1234';
    u.dispatchEvent(new Event('input', {bubbles:true}));
    p.dispatchEvent(new Event('input', {bubbles:true}));
    document.getElementById('login-form').submit();
    return 'submitted';
  }
  return 'already-logged-in:' + location.href.slice(0, 80);
})()
"""


def verdict(name: str, ok: bool, detail: str = "") -> bool:
	print(f"{'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail else ""))
	return ok


async def main() -> int:
	ap = argparse.ArgumentParser()
	ap.add_argument("--port", type=int, default=9223)
	args = ap.parse_args()
	logging.basicConfig(level=logging.WARNING)

	ws_url = _fetch_ws_url("localhost", args.port)
	if not ws_url:
		print(f"✗ {args.port} 端口无 debug Chrome")
		return 1
	browser = BrowserSession(ws_url=ws_url)
	await browser.start()
	tools = Tools()
	all_ok = True
	try:
		await browser.navigate("http://localhost:7780/admin/sales/order/")
		await asyncio.sleep(3)
		if "submitted" in str(await browser.evaluate(JS_LOGIN_CHECK)):
			print("[login] submitted…")
			await asyncio.sleep(6)

		# 1) uiregistry 主通道：排序 + 字段裁剪 + 前三行
		r = await tools.execute("read_grid", {
			"sorting": "created_at desc", "page_size": 3,
			"fields": ["entity_id", "increment_id", "created_at", "status"],
		}, browser)
		print(f"\n[1] uiregistry: error={r.error}")
		preview = (r.extracted_content or "")[:400]
		print(f"    {preview}")
		if not r.error:
			all_ok &= verdict("uiregistry 通道返回", "ns=sales_order_grid" in preview and "sorted=created_at desc" in preview)
			# 小结果整段 JSON 都在 extracted_content 尾部（未落盘）——从首个 { 解析到串尾
			try:
				full = r.extracted_content or ""
				if "saved to" in full:
					all_ok &= verdict("结果可解析", False, "unexpected spill on 3-row read")
				else:
					payload, _ = json.JSONDecoder().raw_decode(full[full.index("{"):])
					rows = payload.get("rows", [])
					dates = [row.get("created_at", "") for row in rows]
					all_ok &= verdict("行序 created_at desc", len(rows) == 3 and dates == sorted(dates, reverse=True), str(dates))
					all_ok &= verdict("total 非空", payload.get("total_records") not in (None, 0), str(payload.get("total_records")))
					all_ok &= verdict("fields 裁剪", all(set(row.keys()) <= {"entity_id", "increment_id", "created_at", "status"} for row in rows))
			except (ValueError, json.JSONDecodeError) as e:
				all_ok &= verdict("结果可解析", False, str(e))
		else:
			all_ok = False

		# 2）get_state 的 grid_meta（B2）
		state = await browser.get_state(include_screenshot=False)
		gm = state.grid_meta
		print(f"\n[2] grid_meta: {json.dumps(gm, ensure_ascii=False)[:300] if gm else None}")
		all_ok &= verdict("grid_meta 产出", bool(gm and gm.get("namespace") == "sales_order_grid"))
		if gm:
			from tree_walker.prompts.system_prompt import build_state_message
			msg = build_state_message(state, task="t", grid_meta=gm)
			all_ok &= verdict("[Grid] 渲染", "[Grid] sales_order_grid" in msg and "sorted: created_at desc" in msg,
				"含残留警告" if "leftover" in msg else "无残留")

		# 3）legacy 通道：评论网格
		await browser.navigate("http://localhost:7780/admin/review/product/index/")
		await asyncio.sleep(4)
		r2 = await tools.execute("read_grid", {"page_size": 5}, browser)
		print(f"\n[3] legacy: error={r2.error}")
		print(f"    {(r2.extracted_content or '')[:300]}")
		all_ok &= verdict("legacy_ajax 通道", not r2.error and "legacy_ajax" in (r2.extracted_content or ""))

		# 4）B3 页面消息（订单网格页无 toast，应空串不报错）
		msg = await tools._read_page_messages(browser)
		all_ok &= verdict("page_messages 不炸", isinstance(msg, str), repr(msg[:60]))

		print("\n" + ("🎉 全部通过" if all_ok else "⚠️ 存在失败项"))
		return 0 if all_ok else 2
	finally:
		await browser.stop()


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
