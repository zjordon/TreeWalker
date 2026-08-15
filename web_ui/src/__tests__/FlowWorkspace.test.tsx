import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";

const { apiMocks } = vi.hoisted(() => ({
	apiMocks: {
		listFiles: vi.fn(),
		loadHistory: vi.fn(),
		saveHistory: vi.fn(),
		detectVariables: vi.fn(),
		rerun: vi.fn(),
		startBatch: vi.fn(),
		cancelBatch: vi.fn(),
		subscribeBatchProgress: vi.fn(),
		listTasks: vi.fn(),
	},
}));

vi.mock("../api", () => apiMocks);

import FlowWorkspace from "../FlowWorkspace";
import { LiveTaskProvider } from "../liveContext";

// T2 H（M2）：FlowWorkspace 经 useLiveTask 接「进行中」zone → 测试须包 Provider
function renderFlowWorkspace() {
	return render(
		<LiveTaskProvider>
			<FlowWorkspace />
		</LiveTaskProvider>,
	);
}

function findButton(container: HTMLElement, text: string): HTMLButtonElement | undefined {
	return Array.from(container.querySelectorAll("button")).find((b) => b.textContent === text) as
		| HTMLButtonElement
		| undefined;
}

describe("FlowWorkspace (P6 M4)", () => {
	beforeEach(() => {
		apiMocks.listFiles.mockReset();
		apiMocks.loadHistory.mockReset();
		apiMocks.saveHistory.mockReset();
		apiMocks.detectVariables.mockReset();
		apiMocks.rerun.mockReset();
		apiMocks.startBatch.mockReset();
		apiMocks.cancelBatch.mockReset();
		apiMocks.subscribeBatchProgress.mockReset();
		apiMocks.subscribeBatchProgress.mockReturnValue({ close: vi.fn() } as unknown as EventSource);
		apiMocks.listTasks.mockReset().mockResolvedValue([]); // T2 H（M2）：LiveZone 轮询源
	});

	it("sidebar 列出流程库文件", async () => {
		apiMocks.listFiles.mockResolvedValue(["a.json", "b.json"]);
		const { container } = renderFlowWorkspace();
		await waitFor(() => expect(apiMocks.listFiles).toHaveBeenCalled());
		expect(container.textContent).toContain("a.json");
		expect(container.textContent).toContain("b.json");
	});

	it("点击流程 → 加载并进入编辑 tab（状态显示已加载）", async () => {
		apiMocks.listFiles.mockResolvedValue(["a.json"]);
		apiMocks.loadHistory.mockResolvedValue({
			history: [{ step_number: 1, model_output: { actions: [] }, result: [] }],
		});
		const { container } = renderFlowWorkspace();
		await waitFor(() => expect(container.textContent).toContain("a.json"));
		fireEvent.click(findButton(container, "a.json")!);
		await waitFor(() => expect(apiMocks.loadHistory).toHaveBeenCalledWith("a.json"));
		await waitFor(() => expect(container.textContent).toContain("已加载 a.json"));
	});

	it("默认编辑 tab 无试跑；切到重放 tab 显示试跑按钮", async () => {
		apiMocks.listFiles.mockResolvedValue([]);
		const { container } = renderFlowWorkspace();
		await waitFor(() => expect(apiMocks.listFiles).toHaveBeenCalled());
		expect(findButton(container, "试跑")).toBeUndefined();
		fireEvent.click(findButton(container, "重放")!);
		await waitFor(() => expect(findButton(container, "试跑")).toBeTruthy());
	});
});
