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
	selectedEvent: null,
	status: "",
	result: null,
};

export type LiveAction =
	| { type: "FIELD"; key: "task" | "filePaths" | "record"; value: string | boolean }
	| { type: "STARTING"; taskId: string }
	| { type: "ADOPT"; taskId: string } // T2 H（M2）：从「进行中」zone 接管非当前 taskId 的任务
	| { type: "SELECT_EVENT"; index: number | null } // T2 G（M7）：时间线选中事件（右 Context 面板）
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
				selectedEvent: null, // G：新任务清选中（旧索引指向被清空的 events）
				result: null,
				status: "运行中…",
			};
		case "ADOPT":
			// T2 H（M2）：接管「进行中」zone 里非当前 taskId 的任务（多为刷新后恢复入口）。
			// 展示字段重置——已消费事件/截图不可恢复（后端 SSE 只补 final + 队列剩余，已知局限）；
			// taskId 换新 → RunView 的 SSE useEffect 重订续播。若任务已 done，SSE 立即补 done 事件。
			return {
				...initialLiveState,
				phase: "running",
				taskId: action.taskId,
				status: "已接入进行中的任务（此前步骤不可恢复）",
			};
		case "SELECT_EVENT":
			// G（M7）：时间线选中/取消（null）。events 只追加不删改，索引稳定。
			return { ...state, selectedEvent: action.index };
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
				// issue #165：除更新 chip 状态外也进时间线（ContextPanel 的 skill_active
				// 渲染分支此前不可达 = 死代码；每步一条，点击右栏看 host/字数/进技能面）。
				return {
					...state,
					activeSkill: {
						host: (e.host as string | null) ?? null,
						skillLoaded: Boolean(e.skill_loaded),
						charCount: Number(e.char_count ?? 0),
					},
					events: [...state.events, e],
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
