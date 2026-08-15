// 后端 /history/* 端点封装。fetch 用相对路径——dev 经 Vite proxy、prod 同源直连。
import type { AgentHistoryList, DetectedVariable, ActionResult, BatchRowProgress, BatchStepProgress, TaskEvent } from "./types";

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

// ── Live agent 探索任务（P6 M1/M2）─────────────────────────────────────────

const TASK = "/task";

export async function startTask(
	task: string,
	filePaths?: string[],
	record?: boolean,
	viewportMode?: "screenshots" | "livestream",
): Promise<{ task_id: string }> {
	const r = await fetch(`${TASK}/start`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ task, file_paths: filePaths, record, viewport_mode: viewportMode }),
	});
	if (!r.ok) throw new Error((await r.json()).error || "start failed");
	return await r.json();
}

const TASK_EVENT_TYPES = [
	"step_start", "model_call", "model_result", "tool_call", "tool_result",
	"step_end", "anomaly", "session_end", "skill_active", "log", "screenshot", "done",
];

export function subscribeTaskEvents(
	taskId: string,
	onEvent: (e: TaskEvent) => void,
): EventSource {
	// EventSource 只支持 GET → 后端 /task/events 必须 GET。后端 event 名即 type；
	// 收到 done 后主动 close，防 EventSource 自动重连（同 batch progress 模式）。
	const es = new EventSource(`${TASK}/events?task_id=${encodeURIComponent(taskId)}`);
	for (const t of TASK_EVENT_TYPES) {
		es.addEventListener(t, (ev: MessageEvent) => {
			onEvent({ type: t, ...JSON.parse(ev.data) });
			if (t === "done") es.close();
		});
	}
	return es;
}

export function subscribeTaskFrames(
	taskId: string,
	onFrame: (f: { data: string }) => void,
): EventSource {
	// 直播视口（A）：独立 SSE（/task/screencast），仅 livestream 任务。收帧即更新 state.screenshot
	// （经 reducer screencast 分支）。此流不发 done——由 RunView 在任务结束时 es.close()（防自动重连）。
	const es = new EventSource(`${TASK}/screencast?task_id=${encodeURIComponent(taskId)}`);
	es.addEventListener("screencast", (ev: MessageEvent) => onFrame(JSON.parse(ev.data)));
	return es;
}

export async function controlTask(
	taskId: string,
	action: "pause" | "resume" | "stop",
): Promise<void> {
	const r = await fetch(`${TASK}/${action}?task_id=${encodeURIComponent(taskId)}`, { method: "POST" });
	if (!r.ok) throw new Error((await r.json()).error || `${action} failed`);
}

// ── Skills 技能面（P6 后续 B）───────────────────────────────────────────────

const SKILLS = "/skills";

// 技能三文件（与后端 _SKILL_FILE_WHITELIST 一致）
export const SKILL_FILES = ["_sop.md", "selectors.md", "quirks.md"] as const;
export type SkillFile = (typeof SKILL_FILES)[number];

export async function listSkills(): Promise<string[]> {
	const r = await fetch(`${SKILLS}/list`);
	return (await r.json()).hosts;
}

export async function getSkill(host: string): Promise<Record<SkillFile, string>> {
	const r = await fetch(`${SKILLS}/get?host=${encodeURIComponent(host)}`);
	if (!r.ok) throw new Error((await r.json()).error || "get skill failed");
	return (await r.json()).files;
}

export async function putSkill(host: string, file: SkillFile, content: string): Promise<void> {
	const r = await fetch(
		`${SKILLS}/put?host=${encodeURIComponent(host)}&file=${encodeURIComponent(file)}`,
		{
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ content }),
		},
	);
	if (!r.ok) throw new Error((await r.json()).error || "put skill failed");
}
