import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";
import type { LiveTaskItem } from "../types";

const { apiMocks } = vi.hoisted(() => ({
	apiMocks: {
		listTasks: vi.fn(),
	},
}));

vi.mock("../api", () => apiMocks);

import LiveZone from "../components/LiveZone";

function findButton(container: HTMLElement, text: string): HTMLButtonElement {
	const btn = Array.from(container.querySelectorAll("button")).find((b) =>
		b.textContent?.includes(text),
	);
	if (!btn) throw new Error(`button containing "${text}" not found`);
	return btn as HTMLButtonElement;
}

describe("LiveZone（T2 H M2）", () => {
	beforeEach(() => {
		apiMocks.listTasks.mockReset();
	});

	it("空列表 → 整区隐藏（不渲染「进行中」标题）", async () => {
		apiMocks.listTasks.mockResolvedValue([]);
		const { container } = render(<LiveZone onOpen={vi.fn()} />);
		await waitFor(() => expect(apiMocks.listTasks).toHaveBeenCalled());
		expect(container.textContent).not.toContain("进行中");
	});

	it("渲染 running/done 条目（状态点 + 截断 + 落库名）", async () => {
		const long = "一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十"; // 30 字 → 截断到 24
		const items: LiveTaskItem[] = [
			{ task_id: "t1", task: long, phase: "running", success: null, saved: null, viewport_mode: "screenshots" },
			{ task_id: "t2", task: "抖音封面设置", phase: "done", success: true, saved: "202608151030.json", viewport_mode: "livestream" },
		];
		apiMocks.listTasks.mockResolvedValue(items);
		const { container } = render(<LiveZone onOpen={vi.fn()} />);
		await waitFor(() => expect(container.textContent).toContain("进行中"));
		// running：● + 超长截断（24 字 + …），完整文本进 title
		expect(container.querySelector(".live-running")).toBeTruthy();
		expect(container.textContent).toContain(`${long.slice(0, 24)}…`);
		// done：✓ + 已存文件名
		expect(container.textContent).toContain("抖音封面设置");
		expect(container.textContent).toContain("202608151030.json");
	});

	it("点击条目 → onOpen 收到整个 item", async () => {
		const item: LiveTaskItem = { task_id: "t9", task: "搜索猫", phase: "paused", success: null, saved: null };
		apiMocks.listTasks.mockResolvedValue([item]);
		const onOpen = vi.fn();
		const { container } = render(<LiveZone onOpen={onOpen} />);
		await waitFor(() => expect(container.textContent).toContain("搜索猫"));
		fireEvent.click(findButton(container, "搜索猫"));
		expect(onOpen).toHaveBeenCalledWith(item);
	});

	it("listTasks 失败 → 静默（不渲染 zone，不抛错）", async () => {
		apiMocks.listTasks.mockRejectedValue(new Error("backend down"));
		const { container } = render(<LiveZone onOpen={vi.fn()} />);
		await waitFor(() => expect(apiMocks.listTasks).toHaveBeenCalled());
		expect(container.textContent).toBe("");
	});
});
