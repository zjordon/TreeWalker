import { describe, it, expect } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import DetailView from "../components/DetailView";
import { initialState } from "../reducer";
import type { EditorState, AgentHistoryList } from "../types";

const hist: AgentHistoryList = {
	history: [
		{
			step_number: 1,
			model_output: { actions: [{ name: "input_text", params: { text: "cat" } }] },
			result: [{ is_done: false, success: null }],
			state_summary: { url: "https://example.com", duration_seconds: 1.2 },
		},
		{
			step_number: 2,
			model_output: { actions: [{ name: "click", params: { index: 3 } }] },
			result: [{ is_done: true, success: true, extracted_content: "done" }],
		},
	],
	action_registry_version: "v1",
	manual_variables: [{ name: "q", step_number: 1, action_index: 0, field: "text", original_value: "cat" }],
};

function stateWith(over: Partial<EditorState>): EditorState {
	return { ...initialState, ...over };
}

function findStepButton(container: HTMLElement, needle: string): HTMLButtonElement {
	const btn = Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.includes(needle));
	if (!btn) throw new Error(`step button "${needle}" not found`);
	return btn as HTMLButtonElement;
}

describe("DetailView (P6 M5)", () => {
	it("无 history 时提示选择流程", () => {
		const { container } = render(<DetailView state={stateWith({ history: null })} />);
		expect(container.textContent).toContain("选择一个流程");
	});

	it("显示 Flow 元信息 + 步骤列表", () => {
		const { container } = render(<DetailView state={stateWith({ history: hist, loadedName: "a.json" })} />);
		expect(container.textContent).toContain("a.json");
		expect(container.textContent).toContain("2 步");
		expect(container.textContent).toContain("✓ 成功");
		expect(container.textContent).toContain("注册表 v1");
		expect(container.textContent).toContain("1 个手工变量");
		expect(container.textContent).toContain("step 1");
		expect(container.textContent).toContain("step 2");
	});

	it("默认选中 step 1；点 step 2 切到其详情", () => {
		const { container } = render(<DetailView state={stateWith({ history: hist })} />);
		// 默认 sel=0 → step 1 的动作 input_text 可见
		expect(container.textContent).toContain("input_text");
		expect(container.textContent).toContain("https://example.com"); // state_summary
		// 切到 step 2
		fireEvent.click(findStepButton(container, "step 2"));
		expect(container.textContent).toContain("click");
		expect(container.textContent).toContain("done"); // extracted_content
	});
});
