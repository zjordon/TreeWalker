"""dump 手工录制每一步的 url + 动作名，看视频上传那步页面跳转的时序。"""

import json
from pathlib import Path


def flow(path, label):
	p = Path("rerun-history") / path
	hist = json.loads(p.read_text(encoding="utf-8"))
	steps = hist.get("history", [])
	print(f"\n========== {label} ({path}) {len(steps)} 步 ==========")
	for i, step in enumerate(steps):
		acts = (step.get("model_output") or {}).get("actions") or []
		url = (step.get("state_summary") or {}).get("url", "")
		# 压缩 url：只留 path 段
		short = url.split("?")[0].replace("https://creator.douyin.com/creator-micro/content", "…/c")
		for a in acts:
			n = a.get("name")
			extra = ""
			if n == "upload_file":
				ie = (step.get("interacted_element") or [None])
				ie0 = ie[0] if ie else None
				if ie0:
					attrs = ie0.get("attributes", {}) or {}
					extra = f"  [accept={attrs.get('accept','')[:25]!r} class={attrs.get('class','')[:30]!r}]"
				else:
					extra = "  [无指纹]"
			elif n == "navigate":
				extra = f"  → {a.get('params',{}).get('url','').split('?')[0][-40:]}"
			print(f"  step {i:2d}  {n:14s} {short[:42]:42s}{extra}")


flow("douyin_redesign3.json", "手工录制")
flow("douyin_upload_history.json", "agent 录制")
