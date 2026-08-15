import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor, act } from "@testing-library/react";
import type { TaskEvent } from "../types";

// vi.hoisted：mock 工厂在 import 前执行，引用需稳定 → 用 hoisted 持有 vi.fn 实例
const { apiMocks } = vi.hoisted(() => ({
	apiMocks: {
		startTask: vi.fn(),
		subscribeTaskEvents: vi.fn(),
		subscribeTaskFrames: vi.fn(),
		controlTask: vi.fn(),
		getTaskHistory: vi.fn(),
		pushTaskHistory: vi.fn(),
	},
}));

vi.mock("../api", () => apiMocks);

import RunView from "../components/RunView";
import { LiveTaskProvider } from "../liveContext";

// T2 H（M1）：live state 提升到 AppShell 级 context，RunView 是消费者 → 测试须包 Provider
function renderRunView() {
	return render(
		<LiveTaskProvider>
			<RunView />
		</LiveTaskProvider>,
	);
}

function findButton(container: HTMLElement, text: string): HTMLButtonElement {
	const btn = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === text);
	if (!btn) throw new Error(`button "${text}" not found`);
	return btn as HTMLButtonElement;
}

function setTask(container: HTMLElement, value: string) {
	const ta = container.querySelector("textarea") as HTMLTextAreaElement;
	fireEvent.change(ta, { target: { value } });
}

describe("RunView (P6 M3)", () => {
	beforeEach(() => {
		apiMocks.startTask.mockReset();
		apiMocks.subscribeTaskEvents.mockReset();
		apiMocks.subscribeTaskFrames.mockReset();
		apiMocks.controlTask.mockReset();
		apiMocks.subscribeTaskEvents.mockReturnValue({ close: vi.fn() } as unknown as EventSource);
		apiMocks.subscribeTaskFrames.mockReturnValue({ close: vi.fn() } as unknown as EventSource);
		apiMocks.getTaskHistory.mockReset().mockResolvedValue([]);
		apiMocks.pushTaskHistory.mockReset().mockResolvedValue(undefined);
	});

	it("空 task 时发送按钮 disabled", () => {
		const { container } = renderRunView();
		expect(findButton(container, "▶ 发送").disabled).toBe(true);
	});

	it("输入 task 点击发送 → startTask 被调", async () => {
		apiMocks.startTask.mockResolvedValue({ task_id: "t1" });
		const { container } = renderRunView();
		setTask(container, "搜索猫");
		const btn = findButton(container, "▶ 发送");
		expect(btn.disabled).toBe(false);
		fireEvent.click(btn);
		await waitFor(() =>
			expect(apiMocks.startTask).toHaveBeenCalledWith("搜索猫", undefined, false, "screenshots", undefined),
		);
	});

	it("SSE done 后显示新任务按钮", async () => {
		let captured: ((e: TaskEvent) => void) | null = null;
		apiMocks.startTask.mockResolvedValue({ task_id: "t1" });
		apiMocks.subscribeTaskEvents.mockImplementation(
			(_id: string, onEvent: (e: TaskEvent) => void) => {
				captured = onEvent;
				return { close: vi.fn() } as unknown as EventSource;
			},
		);
		const { container } = renderRunView();
		setTask(container, "x");
		fireEvent.click(findButton(container, "▶ 发送"));
		await waitFor(() => expect(captured).not.toBeNull());
		act(() => {
			captured!({ type: "done", success: true });
		});
		await waitFor(() => expect(findButton(container, "新任务")).toBeTruthy());
	});

	it("运行中显示暂停/停止，暂停 → controlTask('pause') + 显示恢复", async () => {
		apiMocks.startTask.mockResolvedValue({ task_id: "t1" });
		const { container } = renderRunView();
		setTask(container, "x");
		fireEvent.click(findButton(container, "▶ 发送"));
		await waitFor(() => expect(apiMocks.startTask).toHaveBeenCalled());
		fireEvent.click(findButton(container, "⏸ 暂停"));
		await waitFor(() => expect(apiMocks.controlTask).toHaveBeenCalledWith("t1", "pause"));
		await waitFor(() => expect(findButton(container, "▶ 恢复")).toBeTruthy());
	});

	it("skill_active 事件 → 显示活动技能 chip（I1）", async () => {
		let captured: ((e: TaskEvent) => void) | null = null;
		apiMocks.startTask.mockResolvedValue({ task_id: "t1" });
		apiMocks.subscribeTaskEvents.mockImplementation(
			(_id: string, onEvent: (e: TaskEvent) => void) => {
				captured = onEvent;
				return { close: vi.fn() } as unknown as EventSource;
			},
		);
		const { container } = renderRunView();
		setTask(container, "x");
		fireEvent.click(findButton(container, "▶ 发送"));
		await waitFor(() => expect(captured).not.toBeNull());
		act(() => {
			captured!({ type: "skill_active", step: 1, host: "member.bilibili.com", skill_loaded: true, char_count: 120 });
		});
		await waitFor(() => expect(container.textContent).toContain("member.bilibili.com"));
		expect(container.textContent).toContain("120字");
	});

	it("切到直播模式 → startTask 带 livestream + 开 subscribeTaskFrames", async () => {
		apiMocks.startTask.mockResolvedValue({ task_id: "t1" });
		const { container } = renderRunView();
		fireEvent.change(container.querySelector("select")!, { target: { value: "livestream" } });
		setTask(container, "x");
		fireEvent.click(findButton(container, "▶ 发送"));
		await waitFor(() =>
			expect(apiMocks.startTask).toHaveBeenCalledWith("x", undefined, false, "livestream", undefined),
		);
		await waitFor(() =>
			expect(apiMocks.subscribeTaskFrames).toHaveBeenCalledWith("t1", expect.any(Function)),
		);
	});

	it("截图模式（默认）不开 subscribeTaskFrames", async () => {
		apiMocks.startTask.mockResolvedValue({ task_id: "t1" });
		const { container } = renderRunView();
		setTask(container, "x");
		fireEvent.click(findButton(container, "▶ 发送"));
		await waitFor(() => expect(apiMocks.startTask).toHaveBeenCalled());
		expect(apiMocks.subscribeTaskFrames).not.toHaveBeenCalled();
	});

	it("↑/↓ 翻任务历史：从最新往旧翻，翻到底回到空输入（I6）", async () => {
		apiMocks.getTaskHistory.mockResolvedValue(["任务A", "任务B"]); // 旧→新
		const { container } = renderRunView();
		await waitFor(() => expect(apiMocks.getTaskHistory).toHaveBeenCalled());
		const ta = () => container.querySelector("textarea") as HTMLTextAreaElement;
		fireEvent.keyDown(ta(), { key: "ArrowUp" });    // 新输入位 → 最新「任务B」
		await waitFor(() => expect(ta().value).toBe("任务B"));
		fireEvent.keyDown(ta(), { key: "ArrowUp" });    // → 更旧「任务A」
		await waitFor(() => expect(ta().value).toBe("任务A"));
		fireEvent.keyDown(ta(), { key: "ArrowDown" });  // → 「任务B」
		await waitFor(() => expect(ta().value).toBe("任务B"));
		fireEvent.keyDown(ta(), { key: "ArrowDown" });  // → 回到「新输入」空位
		await waitFor(() => expect(ta().value).toBe(""));
	});

	it("多行输入且光标不在首行 → ↑ 不翻历史（方向键还给 textarea）", async () => {
		apiMocks.getTaskHistory.mockResolvedValue(["任务A"]);
		const { container } = renderRunView();
		await waitFor(() => expect(apiMocks.getTaskHistory).toHaveBeenCalled());
		const ta = container.querySelector("textarea") as HTMLTextAreaElement;
		setTask(container, "line1\nline2");
		ta.selectionStart = ta.selectionEnd = ta.value.length; // 光标在末行
		fireEvent.keyDown(ta, { key: "ArrowUp" });
		expect(ta.value).toBe("line1\nline2"); // 未被历史回填覆盖
	});

	it("启动成功 → pushTaskHistory(trim 后文本)（I6）", async () => {
		apiMocks.startTask.mockResolvedValue({ task_id: "t1" });
		apiMocks.getTaskHistory.mockResolvedValue([]);
		const { container } = renderRunView();
		setTask(container, "  搜索猫  ");
		fireEvent.click(findButton(container, "▶ 发送"));
		await waitFor(() => expect(apiMocks.pushTaskHistory).toHaveBeenCalledWith("搜索猫"));
	});

	it("启动失败 → 不落历史（§8.8）", async () => {
		apiMocks.startTask.mockRejectedValue(new Error("Chrome 未启动"));
		apiMocks.getTaskHistory.mockResolvedValue([]);
		const { container } = renderRunView();
		setTask(container, "x");
		fireEvent.click(findButton(container, "▶ 发送"));
		await waitFor(() => expect(apiMocks.startTask).toHaveBeenCalled());
		expect(apiMocks.pushTaskHistory).not.toHaveBeenCalled();
	});

	it("点击时间线 tool_call → 右 Context 面板出 params/xpath；✕ 关闭（T2 G M7）", async () => {
		let captured: ((e: TaskEvent) => void) | null = null;
		apiMocks.startTask.mockResolvedValue({ task_id: "t1" });
		apiMocks.subscribeTaskEvents.mockImplementation(
			(_id: string, onEvent: (e: TaskEvent) => void) => {
				captured = onEvent;
				return { close: vi.fn() } as unknown as EventSource;
			},
		);
		const { container } = renderRunView();
		setTask(container, "x");
		fireEvent.click(findButton(container, "▶ 发送"));
		await waitFor(() => expect(captured).not.toBeNull());
		act(() => {
			captured!({ type: "tool_call", step: 1, action_name: "click", params: { index: 7 },
				element_index: 7, element_xpath: "//button[@title='发布']" });
		});
		// 时间线项可点击 → 选中 → 右栏渲染详情
		const item = Array.from(container.querySelectorAll(".evt-btn")).find((b) =>
			b.textContent?.includes("click")) as HTMLButtonElement;
		expect(item).toBeTruthy();
		fireEvent.click(item);
		await waitFor(() => expect(container.querySelector(".context-panel")).toBeTruthy());
		expect(container.textContent).toContain("//button[@title='发布']");
		expect(container.textContent).toContain("\"index\": 7");
		// ✕ 关闭 → 面板消失
		fireEvent.click(container.querySelector(".ctx-close")!);
		await waitFor(() => expect(container.querySelector(".context-panel")).toBeNull());
	});
});
