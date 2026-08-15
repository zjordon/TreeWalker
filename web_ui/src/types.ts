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

// CSV 批量重放单行进度（镜像后端 BatchRowResult，issue #155）
export interface BatchRowProgress {
	row_index: number;
	variables: Record<string, string>;
	success: boolean;
	n_steps: number;
	extracted_content: string | null;
	error: string | null;
}

export type BatchPhase = "idle" | "starting" | "running" | "done" | "cancelled" | "error";

export interface BatchStepProgress {
	step_index: number;
	total: number;
	success: boolean;
	extracted_content: string | null;
	error: string | null;
}

export interface BatchState {
	phase: BatchPhase;
	taskId: string | null;
	totalRows: number;
	rows: BatchRowProgress[];
	currentStep: BatchStepProgress | null;
	error: string | null;
}

// 编辑器单一 state（useReducer）
export interface EditorState {
	history: AgentHistoryList | null;
	loadedName: string | null;
	dirty: boolean;
	selected: { stepIdx: number; actionIdx: number } | null;
	variables: Record<string, DetectedVariable>;
	runResult: ActionResult[] | null;
	batch: BatchState;
	status: string;
}

// P6 live agent 任务（后端 /task/* 端点，M1/M2）
export interface TaskEvent {
	type: string; // step_start | model_call | model_result | tool_call | tool_result | step_end | anomaly | session_end | skill_active | log | screenshot | done
	step?: number;
	[key: string]: unknown;
}

export type LivePhase = "idle" | "running" | "paused" | "done" | "error";

// P6 后续 I1：活动 skill（每步 SkillActiveEvent）
export interface SkillActive {
	host: string | null;
	skillLoaded: boolean;
	charCount: number;
}

// P6 后续 I3：元素高亮（当前步各 tool_call 的目标元素几何，归一化百分比）
export interface Highlight {
	index: number; // action_index（角标）
	bbox: { left: number; top: number; width: number; height: number }; // ∈ [0,1]
}

// T2 H（M2）：侧栏「进行中」zone 条目（镜像后端 GET /task/list）
export interface LiveTaskItem {
	task_id: string;
	task: string;
	phase: "running" | "paused" | "done";
	success?: boolean | null;
	saved?: string | null;
	viewport_mode?: string;
}

export interface LiveState {
	phase: LivePhase;
	taskId: string | null;
	task: string;
	filePaths: string;
	record: boolean;
	events: TaskEvent[];      // EventBus 事件 → 步骤时间线
	logs: TaskEvent[];         // type:"log"
	screenshot: string | null; // 最新帧 data URL
	activeSkill: SkillActive | null; // I1：当前 host 的活动 skill
	highlights: Highlight[]; // I3：当前步 tool_call 元素高亮（step_start 清空）
	tokens: { in: number; out: number }; // I2：累计 input/output tokens（ModelResultEvent）
	elapsedMs: number; // I2：累计运行耗时（StepEndEvent.duration_seconds 累加）
	selectedEvent: number | null; // T2 G（M7）：时间线选中事件（events 数组索引；右 Context 面板数据源）
	status: string;
	result: { success?: boolean; error?: string; saved?: string } | null;
}
