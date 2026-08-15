import { useEffect, useState } from "react";
import * as api from "../api";
import type { LiveTaskItem } from "../types";

// T2 H（M2）：FlowWorkspace 侧栏顶部「进行中」区——活跃/最近 live task 列表。
// 数据源 GET /task/list（只读端点），挂载即拉 + 30s 轮询（不用 SSE：zone 在流程库
// 模式，没有对应事件流；列表极短，轮询开销可忽略）。无任务时整区隐藏。
// 点击行为在 FlowWorkspace 接线（taskId 不同才 ADOPT 接管，否则只切模式回运行视图）。
export default function LiveZone({ onOpen }: { onOpen: (item: LiveTaskItem) => void }) {
	const [items, setItems] = useState<LiveTaskItem[]>([]);

	useEffect(() => {
		let alive = true;
		const refresh = async () => {
			try {
				const tasks = await api.listTasks();
				if (alive) setItems(tasks);
			} catch {
				// 静默：zone 非关键路径（后端未起/断连时侧栏其余功能照常）
			}
		};
		refresh();
		const timer = setInterval(refresh, 30_000);
		return () => {
			alive = false;
			clearInterval(timer);
		};
	}, []);

	if (items.length === 0) return null;
	return (
		<div className="live-zone">
			<h2>进行中</h2>
			<ul className="var-list">
				{items.map((t) => (
					<li key={t.task_id}>
						<button className="flow-item" onClick={() => onOpen(t)} title={t.task}>
							{t.phase === "running" && <span className="live-running">●</span>}
							{t.phase === "paused" && <span className="live-paused">◐</span>}
							{t.phase === "done" && <span>{t.success ? "✓" : "✗"}</span>}
							{" "}
							{t.task.length > 24 ? `${t.task.slice(0, 24)}…` : t.task}
							{t.phase === "done" && t.saved && (
								<span className="muted">（已存 {t.saved}）</span>
							)}
						</button>
					</li>
				))}
			</ul>
		</div>
	);
}
