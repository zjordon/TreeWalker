// 后端 /history/* 端点封装。fetch 用相对路径——dev 经 Vite proxy、prod 同源直连。
import type { AgentHistoryList, DetectedVariable, ActionResult, BatchRowProgress, BatchStepProgress, TaskEvent, LiveTaskItem } from "./types";

const BASE = "/history";

// JSON 守卫：响应 content-type 非 JSON（典型 = SPA fallback 的 index.html——路径未被
// Vite 代理 / 后端版本过旧没有该路由）→ 报可诊断的错误，而不是把
// "Unexpected token '<', "<!DOCTYPE"..." 这种原始 SyntaxError 冒给用户。
async function readJson(r: Response): Promise<any> {
	const ct = r.headers.get("content-type") ?? "";
	if (!ct.includes("application/json")) {
		throw new Error(
			`后端返回了非 JSON 响应（content-type: ${ct || "未知"}）——` +
			"该路径未被代理（检查 vite.config 的 proxy 白名单）或后端版本过旧（重启 tw-web）");
	}
	return await r.json();
}

export async function listFiles(): Promise<string[]> {
	const r = await fetch(`${BASE}/list`);
	return (await readJson(r)).files;
}

export async function loadHistory(name: string): Promise<AgentHistoryList> {
	const r = await fetch(`${BASE}/load?name=${encodeURIComponent(name)}`);
	if (!r.ok) throw new Error((await readJson(r)).error || "load failed");
	return (await readJson(r)).history;
}

export async function saveHistory(name: string, history: AgentHistoryList): Promise<void> {
	const r = await fetch(`${BASE}/save?name=${encodeURIComponent(name)}`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(history),
	});
	if (!r.ok) throw new Error((await readJson(r)).error || "save failed");
}

export async function detectVariables(name: string): Promise<Record<string, DetectedVariable>> {
	const r = await fetch(`${BASE}/detect?name=${encodeURIComponent(name)}`);
	if (!r.ok) throw new Error((await readJson(r)).error || "detect failed");
	return (await readJson(r)).variables;
}

export async function rerun(name: string, variables: Record<string, string>): Promise<ActionResult[]> {
	const r = await fetch(`${BASE}/rerun?name=${encodeURIComponent(name)}`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ variables }),
	});
	if (!r.ok) throw new Error((await readJson(r)).error || "rerun failed");
	return (await readJson(r)).results;
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
	if (!r.ok) throw new Error((await readJson(r)).error || "batch start failed");
	return await readJson(r);
}

export async function cancelBatch(taskId: string): Promise<void> {
	const r = await fetch(`${BASE}/batch/cancel?task_id=${encodeURIComponent(taskId)}`, {
		method: "POST",
	});
	if (!r.ok) throw new Error((await readJson(r)).error || "cancel failed");
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
	model?: string,
): Promise<{ task_id: string }> {
	const r = await fetch(`${TASK}/start`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({
			task,
			file_paths: filePaths,
			record,
			viewport_mode: viewportMode,
			model: model || undefined, // T2 I5：空 = 跟随设置默认，不发该字段
		}),
	});
	if (!r.ok) throw new Error((await readJson(r)).error || "start failed");
	return await readJson(r);
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
	if (!r.ok) throw new Error((await readJson(r)).error || `${action} failed`);
}

// T2 H（M2）：侧栏「进行中」zone——活跃/最近 live task（只读，30s 轮询）
export async function listTasks(): Promise<LiveTaskItem[]> {
	const r = await fetch(`${TASK}/list`);
	if (!r.ok) throw new Error((await readJson(r)).error || "list tasks failed");
	return (await readJson(r)).tasks;
}

// T2 I6（M5）：任务历史——与 TUI 共享 ~/.treewalker/history.json，两端互通
export async function getTaskHistory(): Promise<string[]> {
	const r = await fetch(`${TASK}/history`);
	if (!r.ok) throw new Error((await readJson(r)).error || "get history failed");
	return (await readJson(r)).tasks;
}

export async function pushTaskHistory(task: string): Promise<void> {
	const r = await fetch(`${TASK}/history`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ task }),
	});
	if (!r.ok) throw new Error((await readJson(r)).error || "push history failed");
}

// ── Skills 技能面（P6 后续 B）───────────────────────────────────────────────

const SKILLS = "/skills";

// 技能三文件（与后端 _SKILL_FILE_WHITELIST 一致）
export const SKILL_FILES = ["_sop.md", "selectors.md", "quirks.md"] as const;
export type SkillFile = (typeof SKILL_FILES)[number];

export async function listSkills(): Promise<string[]> {
	const r = await fetch(`${SKILLS}/list`);
	return (await readJson(r)).hosts;
}

export async function getSkill(host: string): Promise<Record<SkillFile, string>> {
	const r = await fetch(`${SKILLS}/get?host=${encodeURIComponent(host)}`);
	if (!r.ok) throw new Error((await readJson(r)).error || "get skill failed");
	return (await readJson(r)).files;
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
	if (!r.ok) throw new Error((await readJson(r)).error || "put skill failed");
}

// ── Settings 设置面（T2 C）──────────────────────────────────────────────────

// 镜像后端 SettingField 注册表条目（GET /settings/get 返回）
export interface SettingFieldDTO {
	key: string;
	env: string;
	section: string;
	type: "str" | "int" | "float" | "bool" | "enum";
	choices: string[];
	default: string;
	sensitive: boolean; // 敏感字段（placeholder 提示，不动不提交）
	value: string; // 敏感字段为掩码串（****+尾4）
	masked: boolean;
}

export async function getSettings(): Promise<{ fields: SettingFieldDTO[]; applies: string }> {
	const r = await fetch("/settings/get");
	if (!r.ok) throw new Error((await readJson(r)).error || "get settings failed");
	return await readJson(r);
}

export async function setSettings(values: Record<string, string>): Promise<void> {
	const r = await fetch("/settings/set", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(values),
	});
	if (!r.ok) throw new Error((await readJson(r)).error || "set settings failed");
}
