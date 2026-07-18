"""dump 两个录制文件里的 upload_file 步骤，对比 interacted_element 指纹（class/accept/xpath）。

看手工录制 douyin_redesign3.json 的 step3 录到的 image input 到底长啥样、
vs agent 录的 douyin_upload_history.json 的 video input。
"""

import json
import sys
from pathlib import Path


def dump(path, label):
	p = Path("rerun-history") / path
	if not p.exists():
		print(f"[{label}] 不存在: {p}"); return
	hist = json.loads(p.read_text(encoding="utf-8"))
	steps = hist.get("history", [])
	print(f"\n========== {label}  ({path})  共 {len(steps)} 步 ==========")
	for i, step in enumerate(steps):
		acts = (step.get("model_output") or {}).get("actions") or []
		for a in acts:
			if a.get("name") == "upload_file":
				ie = (step.get("interacted_element") or [None])
				ie0 = ie[0] if ie else None
				print(f"\n--- step {i}  upload_file ---")
				print(f"  params.path = {a.get('params',{}).get('path')}")
				print(f"  state_summary.url = {(step.get('state_summary') or {}).get('url','')[:70]}")
				if ie0:
					print(f"  interacted_element[0]:")
					print(f"    node_name   = {ie0.get('node_name')}")
					print(f"    element_hash= {ie0.get('element_hash')}")
					print(f"    x_path      = {ie0.get('x_path')}")
					# file input 的 accept/type 在 attributes 里？打印所有 key
					print(f"    keys        = {sorted(ie0.keys())}")
					# 找 accept/type/class 相关字段
					for k in ("attributes", "node_attributes", "attrs"):
						if k in ie0:
							print(f"    {k} = {ie0[k]}")
				else:
					print(f"  interacted_element[0] = None（无指纹）")


dump("douyin_redesign3.json", "手工录制（录错 image）")
dump("douyin_upload_history.json", "agent 录制（正确 video）")
