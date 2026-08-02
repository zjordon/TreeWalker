// 与后端 AgentHistoryList.model_dump(mode="json") 对齐的类型。

export interface ActionResult {
	is_done?: boolean;
	success?: boolean | null;
	error?: string | null;
	extracted_content?: string | null;
}

export interface InteractedElement {
	node_name?: string;
	attributes?: Record<string, string>;
	ax_name?: string;
	[k: string]: unknown;
}

export interface AgentAction {
	name: string;
	params: Record<string, unknown>;
}

export interface AgentHistory {
	step_number: number;
	model_output: { actions: AgentAction[]; [k: string]: unknown };
	result: ActionResult[];
	state_summary?: Record<string, unknown> | null;
	interacted_element?: (InteractedElement | null)[] | null;
	metadata?: unknown;
	screenshot_path?: string | null;
}

export interface ManualVariableBinding {
	name: string;
	step_number: number;
	action_index: number;
	field: string;
	original_value: string;
}

export interface AgentHistoryList {
	history: AgentHistory[];
	action_registry_version?: string | null;
	manual_variables?: ManualVariableBinding[];
}

export interface DetectedVariable {
	name: string;
	original_value: string;
	type?: string;
	format?: string | null;
}

// 编辑器单一 state（useReducer）
export interface EditorState {
	history: AgentHistoryList | null;
	loadedName: string | null;
	dirty: boolean;
	selected: { stepIdx: number; actionIdx: number } | null;
	variables: Record<string, DetectedVariable>;
	runResult: ActionResult[] | null;
	status: string;
}
