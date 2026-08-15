import type { ReactNode } from "react";
import type { TaskEvent } from "../types";

// T2 G（M7，阶段一）：右 Context 面板。
// 容器与渲染器分离（01 §4.2「右 Context 可插拔」预留缝）：ContextPanel 是通用容器
// （标题 + 折叠 + ✕），不关心选中物是什么——未来 D（DOM 快照回放）/批量行详情
// 复用容器、换 children 即可，不改本文件结构。当前唯一消费者是 RunView（事件详情）。

export interface ContextPanelProps {
	title: string;
	collapsed: boolean;
	onToggle: () => void;
	onClose?: () => void; // ✕ 清空选中（缺省则不显示关闭钮）
	children: ReactNode;
}

export default function ContextPanel({ title, collapsed, onToggle, onClose, children }: ContextPanelProps) {
	if (collapsed) {
		return (
			<div className="panel context-panel collapsed">
				<button className="ctx-toggle" onClick={onToggle} title="展开详情">
					⟨
				</button>
			</div>
		);
	}
	return (
		<div className="panel context-panel">
			<div className="ctx-head">
				<button className="ctx-toggle" onClick={onToggle} title="折叠（给中心让位）">
					⟩
				</button>
				<h2>{title}</h2>
				{onClose && (
					<button className="ctx-close" onClick={onClose} title="关闭">
						✕
					</button>
				)}
			</div>
			<div className="ctx-body">{children}</div>
		</div>
	);
}

// 按事件类型换渲染器。screenshot/log 事件不经 reducer 入 events（各自走独立通道），
// 天然不在时间线可选列表里——此处无需排除。
export function EventDetail({
	event,
	onOpenSkills,
}: {
	event: TaskEvent;
	onOpenSkills?: (host?: string | null) => void;
}) {
	if (event.type === "tool_call") {
		return (
			<div className="ctx-sections">
				<div className="detail-section">
					<strong>动作</strong>
					<div>
						<span className="evt-type">{String(event.action_name)}</span>
						{event.step != null && <span className="muted"> · step {String(event.step)}</span>}
					</div>
				</div>
				<div className="detail-section">
					<strong>参数</strong>
					<pre className="ctx-pre">{JSON.stringify(event.params ?? {}, null, 2)}</pre>
				</div>
				{event.element_xpath != null && (
					<div className="detail-section">
						<strong>元素定位</strong>
						<div className="ctx-kv">
							<span className="muted">index</span> {String(event.element_index ?? "-")}
						</div>
						<div className="ctx-kv mono">{String(event.element_xpath)}</div>
						{event.element_bbox != null && (
							<div className="ctx-kv muted">bbox {JSON.stringify(event.element_bbox)}</div>
						)}
					</div>
				)}
			</div>
		);
	}
	if (event.type === "model_result") {
		const goal = event.next_goal;
		return (
			<div className="ctx-sections">
				<div className="detail-section">
					<strong>本步目标</strong>
					<div>{goal == null ? "（无）" : String(goal)}</div>
				</div>
				<div className="detail-section">
					<strong>Token</strong>
					<div>
						🪙 ↑{Number(event.input_tokens ?? 0)} ↓{Number(event.output_tokens ?? 0)}
					</div>
				</div>
			</div>
		);
	}
	if (event.type === "skill_active") {
		return (
			<div className="ctx-sections">
				<div className="detail-section">
					<strong>活动技能</strong>
					<div>
						{event.host ? String(event.host) : "（无 host）"} · {Number(event.char_count ?? 0)} 字
					</div>
					{event.host != null && onOpenSkills && (
						<button className="chip-btn" onClick={() => onOpenSkills(String(event.host))}>
							查看/编辑技能 →
						</button>
					)}
				</div>
			</div>
		);
	}
	// 其他类型（step_start/step_end/tool_result/anomaly/session_end…）：原始 JSON 兜底（透明度）
	return <pre className="ctx-pre">{JSON.stringify(event, null, 2)}</pre>;
}
