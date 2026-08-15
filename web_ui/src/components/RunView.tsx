import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api";
import { useAppNav } from "../appNav";
import { useLiveTask } from "../liveContext";
import BrowserView from "./BrowserView";
import ContextPanel, { EventDetail } from "./ContextPanel";

// P6 live agent 控制台（Run 视图）：任务输入 + 实时浏览器视图（截图流）+ 步骤时间线
// + 日志流 + 运行控制（暂停/恢复/停止）+ 录制开关。订阅 /task/events SSE（M1/M2 后端）。
// T2 H（M1）：live state 来自 AppShell 级 <LiveTaskProvider>（切模式卸载不丢 state），
// 本组件只是消费者；SSE 订阅 useEffect 依赖 state.taskId，重挂载即重订续播。
// T2 I6（M5）：任务输入 ↑/↓ 翻历史（与 TUI 共享 ~/.treewalker/history.json）。
export default function RunView() {
	const { state, dispatch } = useLiveTask();
	const nav = useAppNav();
	const running = state.phase === "running" || state.phase === "paused";
	const finished = state.phase === "done" || state.phase === "error";
	// 直播视口（A）：livestream 模式开第二个 EventSource 订阅 /task/screencast 连续帧；仅对新任务生效
	const [vpMode, setVpMode] = useState<"screenshots" | "livestream">("screenshots");
	// running（含 paused）才开帧流；任务结束（done/error）或切走即关，防 EventSource 自动重连
	const frameStreamOn = vpMode === "livestream" && !!state.taskId && running;
	// T2 I6（M5）：任务历史（挂载拉一次；启动成功后 push + 重拉）。histIdx 用 ref（不影响
	// 渲染），哨兵 = taskHistory.length 表示「新输入」位——从这里开始 ↑ 向旧翻。
	const [taskHistory, setTaskHistory] = useState<string[]>([]);
	const histIdxRef = useRef<number>(0);
	// T2 G（M7）：右 Context 面板折叠态（选中态在 live state 的 selectedEvent）
	const [ctxCollapsed, setCtxCollapsed] = useState(false);
	const selectedEvt =
		state.selectedEvent != null ? (state.events[state.selectedEvent] ?? null) : null;

	useEffect(() => {
		api.getTaskHistory()
			.then((tasks) => {
				setTaskHistory(tasks);
				histIdxRef.current = tasks.length; // 初始停在「新输入」位
			})
			.catch(() => {}); // 历史非关键路径，静默
	}, []);

	const refreshHistory = useCallback(async () => {
		try {
			const tasks = await api.getTaskHistory();
			setTaskHistory(tasks);
			histIdxRef.current = tasks.length;
		} catch {
			/* 静默 */
		}
	}, []);

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
				nav?.model || undefined, // T2 I5：本次 override（空 = 跟随设置默认）
			);
			dispatch({ type: "STARTING", taskId: task_id });
			// I6：启动成功后才落历史（§8.8：失败任务不污染）；失败静默
			void api.pushTaskHistory(state.task.trim()).then(refreshHistory).catch(() => {});
		} catch (e) {
			dispatch({ type: "STATUS", status: `启动失败: ${e}` });
		}
	}, [state.task, state.filePaths, state.record, vpMode, refreshHistory, nav?.model]);

	// I6：任务输入框 ↑/↓ 翻历史。光标不在首行（↑）/末行（↓）时把方向键还给 textarea 的
	// 多行编辑（preventDefault 只在跨「新输入位 ↔ 历史」边界时发生）。
	const onTaskKeyDown = useCallback(
		(e: React.KeyboardEvent<HTMLTextAreaElement>) => {
			if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
			if (taskHistory.length === 0) return;
			const ta = e.currentTarget;
			const cursorLine = ta.value.slice(0, ta.selectionStart).split("\n").length;
			const lastLine = ta.value.split("\n").length;
			if (e.key === "ArrowUp" && cursorLine > 1) return;
			if (e.key === "ArrowDown" && cursorLine < lastLine) return;
			let idx = histIdxRef.current;
			if (e.key === "ArrowUp" && idx > 0) idx -= 1;
			else if (e.key === "ArrowDown" && idx < taskHistory.length) idx += 1; // 翻到底 → 回到「新输入」位
			else return;
			e.preventDefault();
			histIdxRef.current = idx;
			dispatch({
				type: "FIELD",
				key: "task",
				value: idx === taskHistory.length ? "" : taskHistory[idx],
			});
		},
		[taskHistory, dispatch],
	);

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
					placeholder="想让 agent 在网页上做什么？（↑ 翻历史）"
					value={state.task}
					onChange={(e) => dispatch({ type: "FIELD", key: "task", value: e.target.value })}
					onKeyDown={onTaskKeyDown}
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

			<div
				className={
					selectedEvt == null
						? "run-body"
						: ctxCollapsed
							? "run-body ctx-open ctx-collapsed"
							: "run-body ctx-open"
				}
			>
				<BrowserView mode={vpMode} screenshot={state.screenshot} highlights={state.highlights} />
				<div className="panel timeline">
					<h2>步骤时间线</h2>
					<div className="run-stats muted">
						⏱ {(state.elapsedMs / 1000).toFixed(1)}s · 🪙 ↑{state.tokens.in} ↓{state.tokens.out}
					</div>
					{state.events.length === 0 && <div className="muted">尚无步骤</div>}
					<ul className="var-list">
						{state.events.map((e, i) => (
							<li
								key={i}
								className={`evt evt-${e.type}${i === state.selectedEvent ? " selected" : ""}`}
							>
								<button
									className="evt-btn"
									onClick={() => dispatch({ type: "SELECT_EVENT", index: i })}
									title="点击在右侧查看详情"
								>
									<span className="evt-type">{e.type}</span>
									{e.step != null && <span className="muted"> step {e.step}</span>}
									{"action_name" in e && <span> {String(e.action_name)}</span>}
								</button>
							</li>
						))}
					</ul>
				</div>
				{selectedEvt != null && (
					<ContextPanel
						title={`${selectedEvt.type}${selectedEvt.step != null ? ` · step ${String(selectedEvt.step)}` : ""}`}
						collapsed={ctxCollapsed}
						onToggle={() => setCtxCollapsed((c) => !c)}
						onClose={() => dispatch({ type: "SELECT_EVENT", index: null })}
					>
						<EventDetail event={selectedEvt} onOpenSkills={(host) => nav?.openSkills(host)} />
					</ContextPanel>
				)}
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
