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
	},
}));

vi.mock("../api", () => apiMocks);

import RunView from "../components/RunView";

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
	});

	it("空 task 时发送按钮 disabled", () => {
		const { container } = render(<RunView />);
		expect(findButton(container, "▶ 发送").disabled).toBe(true);
	});

	it("输入 task 点击发送 → startTask 被调", async () => {
		apiMocks.startTask.mockResolvedValue({ task_id: "t1" });
		const { container } = render(<RunView />);
		setTask(container, "搜索猫");
		const btn = findButton(container, "▶ 发送");
		expect(btn.disabled).toBe(false);
		fireEvent.click(btn);
		await waitFor(() =>
			expect(apiMocks.startTask).toHaveBeenCalledWith("搜索猫", undefined, false, "screenshots"),
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
		const { container } = render(<RunView />);
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
		const { container } = render(<RunView />);
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
		const { container } = render(<RunView />);
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
		const { container } = render(<RunView />);
		fireEvent.change(container.querySelector("select")!, { target: { value: "livestream" } });
		setTask(container, "x");
		fireEvent.click(findButton(container, "▶ 发送"));
		await waitFor(() =>
			expect(apiMocks.startTask).toHaveBeenCalledWith("x", undefined, false, "livestream"),
		);
		await waitFor(() =>
			expect(apiMocks.subscribeTaskFrames).toHaveBeenCalledWith("t1", expect.any(Function)),
		);
	});

	it("截图模式（默认）不开 subscribeTaskFrames", async () => {
		apiMocks.startTask.mockResolvedValue({ task_id: "t1" });
		const { container } = render(<RunView />);
		setTask(container, "x");
		fireEvent.click(findButton(container, "▶ 发送"));
		await waitFor(() => expect(apiMocks.startTask).toHaveBeenCalled());
		expect(apiMocks.subscribeTaskFrames).not.toHaveBeenCalled();
	});
});
