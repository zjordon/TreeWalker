import { describe, it, expect } from "vitest";
import { initialLiveState, liveReducer } from "../liveReducer";

describe("liveReducer (P6 M3)", () => {
	it("FIELD 更新 task / record", () => {
		const s1 = liveReducer(initialLiveState, { type: "FIELD", key: "task", value: "hi" });
		expect(s1.task).toBe("hi");
		const s2 = liveReducer(s1, { type: "FIELD", key: "record", value: true });
		expect(s2.record).toBe(true);
	});

	it("STARTING 进入 running 并清空旧事件", () => {
		const s = liveReducer(initialLiveState, { type: "STARTING", taskId: "t1" });
		expect(s.phase).toBe("running");
		expect(s.taskId).toBe("t1");
		expect(s.events).toEqual([]);
		expect(s.screenshot).toBeNull();
	});

	it("EVENT 分发：log→logs、screenshot→screenshot、EventBus→events", () => {
		let s = liveReducer(initialLiveState, { type: "STARTING", taskId: "t1" });
		s = liveReducer(s, { type: "EVENT", event: { type: "log", level: "INFO", msg: "x", logger: "t" } });
		s = liveReducer(s, { type: "EVENT", event: { type: "screenshot", step: 1, data: "data:x" } });
		s = liveReducer(s, { type: "EVENT", event: { type: "step_end", step: 1 } });
		expect(s.logs).toHaveLength(1);
		expect(s.screenshot).toBe("data:x");
		expect(s.events).toHaveLength(1);
	});

	it("EVENT done(success) → phase done + saved 入 result", () => {
		let s = liveReducer(initialLiveState, { type: "STARTING", taskId: "t1" });
		s = liveReducer(s, { type: "EVENT", event: { type: "done", success: true, saved: "20260812.json" } });
		expect(s.phase).toBe("done");
		expect(s.result?.saved).toBe("20260812.json");
	});

	it("EVENT done(error) → phase error", () => {
		let s = liveReducer(initialLiveState, { type: "STARTING", taskId: "t1" });
		s = liveReducer(s, { type: "EVENT", event: { type: "done", success: false, error: "boom" } });
		expect(s.phase).toBe("error");
		expect(s.status).toContain("boom");
	});

	it("PAUSED / RESUMED 切换 phase", () => {
		let s = liveReducer(initialLiveState, { type: "STARTING", taskId: "t1" });
		s = liveReducer(s, { type: "PAUSED" });
		expect(s.phase).toBe("paused");
		s = liveReducer(s, { type: "RESUMED" });
		expect(s.phase).toBe("running");
	});

	it("RESET 回到 idle 但保留输入", () => {
		let s = liveReducer(initialLiveState, { type: "FIELD", key: "task", value: "keep" });
		s = liveReducer(s, { type: "STARTING", taskId: "t1" });
		s = liveReducer(s, { type: "RESET" });
		expect(s.phase).toBe("idle");
		expect(s.taskId).toBeNull();
		expect(s.task).toBe("keep");
	});
});
