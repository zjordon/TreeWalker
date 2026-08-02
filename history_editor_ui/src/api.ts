// 后端 /history/* 端点封装。fetch 用相对路径——dev 经 Vite proxy、prod 同源直连。
import type { AgentHistoryList, DetectedVariable, ActionResult } from "./types";

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
