import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import BatchRunPanel from "../components/BatchRunPanel";
import { initialState } from "../reducer";
import type { EditorState, BatchState } from "../types";

function stateWith(over: Partial<EditorState>): EditorState {
	return { ...initialState, ...over };
}

function findButton(container: HTMLElement, text: string): HTMLButtonElement {
	const btn = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === text);
	if (!btn) throw new Error(`button "${text}" not found`);
	return btn as HTMLButtonElement;
}

describe("BatchRunPanel (#155)", () => {
	it("开始按钮在无 loadedName 时 disabled", () => {
		const onStart = vi.fn() as unknown as (file: File) => void;
		const { container } = render(
			<BatchRunPanel
				state={stateWith({ loadedName: null })}
				onStart={onStart}
				onCancel={() => {}}
				onReset={() => {}}
			/>,
		);
		expect(findButton(container, "开始批量").disabled).toBe(true);
	});

	it("有 loadedName + 选 file 后开始按钮 enabled，点击触发 onStart(file)", () => {
		const onStart = vi.fn() as unknown as (file: File) => void;
		const { container } = render(
			<BatchRunPanel
				state={stateWith({ loadedName: "a.json" })}
				onStart={onStart}
				onCancel={() => {}}
				onReset={() => {}}
			/>,
		);
		const input = container.querySelector('input[type="file"]') as HTMLInputElement;
		const file = new File(["email\na@b.com"], "d.csv", { type: "text/csv" });
		fireEvent.change(input, { target: { files: [file] } });
		const startBtn = findButton(container, "开始批量");
		expect(startBtn.disabled).toBe(false);
		fireEvent.click(startBtn);
		expect(onStart).toHaveBeenCalledWith(file);
	});

	it("running 时显示中止按钮", () => {
		const runningBatch: BatchState = {
			phase: "running", taskId: "t1", totalRows: 2, rows: [], currentStep: null, error: null,
		};
		const { container } = render(
			<BatchRunPanel
				state={stateWith({ loadedName: "a.json", batch: runningBatch })}
				onStart={() => {}}
				onCancel={() => {}}
				onReset={() => {}}
			/>,
		);
		expect(findButton(container, "中止")).toBeTruthy();
	});

	it("done 后显示重置按钮 + 完成汇总", () => {
		const doneBatch: BatchState = {
			phase: "done", taskId: "t1", totalRows: 2, currentStep: null,
			rows: [
				{ row_index: 0, variables: {}, success: true, n_steps: 3, extracted_content: null, error: null },
				{ row_index: 1, variables: {}, success: false, n_steps: 1, extracted_content: null, error: "x" },
			],
			error: null,
		};
		const { container } = render(
			<BatchRunPanel
				state={stateWith({ loadedName: "a.json", batch: doneBatch })}
				onStart={() => {}}
				onCancel={() => {}}
				onReset={() => {}}
			/>,
		);
		expect(findButton(container, "重置")).toBeTruthy();
		expect(container.textContent).toContain("1/2 行成功");
	});
});
