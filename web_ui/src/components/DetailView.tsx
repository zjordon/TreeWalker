import { useState } from "react";
import type { EditorState, AgentHistory } from "../types";

// P6 详情视图（M5）：Flow 元信息 + 步骤 master-detail。
// 左步骤列表、右选中步骤详情（=「Context 面板」）。
// ⚠️ 每步 DOM 快照（element_tree_text）未持久化（同 screenshot_path 被视觉阶段二阻塞），
// 这里用 state_summary（url/title/tabs/时长等）作为每步上下文的最佳近似；真 DOM 快照回放待后端存盘。
export default function DetailView({ state }: { state: EditorState }) {
	const [sel, setSel] = useState(0);
	if (!state.history) {
		return <div className="panel muted">从左侧选择一个流程查看详情</div>;
	}
	const steps = state.history.history;
	const success = steps.some((s) => s.result.some((r) => r.is_done && r.success));
	const cur = steps[Math.min(sel, steps.length - 1)] ?? null;
	const manualN = state.history.manual_variables?.length ?? 0;

	return (
		<div className="detail-view">
			<div className="panel detail-meta">
				<strong>{state.loadedName}</strong> · {steps.length} 步 ·{" "}
				{success ? "✓ 成功" : "✗ 未完成"}
				{state.history.action_registry_version && (
					<span className="muted"> · 注册表 {state.history.action_registry_version}</span>
				)}
				{manualN > 0 && <span className="muted"> · {manualN} 个手工变量</span>}
			</div>

			<div className="detail-body">
				<div className="panel detail-steps">
					<h2>步骤</h2>
					{steps.length === 0 && <div className="muted">（空）</div>}
					<ul className="var-list">
						{steps.map((s, i) => (
							<li key={i}>
								<button
									className={`flow-item${i === sel ? " selected" : ""}`}
									onClick={() => setSel(i)}
								>
									step {s.step_number} ·{" "}
									{s.model_output.actions.map((a) => a.name).join(",") || "(无动作)"}
								</button>
							</li>
						))}
					</ul>
				</div>
				<div className="panel detail-context">
					{cur && <StepDetail step={cur} />}
				</div>
			</div>
		</div>
	);
}

function StepDetail({ step }: { step: AgentHistory }) {
	const actions = step.model_output.actions ?? [];
	const ie = step.interacted_element ?? [];
	return (
		<div>
			<h2>step {step.step_number} 详情</h2>

			<div className="detail-section">
				<strong>动作（{actions.length}）</strong>
				<ul className="var-list">
					{actions.map((a, i) => (
						<li key={i}>
							<span className="evt-type">{a.name}</span>{" "}
							<span className="muted">{JSON.stringify(a.params)}</span>
						</li>
					))}
				</ul>
			</div>

			<div className="detail-section">
				<strong>结果（{step.result.length}）</strong>
				<ul className="var-list">
					{step.result.map((r, i) => (
						<li key={i}>
							{r.is_done ? (r.success ? "✓" : "✗") : "·"}
							{r.error && <span className="error-text"> {r.error}</span>}
							{r.extracted_content && <span> | {r.extracted_content.slice(0, 120)}</span>}
						</li>
					))}
				</ul>
			</div>

			{step.state_summary && (
				<div className="detail-section">
					<strong>状态摘要</strong>
					<pre className="detail-pre">{JSON.stringify(step.state_summary, null, 2)}</pre>
				</div>
			)}

			{ie.length > 0 && (
				<div className="detail-section">
					<strong>交互元素（{ie.length}）</strong>
					<ul className="var-list">
						{ie.map((e, i) => (
							<li key={i} className="muted">
								{e
									? e.node_name || e.ax_name || JSON.stringify(e.attributes ?? {}).slice(0, 60)
									: "(无)"}
							</li>
						))}
					</ul>
				</div>
			)}
		</div>
	);
}
