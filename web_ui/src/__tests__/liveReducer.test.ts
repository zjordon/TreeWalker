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

	it("EVENT screencast → 更新 screenshot（直播视口 A，复用字段）", () => {
		let s = liveReducer(initialLiveState, { type: "STARTING", taskId: "t1" });
		s = liveReducer(s, { type: "EVENT", event: { type: "screencast", data: "data:frame1" } });
		expect(s.screenshot).toBe("data:frame1");
		s = liveReducer(s, { type: "EVENT", event: { type: "screencast", data: "data:frame2" } });
		expect(s.screenshot).toBe("data:frame2");
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

	it("EVENT skill_active → 设置 activeSkill（I1）", () => {
		let s = liveReducer(initialLiveState, { type: "STARTING", taskId: "t1" });
		expect(s.activeSkill).toBeNull();
		s = liveReducer(s, {
			type: "EVENT",
			event: { type: "skill_active", step: 1, host: "member.bilibili.com", skill_loaded: true, char_count: 120 },
		});
		expect(s.activeSkill).toEqual({ host: "member.bilibili.com", skillLoaded: true, charCount: 120 });
	});

	it("EVENT skill_active(无 host/未命中) → activeSkill 反映未命中", () => {
		let s = liveReducer(initialLiveState, { type: "STARTING", taskId: "t1" });
		s = liveReducer(s, {
			type: "EVENT",
			event: { type: "skill_active", step: 1, host: null, skill_loaded: false, char_count: 0 },
		});
		expect(s.activeSkill).toEqual({ host: null, skillLoaded: false, charCount: 0 });
	});

	it("EVENT model_result → 累计 token（I2）", () => {
		let s = liveReducer(initialLiveState, { type: "STARTING", taskId: "t1" });
		expect(s.tokens).toEqual({ in: 0, out: 0 });
		s = liveReducer(s, { type: "EVENT", event: { type: "model_result", input_tokens: 100, output_tokens: 20 } });
		s = liveReducer(s, { type: "EVENT", event: { type: "model_result", input_tokens: 50, output_tokens: 10 } });
		expect(s.tokens).toEqual({ in: 150, out: 30 });
	});

	it("EVENT step_end → 累计耗时 ms（I2）", () => {
		let s = liveReducer(initialLiveState, { type: "STARTING", taskId: "t1" });
		s = liveReducer(s, { type: "EVENT", event: { type: "step_end", duration_seconds: 1.5 } });
		s = liveReducer(s, { type: "EVENT", event: { type: "step_end", duration_seconds: 0.5 } });
		expect(s.elapsedMs).toBe(2000);
	});

	it("STARTING 重置 token/耗时（I2）", () => {
		let s = liveReducer(initialLiveState, { type: "STARTING", taskId: "t1" });
		s = liveReducer(s, { type: "EVENT", event: { type: "model_result", input_tokens: 100, output_tokens: 20 } });
		s = liveReducer(s, { type: "STARTING", taskId: "t2" });
		expect(s.tokens).toEqual({ in: 0, out: 0 });
		expect(s.elapsedMs).toBe(0);
	});

	it("EVENT tool_call(element_bbox) → 收集高亮（I3）", () => {
		let s = liveReducer(initialLiveState, { type: "STARTING", taskId: "t1" });
		s = liveReducer(s, {
			type: "EVENT",
			event: { type: "tool_call", action_index: 0, element_bbox: { left: 0.1, top: 0.2, width: 0.3, height: 0.4 } },
		});
		s = liveReducer(s, {
			type: "EVENT",
			event: { type: "tool_call", action_index: 1, element_bbox: { left: 0.5, top: 0.5, width: 0.1, height: 0.1 } },
		});
		expect(s.highlights).toEqual([
			{ index: 0, bbox: { left: 0.1, top: 0.2, width: 0.3, height: 0.4 } },
			{ index: 1, bbox: { left: 0.5, top: 0.5, width: 0.1, height: 0.1 } },
		]);
	});

	it("EVENT tool_call 无 element_bbox → 不收集高亮但仍入 events", () => {
		let s = liveReducer(initialLiveState, { type: "STARTING", taskId: "t1" });
		s = liveReducer(s, { type: "EVENT", event: { type: "tool_call", action_index: 0 } });
		expect(s.highlights).toEqual([]);
		expect(s.events).toHaveLength(1);
	});

	it("EVENT step_start → 清空上一步高亮（I3）", () => {
		let s = liveReducer(initialLiveState, { type: "STARTING", taskId: "t1" });
		s = liveReducer(s, {
			type: "EVENT",
			event: { type: "tool_call", action_index: 0, element_bbox: { left: 0.1, top: 0.1, width: 0.1, height: 0.1 } },
		});
		expect(s.highlights).toHaveLength(1);
		s = liveReducer(s, { type: "EVENT", event: { type: "step_start", step: 2 } });
		expect(s.highlights).toEqual([]);
	});
});
