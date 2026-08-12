import { useCallback, useEffect, useReducer } from "react";
import { initialLiveState, liveReducer } from "../liveReducer";
import * as api from "../api";
import BrowserView from "./BrowserView";

// P6 live agent 控制台（Run 视图）：任务输入 + 实时浏览器视图（截图流）+ 步骤时间线
// + 日志流 + 运行控制（暂停/恢复/停止）+ 录制开关。订阅 /task/events SSE（M1/M2 后端）。
export default function RunView() {
	const [state, dispatch] = useReducer(liveReducer, initialLiveState);
	const running = state.phase === "running" || state.phase === "paused";
	const finished = state.phase === "done" || state.phase === "error";

	const onStart = useCallback(async () => {
		if (!state.task.trim()) return;
		dispatch({ type: "STATUS", status: "启动中…" });
		try {
			const fps = state.filePaths.split("\n").map((s) => s.trim()).filter(Boolean);
			const { task_id } = await api.startTask(
				state.task,
				fps.length ? fps : undefined,
				state.record,
			);
			dispatch({ type: "STARTING", taskId: task_id });
		} catch (e) {
			dispatch({ type: "STATUS", status: `启动失败: ${e}` });
		}
	}, [state.task, state.filePaths, state.record]);

	// SSE 订阅（依赖 taskId：仅新任务建连；done 由 handler 关闭、不重建；events/logs 变化不触发）
	useEffect(() => {
		const tid = state.taskId;
		if (!tid) return;
		const es = api.subscribeTaskEvents(tid, (ev) => dispatch({ type: "EVENT", event: ev }));
		return () => es.close();
	}, [state.taskId]);

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
					<span className="status">{state.status}</span>
				</div>
			</div>

			<div className="run-body">
				<BrowserView mode="screenshots" screenshot={state.screenshot} />
				<div className="panel timeline">
					<h2>步骤时间线</h2>
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
