import { useReducer, useCallback, useEffect, useState } from "react";
import { initialState, reducer } from "./reducer";
import * as api from "./api";
import ActionList from "./components/ActionList";
import ActionEditor from "./components/ActionEditor";
import VariablePanel from "./components/VariablePanel";
import RunPanel from "./components/RunPanel";
import BatchRunPanel from "./components/BatchRunPanel";
import DetailView from "./components/DetailView";

// P6 流程库工作区：左 sidebar 列流程（点击加载）+ 编辑/重放/详情 tab（M4）。
// reducer / handlers / 子组件全部复用原编辑器（零回归），仅把渲染重排成 sidebar + tab。
// 运行（live 探索）在 AppShell 的「探索」模式（RunView）；技能/设置后置。
type Tab = "edit" | "replay" | "detail";

export default function FlowWorkspace() {
	const [state, dispatch] = useReducer(reducer, initialState);
	const [files, setFiles] = useState<string[]>([]);
	const [tab, setTab] = useState<Tab>("edit");

	const refresh = useCallback(async () => {
		try {
			setFiles(await api.listFiles());
		} catch {
			setFiles([]);
		}
	}, []);
	// 流程列表：挂载 + 加载/保存后刷新（保存新生成的 history 后立即可见）
	useEffect(() => {
		refresh();
	}, [refresh, state.loadedName, state.dirty]);

	const onLoad = useCallback(async (name: string) => {
		try {
			const h = await api.loadHistory(name);
			dispatch({ type: "LOAD", history: h, name });
			setTab("edit");
		} catch (e) {
			dispatch({ type: "STATUS", status: `加载失败: ${e}` });
		}
	}, []);

	const onDetect = useCallback(async () => {
		if (!state.loadedName) return;
		if (state.dirty) {
			dispatch({ type: "STATUS", status: "提示：先保存再检测（detect 读盘上文件）" });
		}
		try {
			const vars = await api.detectVariables(state.loadedName);
			dispatch({ type: "DETECT_DONE", variables: vars });
		} catch (e) {
			dispatch({ type: "STATUS", status: `检测失败: ${e}` });
		}
	}, [state.loadedName, state.dirty]);

	const onSave = useCallback(async () => {
		if (!state.loadedName || !state.history) return;
		try {
			await api.saveHistory(state.loadedName, state.history);
			dispatch({ type: "SET_DIRTY", dirty: false });
			dispatch({ type: "STATUS", status: "已保存 ✓" });
		} catch (e) {
			dispatch({ type: "STATUS", status: `保存失败: ${e}` });
		}
	}, [state.loadedName, state.history]);

	const onRun = useCallback(async () => {
		if (!state.loadedName) return;
		dispatch({ type: "STATUS", status: "试跑中…（真实起浏览器）" });
		try {
			const results = await api.rerun(state.loadedName, {});
			dispatch({ type: "RUN_DONE", results });
		} catch (e) {
			dispatch({ type: "RUN_DONE", results: null });
			dispatch({ type: "STATUS", status: `试跑失败: ${e}` });
		}
	}, [state.loadedName]);

	const onStartBatch = useCallback(async (file: File) => {
		if (!state.loadedName) return;
		dispatch({ type: "BATCH_START" });
		try {
			const { task_id, total_rows } = await api.startBatch(state.loadedName, file);
			dispatch({ type: "BATCH_STARTED", taskId: task_id, totalRows: total_rows });
		} catch (e) {
			dispatch({ type: "BATCH_ERROR", error: `启动失败: ${e}` });
		}
	}, [state.loadedName]);

	const onCancelBatch = useCallback(async () => {
		if (!state.batch.taskId) return;
		try {
			await api.cancelBatch(state.batch.taskId);
			dispatch({ type: "BATCH_CANCEL" });
		} catch (e) {
			dispatch({ type: "STATUS", status: `中止失败: ${e}` });
		}
	}, [state.batch.taskId]);

	const onResetBatch = useCallback(() => {
		dispatch({ type: "BATCH_RESET" });
	}, []);

	// SSE 订阅批量进度（依赖只 taskId，避免每行 row 触发重建连接）
	useEffect(() => {
		const taskId = state.batch.taskId;
		if (!taskId || state.batch.phase !== "running") return;
		const es = api.subscribeBatchProgress(
			taskId,
			(row) => dispatch({ type: "BATCH_ROW", row }),
			(step) => dispatch({ type: "BATCH_STEP", step }),
			(done) =>
				dispatch({
					type: "BATCH_DONE",
					total: done.total,
					succeeded: done.succeeded,
					failed: done.failed,
					...(done.error ? { error: done.error } : {}),
				}),
		);
		return () => es.close();
	}, [state.batch.taskId]);

	const running = state.batch.phase === "running" || state.batch.phase === "starting";

	return (
		<div className="flow-workspace">
			<aside className="flow-sidebar">
				<h2>流程库</h2>
				<ul className="var-list flow-list">
					{files.length === 0 && <li className="muted">（空）</li>}
					{files.map((f) => (
						<li key={f} className={f === state.loadedName ? "selected-flow" : ""}>
							<button className="flow-item" onClick={() => onLoad(f)} disabled={running}>
								{f}
							</button>
						</li>
					))}
				</ul>
			</aside>

			<section className="flow-main">
				<nav className="flow-tabs">
					<button className={tab === "edit" ? "active" : ""} onClick={() => setTab("edit")}>
						编辑
					</button>
					<button className={tab === "replay" ? "active" : ""} onClick={() => setTab("replay")}>
						重放
					</button>
					<button className={tab === "detail" ? "active" : ""} onClick={() => setTab("detail")}>
						详情
					</button>
					<span className="status">{state.status}</span>
				</nav>

				{tab === "edit" && (
					<div className="edit-tab">
						<div className="bar">
							<button onClick={onDetect} disabled={!state.loadedName}>
								检测变量
							</button>
							<button onClick={onSave} disabled={!state.history}>
								保存{state.dirty ? " *" : ""}
							</button>
						</div>
						{state.history ? (
							<div className="layout">
								<ActionList state={state} dispatch={dispatch} />
								<div className="right">
									<ActionEditor state={state} dispatch={dispatch} />
									<VariablePanel state={state} dispatch={dispatch} />
								</div>
							</div>
						) : (
							<div className="panel muted">从左侧选择一个流程开始编辑</div>
						)}
					</div>
				)}

				{tab === "replay" && (
					<div className="replay-tab">
						<div className="bar">
							<button onClick={onRun} disabled={!state.loadedName}>
								试跑
							</button>
						</div>
						<RunPanel state={state} />
						<BatchRunPanel
							state={state}
							onStart={onStartBatch}
							onCancel={onCancelBatch}
							onReset={onResetBatch}
						/>
					</div>
				)}

				{tab === "detail" && <DetailView state={state} />}
			</section>
		</div>
	);
}
