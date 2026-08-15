import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";

// T2 H（M1）验收：live 状态提升后，切模式往返 RunView 状态不丢 + TopBar 状态点行为。
// AppShell 挂载全 shell → 需 mock 整个 api（RunView 的 task 端点 + FlowWorkspace 的 listFiles）。
const { apiMocks } = vi.hoisted(() => ({
	apiMocks: {
		startTask: vi.fn(),
		subscribeTaskEvents: vi.fn(),
		subscribeTaskFrames: vi.fn(),
		controlTask: vi.fn(),
		listFiles: vi.fn(),
		listTasks: vi.fn(), // T2 H（M2）：FlowWorkspace 侧栏 LiveZone 轮询源
		getTaskHistory: vi.fn(), // T2 I6（M5）：RunView 挂载拉任务历史
		pushTaskHistory: vi.fn(),
		listSkills: vi.fn(), // T2 I4（M8）：⌘K 面板数据源
		loadHistory: vi.fn(), // openFlow 预选加载
	},
}));

vi.mock("../api", () => apiMocks);

import AppShell from "../AppShell";

function findButton(container: HTMLElement, text: string): HTMLButtonElement {
	const btn = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === text);
	if (!btn) throw new Error(`button "${text}" not found`);
	return btn as HTMLButtonElement;
}

function setTaskText(container: HTMLElement, value: string) {
	const ta = container.querySelector("textarea") as HTMLTextAreaElement;
	fireEvent.change(ta, { target: { value } });
}

describe("AppShell live 状态提升（T2 H M1）", () => {
	beforeEach(() => {
		apiMocks.startTask.mockReset();
		apiMocks.subscribeTaskEvents.mockReset();
		apiMocks.subscribeTaskFrames.mockReset();
		apiMocks.controlTask.mockReset();
		apiMocks.listFiles.mockReset().mockResolvedValue([]);
		apiMocks.listTasks.mockReset().mockResolvedValue([]);
		apiMocks.getTaskHistory.mockReset().mockResolvedValue([]);
		apiMocks.pushTaskHistory.mockReset().mockResolvedValue(undefined);
		apiMocks.listSkills.mockReset().mockResolvedValue([]);
		apiMocks.loadHistory.mockReset().mockResolvedValue({ history: [] });
		apiMocks.subscribeTaskEvents.mockReturnValue({ close: vi.fn() } as unknown as EventSource);
		apiMocks.subscribeTaskFrames.mockReturnValue({ close: vi.fn() } as unknown as EventSource);
	});

	it("运行中切模式往返：RunView 状态保留 + SSE 重订 + 状态点可见且点击回运行视图", async () => {
		apiMocks.startTask.mockResolvedValue({ task_id: "t1" });
		const { container } = render(<AppShell />);
		// 起一个任务 → 运行中（出现暂停按钮 = phase running）
		setTaskText(container, "搜索猫");
		fireEvent.click(findButton(container, "▶ 发送"));
		await waitFor(() => expect(findButton(container, "⏸ 暂停")).toBeTruthy());
		expect(apiMocks.subscribeTaskEvents).toHaveBeenCalledTimes(1);
		// 切到流程库：RunView 卸载（textarea 消失），状态点仍在 topbar
		fireEvent.click(findButton(container, "流程库"));
		await waitFor(() => expect(apiMocks.listFiles).toHaveBeenCalled()); // 等 listFiles resolve，防 act 警告
		expect(container.querySelector("textarea")).toBeNull();
		expect(findButton(container, "● 任务运行中")).toBeTruthy();
		// 切回探索：暂停按钮仍在（state 未丢）+ SSE 按同一 taskId 重订
		fireEvent.click(findButton(container, "探索"));
		await waitFor(() => expect(findButton(container, "⏸ 暂停")).toBeTruthy());
		expect(apiMocks.subscribeTaskEvents).toHaveBeenCalledTimes(2);
		expect(apiMocks.subscribeTaskEvents).toHaveBeenLastCalledWith("t1", expect.any(Function));
		// 状态点点击 = 回运行视图（从流程库一键跳回）
		fireEvent.click(findButton(container, "流程库"));
		await waitFor(() => expect(apiMocks.listFiles).toHaveBeenCalled());
		fireEvent.click(findButton(container, "● 任务运行中"));
		await waitFor(() => expect(findButton(container, "⏸ 暂停")).toBeTruthy());
	});

	it("暂停后状态点显示「任务已暂停」", async () => {
		apiMocks.startTask.mockResolvedValue({ task_id: "t1" });
		apiMocks.controlTask.mockResolvedValue(undefined);
		const { container } = render(<AppShell />);
		setTaskText(container, "x");
		fireEvent.click(findButton(container, "▶ 发送"));
		await waitFor(() => expect(findButton(container, "⏸ 暂停")).toBeTruthy());
		fireEvent.click(findButton(container, "⏸ 暂停"));
		await waitFor(() => expect(findButton(container, "▶ 恢复")).toBeTruthy());
		fireEvent.click(findButton(container, "流程库"));
		await waitFor(() => expect(apiMocks.listFiles).toHaveBeenCalled());
		expect(findButton(container, "● 任务已暂停")).toBeTruthy();
	});

	it("无任务时 TopBar 无状态点", () => {
		const { container } = render(<AppShell />);
		expect(
			Array.from(container.querySelectorAll("button")).find((b) => b.className.includes("live-dot")),
		).toBeUndefined();
	});

	it("模型选择器：选择后 startTask 带该 model，且 localStorage 记忆（I5）", async () => {
		localStorage.removeItem("tw-web.model");
		apiMocks.startTask.mockResolvedValue({ task_id: "t1" });
		const { container, unmount } = render(<AppShell />);
		const modelInput = container.querySelector(".model-picker input") as HTMLInputElement;
		expect(modelInput.value).toBe(""); // 空 = 跟随设置默认
		fireEvent.change(modelInput, { target: { value: "glm-custom" } });
		expect(localStorage.getItem("tw-web.model")).toBe("glm-custom");
		// 发任务 → startTask 带 model（第 5 参）
		setTaskText(container, "x");
		fireEvent.click(findButton(container, "▶ 发送"));
		await waitFor(() =>
			expect(apiMocks.startTask).toHaveBeenCalledWith("x", undefined, false, "screenshots", "glm-custom"));
		// 重挂载（模拟刷新）→ 从 localStorage 恢复
		unmount();
		const { container: c2 } = render(<AppShell />);
		expect((c2.querySelector(".model-picker input") as HTMLInputElement).value).toBe("glm-custom");
		localStorage.removeItem("tw-web.model");
	});

	it("Ctrl+K 呼出命令面板 → 搜流程名 Enter → 打开流程（T2 I4 M8）", async () => {
		apiMocks.listFiles.mockResolvedValue(["douyin_upload.json"]);
		apiMocks.loadHistory.mockResolvedValue({
			history: [{ step_number: 1, model_output: { actions: [] }, result: [] }],
		});
		const { container } = render(<AppShell />);
		expect(container.querySelector(".cmdk")).toBeNull(); // 未呼出
		// Ctrl+K 呼出
		fireEvent.keyDown(window, { key: "k", ctrlKey: true });
		await waitFor(() => expect(container.querySelector(".cmdk")).toBeTruthy());
		// 等数据源加载 + 输入过滤 + Enter 执行「打开流程」
		const input = container.querySelector(".cmdk input") as HTMLInputElement;
		await waitFor(() => expect(container.textContent).toContain("打开流程 douyin_upload.json"));
		fireEvent.change(input, { target: { value: "douyin_upload" } });
		fireEvent.keyDown(input, { key: "Enter" });
		// 面板关闭 + 切到流程库 + 预选加载该流程
		await waitFor(() => expect(container.querySelector(".cmdk")).toBeNull());
		await waitFor(() => expect(apiMocks.loadHistory).toHaveBeenCalledWith("douyin_upload.json"));
		await waitFor(() => expect(container.textContent).toContain("已加载 douyin_upload.json"));
	});
});
