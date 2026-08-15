// P6 live agent 任务的单一 state（useReducer）。事件来源：后端 /task/events SSE。
import type { LiveState, TaskEvent, Highlight } from "./types";

export const initialLiveState: LiveState = {
	phase: "idle",
	taskId: null,
	task: "",
	filePaths: "",
	record: false,
	events: [],
	logs: [],
	screenshot: null,
	activeSkill: null,
	highlights: [],
	tokens: { in: 0, out: 0 },
	elapsedMs: 0,
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
				activeSkill: null,
				highlights: [],
				tokens: { in: 0, out: 0 },
				elapsedMs: 0,
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
			if (e.type === "screenshot" || e.type === "screencast") {
				// 直播视口（A）：screencast 帧复用 screenshot 字段（同为 data URL，渲染路径一致）
				const data = e.data as string | undefined;
				return { ...state, screenshot: data ?? state.screenshot };
			}
			if (e.type === "skill_active") {
				return {
					...state,
					activeSkill: {
						host: (e.host as string | null) ?? null,
						skillLoaded: Boolean(e.skill_loaded),
						charCount: Number(e.char_count ?? 0),
					},
				};
			}
			if (e.type === "model_result") {
				// I2：累计 token（input_tokens/output_tokens，镜像后端 MetricsAggregator）
				return {
					...state,
					tokens: {
						in: state.tokens.in + Number(e.input_tokens ?? 0),
						out: state.tokens.out + Number(e.output_tokens ?? 0),
					},
					events: [...state.events, e],
				};
			}
			if (e.type === "step_end") {
				// I2：累计耗时（StepEndEvent.duration_seconds，秒→毫秒）
				return {
					...state,
					elapsedMs: state.elapsedMs + Number(e.duration_seconds ?? 0) * 1000,
					events: [...state.events, e],
				};
			}
			if (e.type === "step_start") {
				// I3：新步开始 → 清空上一步的高亮（保留至下一步 start，与该步截图同框）
				return { ...state, highlights: [], events: [...state.events, e] };
			}
			if (e.type === "tool_call") {
				// I3：收集本步 tool_call 的目标元素几何（归一化 bbox，无则跳过）
				const bbox = e.element_bbox as Highlight["bbox"] | undefined;
				if (bbox) {
					return {
						...state,
						highlights: [
							...state.highlights,
							{ index: Number(e.action_index ?? 0), bbox },
						],
						events: [...state.events, e],
					};
				}
				return { ...state, events: [...state.events, e] };
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
