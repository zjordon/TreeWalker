import type {
	EditorState,
	AgentHistoryList,
	DetectedVariable,
	ActionResult,
} from "./types";

export const initialState: EditorState = {
	history: null,
	loadedName: null,
	dirty: false,
	selected: null,
	variables: {},
	runResult: null,
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
		case "STATUS":
			return { ...state, status: action.status };
		default:
			return state;
	}
}
