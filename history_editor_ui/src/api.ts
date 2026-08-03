// 后端 /history/* 端点封装。fetch 用相对路径——dev 经 Vite proxy、prod 同源直连。
import type { AgentHistoryList, DetectedVariable, ActionResult, BatchRowProgress, BatchStepProgress } from "./types";

const BASE = "/history";

export async function listFiles(): Promise<string[]> {
	const r = await fetch(`${BASE}/list`);
	return (await r.json()).files;
}

export async function loadHistory(name: string): Promise<AgentHistoryList> {
	const r = await fetch(`${BASE}/load?name=${encodeURIComponent(name)}`);
	if (!r.ok) throw new Error((await r.json()).error || "load failed");
	return (await r.json()).history;
}

export async function saveHistory(name: string, history: AgentHistoryList): Promise<void> {
	const r = await fetch(`${BASE}/save?name=${encodeURIComponent(name)}`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(history),
	});
	if (!r.ok) throw new Error((await r.json()).error || "save failed");
}

export async function detectVariables(name: string): Promise<Record<string, DetectedVariable>> {
	const r = await fetch(`${BASE}/detect?name=${encodeURIComponent(name)}`);
	if (!r.ok) throw new Error((await r.json()).error || "detect failed");
	return (await r.json()).variables;
}

export async function rerun(name: string, variables: Record<string, string>): Promise<ActionResult[]> {
	const r = await fetch(`${BASE}/rerun?name=${encodeURIComponent(name)}`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ variables }),
	});
	if (!r.ok) throw new Error((await r.json()).error || "rerun failed");
	return (await r.json()).results;
}

export async function startBatch(
	name: string,
	file: File,
): Promise<{ task_id: string; total_rows: number }> {
	const fd = new FormData();
	fd.append("file", file); // 不设 Content-Type——浏览器自动加 multipart boundary
	const r = await fetch(`${BASE}/batch/start?name=${encodeURIComponent(name)}`, {
		method: "POST",
		body: fd,
	});
	if (!r.ok) throw new Error((await r.json()).error || "batch start failed");
	return await r.json();
}

export async function cancelBatch(taskId: string): Promise<void> {
	const r = await fetch(`${BASE}/batch/cancel?task_id=${encodeURIComponent(taskId)}`, {
		method: "POST",
	});
	if (!r.ok) throw new Error((await r.json()).error || "cancel failed");
}

export interface BatchDoneData {
	total: number;
	succeeded: number;
	failed: number;
	error?: string;
}

export function subscribeBatchProgress(
	taskId: string,
	onRow: (row: BatchRowProgress) => void,
	onStep: (step: BatchStepProgress) => void,
	onDone: (data: BatchDoneData) => void,
): EventSource {
	// EventSource 只支持 GET → 后端 progress 端点必须 GET
	const es = new EventSource(`${BASE}/batch/progress?task_id=${encodeURIComponent(taskId)}`);
	es.addEventListener("row", (e: MessageEvent) => onRow(JSON.parse(e.data)));
	es.addEventListener("step", (e: MessageEvent) => onStep(JSON.parse(e.data)));
	es.addEventListener("done", (e: MessageEvent) => {
		onDone(JSON.parse(e.data));
		es.close();
	});
	// 不监听 es.onerror：原生 error = 连接断开（自动重连）；服务端错误走 done.error
	return es;
}
