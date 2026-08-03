import { describe, it, expect, vi } from "vitest";
import type { Dispatch } from "react";
import { render, fireEvent } from "@testing-library/react";
import ActionList from "../components/ActionList";
import { initialState } from "../reducer";
import type { EditorAction } from "../App";
import type { AgentHistoryList, EditorState } from "../types";

// 含 3 个 action 的单步——复现 #153：step 3（input_text → click → input_text）。
// 旧实现把整步折叠成一行、点击固定 onSelect(stepIdx, 0)，无法选中后两个 action。
function makeHistory(): AgentHistoryList {
	return {
		history: [
			{
				step_number: 1,
				model_output: {
					actions: [
						{ name: "input_text", params: { text: "标题", index: 1 } },
						{ name: "click", params: { index: 2 } },
						{ name: "input_text", params: { text: "描述", index: 3 } },
					],
				},
				result: [],
				interacted_element: [
					{ node_name: "INPUT" },
					{ node_name: "DIV" },
					{ node_name: "DIV" },
				],
			},
		],
	};
}

function stateWith(
	history: AgentHistoryList,
	selected: EditorState["selected"] = null,
): EditorState {
	return { ...initialState, history, selected };
}

describe("ActionList 多 action 选中（#153）", () => {
	it("点击第 3 个 action chip → SELECT actionIdx:2（不再固定 0）", () => {
		const dispatch = vi.fn() as unknown as Dispatch<EditorAction>;
		const { container } = render(
			<ActionList state={stateWith(makeHistory())} dispatch={dispatch} />,
		);
		const chips = container.querySelectorAll(".action-chip");
		expect(chips).toHaveLength(3);
		fireEvent.click(chips[2]);
		expect(dispatch).toHaveBeenCalledWith({
			type: "SELECT",
			selected: { stepIdx: 0, actionIdx: 2 },
		});
	});

	it("点击第 2 个 action chip → SELECT actionIdx:1", () => {
		const dispatch = vi.fn() as unknown as Dispatch<EditorAction>;
		const { container } = render(
			<ActionList state={stateWith(makeHistory())} dispatch={dispatch} />,
		);
		const chips = container.querySelectorAll(".action-chip");
		fireEvent.click(chips[1]);
		expect(dispatch).toHaveBeenCalledWith({
			type: "SELECT",
			selected: { stepIdx: 0, actionIdx: 1 },
		});
	});

	it("selected.actionIdx 对应的 chip 带 .selected 高亮，其余不高亮", () => {
		const dispatch = vi.fn() as unknown as Dispatch<EditorAction>;
		const { container } = render(
			<ActionList
				state={stateWith(makeHistory(), { stepIdx: 0, actionIdx: 1 })}
				dispatch={dispatch}
			/>,
		);
		const chips = container.querySelectorAll(".action-chip");
		expect(chips[0].classList.contains("selected")).toBe(false);
		expect(chips[1].classList.contains("selected")).toBe(true);
		expect(chips[2].classList.contains("selected")).toBe(false);
	});
});
