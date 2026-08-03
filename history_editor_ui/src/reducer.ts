import type {
	EditorState,
	AgentHistoryList,
	DetectedVariable,
	ActionResult,
	BatchRowProgress,
	BatchStepProgress,
} from "./types";

export const initialState: EditorState = {
	history: null,
	loadedName: null,
	dirty: false,
	selected: null,
	variables: {},
	runResult: null,
	batch: { phase: "idle", taskId: null, totalRows: 0, rows: [], currentStep: null, error: null },
	status: "",
};

export type EditorAction =
	| { type: "LOAD"; history: AgentHistoryList; name: string }
	| { type: "SELECT"; selected: { stepIdx: number; actionIdx: number } | null }
	| { type: "UPDATE_PARAM"; stepIdx: number; actionIdx: number; field: string; value: string }
	| { type: "MOVE_STEP"; from: number; to: number }
	| { type: "DELETE_STEP"; stepIdx: number }
	| {
			type: "ADD_MANUAL_VAR";
			name: string;
			stepIdx: number;
			actionIdx: number;
			field: string;
			original: string;
	  }
	| { type: "REMOVE_VAR"; name: string }
	| { type: "SET_DIRTY"; dirty: boolean }
	| { type: "DETECT_DONE"; variables: Record<string, DetectedVariable> }
	| { type: "RUN_DONE"; results: ActionResult[] | null }
	| { type: "BATCH_START" }
	| { type: "BATCH_STARTED"; taskId: string; totalRows: number }
	| { type: "BATCH_ROW"; row: BatchRowProgress }
	| { type: "BATCH_STEP"; step: BatchStepProgress }
	| {
			type: "BATCH_DONE";
			total: number;
			succeeded: number;
			failed: number;
			error?: string;
	  }
	| { type: "BATCH_CANCEL" }
	| { type: "BATCH_ERROR"; error: string }
	| { type: "BATCH_RESET" }
	| { type: "STATUS"; status: string };

export function reducer(state: EditorState, action: EditorAction): EditorState {
	switch (action.type) {
		case "LOAD":
			return {
				...state,
				history: action.history,
				loadedName: action.name,
				dirty: false,
				selected: null,
				runResult: null,
				batch: { phase: "idle", taskId: null, totalRows: 0, rows: [], currentStep: null, error: null },
			status: `已加载 ${action.name}（${action.history.history.length} 步）`,
			};
		case "SELECT":
			return { ...state, selected: action.selected };
		case "UPDATE_PARAM": {
			if (!state.history) return state;
			const history = structuredClone(state.history) as AgentHistoryList;
			(history.history[action.stepIdx].model_output.actions[action.actionIdx].params[
				action.field
			] as string) = action.value;
			return { ...state, history, dirty: true };
		}
		case "MOVE_STEP": {
			// 整步搬运：actions[] + interacted_element[] 同步移动（天然守等长不变量）
			if (!state.history) return state;
			const history = structuredClone(state.history) as AgentHistoryList;
			const [m] = history.history.splice(action.from, 1);
			history.history.splice(action.to, 0, m);
			return { ...state, history, dirty: true, selected: null };
		}
		case "DELETE_STEP": {
			if (!state.history) return state;
			const history = structuredClone(state.history) as AgentHistoryList;
			const sn = history.history[action.stepIdx].step_number;
			history.history.splice(action.stepIdx, 1);
			history.manual_variables = (history.manual_variables || []).filter(
				(v) => v.step_number !== sn
			);
			return { ...state, history, dirty: true, selected: null };
		}
		case "ADD_MANUAL_VAR": {
			if (!state.history) return state;
			const history = structuredClone(state.history) as AgentHistoryList;
			if (!history.manual_variables) history.manual_variables = [];
			history.manual_variables = history.manual_variables.filter((v) => v.name !== action.name);
			history.manual_variables.push({
				name: action.name,
				step_number: history.history[action.stepIdx].step_number,
				action_index: action.actionIdx,
				field: action.field,
				original_value: action.original,
			});
			return { ...state, history, dirty: true };
		}
		case "REMOVE_VAR": {
			if (!state.history) return state;
			const history = structuredClone(state.history) as AgentHistoryList;
			history.manual_variables = (history.manual_variables || []).filter(
				(v) => v.name !== action.name
			);
			const variables = { ...state.variables };
			delete variables[action.name]; // 前端即时移除；下次 detect 恢复 detect 部分
			return { ...state, history, variables, dirty: true };
		}
		case "SET_DIRTY":
			return { ...state, dirty: action.dirty };
		case "DETECT_DONE":
			return {
				...state,
				variables: action.variables,
				status: `检测到 ${Object.keys(action.variables).length} 个变量`,
			};
		case "RUN_DONE":
			return {
				...state,
				runResult: action.results,
				status: action.results ? "试跑完成" : "",
			};
		case "BATCH_START":
		return { ...state, batch: { ...state.batch, phase: "starting", rows: [], error: null } };
	case "BATCH_STARTED":
		return {
			...state,
			batch: { ...state.batch, phase: "running", taskId: action.taskId, totalRows: action.totalRows },
		};
	case "BATCH_STEP":
		return { ...state, batch: { ...state.batch, currentStep: action.step } };
	case "BATCH_ROW":
		return { ...state, batch: { ...state.batch, rows: [...state.batch.rows, action.row] } };
	case "BATCH_DONE":
		return {
			...state,
			batch: {
				...state.batch,
				phase: action.error ? "error" : "done",
				error: action.error ?? null,
			},
		};
	case "BATCH_CANCEL":
		return { ...state, batch: { ...state.batch, phase: "cancelled" } };
	case "BATCH_ERROR":
		return { ...state, batch: { ...state.batch, phase: "error", error: action.error } };
	case "BATCH_RESET":
		return {
			...state,
			batch: { phase: "idle", taskId: null, totalRows: 0, rows: [], currentStep: null, error: null },
		};
	case "STATUS":
			return { ...state, status: action.status };
		default:
			return state;
	}
}
