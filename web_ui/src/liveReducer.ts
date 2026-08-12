// P6 live agent 任务的单一 state（useReducer）。事件来源：后端 /task/events SSE。
import type { LiveState, TaskEvent } from "./types";

export const initialLiveState: LiveState = {
	phase: "idle",
	taskId: null,
	task: "",
	filePaths: "",
	record: false,
	events: [],
	logs: [],
	screenshot: null,
	status: "",
	result: null,
};

export type LiveAction =
	| { type: "FIELD"; key: "task" | "filePaths" | "record"; value: string | boolean }
	| { type: "STARTING"; taskId: string }
	| { type: "PAUSED" }
	| { type: "RESUMED" }
	| { type: "EVENT"; event: TaskEvent }
	| { type: "STATUS"; status: string }
	| { type: "RESET" };

export function liveReducer(state: LiveState, action: LiveAction): LiveState {
	switch (action.type) {
		case "FIELD":
			return { ...state, [action.key]: action.value };
		case "STARTING":
			return {
				...state,
				phase: "running",
				taskId: action.taskId,
				events: [],
				logs: [],
				screenshot: null,
				result: null,
				status: "运行中…",
			};
		case "PAUSED":
			return { ...state, phase: "paused", status: "已暂停" };
		case "RESUMED":
			return { ...state, phase: "running", status: "运行中…" };
		case "EVENT": {
			const e = action.event;
			if (e.type === "done") {
				const success = e.success as boolean | undefined;
				const error = e.error as string | undefined;
				const saved = e.saved as string | undefined;
				return {
					...state,
					phase: error ? "error" : "done",
					result: { success, error, saved },
					status: error ? `失败：${error}` : saved ? `完成，已存 ${saved}` : "完成",
				};
			}
			if (e.type === "log") {
				return { ...state, logs: [...state.logs, e] };
			}
			if (e.type === "screenshot") {
				const data = e.data as string | undefined;
				return { ...state, screenshot: data ?? state.screenshot };
			}
			return { ...state, events: [...state.events, e] };
		}
		case "STATUS":
			return { ...state, status: action.status };
		case "RESET":
			// 回到 idle 但保留任务输入/录制偏好（方便连跑）
			return { ...initialLiveState, task: state.task, filePaths: state.filePaths, record: state.record };
		default:
			return state;
	}
}
