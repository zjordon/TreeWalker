import { describe, it, expect } from "vitest";
import { reducer, initialState } from "../reducer";
import type { AgentHistoryList, BatchRowProgress } from "../types";

function makeHistory(): AgentHistoryList {
	return {
		history: [
			{
				step_number: 1,
				model_output: { actions: [
					{ name: "input_text", params: { text: "alice@example.com", index: 0 } },
				] },
				result: [],
				interacted_element: [{ node_name: "INPUT", attributes: { type: "email" } }],
			},
			{
				step_number: 2,
				model_output: { actions: [
					{ name: "click", params: { index: 1 } },
					{ name: "input_text", params: { text: "iPhone", index: 2 } },
				] },
				result: [],
				interacted_element: [
					{ node_name: "BUTTON" },
					{ node_name: "INPUT", attributes: { name: "q" } },
				],
			},
		],
	};
}

describe("reducer", () => {
	it("LOAD 设置 history + 清状态", () => {
		const h = makeHistory();
		const s = reducer(initialState, { type: "LOAD", history: h, name: "a.json" });
		expect(s.history).toBe(h);
		expect(s.loadedName).toBe("a.json");
		expect(s.dirty).toBe(false);
		expect(s.selected).toBeNull();
	});

	it("UPDATE_PARAM 改 text + 标 dirty（不改原对象）", () => {
		const s0 = reducer(initialState, { type: "LOAD", history: makeHistory(), name: "a.json" });
		const s1 = reducer(s0, {
			type: "UPDATE_PARAM", stepIdx: 0, actionIdx: 0, field: "text", value: "bob@mail.com",
		});
		expect(s1.history!.history[0].model_output.actions[0].params.text).toBe("bob@mail.com");
		expect(s1.dirty).toBe(true);
		expect(s0.history!.history[0].model_output.actions[0].params.text).toBe("alice@example.com");
	});

	it("MOVE_STEP 整步搬运 + 标 dirty", () => {
		const s0 = reducer(initialState, { type: "LOAD", history: makeHistory(), name: "a.json" });
		const s1 = reducer(s0, { type: "MOVE_STEP", from: 0, to: 1 });
		expect(s1.history!.history[0].step_number).toBe(2);
		expect(s1.history!.history[1].step_number).toBe(1);
		expect(s1.dirty).toBe(true);
	});

	it("DELETE_STEP 删整步 + 清该步 manual 标注", () => {
		let s = reducer(initialState, { type: "LOAD", history: makeHistory(), name: "a.json" });
		s = reducer(s, {
			type: "ADD_MANUAL_VAR", name: "q", stepIdx: 1, actionIdx: 1, field: "text", original: "iPhone",
		});
		s = reducer(s, { type: "DELETE_STEP", stepIdx: 1 });
		expect(s.history!.history.length).toBe(1);
		expect(s.history!.history[0].step_number).toBe(1);
		expect(s.history!.manual_variables).toEqual([]);
	});

	it("ADD_MANUAL_VAR 同名替换", () => {
		let s = reducer(initialState, { type: "LOAD", history: makeHistory(), name: "a.json" });
		s = reducer(s, { type: "ADD_MANUAL_VAR", name: "x", stepIdx: 0, actionIdx: 0, field: "text", original: "a" });
		s = reducer(s, { type: "ADD_MANUAL_VAR", name: "x", stepIdx: 0, actionIdx: 0, field: "text", original: "b" });
		expect(s.history!.manual_variables!.length).toBe(1);
		expect(s.history!.manual_variables![0].original_value).toBe("b");
	});

	it("REMOVE_VAR 删 variables 缓存", () => {
		let s = reducer(initialState, { type: "LOAD", history: makeHistory(), name: "a.json" });
		s = reducer(s, {
			type: "DETECT_DONE",
			variables: { email: { name: "email", original_value: "a@b.com" } },
		});
		s = reducer(s, { type: "REMOVE_VAR", name: "email" });
		expect(s.variables.email).toBeUndefined();
	});

	it("无 history 时 mutation 不崩（返回原 state）", () => {
		expect(
			reducer(initialState, { type: "UPDATE_PARAM", stepIdx: 0, actionIdx: 0, field: "text", value: "x" })
		).toBe(initialState);
		expect(reducer(initialState, { type: "MOVE_STEP", from: 0, to: 1 })).toBe(initialState);
	});
});

describe("reducer batch (#155)", () => {
	const mockRow = (i: number, success = true): BatchRowProgress => ({
		row_index: i,
		variables: { email: `r${i}@x.com` },
		success,
		n_steps: 3,
		extracted_content: null,
		error: null,
	});

	it("BATCH_START 清 rows + phase=starting", () => {
		const s0 = {
			...initialState,
			batch: { ...initialState.batch, rows: [mockRow(0)], phase: "done" as const },
		};
		const s1 = reducer(s0, { type: "BATCH_START" });
		expect(s1.batch.phase).toBe("starting");
		expect(s1.batch.rows).toEqual([]);
	});

	it("BATCH_STARTED 设 taskId + totalRows + running", () => {
		const s = reducer(initialState, { type: "BATCH_STARTED", taskId: "t1", totalRows: 5 });
		expect(s.batch.phase).toBe("running");
		expect(s.batch.taskId).toBe("t1");
		expect(s.batch.totalRows).toBe(5);
	});

	it("BATCH_ROW 追加 rows", () => {
		let s = reducer(initialState, { type: "BATCH_STARTED", taskId: "t1", totalRows: 2 });
		s = reducer(s, { type: "BATCH_ROW", row: mockRow(0) });
		s = reducer(s, { type: "BATCH_ROW", row: mockRow(1) });
		expect(s.batch.rows).toHaveLength(2);
		expect(s.batch.rows[1].row_index).toBe(1);
	});

	it("BATCH_STEP 设 currentStep", () => {
		const step = { step_index: 1, total: 3, success: true, extracted_content: "ok", error: null };
		const s = reducer(initialState, { type: "BATCH_STEP", step });
		expect(s.batch.currentStep).toEqual(step);
	});

	it("BATCH_DONE → done phase", () => {
		const s = reducer(initialState, { type: "BATCH_DONE", total: 2, succeeded: 1, failed: 1 });
		expect(s.batch.phase).toBe("done");
		expect(s.batch.error).toBeNull();
	});

	it("BATCH_DONE 带 error → error phase", () => {
		const s = reducer(initialState, {
			type: "BATCH_DONE", total: 0, succeeded: 0, failed: 0, error: "boom",
		});
		expect(s.batch.phase).toBe("error");
		expect(s.batch.error).toBe("boom");
	});

	it("BATCH_CANCEL → cancelled", () => {
		const s = reducer(initialState, { type: "BATCH_CANCEL" });
		expect(s.batch.phase).toBe("cancelled");
	});

	it("BATCH_RESET → idle 清空", () => {
		let s = reducer(initialState, { type: "BATCH_STARTED", taskId: "t1", totalRows: 3 });
		s = reducer(s, { type: "BATCH_RESET" });
		expect(s.batch.phase).toBe("idle");
		expect(s.batch.taskId).toBeNull();
		expect(s.batch.rows).toEqual([]);
	});

	it("LOAD 重置 batch 回 idle", () => {
		let s = reducer(initialState, { type: "BATCH_STARTED", taskId: "t1", totalRows: 3 });
		s = reducer(s, { type: "LOAD", history: makeHistory(), name: "x.json" });
		expect(s.batch.phase).toBe("idle");
		expect(s.batch.taskId).toBeNull();
	});
});
