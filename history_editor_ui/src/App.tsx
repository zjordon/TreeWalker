import { useReducer, useCallback, useEffect } from "react";
import { initialState, reducer } from "./reducer";
export type { EditorAction } from "./reducer";
import * as api from "./api";
import Toolbar from "./components/Toolbar";
import ActionList from "./components/ActionList";
import ActionEditor from "./components/ActionEditor";
import VariablePanel from "./components/VariablePanel";
import RunPanel from "./components/RunPanel";
import BatchRunPanel from "./components/BatchRunPanel";

export default function App() {
	const [state, dispatch] = useReducer(reducer, initialState);

	const onLoad = useCallback(async (name: string) => {
		try {
			const h = await api.loadHistory(name);
			dispatch({ type: "LOAD", history: h, name });
		} catch (e) {
			dispatch({ type: "STATUS", status: `加载失败: ${e}` });
		}
	}, []);

	const onDetect = useCallback(async () => {
		if (!state.loadedName) return;
		// detect 读盘上文件；本地有未保存改动时先提示
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

	return (
		<div className="app">
			<h1>TreeWalker 历史编辑器</h1>
			<Toolbar
				state={state}
				onLoad={onLoad}
				onDetect={onDetect}
				onSave={onSave}
				onRun={onRun}
			/>
			<div className="layout">
				<ActionList state={state} dispatch={dispatch} />
				<div className="right">
					<ActionEditor state={state} dispatch={dispatch} />
					<VariablePanel state={state} dispatch={dispatch} />
				</div>
			</div>
			<RunPanel state={state} />
			<BatchRunPanel
				state={state}
				onStart={onStartBatch}
				onCancel={onCancelBatch}
				onReset={onResetBatch}
			/>
		</div>
	);
}
