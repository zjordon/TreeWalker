import type { Dispatch } from "react";
import {
	DndContext,
	closestCenter,
	KeyboardSensor,
	PointerSensor,
	useSensor,
	useSensors,
	type DragEndEvent,
} from "@dnd-kit/core";
import {
	SortableContext,
	sortableKeyboardCoordinates,
	verticalListSortingStrategy,
	useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type { EditorState, AgentHistory, InteractedElement, AgentAction } from "../types";
import type { EditorAction } from "../App";

function elemDesc(el: InteractedElement | null | undefined): string {
	if (!el) return "无";
	const a = el.attributes || {};
	const label = a.placeholder || a["aria-label"] || a.id || a.name || el.ax_name;
	return label ? `${el.node_name || ""} "${label}"` : el.node_name || "无";
}

interface RowProps {
	step: AgentHistory;
	stepIdx: number;
	// 该步当前选中的 action 下标；null 表示此步未选中任何 action。
	selectedActionIdx: number | null;
	failed: boolean;
	onSelect: (stepIdx: number, actionIdx: number) => void;
}

function SortableStepRow({ step, stepIdx, selectedActionIdx, failed, onSelect }: RowProps) {
	const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
		id: `step-${step.step_number}`,
	});
	const style: React.CSSProperties = {
		transform: CSS.Transform.toString(transform),
		transition,
		opacity: isDragging ? 0.5 : 1,
	};
	const actions = step.model_output.actions || [];
	const interacted = step.interacted_element || [];
	return (
		<tr
			ref={setNodeRef}
			style={style}
			className={`row ${selectedActionIdx !== null ? "selected" : ""} ${failed ? "failed" : ""}`}
		>
			<td className="grip" {...attributes} {...listeners}>
				⠿
			</td>
			<td>{step.step_number}</td>
			<td className="action-cell">
				{actions.length === 0 ? (
					"-"
				) : (
					actions.map((a: AgentAction, i: number) => (
						<span
							key={i}
							className={`action-chip${selectedActionIdx === i ? " selected" : ""}`}
							onClick={() => onSelect(stepIdx, i)}
						>
							{i + 1}. {a.name}
						</span>
					))
				)}
			</td>
			<td>
				{actions.length === 0 ? (
					elemDesc(interacted[0])
				) : (
					actions.map((_: AgentAction, i: number) => (
						<span key={i} className="action-desc">
							{elemDesc(interacted[i])}
						</span>
					))
				)}
			</td>
		</tr>
	);
}

interface ListProps {
	state: EditorState;
	dispatch: Dispatch<EditorAction>;
}

export default function ActionList({ state, dispatch }: ListProps) {
	const sensors = useSensors(
		useSensor(PointerSensor),
		useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
	);

	if (!state.history) {
		return (
			<table>
				<thead>
					<tr>
						<th></th>
						<th>步</th>
						<th>动作</th>
						<th>目标元素</th>
					</tr>
				</thead>
				<tbody>
					<tr>
						<td colSpan={4} className="muted">
							未加载
						</td>
					</tr>
				</tbody>
			</table>
		);
	}

	const steps = state.history.history;
	const failedIdx = new Set<number>();
	if (state.runResult) {
		state.runResult.forEach((r, i) => {
			if (r.error) failedIdx.add(i);
		});
	}

	return (
		<DndContext
			sensors={sensors}
			collisionDetection={closestCenter}
			onDragEnd={(e: DragEndEvent) => {
				const { active, over } = e;
				if (over && active.id !== over.id) {
					const from = steps.findIndex((s) => `step-${s.step_number}` === active.id);
					const to = steps.findIndex((s) => `step-${s.step_number}` === over.id);
					if (from >= 0 && to >= 0) dispatch({ type: "MOVE_STEP", from, to });
				}
			}}
		>
			<SortableContext
				items={steps.map((s) => `step-${s.step_number}`)}
				strategy={verticalListSortingStrategy}
			>
				<table>
					<thead>
						<tr>
							<th></th>
							<th>步</th>
							<th>动作</th>
							<th>目标元素</th>
						</tr>
					</thead>
					<tbody>
						{steps.map((step, stepIdx) => (
							<SortableStepRow
								key={step.step_number}
								step={step}
								stepIdx={stepIdx}
								selectedActionIdx={
									state.selected && state.selected.stepIdx === stepIdx
										? state.selected.actionIdx
										: null
								}
								failed={failedIdx.has(stepIdx)}
								onSelect={(si, ai) =>
									dispatch({ type: "SELECT", selected: { stepIdx: si, actionIdx: ai } })
								}
							/>
						))}
					</tbody>
				</table>
			</SortableContext>
		</DndContext>
	);
}
