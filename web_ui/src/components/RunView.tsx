import { useCallback, useEffect, useReducer, useState } from "react";
import { initialLiveState, liveReducer } from "../liveReducer";
import * as api from "../api";
import { useAppNav } from "../appNav";
import BrowserView from "./BrowserView";

// P6 live agent 控制台（Run 视图）：任务输入 + 实时浏览器视图（截图流）+ 步骤时间线
// + 日志流 + 运行控制（暂停/恢复/停止）+ 录制开关。订阅 /task/events SSE（M1/M2 后端）。
export default function RunView() {
	const [state, dispatch] = useReducer(liveReducer, initialLiveState);
	const nav = useAppNav();
	const running = state.phase === "running" || state.phase === "paused";
	const finished = state.phase === "done" || state.phase === "error";
	// 直播视口（A）：livestream 模式开第二个 EventSource 订阅 /task/screencast 连续帧；仅对新任务生效
	const [vpMode, setVpMode] = useState<"screenshots" | "livestream">("screenshots");
	// running（含 paused）才开帧流；任务结束（done/error）或切走即关，防 EventSource 自动重连
	const frameStreamOn = vpMode === "livestream" && !!state.taskId && running;

	const onStart = useCallback(async () => {
		if (!state.task.trim()) return;
		dispatch({ type: "STATUS", status: "启动中…" });
		try {
			const fps = state.filePaths.split("\n").map((s) => s.trim()).filter(Boolean);
			const { task_id } = await api.startTask(
				state.task,
				fps.length ? fps : undefined,
				state.record,
				vpMode,
			);
			dispatch({ type: "STARTING", taskId: task_id });
		} catch (e) {
			dispatch({ type: "STATUS", status: `启动失败: ${e}` });
		}
	}, [state.task, state.filePaths, state.record, vpMode]);

	// SSE 订阅（依赖 taskId：仅新任务建连；done 由 handler 关闭、不重建；events/logs 变化不触发）
	useEffect(() => {
		const tid = state.taskId;
		if (!tid) return;
		const es = api.subscribeTaskEvents(tid, (ev) => dispatch({ type: "EVENT", event: ev }));
		return () => es.close();
	}, [state.taskId]);

	// 直播视口（A）：livestream 额外订阅 /task/screencast 连续帧 → 帧复用 screenshot 渲染
	useEffect(() => {
		if (!frameStreamOn || !state.taskId) return;
		const es = api.subscribeTaskFrames(state.taskId, (frame) =>
			dispatch({ type: "EVENT", event: { type: "screencast", data: frame.data } }),
		);
		return () => es.close();
	}, [frameStreamOn, state.taskId]);

	const onControl = useCallback(
		async (action: "pause" | "resume" | "stop") => {
			if (!state.taskId) return;
			try {
				await api.controlTask(state.taskId, action);
				if (action === "pause") dispatch({ type: "PAUSED" });
				else if (action === "resume") dispatch({ type: "RESUMED" });
				else dispatch({ type: "STATUS", status: "停止中…" });
			} catch (e) {
				dispatch({ type: "STATUS", status: `${action} 失败: ${e}` });
			}
		},
		[state.taskId],
	);

	return (
		<div className="run-view">
			<div className="panel run-input">
				<textarea
					placeholder="想让 agent 在网页上做什么？"
					value={state.task}
					onChange={(e) => dispatch({ type: "FIELD", key: "task", value: e.target.value })}
					disabled={running}
					rows={2}
				/>
				<textarea
					className="file-paths-input"
					placeholder="文件路径（每行一个，可选）"
					value={state.filePaths}
					onChange={(e) => dispatch({ type: "FIELD", key: "filePaths", value: e.target.value })}
					disabled={running}
					rows={1}
				/>
				<div className="bar">
					<select
						className="vp-mode"
						value={vpMode}
						onChange={(e) => setVpMode(e.target.value as "screenshots" | "livestream")}
						disabled={running}
						title="视口推流模式（仅对新任务生效）"
					>
						<option value="screenshots">📷 截图</option>
						<option value="livestream">📡 直播</option>
					</select>
					<label className="record-toggle">
						<input
							type="checkbox"
							checked={state.record}
							onChange={(e) => dispatch({ type: "FIELD", key: "record", value: e.target.checked })}
							disabled={running}
						/>
						录制轨迹
					</label>
					{!running && (
						<button onClick={onStart} disabled={!state.task.trim()}>
							▶ 发送
						</button>
					)}
					{running && state.phase === "running" && (
						<button onClick={() => onControl("pause")}>⏸ 暂停</button>
					)}
					{running && state.phase === "paused" && (
						<button onClick={() => onControl("resume")}>▶ 恢复</button>
					)}
					{running && (
						<button className="error" onClick={() => onControl("stop")}>
							⏹ 停止
						</button>
					)}
					{finished && <button onClick={() => dispatch({ type: "RESET" })}>新任务</button>}
					{state.activeSkill && (
						<span className="skill-chip">
							{state.activeSkill.skillLoaded ? (
								<button
									className="chip-btn"
									onClick={() => nav?.openSkills(state.activeSkill?.host)}
									title="点击查看/编辑该 host 的技能"
								>
									🔧 {state.activeSkill.host}（{state.activeSkill.charCount}字）
								</button>
							) : (
								<span className="muted">🔧 无技能{state.activeSkill.host ? `（${state.activeSkill.host}）` : ""}</span>
							)}
						</span>
					)}
					<span className="status">{state.status}</span>
				</div>
			</div>

			<div className="run-body">
				<BrowserView mode={vpMode} screenshot={state.screenshot} highlights={state.highlights} />
				<div className="panel timeline">
					<h2>步骤时间线</h2>
					<div className="run-stats muted">
						⏱ {(state.elapsedMs / 1000).toFixed(1)}s · 🪙 ↑{state.tokens.in} ↓{state.tokens.out}
					</div>
					{state.events.length === 0 && <div className="muted">尚无步骤</div>}
					<ul className="var-list">
						{state.events.map((e, i) => (
							<li key={i} className={`evt evt-${e.type}`}>
								<span className="evt-type">{e.type}</span>
								{e.step != null && <span className="muted"> step {e.step}</span>}
								{"action_name" in e && <span> {String(e.action_name)}</span>}
							</li>
						))}
					</ul>
				</div>
			</div>

			<div className="panel log-stream">
				<h2>日志</h2>
				<div className="logs">
					{state.logs.length === 0 && <div className="muted">暂无日志</div>}
					{state.logs.map((e, i) => (
						<div key={i} className={`log-line log-${String(e.level).toLowerCase()}`}>
							<span className="muted">{String(e.logger)}</span> {String(e.msg)}
						</div>
					))}
				</div>
			</div>
		</div>
	);
}
